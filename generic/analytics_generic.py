#!/usr/bin/env python3
"""
Generic analytics UI — works with any user-defined universe.

Imports the XIRR computation, trade duration calculation, and sweep logic
from the Nifty-specific analytics.py. Serves the same static/index.html
frontend. All paths point at the given universe's data directory.

Usage:
    python -m generic.analytics_generic --universe universes/midcapshop
    python -m generic.analytics_generic --universe universes/midcapshop --port 5001
"""

import json
import subprocess
import sys
import argparse
import itertools
import threading
import uuid
from datetime import date
from pathlib import Path
from flask import Flask, jsonify, request, send_from_directory, abort

# ── Import reusable computation functions from Nifty analytics ──────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from analytics import (
    xirr,
    compute_xirr_for_run,
    compute_trade_durations,
    _set_nested,
)

GENERIC_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = GENERIC_DIR.parent
# Reuse the same frontend — no fork needed
STATIC_DIR = PROJECT_ROOT / "static"


def create_app(universe_dir: Path) -> Flask:
    """Build a Flask app wired to a specific universe's data directory."""

    universe_dir = universe_dir.resolve()
    data_dir = universe_dir / "data"
    runs_dir = data_dir / "runs"
    configs_dir = universe_dir / "configs"
    example_cfg = configs_dir / "backtest_config.json"

    app = Flask(__name__, static_folder=str(STATIC_DIR))

    _active_runs: dict[str, dict] = {}
    _sweep_jobs: dict[str, dict] = {}
    _lock = threading.Lock()

    # ── Static routes ───────────────────────────────────────────────

    @app.route("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.route("/static/<path:filename>")
    def static_files(filename):
        return send_from_directory(STATIC_DIR, filename)

    # ── Runs ────────────────────────────────────────────────────────

    @app.route("/api/runs")
    def list_runs():
        runs = []
        if not runs_dir.exists():
            return jsonify(runs)
        for d in sorted(runs_dir.iterdir()):
            if not d.is_dir():
                continue
            summary_path = d / "summary.json"
            if summary_path.exists():
                try:
                    s = json.loads(summary_path.read_text())
                    runs.append({
                        "run_name": s.get("run_name", d.name),
                        "cagr_pct": s["returns"]["cagr_pct"],
                        "max_dd_pct": s["returns"]["max_drawdown_pct"],
                        "win_rate_pct": s["trades"]["win_rate_pct"],
                        "total_sells": s["trades"]["total_sells"],
                        "final_value": s["returns"]["final_value"],
                        "initial_capital": s["returns"]["initial_capital"],
                        "period": s["period"],
                    })
                except (json.JSONDecodeError, KeyError):
                    continue
        return jsonify(runs)

    @app.route("/api/runs/<run_name>/summary")
    def get_summary(run_name):
        p = runs_dir / run_name / "summary.json"
        if not p.exists():
            abort(404)
        return jsonify(json.loads(p.read_text()))

    @app.route("/api/runs/<run_name>/trades")
    def get_trades(run_name):
        p = runs_dir / run_name / "trade_log.jsonl"
        if not p.exists():
            abort(404)
        offset = int(request.args.get("offset", 0))
        limit = int(request.args.get("limit", 200))
        action_filter = request.args.get("action")
        trades = []
        with open(p) as f:
            for line in f:
                t = json.loads(line)
                if action_filter and t.get("action") != action_filter:
                    continue
                trades.append(t)
        total = len(trades)
        page = trades[offset:offset + limit]
        return jsonify({"total": total, "offset": offset, "trades": page})

    @app.route("/api/runs/<run_name>/equity")
    def get_equity(run_name):
        p = runs_dir / run_name / "daily_portfolio.jsonl"
        if not p.exists():
            abort(404)
        points = []
        with open(p) as f:
            for line in f:
                snap = json.loads(line)
                points.append({
                    "date": snap["date"],
                    "value": snap["portfolio_value"],
                    "cash": snap["cash"],
                    "open_lots": snap["open_lots"],
                    "open_positions": snap["open_positions"],
                    "lot_size": snap["lot_size"],
                    "cumulative_rpnl": snap["cumulative_rpnl"],
                })
        return jsonify(points)

    @app.route("/api/runs/<run_name>/xirr")
    def get_xirr(run_name):
        tlog = runs_dir / run_name / "trade_log.jsonl"
        dlog = runs_dir / run_name / "daily_portfolio.jsonl"
        slog = runs_dir / run_name / "summary.json"
        if not tlog.exists() or not dlog.exists():
            abort(404)
        is_end = None
        if slog.exists():
            s = json.loads(slog.read_text())
            is_end = s.get("config", {}).get("in_sample_end")
        result = compute_xirr_for_run(tlog, dlog, is_end)
        return jsonify(result)

    @app.route("/api/runs/<run_name>/durations")
    def get_durations(run_name):
        tlog = runs_dir / run_name / "trade_log.jsonl"
        if not tlog.exists():
            abort(404)
        return jsonify(compute_trade_durations(tlog))

    @app.route("/api/runs/<run_name>/monthly")
    def get_monthly_returns(run_name):
        p = runs_dir / run_name / "daily_portfolio.jsonl"
        if not p.exists():
            abort(404)
        daily = []
        with open(p) as f:
            for line in f:
                snap = json.loads(line)
                daily.append((snap["date"], snap["portfolio_value"]))
        if len(daily) < 2:
            return jsonify({"monthly": [], "yearly": []})

        months: dict[str, list] = {}
        for d_str, v in daily:
            ym = d_str[:7]
            if ym not in months:
                months[ym] = []
            months[ym].append((d_str, v))

        monthly = []
        prev_end_val = None
        for ym in sorted(months.keys()):
            entries = months[ym]
            end_val = entries[-1][1]
            if prev_end_val is not None and prev_end_val > 0:
                ret = (end_val / prev_end_val - 1) * 100
                monthly.append({"month": ym, "return_pct": round(ret, 2),
                                "end_value": round(end_val, 2)})
            else:
                monthly.append({"month": ym, "return_pct": 0,
                                "end_value": round(end_val, 2)})
            prev_end_val = end_val

        years: dict[str, list] = {}
        for d_str, v in daily:
            y = d_str[:4]
            if y not in years:
                years[y] = []
            years[y].append((d_str, v))

        yearly = []
        prev_year_end = None
        for y in sorted(years.keys()):
            entries = years[y]
            end_val = entries[-1][1]
            if prev_year_end is not None and prev_year_end > 0:
                ret = (end_val / prev_year_end - 1) * 100
                yearly.append({"year": y, "return_pct": round(ret, 2),
                               "end_value": round(end_val, 2)})
            else:
                yearly.append({"year": y, "return_pct": 0,
                               "end_value": round(end_val, 2)})
            prev_year_end = end_val

        return jsonify({"monthly": monthly, "yearly": yearly})

    @app.route("/api/runs/<run_name>/stats")
    def get_extended_stats(run_name):
        tlog = runs_dir / run_name / "trade_log.jsonl"
        if not tlog.exists():
            abort(404)
        trades = []
        with open(tlog) as f:
            for line in f:
                trades.append(json.loads(line))

        sells = [t for t in trades if t["action"] == "sell"]
        gross_profit = sum(t["realized_pnl_post_cost"] for t in sells
                           if t["realized_pnl_post_cost"] > 0)
        gross_loss = abs(sum(t["realized_pnl_post_cost"] for t in sells
                             if t["realized_pnl_post_cost"] < 0))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")

        sells_sorted = sorted(sells, key=lambda t: t.get("realized_pnl_post_cost", 0))
        largest_loss = sells_sorted[:5] if sells_sorted else []
        largest_win = sells_sorted[-5:][::-1] if sells_sorted else []

        max_consec = cur_consec = 0
        for t in sells:
            if t.get("realized_pnl_post_cost", 0) <= 0:
                cur_consec += 1
                max_consec = max(max_consec, cur_consec)
            else:
                cur_consec = 0

        wins = [t["realized_pnl_post_cost"] for t in sells if t["realized_pnl_post_cost"] > 0]
        losses = [t["realized_pnl_post_cost"] for t in sells if t["realized_pnl_post_cost"] <= 0]
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0

        return jsonify({
            "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else "Inf",
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "max_consecutive_losses": max_consec,
            "largest_wins": [{"symbol": t["symbol"], "date": t["date"],
                              "pnl": t["realized_pnl_post_cost"],
                              "gain_pct": t.get("gain_pct")} for t in largest_win],
            "largest_losses": [{"symbol": t["symbol"], "date": t["date"],
                                "pnl": t["realized_pnl_post_cost"],
                                "gain_pct": t.get("gain_pct")} for t in largest_loss],
            "total_sells": len(sells),
            "total_wins": len(wins),
            "total_losses": len(losses),
        })

    # ── Run trigger ─────────────────────────────────────────────────

    @app.route("/api/trigger", methods=["POST"])
    def trigger_run():
        cfg = request.get_json()
        if not cfg:
            return jsonify({"error": "No config provided"}), 400

        run_name = cfg.get("run_name", "unnamed")
        job_id = str(uuid.uuid4())[:8]

        cfg_path = runs_dir / f"_trigger_cfg_{job_id}.json"
        runs_dir.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(json.dumps(cfg, indent=2))

        proc = subprocess.Popen(
            [sys.executable, "-m", "generic.backtest_generic",
             "--universe", str(universe_dir),
             "-c", str(cfg_path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            cwd=str(PROJECT_ROOT),
        )

        with _lock:
            _active_runs[job_id] = {
                "proc": proc, "run_name": run_name, "cfg_path": str(cfg_path),
            }

        def _wait():
            proc.wait()
            cfg_path.unlink(missing_ok=True)
            with _lock:
                _active_runs[job_id]["returncode"] = proc.returncode
                _active_runs[job_id]["stdout"] = proc.stdout.read() if proc.stdout else ""
                _active_runs[job_id]["stderr"] = proc.stderr.read() if proc.stderr else ""

        threading.Thread(target=_wait, daemon=True).start()
        return jsonify({"job_id": job_id, "run_name": run_name, "status": "started"})

    @app.route("/api/trigger/<job_id>")
    def trigger_status(job_id):
        with _lock:
            job = _active_runs.get(job_id)
        if not job:
            return jsonify({"error": "Unknown job"}), 404
        if "returncode" in job:
            status = "success" if job["returncode"] == 0 else "failed"
            return jsonify({
                "job_id": job_id, "run_name": job["run_name"],
                "status": status, "returncode": job["returncode"],
                "stderr": job.get("stderr", "")[-500:],
            })
        return jsonify({"job_id": job_id, "run_name": job["run_name"],
                        "status": "running"})

    # ── Parameter sweep (IN-SAMPLE ONLY) ────────────────────────────

    @app.route("/api/sweep", methods=["POST"])
    def start_sweep():
        body = request.get_json()
        if not body:
            return jsonify({"error": "No body"}), 400

        base_config = body.get("base_config")
        params = body.get("params")
        if not base_config or not params:
            return jsonify({"error": "Need base_config and params"}), 400

        is_end = base_config.get("in_sample_end")
        if not is_end:
            return jsonify({"error": "base_config must have in_sample_end"}), 400

        keys = list(params.keys())
        values = list(params.values())
        n_combos = 1
        for v in values:
            n_combos *= len(v)

        if n_combos > 100:
            return jsonify({"error": f"Grid has {n_combos} combos — cap at 100"}), 400

        job_id = str(uuid.uuid4())[:8]
        _sweep_jobs[job_id] = {
            "done": False, "total": n_combos, "completed": 0,
            "results": [],
            "warning": f"Grid has {n_combos} combinations" if n_combos > 25 else None,
        }

        def _worker():
            combos = list(itertools.product(*values))
            _sweep_jobs[job_id]["total"] = len(combos)
            results = []

            for i, combo in enumerate(combos):
                cfg = json.loads(json.dumps(base_config))
                label_parts = []
                for k, v in zip(keys, combo):
                    _set_nested(cfg, k, v)
                    label_parts.append(f"{k.split('.')[-1]}={v}")

                cfg["backtest_end"] = is_end
                sweep_name = f"{cfg['run_name']}_sweep_{'_'.join(label_parts)}"
                cfg["run_name"] = sweep_name

                tmp_cfg_path = runs_dir / f"_sweep_cfg_{job_id}_{i}.json"
                runs_dir.mkdir(parents=True, exist_ok=True)
                tmp_cfg_path.write_text(json.dumps(cfg, indent=2))

                try:
                    proc = subprocess.run(
                        [sys.executable, "-m", "generic.backtest_generic",
                         "--universe", str(universe_dir),
                         "-c", str(tmp_cfg_path)],
                        capture_output=True, text=True, timeout=300,
                        cwd=str(PROJECT_ROOT),
                    )
                    summary_path = runs_dir / sweep_name / "summary.json"
                    if summary_path.exists():
                        summary = json.loads(summary_path.read_text())
                        trade_log_path = runs_dir / sweep_name / "trade_log.jsonl"
                        daily_portfolio_path = runs_dir / sweep_name / "daily_portfolio.jsonl"
                        xirr_result = compute_xirr_for_run(
                            trade_log_path, daily_portfolio_path)
                        results.append({
                            "params": dict(zip(keys, combo)),
                            "run_name": sweep_name,
                            "xirr_pct": xirr_result["overall"],
                            "cagr_pct": summary["returns"]["cagr_pct"],
                            "max_dd_pct": summary["returns"]["max_drawdown_pct"],
                            "win_rate_pct": summary["trades"]["win_rate_pct"],
                            "total_sells": summary["trades"]["total_sells"],
                            "final_value": summary["returns"]["final_value"],
                            "status": "ok",
                        })
                    else:
                        results.append({
                            "params": dict(zip(keys, combo)),
                            "run_name": sweep_name, "status": "error",
                            "error": proc.stderr[-500:] if proc.stderr else "no summary",
                        })
                except subprocess.TimeoutExpired:
                    results.append({
                        "params": dict(zip(keys, combo)),
                        "run_name": sweep_name, "status": "timeout",
                    })
                finally:
                    tmp_cfg_path.unlink(missing_ok=True)

                _sweep_jobs[job_id]["completed"] = i + 1
                _sweep_jobs[job_id]["results"] = results

            _sweep_jobs[job_id]["done"] = True

        threading.Thread(target=_worker, daemon=True).start()

        resp = {"job_id": job_id, "total_combos": n_combos, "status": "started"}
        if n_combos > 25:
            resp["warning"] = f"Large grid: {n_combos} combinations"
        return jsonify(resp)

    @app.route("/api/sweep/<job_id>")
    def sweep_status(job_id):
        job = _sweep_jobs.get(job_id)
        if not job:
            return jsonify({"error": "Unknown sweep job"}), 404
        return jsonify({
            "job_id": job_id, "done": job["done"], "total": job["total"],
            "completed": job["completed"], "results": job["results"],
            "warning": job.get("warning"),
        })

    @app.route("/api/example-config")
    def get_example_config():
        if example_cfg.exists():
            return jsonify(json.loads(example_cfg.read_text()))
        # Fall back to the project-level example
        root_example = PROJECT_ROOT / "backtest_config.example.json"
        if root_example.exists():
            return jsonify(json.loads(root_example.read_text()))
        return jsonify({}), 404

    return app


# ═════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Generic analytics UI — any universe",
    )
    parser.add_argument(
        "--universe", required=True,
        help="Path to universe directory (e.g. universes/midcapshop)",
    )
    parser.add_argument("--port", type=int, default=5001)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    universe_dir = Path(args.universe)
    if not universe_dir.is_absolute():
        universe_dir = GENERIC_DIR / universe_dir
    universe_dir = universe_dir.resolve()

    if not universe_dir.is_dir():
        sys.exit(f"Universe directory not found: {universe_dir}")

    app = create_app(universe_dir)

    print(f"\nGeneric Analytics — http://{args.host}:{args.port}")
    print(f"  Universe : {universe_dir.name}")
    print(f"  Data     : {universe_dir / 'data'}")
    print(f"  Frontend : {STATIC_DIR / 'index.html'}\n")

    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
NiftyShop Stage 4 — Analytics UI

Local web app for inspecting backtest runs, visualizing results,
triggering new runs, and running parameter sweeps.

Usage:
    python analytics.py                    # default port 5000
    python analytics.py --port 8080        # custom port
"""

import json
import math
import subprocess
import sys
import argparse
import itertools
import threading
import uuid
from datetime import date, timedelta
from pathlib import Path
from flask import Flask, jsonify, request, send_from_directory, abort

# ── Paths ────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
RUNS_DIR = DATA_DIR / "runs"
STATIC_DIR = BASE_DIR / "static"
EXAMPLE_CFG = BASE_DIR / "backtest_config.example.json"

app = Flask(__name__, static_folder=str(STATIC_DIR))

# Track running backtest processes
_active_runs: dict[str, dict] = {}  # job_id → {"proc": Popen, "run_name": str, ...}
_lock = threading.Lock()


# ═════════════════════════════════════════════════════════════════════
# XIRR COMPUTATION
# ═════════════════════════════════════════════════════════════════════

def xirr(cashflows: list[tuple[date, float]], guess: float = 0.1,
         tol: float = 1e-9, max_iter: int = 200) -> float | None:
    """
    Compute XIRR via Newton's method on actual cash-flow timing.

    cashflows: list of (date, amount) — negative for outflows (buys),
               positive for inflows (sells + terminal mark-to-market).
    Returns annualized rate as a fraction (0.15 = 15%), or None if
    no convergence.
    """
    if not cashflows or len(cashflows) < 2:
        return None

    d0 = cashflows[0][0]
    # Convert dates to year fractions
    years = [(d - d0).days / 365.25 for d, _ in cashflows]
    amounts = [a for _, a in cashflows]

    def npv(r: float) -> float:
        return sum(a / (1 + r) ** t for a, t in zip(amounts, years))

    def dnpv(r: float) -> float:
        return sum(-t * a / (1 + r) ** (t + 1) for a, t in zip(amounts, years))

    r = guess
    for _ in range(max_iter):
        f = npv(r)
        df = dnpv(r)
        if abs(df) < 1e-14:
            break
        r_new = r - f / df
        if abs(r_new - r) < tol:
            return r_new
        r = r_new
        # Guard against divergence
        if abs(r) > 10:
            return None

    # Final check
    if abs(npv(r)) < 1e-6:
        return r
    return None


def compute_xirr_for_run(trade_log_path: Path, daily_portfolio_path: Path,
                         is_end: str | None = None) -> dict:
    """
    Build cashflows from trade_log and compute XIRR.

    Returns dict with overall, in_sample, out_of_sample XIRRs.
    """
    trades = []
    with open(trade_log_path) as f:
        for line in f:
            trades.append(json.loads(line))

    # Read last daily snapshot for terminal mark-to-market of open positions
    last_snap = None
    with open(daily_portfolio_path) as f:
        for line in f:
            last_snap = json.loads(line)

    # Build cashflows: buy = negative, sell = positive
    all_cfs: list[tuple[date, float]] = []
    is_cfs: list[tuple[date, float]] = []
    oos_cfs: list[tuple[date, float]] = []

    is_end_date = date.fromisoformat(is_end) if is_end else None

    for t in trades:
        d = date.fromisoformat(t["date"])
        if t["action"] == "buy":
            amt = -(t["qty"] * t["price"] + t.get("brokerage", 0))
            all_cfs.append((d, amt))
            if is_end_date:
                if d <= is_end_date:
                    is_cfs.append((d, amt))
                else:
                    oos_cfs.append((d, amt))
        elif t["action"] == "sell":
            cost_at_sell = t.get("costs", 0)
            amt = t["qty"] * t["price"] - cost_at_sell
            all_cfs.append((d, amt))
            if is_end_date:
                if d <= is_end_date:
                    is_cfs.append((d, amt))
                else:
                    oos_cfs.append((d, amt))

    # Terminal value for open positions (mark-to-market at last day's close)
    if last_snap and last_snap.get("positions"):
        d = date.fromisoformat(last_snap["date"])
        terminal_val = sum(
            p["current_value"] for p in last_snap["positions"].values()
        )
        if terminal_val > 0:
            all_cfs.append((d, terminal_val))
            if is_end_date and d > is_end_date:
                oos_cfs.append((d, terminal_val))
            elif is_end_date:
                is_cfs.append((d, terminal_val))

    # Also add terminal cash if there's remaining cash (represents returned capital)
    # Actually, cash is just the accumulation of sell proceeds minus buy costs,
    # which are already captured. Terminal value for XIRR = open positions only.

    result = {"overall": None, "in_sample": None, "out_of_sample": None}

    overall = xirr(sorted(all_cfs, key=lambda x: x[0]))
    result["overall"] = round(overall * 100, 4) if overall is not None else None

    if is_cfs:
        # For IS XIRR, add mark-to-market of positions at IS end
        # We approximate by using the last IS snapshot
        is_terminal_val = 0
        if is_end_date and daily_portfolio_path.exists():
            with open(daily_portfolio_path) as f:
                for line in f:
                    snap = json.loads(line)
                    if snap["date"] == is_end:
                        is_terminal_val = sum(
                            p["current_value"]
                            for p in snap.get("positions", {}).values()
                        )
                        is_cfs.append((is_end_date, is_terminal_val + snap["cash"]))
                        break
        is_r = xirr(sorted(is_cfs, key=lambda x: x[0]))
        result["in_sample"] = round(is_r * 100, 4) if is_r is not None else None

    if oos_cfs:
        # For OOS start, the "investment" is the portfolio value at IS end
        if is_end_date and daily_portfolio_path.exists():
            with open(daily_portfolio_path) as f:
                for line in f:
                    snap = json.loads(line)
                    if snap["date"] == is_end:
                        oos_start_val = snap["portfolio_value"]
                        # Insert as initial outflow at IS end
                        oos_cfs.insert(0, (is_end_date, -oos_start_val))
                        break
        oos_r = xirr(sorted(oos_cfs, key=lambda x: x[0]))
        result["out_of_sample"] = round(oos_r * 100, 4) if oos_r is not None else None

    return result


# ═════════════════════════════════════════════════════════════════════
# TRADE DURATION — quantity-weighted per-lot basis
# ═════════════════════════════════════════════════════════════════════

def compute_trade_durations(trade_log_path: Path) -> list[dict]:
    """
    Compute trade duration on a quantity-weighted per-lot basis.

    For each sell, duration = Σ(lot_qty × lot_holding_days) / total_qty.
    Returns list of {symbol, sell_date, duration_days, qty, avg_cost, sell_price, gain_pct}.
    """
    trades = []
    with open(trade_log_path) as f:
        for line in f:
            trades.append(json.loads(line))

    # Track open lots per symbol
    open_lots: dict[str, list[dict]] = {}  # symbol → [{"date": ..., "qty": ...}]
    durations = []

    for t in trades:
        if t["action"] == "buy":
            sym = t["symbol"]
            if sym not in open_lots:
                open_lots[sym] = []
            open_lots[sym].append({
                "date": date.fromisoformat(t["date"]),
                "qty": t["qty"],
            })
        elif t["action"] == "sell":
            sym = t["symbol"]
            sell_date = date.fromisoformat(t["date"])
            lots = open_lots.pop(sym, [])
            if not lots:
                continue

            total_qty = sum(l["qty"] for l in lots)
            weighted_days = sum(
                l["qty"] * (sell_date - l["date"]).days for l in lots
            )
            dur = weighted_days / total_qty if total_qty else 0

            durations.append({
                "symbol": sym,
                "sell_date": sell_date.isoformat(),
                "duration_days": round(dur, 1),
                "qty": total_qty,
                "avg_cost": t.get("avg_cost", 0),
                "sell_price": t["price"],
                "gain_pct": t.get("gain_pct", 0),
                "realized_pnl": t.get("realized_pnl_post_cost", 0),
            })

    return durations


# ═════════════════════════════════════════════════════════════════════
# PARAMETER SWEEP — IN-SAMPLE ONLY
# ═════════════════════════════════════════════════════════════════════

_sweep_jobs: dict[str, dict] = {}


def _run_sweep_worker(job_id: str, base_config: dict, param_grid: dict,
                      is_end: str):
    """Background worker: run sweep combos sequentially, IS only."""
    keys = list(param_grid.keys())
    values = list(param_grid.values())
    combos = list(itertools.product(*values))

    _sweep_jobs[job_id]["total"] = len(combos)
    results = []

    for i, combo in enumerate(combos):
        # Deep copy base config
        cfg = json.loads(json.dumps(base_config))

        # Override with sweep params
        label_parts = []
        for k, v in zip(keys, combo):
            _set_nested(cfg, k, v)
            label_parts.append(f"{k.split('.')[-1]}={v}")

        # Force IS-only: set backtest_end = in_sample_end
        cfg["backtest_end"] = is_end
        sweep_name = f"{cfg['run_name']}_sweep_{'_'.join(label_parts)}"
        cfg["run_name"] = sweep_name

        # Write temp config
        tmp_cfg_path = RUNS_DIR / f"_sweep_cfg_{job_id}_{i}.json"
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        tmp_cfg_path.write_text(json.dumps(cfg, indent=2))

        try:
            proc = subprocess.run(
                [sys.executable, str(BASE_DIR / "backtest.py"),
                 "-c", str(tmp_cfg_path)],
                capture_output=True, text=True, timeout=300,
            )
            # Read summary if it was produced
            summary_path = RUNS_DIR / sweep_name / "summary.json"
            if summary_path.exists():
                summary = json.loads(summary_path.read_text())
                results.append({
                    "params": dict(zip(keys, combo)),
                    "run_name": sweep_name,
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
                    "run_name": sweep_name,
                    "status": "error",
                    "error": proc.stderr[-500:] if proc.stderr else "no summary",
                })
        except subprocess.TimeoutExpired:
            results.append({
                "params": dict(zip(keys, combo)),
                "run_name": sweep_name,
                "status": "timeout",
            })
        finally:
            tmp_cfg_path.unlink(missing_ok=True)

        _sweep_jobs[job_id]["completed"] = i + 1
        _sweep_jobs[job_id]["results"] = results

    _sweep_jobs[job_id]["done"] = True


def _set_nested(d: dict, dotted_key: str, value):
    """Set a value in a nested dict using dot notation, e.g. 'entry.dma_period'."""
    keys = dotted_key.split(".")
    for k in keys[:-1]:
        d = d[k]
    d[keys[-1]] = value


# ═════════════════════════════════════════════════════════════════════
# API ROUTES
# ═════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(STATIC_DIR, filename)


# ── Runs ─────────────────────────────────────────────────────────────

@app.route("/api/runs")
def list_runs():
    """List all completed backtest runs."""
    runs = []
    if not RUNS_DIR.exists():
        return jsonify(runs)

    for d in sorted(RUNS_DIR.iterdir()):
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
def get_summary(run_name: str):
    """Full summary JSON for a run."""
    p = RUNS_DIR / run_name / "summary.json"
    if not p.exists():
        abort(404)
    return jsonify(json.loads(p.read_text()))


@app.route("/api/runs/<run_name>/trades")
def get_trades(run_name: str):
    """Trade log — supports pagination via ?offset=&limit=."""
    p = RUNS_DIR / run_name / "trade_log.jsonl"
    if not p.exists():
        abort(404)

    offset = int(request.args.get("offset", 0))
    limit = int(request.args.get("limit", 200))
    action_filter = request.args.get("action")  # buy, sell, skip, recalc

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
def get_equity(run_name: str):
    """Daily portfolio values for equity curve + drawdown."""
    p = RUNS_DIR / run_name / "daily_portfolio.jsonl"
    if not p.exists():
        abort(404)

    # Downsample if too many points — keep every Nth for chart
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
def get_xirr(run_name: str):
    """Compute XIRR for a run (overall, IS, OOS)."""
    tlog = RUNS_DIR / run_name / "trade_log.jsonl"
    dlog = RUNS_DIR / run_name / "daily_portfolio.jsonl"
    slog = RUNS_DIR / run_name / "summary.json"
    if not tlog.exists() or not dlog.exists():
        abort(404)

    is_end = None
    if slog.exists():
        s = json.loads(slog.read_text())
        is_end = s.get("config", {}).get("in_sample_end")

    result = compute_xirr_for_run(tlog, dlog, is_end)
    return jsonify(result)


@app.route("/api/runs/<run_name>/durations")
def get_durations(run_name: str):
    """Trade durations (quantity-weighted per-lot)."""
    tlog = RUNS_DIR / run_name / "trade_log.jsonl"
    if not tlog.exists():
        abort(404)
    return jsonify(compute_trade_durations(tlog))


@app.route("/api/runs/<run_name>/monthly")
def get_monthly_returns(run_name: str):
    """Monthly and yearly return aggregation."""
    p = RUNS_DIR / run_name / "daily_portfolio.jsonl"
    if not p.exists():
        abort(404)

    daily = []
    with open(p) as f:
        for line in f:
            snap = json.loads(line)
            daily.append((snap["date"], snap["portfolio_value"]))

    if len(daily) < 2:
        return jsonify({"monthly": [], "yearly": []})

    # First value each month and last value each month
    months: dict[str, list[tuple[str, float]]] = {}
    for d_str, v in daily:
        ym = d_str[:7]  # YYYY-MM
        if ym not in months:
            months[ym] = []
        months[ym].append((d_str, v))

    monthly = []
    sorted_months = sorted(months.keys())
    prev_end_val = None
    for ym in sorted_months:
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

    # Yearly
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
def get_extended_stats(run_name: str):
    """Extended stats: profit factor, largest trades, max consecutive losses,
    capital exposure over time."""
    tlog = RUNS_DIR / run_name / "trade_log.jsonl"
    if not tlog.exists():
        abort(404)

    trades = []
    with open(tlog) as f:
        for line in f:
            trades.append(json.loads(line))

    sells = [t for t in trades if t["action"] == "sell"]

    # Profit factor
    gross_profit = sum(t["realized_pnl_post_cost"] for t in sells
                       if t["realized_pnl_post_cost"] > 0)
    gross_loss = abs(sum(t["realized_pnl_post_cost"] for t in sells
                         if t["realized_pnl_post_cost"] < 0))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")

    # Largest winning and losing trades
    sells_sorted_gain = sorted(sells, key=lambda t: t.get("realized_pnl_post_cost", 0))
    largest_loss = sells_sorted_gain[:5] if sells_sorted_gain else []
    largest_win = sells_sorted_gain[-5:][::-1] if sells_sorted_gain else []

    # Max consecutive losses
    max_consec = 0
    cur_consec = 0
    for t in sells:
        if t.get("realized_pnl_post_cost", 0) <= 0:
            cur_consec += 1
            max_consec = max(max_consec, cur_consec)
        else:
            cur_consec = 0

    # Average win / average loss
    wins = [t["realized_pnl_post_cost"] for t in sells
            if t["realized_pnl_post_cost"] > 0]
    losses = [t["realized_pnl_post_cost"] for t in sells
              if t["realized_pnl_post_cost"] <= 0]
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = sum(losses) / len(losses) if losses else 0

    return jsonify({
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else "Inf",
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "max_consecutive_losses": max_consec,
        "largest_wins": [{
            "symbol": t["symbol"], "date": t["date"],
            "pnl": t["realized_pnl_post_cost"], "gain_pct": t.get("gain_pct"),
        } for t in largest_win],
        "largest_losses": [{
            "symbol": t["symbol"], "date": t["date"],
            "pnl": t["realized_pnl_post_cost"], "gain_pct": t.get("gain_pct"),
        } for t in largest_loss],
        "total_sells": len(sells),
        "total_wins": len(wins),
        "total_losses": len(losses),
    })


# ── Run trigger ──────────────────────────────────────────────────────

@app.route("/api/trigger", methods=["POST"])
def trigger_run():
    """
    Trigger a new backtest run. Accepts config JSON in body.
    Does NOT wire in Kite fetch (Stage 1) — backtest only.
    """
    cfg = request.get_json()
    if not cfg:
        return jsonify({"error": "No config provided"}), 400

    run_name = cfg.get("run_name", "unnamed")
    job_id = str(uuid.uuid4())[:8]

    # Write config to temp file
    cfg_path = RUNS_DIR / f"_trigger_cfg_{job_id}.json"
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(cfg, indent=2))

    proc = subprocess.Popen(
        [sys.executable, str(BASE_DIR / "backtest.py"), "-c", str(cfg_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )

    with _lock:
        _active_runs[job_id] = {
            "proc": proc,
            "run_name": run_name,
            "cfg_path": str(cfg_path),
        }

    # Monitor in background thread
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
def trigger_status(job_id: str):
    """Check status of a triggered run."""
    with _lock:
        job = _active_runs.get(job_id)
    if not job:
        return jsonify({"error": "Unknown job"}), 404

    if "returncode" in job:
        status = "success" if job["returncode"] == 0 else "failed"
        return jsonify({
            "job_id": job_id,
            "run_name": job["run_name"],
            "status": status,
            "returncode": job["returncode"],
            "stderr": job.get("stderr", "")[-500:],
        })
    return jsonify({"job_id": job_id, "run_name": job["run_name"],
                    "status": "running"})


# ── Parameter sweep ──────────────────────────────────────────────────

@app.route("/api/sweep", methods=["POST"])
def start_sweep():
    """
    Start a parameter sweep. IN-SAMPLE ONLY — backtest_end is forced to
    in_sample_end. Out-of-sample is structurally unavailable to sweep.

    Body JSON:
    {
        "base_config": { ... full backtest config ... },
        "params": {
            "entry.dma_period": [10, 20, 30],
            "entry.min_pct_below_dma": [1.5, 2.0, 3.0]
        }
    }
    """
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

    # Count combos
    keys = list(params.keys())
    values = list(params.values())
    n_combos = 1
    for v in values:
        n_combos *= len(v)

    if n_combos > 100:
        return jsonify({"error": f"Grid has {n_combos} combos — cap at 100"}), 400

    job_id = str(uuid.uuid4())[:8]
    _sweep_jobs[job_id] = {
        "done": False,
        "total": n_combos,
        "completed": 0,
        "results": [],
        "warning": f"Grid has {n_combos} combinations" if n_combos > 25 else None,
    }

    t = threading.Thread(
        target=_run_sweep_worker,
        args=(job_id, base_config, params, is_end),
        daemon=True,
    )
    t.start()

    resp = {"job_id": job_id, "total_combos": n_combos, "status": "started"}
    if n_combos > 25:
        resp["warning"] = f"Large grid: {n_combos} combinations"
    return jsonify(resp)


@app.route("/api/sweep/<job_id>")
def sweep_status(job_id: str):
    """Poll sweep progress."""
    job = _sweep_jobs.get(job_id)
    if not job:
        return jsonify({"error": "Unknown sweep job"}), 404
    return jsonify({
        "job_id": job_id,
        "done": job["done"],
        "total": job["total"],
        "completed": job["completed"],
        "results": job["results"],
        "warning": job.get("warning"),
    })


@app.route("/api/example-config")
def get_example_config():
    """Return example config for the UI to pre-fill."""
    if EXAMPLE_CFG.exists():
        return jsonify(json.loads(EXAMPLE_CFG.read_text()))
    return jsonify({}), 404


# ═════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="NiftyShop Analytics UI")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    print(f"\nNiftyShop Analytics — http://{args.host}:{args.port}")
    print(f"  Runs directory: {RUNS_DIR}")
    print(f"  {'Debug mode ON' if args.debug else ''}\n")

    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()

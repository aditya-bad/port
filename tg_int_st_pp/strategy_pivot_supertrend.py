#!/usr/bin/env python3
"""
tg_int_st_pp — Pivot Points + SuperTrend(7,3) intraday strategy.

Standalone: no imports from the rest of the `port` repo.

STRATEGY (as specified):
  Long entry  — 5-min close is above any of R1/R2/R3 AND SuperTrend is green (up).
  Short entry — 5-min close is below any of S1/S2/S3 AND SuperTrend is red (down).
  Exit        — SuperTrend flips color -> exit at the NEXT candle's open.
                OR force-exit at end-of-day cutoff, whichever comes first.
  Only 1 open position at a time. Multiple trades/day allowed — a fresh
  entry (long or short) can fire immediately after an exit, same candle
  or later, as long as no position is currently open.

ASSUMPTIONS MADE (spec had gaps — flagged here, not guessed silently;
change the constants below if any of these are wrong):

  1. Pivot formula = CLASSIC/STANDARD pivots, computed from the PREVIOUS
     trading day's high/low/close:
        P  = (H + L + C) / 3
        R1 = 2P - L      S1 = 2P - H
        R2 = P + (H-L)   S2 = P - (H-L)
        R3 = H + 2(P-L)  S3 = L - 2(H-P)
     "Above any of R1/R2/R3" collapses to "close > R1" since R1 < R2 < R3
     by construction (same for S1 > S2 > S3) — implemented as an explicit
     "any" check over the level list regardless, not hardcoded to R1/S1,
     so it stays correct if you swap in a non-monotonic pivot system later.
     Previous day's H/L/C is derived from that day's 5-min candles
     (max high, min low, last 5-min candle's close as EOD proxy) since
     this dataset only has 5-min data, not separate daily OHLC.
     → The FIRST day in any dataset has no previous day inside the
       window, so it's excluded from trading entirely (flagged in output).

  2. SuperTrend(7,3) = ATR period 7, multiplier 3, Wilder-smoothed ATR
     (RMA) — the standard convention on every charting platform. Computed
     CONTINUOUSLY across the whole multi-day 5-min series (not reset each
     day) — this is how TradingView/Kite charts compute it by default.

  3. Force-exit: 3:00 PM (15:00), exit at the OPEN of the 15:00 candle —
     same "exit at candle open" convention as the ST-flip exit, so both
     exit types are consistent. No new entries are taken at/after 15:00.

  4. Entry price = the CLOSE of the signal candle itself (the spec says
     "if close is above R1 ... enter trade", with no next-candle language
     for entries — only exits say "next candle open").

  5. Same-candle re-entry after an exit is allowed (exit then immediately
     re-evaluate entry conditions within the same loop pass) — never two
     positions open simultaneously, but no cooldown period is imposed.

Usage:
    python strategy_pivot_supertrend.py                    # uses data/NIFTY50_5minute.json
    python strategy_pivot_supertrend.py --input path.json
    python strategy_pivot_supertrend.py --force-exit 15:15  # override cutoff time
"""

import json
import sys
import argparse
from collections import OrderedDict
from datetime import datetime, date, time as dtime
from pathlib import Path

THIS_DIR = Path(__file__).parent
DATA_DIR = THIS_DIR / "data"
DEFAULT_INPUT = DATA_DIR / "NIFTY50_5minute.json"
TRADES_OUTPUT = DATA_DIR / "trades_pivot_supertrend.json"
SUMMARY_OUTPUT = DATA_DIR / "summary_pivot_supertrend.json"

ST_PERIOD = 7
ST_MULTIPLIER = 3
DEFAULT_FORCE_EXIT_TIME = dtime(15, 0)   # 3:00 PM — see assumption #3 above
R_KEYS = ("R1", "R2", "R3")
S_KEYS = ("S1", "S2", "S3")


# ═════════════════════════════════════════════════════════════════════
# DATA LOADING
# ═════════════════════════════════════════════════════════════════════

def load_candles(path: Path) -> list[dict]:
    """Load 5-min candles, parse timestamps, sort chronologically."""
    raw = json.loads(path.read_text())
    for c in raw:
        c["_dt"] = datetime.strptime(c["date"], "%Y-%m-%d %H:%M:%S")
    raw.sort(key=lambda c: c["_dt"])
    return raw


def group_by_day(candles: list[dict]) -> "OrderedDict[date, list[dict]]":
    days: OrderedDict[date, list[dict]] = OrderedDict()
    for c in candles:
        d = c["_dt"].date()
        days.setdefault(d, []).append(c)
    return days


def daily_ohlc(day_candles: list[dict]) -> dict:
    """Aggregate a day's 5-min candles into daily O/H/L/C."""
    return {
        "open":  day_candles[0]["open"],
        "high":  max(c["high"] for c in day_candles),
        "low":   min(c["low"] for c in day_candles),
        "close": day_candles[-1]["close"],   # last 5-min close as EOD proxy
    }


# ═════════════════════════════════════════════════════════════════════
# CLASSIC PIVOT POINTS
# ═════════════════════════════════════════════════════════════════════

def compute_classic_pivots(prev_high: float, prev_low: float, prev_close: float) -> dict:
    p = (prev_high + prev_low + prev_close) / 3
    r1 = 2 * p - prev_low
    s1 = 2 * p - prev_high
    r2 = p + (prev_high - prev_low)
    s2 = p - (prev_high - prev_low)
    r3 = prev_high + 2 * (p - prev_low)
    s3 = prev_low - 2 * (prev_high - p)
    return {"P": p, "R1": r1, "R2": r2, "R3": r3, "S1": s1, "S2": s2, "S3": s3}


def build_pivots_by_day(day_list: list[date], daily_ohlc_map: dict) -> dict:
    """
    Pivots for day[i] are computed from day[i-1]'s OHLC.
    day_list[0] has no entry — no previous day available in-window.
    """
    pivots_by_day = {}
    for idx in range(1, len(day_list)):
        prev = daily_ohlc_map[day_list[idx - 1]]
        pivots_by_day[day_list[idx]] = compute_classic_pivots(
            prev["high"], prev["low"], prev["close"])
    return pivots_by_day


# ═════════════════════════════════════════════════════════════════════
# SUPERTREND(7,3) — Wilder ATR + standard band/trend recursion
# ═════════════════════════════════════════════════════════════════════

def _true_range(candles: list[dict]) -> list[float]:
    tr = [0.0] * len(candles)
    for i, c in enumerate(candles):
        h, l = c["high"], c["low"]
        if i == 0:
            tr[i] = h - l
        else:
            pc = candles[i - 1]["close"]
            tr[i] = max(h - l, abs(h - pc), abs(l - pc))
    return tr


def _atr_wilder(candles: list[dict], period: int) -> list[float | None]:
    tr = _true_range(candles)
    n = len(candles)
    atr: list[float | None] = [None] * n
    if n < period:
        return atr
    atr[period - 1] = sum(tr[:period]) / period          # seed = simple avg
    for i in range(period, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period   # Wilder smoothing
    return atr


def compute_supertrend(
    candles: list[dict], period: int = ST_PERIOD, multiplier: float = ST_MULTIPLIER,
) -> tuple[list[str | None], list[float | None]]:
    """
    Standard SuperTrend algorithm. Returns (trend, value) lists aligned
    with `candles`; trend is 'up' | 'down' | None (during ATR warmup).
    """
    atr = _atr_wilder(candles, period)
    n = len(candles)
    final_upper: list[float | None] = [None] * n
    final_lower: list[float | None] = [None] * n
    trend: list[str | None] = [None] * n
    st_value: list[float | None] = [None] * n

    start = period - 1
    for i in range(start, n):
        hl2 = (candles[i]["high"] + candles[i]["low"]) / 2
        basic_upper = hl2 + multiplier * atr[i]
        basic_lower = hl2 - multiplier * atr[i]

        if i == start:
            final_upper[i] = basic_upper
            final_lower[i] = basic_lower
            close = candles[i]["close"]
            if close <= basic_upper:
                trend[i] = "down"
                st_value[i] = basic_upper
            else:
                trend[i] = "up"
                st_value[i] = basic_lower
            continue

        prev_close = candles[i - 1]["close"]

        final_upper[i] = (
            basic_upper if (basic_upper < final_upper[i - 1] or prev_close > final_upper[i - 1])
            else final_upper[i - 1]
        )
        final_lower[i] = (
            basic_lower if (basic_lower > final_lower[i - 1] or prev_close < final_lower[i - 1])
            else final_lower[i - 1]
        )

        close = candles[i]["close"]
        prev_trend = trend[i - 1]
        if prev_trend == "up":
            trend[i] = "down" if close < final_lower[i] else "up"
        else:
            trend[i] = "up" if close > final_upper[i] else "down"

        st_value[i] = final_lower[i] if trend[i] == "up" else final_upper[i]

    return trend, st_value


# ═════════════════════════════════════════════════════════════════════
# TRADE SIMULATION
# ═════════════════════════════════════════════════════════════════════

def _make_trade(position: dict, exit_dt: datetime, exit_price: float, reason: str) -> dict:
    side = position["side"]
    entry_price = position["entry_price"]
    points = (exit_price - entry_price) if side == "long" else (entry_price - exit_price)
    return {
        "date":         position["entry_time"].date().isoformat(),
        "side":         side,
        "entry_time":   position["entry_time"].strftime("%Y-%m-%d %H:%M:%S"),
        "entry_price":  round(entry_price, 2),
        "exit_time":    exit_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "exit_price":   round(exit_price, 2),
        "exit_reason":  reason,
        "points":       round(points, 2),
        "pivots_used":  {k: round(v, 2) for k, v in position["pivots"].items()},
    }


def run_strategy(
    candles: list[dict], force_exit_time: dtime = DEFAULT_FORCE_EXIT_TIME,
) -> tuple[list[dict], list[date]]:
    """
    Run the pivot + SuperTrend intraday strategy over the full candle series.
    Returns (trades, tradeable_days) — tradeable_days excludes the first
    calendar day (no previous-day OHLC available for its pivots).
    """
    trend, _st_value = compute_supertrend(candles, ST_PERIOD, ST_MULTIPLIER)

    days_map = group_by_day(candles)
    day_list = list(days_map.keys())
    daily_ohlc_map = {d: daily_ohlc(days_map[d]) for d in day_list}
    pivots_by_day = build_pivots_by_day(day_list, daily_ohlc_map)
    tradeable_days = day_list[1:]   # first day excluded — see assumption #1

    trades: list[dict] = []
    position: dict | None = None
    pending_exit = False

    for i, c in enumerate(candles):
        day = c["_dt"].date()
        t = c["_dt"].time()
        pivots = pivots_by_day.get(day)
        cur_trend = trend[i]

        # 1 — execute a pending ST-flip exit at THIS candle's open
        if position is not None and pending_exit:
            trades.append(_make_trade(position, c["_dt"], c["open"], "st_flip"))
            position = None
            pending_exit = False

        # 2 — force-exit at/after cutoff if still open
        if position is not None and t >= force_exit_time:
            trades.append(_make_trade(position, c["_dt"], c["open"], "force_exit"))
            position = None
            pending_exit = False

        # 3 — detect a ST flip on this candle -> exit fires on the NEXT candle
        if i > 0 and trend[i - 1] is not None and cur_trend is not None \
                and cur_trend != trend[i - 1]:
            if position is not None:
                pending_exit = True

        # 4 — evaluate a fresh entry (flat, pivots known, ST known, before cutoff)
        if position is None and pivots is not None and cur_trend is not None \
                and t < force_exit_time:
            close = c["close"]
            r_levels = [pivots[k] for k in R_KEYS]
            s_levels = [pivots[k] for k in S_KEYS]
            if cur_trend == "up" and any(close > r for r in r_levels):
                position = {"side": "long", "entry_time": c["_dt"],
                            "entry_price": close, "pivots": pivots}
            elif cur_trend == "down" and any(close < s for s in s_levels):
                position = {"side": "short", "entry_time": c["_dt"],
                            "entry_price": close, "pivots": pivots}

    # Safety net — should never fire given the force-exit rule, but guard
    # against silently carrying a position past the end of the dataset.
    if position is not None:
        last = candles[-1]
        trades.append(_make_trade(position, last["_dt"], last["close"], "end_of_data"))

    return trades, tradeable_days


# ═════════════════════════════════════════════════════════════════════
# SUMMARY / REPORTING
# ═════════════════════════════════════════════════════════════════════

def build_summary(trades: list[dict], tradeable_days: list[date]) -> dict:
    by_day: "OrderedDict[str, list[dict]]" = OrderedDict()
    for d in tradeable_days:
        by_day[d.isoformat()] = []
    for tr in trades:
        by_day.setdefault(tr["date"], []).append(tr)

    day_summaries = []
    for d_str, day_trades in by_day.items():
        pts = [t["points"] for t in day_trades]
        day_summaries.append({
            "date": d_str,
            "num_trades": len(day_trades),
            "total_points": round(sum(pts), 2) if pts else 0.0,
            "avg_points": round(sum(pts) / len(pts), 2) if pts else 0.0,
            "wins": sum(1 for p in pts if p > 0),
            "losses": sum(1 for p in pts if p <= 0),
        })

    all_points = [t["points"] for t in trades]
    wins = [p for p in all_points if p > 0]
    losses = [p for p in all_points if p <= 0]
    longs = [t for t in trades if t["side"] == "long"]
    shorts = [t for t in trades if t["side"] == "short"]
    exit_reasons: dict[str, int] = {}
    for t in trades:
        exit_reasons[t["exit_reason"]] = exit_reasons.get(t["exit_reason"], 0) + 1

    return {
        "tradeable_days": len(tradeable_days),
        "total_trades": len(trades),
        "total_points": round(sum(all_points), 2) if all_points else 0.0,
        "avg_points_per_trade": round(sum(all_points) / len(all_points), 2) if all_points else 0.0,
        "win_rate_pct": round(len(wins) / len(trades) * 100, 2) if trades else 0.0,
        "avg_win_points": round(sum(wins) / len(wins), 2) if wins else 0.0,
        "avg_loss_points": round(sum(losses) / len(losses), 2) if losses else 0.0,
        "long_trades": len(longs),
        "short_trades": len(shorts),
        "avg_trades_per_day": round(len(trades) / len(tradeable_days), 2) if tradeable_days else 0.0,
        "exit_reason_breakdown": exit_reasons,
        "day_by_day": day_summaries,
    }


# ═════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Pivot Points + SuperTrend(7,3) intraday strategy on NIFTY 50 5-min data",
    )
    parser.add_argument("--input", default=str(DEFAULT_INPUT),
                        help="Path to 5-min candle JSON (default: data/NIFTY50_5minute.json)")
    parser.add_argument("--force-exit", default="15:00",
                        help="Force-exit cutoff time HH:MM (default: 15:00)")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        sys.exit(
            f"Input not found: {input_path}\n"
            f"Run fetch_nifty_5min.py first to populate data/NIFTY50_5minute.json."
        )

    hh, mm = (int(x) for x in args.force_exit.split(":"))
    force_exit_time = dtime(hh, mm)

    candles = load_candles(input_path)
    print(f"Loaded {len(candles)} candles from {input_path}")

    trades, tradeable_days = run_strategy(candles, force_exit_time)
    summary = build_summary(trades, tradeable_days)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TRADES_OUTPUT.write_text(json.dumps(trades, indent=2))
    SUMMARY_OUTPUT.write_text(json.dumps(summary, indent=2))

    print(f"\n{'═' * 60}")
    print(f"  Pivot + SuperTrend(7,3) — NIFTY 50, 5-min")
    print(f"  Tradeable days: {summary['tradeable_days']}  "
          f"(1st day in dataset excluded — no prior-day pivots)")
    print(f"  Total trades:   {summary['total_trades']}  "
          f"({summary['long_trades']} long / {summary['short_trades']} short)")
    print(f"  Total points:   {summary['total_points']}")
    print(f"  Avg points/trade: {summary['avg_points_per_trade']}")
    print(f"  Win rate:       {summary['win_rate_pct']}%")
    print(f"  Avg win/loss:   {summary['avg_win_points']} / {summary['avg_loss_points']}")
    print(f"  Exit reasons:   {summary['exit_reason_breakdown']}")
    print(f"{'═' * 60}")
    print(f"\n  Day-by-day:")
    for d in summary["day_by_day"]:
        print(f"    {d['date']}  trades={d['num_trades']:<3} "
              f"pts={d['total_points']:>8}  avg={d['avg_points']:>7}  "
              f"W{d['wins']}/L{d['losses']}")
    print(f"\n  → {TRADES_OUTPUT}")
    print(f"  → {SUMMARY_OUTPUT}")


if __name__ == "__main__":
    main()

"""Shared checks and fixtures for strategy simulations: which strategies
there are, how each runs (positional or intraday, and for how many
days), one market for them all, and the book's invariants."""

import math
from datetime import date, datetime, timedelta

from app.strategies.registry import list_strategies

STRATS = sorted(s["name"] for s in list_strategies())
# Run as positional (held across days); the rest are intraday.
POSITIONAL = {"strangle_monthly_v2", "calendar_btst", "trend_credit_spread", "weekly_ema_st_spread"}
# Trading days to simulate where 3 aren't enough: the hourly-signal
# strategy needs a week of candles before its indicators exist.
DAYS = {"weekly_ema_st_spread": 10}


def mode_of(strategy: str) -> str:
    return "positional" if strategy in POSITIONAL else "intraday"


def market(day_i: int):
    """An oscillating, drifting market: plenty of signal flips."""
    def spot(t):
        m = (t.hour * 60 + t.minute) - 555
        return 25000 + 250 * math.sin((m + day_i * 120) / 60.0) + 40 * day_i * (1 if day_i % 2 else -1)
    return spot


def trading_days(start: date, n: int) -> list:
    d, out = start, []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def sim_time(d: date, hhmm: str) -> datetime:
    return datetime.combine(d, datetime.strptime(hhmm, "%H:%M").time())


def cfg(strategy: str, **over) -> dict:
    """The strategy's own registered default config, with overrides."""
    base = next(s for s in list_strategies() if s["name"] == strategy)["default_config"]
    return {**base, **over}


async def assert_book_consistent(pool, dep_id):
    """The fill engine's invariants, from the database alone:
    cash = capital + sells - buys; open qty = net of fills; a closed
    position's realized P&L = (sell avg - buy avg) * qty."""
    dep = await pool.fetchrow("SELECT * FROM deployments WHERE id = $1", dep_id)
    lots = await pool.fetch("SELECT * FROM position_lots WHERE deployment_id = $1", dep_id)
    flow = sum(float(l["qty"]) * float(l["price"]) * (1 if l["action"] == "sell" else -1) for l in lots)
    assert abs(float(dep["initial_capital"]) + flow - float(dep["current_cash"])) < 0.05, \
        f"cash {dep['current_cash']} != capital {dep['initial_capital']} + fills {flow:.2f}"
    for p in await pool.fetch("SELECT * FROM positions WHERE deployment_id = $1", dep_id):
        pl = [l for l in lots if l["position_id"] == p["id"]]
        opening = "sell" if p["side"] == "short" else "buy"
        opened = sum(float(l["qty"]) for l in pl if l["action"] == opening)
        closed = sum(float(l["qty"]) for l in pl if l["action"] != opening)
        if p["status"] == "open":
            assert closed == 0 and abs(float(p["qty"]) - opened) < 1e-6, f"{p['symbol']}: open qty {p['qty']} vs fills {opened}"
        else:
            assert abs(closed - opened) < 1e-6, f"{p['symbol']}: opened {opened} closed {closed}"
            avg_in = sum(float(l["qty"]) * float(l["price"]) for l in pl if l["action"] == opening) / opened
            avg_out = sum(float(l["qty"]) * float(l["price"]) for l in pl if l["action"] != opening) / closed
            pnl = (avg_out - avg_in) * opened if p["side"] == "long" else (avg_in - avg_out) * opened
            assert abs(pnl - float(p["realized_pnl"])) < 0.05, f"{p['symbol']}: realized {p['realized_pnl']} vs {pnl:.2f}"

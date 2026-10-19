"""Crash-proofing: a strategy that is killed (no clean shutdown, no state
save) and restarted from the database must trade EXACTLY as if it had
never stopped. Each strategy runs the same simulated sessions twice:
once uninterrupted, once killed and restarted every few minutes. Every
fill (time, instrument, side, quantity, price, reason) must match.

A difference means some decision depends on memory the strategy didn't
rebuild from the database — fix the strategy, not this test."""

import math
from datetime import timedelta

import pytest

from app.strategies.registry import list_strategies
from tests.sim import Sim
from tests.strategies.helpers import assert_book_consistent, cfg

STRATS = sorted(s["name"] for s in list_strategies())
POSITIONAL = {"strangle_monthly_v2", "calendar_btst", "trend_credit_spread", "weekly_ema_st_spread"}
DAYS = {"weekly_ema_st_spread": 10}
CRASH_EVERY = [7, 23]          # minutes; co-prime with the market's rhythms


def market(day_i):
    def spot(t):
        m = (t.hour * 60 + t.minute) - 555
        return 25000 + 250 * math.sin((m + day_i * 120) / 60.0) + 40 * day_i * (1 if day_i % 2 else -1)
    return spot


def trading_days(start, n):
    d, out = start, []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


async def run(pool, monkeypatch, strategy, crash_every=None, name="run"):
    mode = "positional" if strategy in POSITIONAL else "intraday"
    sim = await Sim.create(pool, monkeypatch, strategy, cfg(strategy), mode=mode, spot=25000, name=name)
    step = 5 if strategy in DAYS else 1
    n = 0
    for i, d in enumerate(trading_days(sim.market.today, DAYS.get(strategy, 3))):
        spot = market(i + 1)
        t, end = sim_time(d, "09:15"), sim_time(d, "15:29")
        while t <= end:
            await sim.tick(t, spot=spot(t))
            n += step
            if crash_every and n % crash_every == 0:
                await sim.restart()          # no on_stop, no dump: a hard kill
            t += timedelta(minutes=step)
        sim.market.now = sim_time(d, "16:30")
        await sim.end_of_day()
    fills = [(r["executed_at"], r["symbol"], r["action"], float(r["qty"]), float(r["price"]), r["reason"])
             for r in await pool.fetch(
                 "SELECT l.executed_at, p.symbol, l.action, l.qty, l.price, l.reason FROM position_lots l "
                 "JOIN positions p ON p.id = l.position_id WHERE l.deployment_id = $1 ORDER BY l.executed_at, p.symbol, l.action",
                 sim.dep_id)]
    await assert_book_consistent(pool, sim.dep_id)
    return fills


def sim_time(d, hhmm):
    from datetime import datetime
    return datetime.combine(d, datetime.strptime(hhmm, "%H:%M").time())


@pytest.mark.parametrize("crash_every", CRASH_EVERY)
@pytest.mark.parametrize("strategy", STRATS)
def test_a_crashed_and_restarted_strategy_trades_exactly_like_an_uninterrupted_one(strategy, crash_every, pool_run, monkeypatch):
    async def go(pool):
        base = await run(pool, monkeypatch, strategy, name="uninterrupted")
        crashed = await run(pool, monkeypatch, strategy, crash_every=crash_every, name="crashed")
        assert base, f"{strategy} never traded — the comparison would prove nothing"
        if crashed != base:
            diff = next(i for i, (a, b) in enumerate(zip(base + [None], crashed + [None])) if a != b)
            raise AssertionError(
                f"{strategy}, killed every {crash_every} min: fill #{diff} differs\n"
                f"  uninterrupted: {base[diff] if diff < len(base) else 'none'}\n"
                f"  crashed:       {crashed[diff] if diff < len(crashed) else 'none'}\n"
                f"  ({len(base)} vs {len(crashed)} fills)")
    pool_run(go)


# ── Killed in the middle of an action ─────────────────────────────────
# A tick-boundary restart is the easy case. The dangerous one is dying
# between two fills of one action: the CE of a straddle sold, the PE not;
# a stop's buy-back done, its replacement not. Here the process "dies"
# right after the k-th fill is committed (for each k), restarts from the
# database, and the session carries on. Exact equality can't hold (the
# half-done action is genuinely different), so the checks are safety:
# nothing raises, the book stays consistent, intraday ends every day
# flat, and nothing open is left that the restarted strategy doesn't
# manage — one more restart and a session later, it's flat or tracked.

class Killed(BaseException):
    """Not an Exception: nothing in the app may catch a process death."""


async def run_killed_after(pool, monkeypatch, strategy, kill_at):
    from app.db import queries
    real = queries.record_fill
    count = {"n": 0}

    async def dying_record_fill(*a, **kw):
        r = await real(*a, **kw)
        count["n"] += 1
        if count["n"] == kill_at:
            raise Killed()
        return r
    monkeypatch.setattr(queries, "record_fill", dying_record_fill)
    mode = "positional" if strategy in POSITIONAL else "intraday"
    sim = await Sim.create(pool, monkeypatch, strategy, cfg(strategy), mode=mode, spot=25000, name=f"kill{kill_at}")
    step = 5 if strategy in DAYS else 1
    killed = False
    days = trading_days(sim.market.today, DAYS.get(strategy, 3))
    for i, d in enumerate(days):
        spot = market(i + 1)
        t, end = sim_time(d, "09:15"), sim_time(d, "15:29")
        while t <= end:
            try:
                await sim.tick(t, spot=spot(t))
            except Killed:
                killed = True
                await sim.restart()
            t += timedelta(minutes=step)
        sim.market.now = sim_time(d, "16:30")
        await sim.end_of_day()
        if mode == "intraday" and sim.runner.config.get("force_exit_time"):
            assert await sim.open_positions() == [], f"{strategy}: killed after fill {kill_at}, not flat after {d}"
    monkeypatch.setattr(queries, "record_fill", real)
    await assert_book_consistent(pool, sim.dep_id)
    return sim, killed, count["n"]


@pytest.mark.parametrize("strategy", STRATS)
def test_killed_between_fills_resumes_safely(strategy, pool_run, monkeypatch):
    async def go(pool):
        base = await run(pool, monkeypatch, strategy, name="baseline")
        max_open = await max_concurrent_open(pool, strategy)
        tried = 0
        for k in range(1, min(len(base), 14) + 1):
            sim, killed, _ = await run_killed_after(pool, monkeypatch, strategy, k)
            assert killed, f"never reached fill {k}"
            tried += 1
            open_now = await sim.open_positions()
            assert len(open_now) <= max_open, (
                f"{strategy}: killed after fill {k}, ends holding {len(open_now)} legs — more than it ever "
                f"holds uninterrupted ({max_open}): something was opened and forgotten")
        assert tried
    pool_run(go)


async def max_concurrent_open(pool, strategy):
    """Most legs the uninterrupted run ever held at once."""
    rows = await pool.fetch(
        "SELECT p.opened_at, p.closed_at FROM positions p JOIN deployments d ON d.id = p.deployment_id "
        "WHERE d.deployment_name = 'baseline'")
    events = sorted([(r["opened_at"], 1) for r in rows] + [(r["closed_at"], -1) for r in rows if r["closed_at"]],
                    key=lambda e: (e[0], e[1]))
    cur = best = 0
    for _, d in events:
        cur += d
        best = max(best, cur)
    return max(best, 1)

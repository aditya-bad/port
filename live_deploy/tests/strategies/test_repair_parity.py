"""Does a repair trade the way the strategy trades live?

For every strategy, the same simulated day twice:

  live      one deployment runs the whole day on live ticks;
  repaired  an identical deployment runs live until a stall, and the repair
            replays the rest of the day on the 1-minute candles of the very
            ticks the live one saw.

Every minute is ticked four times, at :00, :20, :40 and :59, through its
open, its two extremes (in the order the candle's colour implies) and its
close, so the four ticks a repair plays per minute are the live ticks
exactly. Nothing is lost in the candles, and any difference between the two
runs is the repair's, not the data's: every trade after the stall must be
the same contract (so the same strike), side, quantity, reason, minute,
tick and price.
"""

import math
from datetime import datetime, time, timedelta

import pytest

from app.db import queries
from app.repair import engine
from app.repair.market import IST, MinuteBook, natural_order
from app.strategies.registry import list_strategies
from tests.sim import INDEX, FakeKite, Sim
from tests.strategies.helpers import cfg

POSITIONAL = {"strangle_monthly_v2", "calendar_btst", "trend_credit_spread", "weekly_ema_st_spread"}
VIX = 264969
SECONDS = (0, 20, 40, 59)


def base(m: datetime) -> float:
    """The index at the start of minute `m`: a trend, a slow swing and a
    faster one, so entries, stops, rolls and exits all happen."""
    x = (m.hour * 60 + m.minute) - 555
    return 25000 + 0.6 * x + 220 * math.sin(x / 40.0) + 60 * math.sin(x / 7.0)


def trending(m: datetime) -> float:
    """A strong one-way day (+1,400 points with pullbacks): enough for the
    strangle to roll its tested side, more than once."""
    x = (m.hour * 60 + m.minute) - 555
    return 25000 + 3.8 * x + 80 * math.sin(x / 9.0)


def by_strike_oi(strike: float) -> int:
    """Open interest by strike, as on a real chain: round hundreds carry
    far more than the fifties between them, so the strangle's liquidity
    tie-break has a real choice to make."""
    return 900_000 if strike % 100 == 0 else 250_000 + int(strike) % 7 * 10_000


def minute_ticks(m: datetime, path=base) -> list[tuple[datetime, float]]:
    """Four ticks: open, the two extremes in the candle's own order, close."""
    o, c = round(path(m), 2), round(path(m + timedelta(minutes=1)), 2)
    w = 4 + 3 * abs(math.sin((m.hour * 60 + m.minute) / 3.0))
    hi, lo = round(max(o, c) + w, 2), round(min(o, c) - w, 2)
    order = natural_order({"open": o, "close": c})
    mid = (lo, hi) if order == "down_first" else (hi, lo)
    return [(m + timedelta(seconds=s), p) for s, p in zip(SECONDS, (o, *mid, c))]


def day_minutes(day, start="09:15", end="15:30"):
    t = datetime.combine(day, datetime.strptime(start, "%H:%M").time())
    stop = datetime.combine(day, datetime.strptime(end, "%H:%M").time())
    while t < stop:
        yield t
        t += timedelta(minutes=1)


async def run_live(sim, start="09:15", end="15:30", path=base):
    for m in day_minutes(sim.market.today, start, end):
        for at, spot in minute_ticks(m, path):
            await sim.tick(at, spot=spot)
            sim.dispatcher.last_prices[VIX] = 14.0      # live has India VIX too
            sim.dispatcher.last_price_times[VIX] = at


def exact_source(sim, path=base):
    """A MinuteBook source: each minute's candle built from the four live
    ticks, every option priced as the simulated market priced it at each."""
    market = sim.market
    index_tok = {v[0]: k for k, v in INDEX.items()}

    def source(token, start, end):
        token = int(token)
        out = []
        saved_now, saved_spot = market.now, dict(market.spot)
        try:
            for m in day_minutes(start.date(), f"{start:%H:%M}", f"{end:%H:%M}"):
                if token == VIX:
                    px = [14.0] * 4
                else:
                    name = index_tok.get(token) or (market.by_token.get(token) or {}).get("name")
                    if name is None:
                        return out
                    px = []
                    for at, spot in minute_ticks(m, path):
                        market.now = at
                        market.spot[name] = spot if name == sim.underlying else saved_spot[name]
                        px.append(market.spot[name] if token in index_tok else market.price_of(market.by_token[token]))
                row = market.by_token.get(token)
                out.append({"date": m, "open": px[0], "high": max(px), "low": min(px), "close": px[-1],
                            "volume": 100, "oi": by_strike_oi(row["strike"]) if row else 0})
        finally:
            market.now, market.spot = saved_now, saved_spot
        return out
    return source


def live_fills(rows, after: datetime):
    out = []
    for r in rows:
        at = r["executed_at"].astimezone(IST).replace(tzinfo=None)
        if at < after:
            continue
        out.append((f"{at:%H:%M}", SECONDS.index(at.second) if at.second in SECONDS else at.second,
                    r["action"], r["symbol"], float(r["qty"]), round(float(r["price"]), 2), r["reason"] or ""))
    return out


async def fills_with_symbols(pool, dep_id):
    return [dict(r) for r in await pool.fetch(
        "SELECT l.*, p.symbol FROM position_lots l JOIN positions p ON p.id = l.position_id "
        "WHERE l.deployment_id = $1 ORDER BY l.executed_at, l.id", dep_id)]


def replay_fills(fills):
    """The repair's fills as live_fills reads live ones: by the time each
    fill is stamped with (a strategy may stamp a fill with its signal
    candle's start), not the replay's clock."""
    out = []
    for f in fills:
        at = datetime.fromisoformat(f["at"]).astimezone(IST).replace(tzinfo=None)
        out.append((f"{at:%H:%M}", SECONDS.index(at.second) if at.second in SECONDS else at.second,
                    f["action"], f["symbol"], f["qty"], f["price"], f["reason"]))
    return out


STRATEGIES = sorted(s["name"] for s in list_strategies())


def chain_oi(monkeypatch):
    """Live quotes carry the same open interest by strike as the candles."""
    real = FakeKite.quote

    def quote(self, keys):
        out = real(self, keys)
        for k, q in out.items():
            row = self.m.by_key.get(k)
            if row is not None:
                q["oi"] = by_strike_oi(row["strike"])
        return out
    monkeypatch.setattr(FakeKite, "quote", quote)


async def parity(pool, monkeypatch, strategy, stall="11:00", path=base, **over):
    mode = "positional" if strategy in POSITIONAL else "intraday"
    config = cfg(strategy, **over)
    chain_oi(monkeypatch)
    start = path(datetime(2026, 1, 1, 9, 15))
    live = await Sim.create(pool, monkeypatch, strategy, config, mode=mode, spot=start, name=f"live {strategy}")
    await run_live(live, path=path)
    want = live_fills(await fills_with_symbols(pool, live.dep_id), datetime.combine(live.market.today, time(0)))

    rep = await Sim.create(pool, monkeypatch, strategy, config, mode=mode, spot=start, name=f"repaired {strategy}")
    h, mi = map(int, stall.split(":"))
    last = (datetime.combine(rep.market.today, time(h, mi)) - timedelta(minutes=1)).strftime("%H:%M")
    await run_live(rep, end=f"{h:02d}:{mi:02d}", path=path)
    w = engine.Window(rep.market.today, time(h, mi), time(15, 30))
    book = MinuteBook(rep.market.today, exact_source(rep, path))
    orders = {f"{m:%H:%M}": natural_order(book.candle(INDEX[rep.underlying][0], m)) for m in w.minutes}
    out = await engine.plan(pool, rep.dep_id, w, book, rep.kite, orders)
    assert out.complete, "the replay stopped on an ambiguous minute despite every choice given"
    # the whole day: what it did live before the stall, then the repair (a
    # fill may be stamped earlier than it happened, so no cut by time)
    got = live_fills(await fills_with_symbols(pool, rep.dep_id), datetime.combine(rep.market.today, time(0))) + replay_fills(out.fills)
    return sorted(want), sorted(got), out, last   # fills on one tick in any order


def check(want, got, out):
    assert out.problems == [], out.problems
    if want != got:
        lines = [f"live {len(want)} fills, repaired day {len(got)}"]
        for i in range(max(len(want), len(got))):
            a = want[i] if i < len(want) else None
            b = got[i] if i < len(got) else None
            if a != b:
                lines.append(f"  #{i}: live {a}\n       repair {b}")
        pytest.fail("\n".join(lines[:25]))


# A stall before the morning entry: the repair picks every strike itself.
@pytest.mark.parametrize("strategy", STRATEGIES)
def test_a_repair_from_the_open_picks_the_same_strikes_as_live(strategy, pool_run, monkeypatch):
    async def go(pool):
        check(*(await parity(pool, monkeypatch, strategy, stall="09:16"))[:3])
    pool_run(go)


# The strangle on a strong one-way day: entry, then rolls of the tested
# side by premium, each through the liquidity tie-break.
@pytest.mark.parametrize("stall", ["09:16", "11:00", "13:30"])
def test_a_repaired_strangle_rolls_the_same_strikes_as_live(stall, pool_run, monkeypatch):
    async def go(pool):
        want, got, out, _ = await parity(pool, monkeypatch, "strangle_monthly_v2", stall=stall, path=trending)
        assert any(f[6] not in ("entry",) for f in want), f"the day must roll the strangle: {want}"
        check(want, got, out)
    pool_run(go)


# A stall in the middle of the day, with positions open.
@pytest.mark.parametrize("strategy", STRATEGIES)
def test_a_repair_makes_the_same_trades_as_the_strategy_live(strategy, pool_run, monkeypatch):
    async def go(pool):
        want, got, out, _ = await parity(pool, monkeypatch, strategy)
        assert out.problems == [], out.problems
        if want != got:
            lines = [f"live {len(want)} fills, repaired day {len(got)}"]
            for i in range(max(len(want), len(got))):
                a = want[i] if i < len(want) else None
                b = got[i] if i < len(got) else None
                if a != b:
                    lines.append(f"  #{i}: live {a}\n       repair {b}")
            pytest.fail("\n".join(lines[:25]))
    pool_run(go)


# Kite lists only contracts that are still trading, and drops an option's
# history the day it expires. A repair run after the week's expiry can't
# see that week's contracts: it must say so, not quietly trade next week's.
def test_a_repair_run_after_the_weeks_expiry_refuses_instead_of_trading_next_weeks_contracts(pool_run, monkeypatch):
    async def go(pool):
        strategy = "intraday_dtt_simple"
        chain_oi(monkeypatch)
        start = base(datetime(2026, 1, 1, 9, 15))
        live = await Sim.create(pool, monkeypatch, strategy, cfg(strategy), spot=start, name="live")
        await run_live(live)
        held = {r["symbol"] for r in await fills_with_symbols(pool, live.dep_id)}
        # what the app's candle service stores for every contract held that day
        await queries.upsert_contracts(pool, [{"instrument_token": int(r["instrument_token"]), "tradingsymbol": r["symbol"],
                                               "exchange": "NFO", "underlying": "NIFTY", "expiry": live.market.by_token[int(r["instrument_token"])]["expiry"],
                                               "strike": live.market.by_token[int(r["instrument_token"])]["strike"],
                                               "instrument_type": r["symbol"][-2:]}
                                              for r in await pool.fetch("SELECT DISTINCT instrument_token, symbol FROM positions WHERE deployment_id = $1", live.dep_id)])
        rep = await Sim.create(pool, monkeypatch, strategy, cfg(strategy), spot=start, name="repaired")
        await run_live(rep, end="09:16")
        week = min(r["expiry"] for r in rep.market.rows["NFO"] if r["name"] == "NIFTY")
        assert any(f"{week:%d}" in s for s in held), "live traded this week's contracts"
        # the repair runs after that expiry: Kite's list no longer has it
        real = FakeKite.instruments
        monkeypatch.setattr(FakeKite, "instruments", lambda self, ex: [r for r in real(self, ex) if r["expiry"] > week])
        w = engine.Window(rep.market.today, time(9, 16), time(15, 30))
        book = MinuteBook(rep.market.today, exact_source(rep))
        orders = {f"{m:%H:%M}": natural_order(book.candle(INDEX["NIFTY"][0], m)) for m in w.minutes}
        out = await engine.plan(pool, rep.dep_id, w, book, rep.kite, orders)
        traded = {f["symbol"] for f in out.fills}
        assert not traded - held, f"the repair traded contracts live never did: {sorted(traded - held)}"
        assert out.gone, "the plan must say which expiry it couldn't see"
        assert "NIFTY" in out.gone[0] and f"{week:%d %b}" in out.gone[0]
        assert any("expired" in n for n in out.notes)
        with pytest.raises(engine.PlanChanged):
            await engine.apply(pool, rep.dep_id, w, book, rep.kite, out)
    pool_run(go)


# Some strategies stamp a fill with the start of the candle that triggered
# it, though it happens at that candle's close. A day repair from a minute
# in between must not keep the fill AND make it again.
def test_a_day_repair_from_inside_a_signal_candle_does_not_trade_twice(pool_run, monkeypatch):
    async def go(pool):
        from app.repair import daycheck as D
        from app.repair import dayrepair as R
        from tests.conftest import fill
        from tests.strategies.helpers import assert_book_consistent
        strategy = "pivot_supertrend_options"
        chain_oi(monkeypatch)
        sim = await Sim.create(pool, monkeypatch, strategy, cfg(strategy), spot=base(datetime(2026, 1, 1, 9, 15)))
        day = sim.market.today
        await D.capture_states(pool, day, "open")
        await run_live(sim)
        lots = await fills_with_symbols(pool, sim.dep_id)
        entry = next(r for r in lots if r["reason"] == "entry" and r["executed_at"].astimezone(IST).hour >= 10)
        stamp = entry["executed_at"].astimezone(IST)
        # a stray fill two minutes after the stamp (before the entry really happened) makes the day part there
        stray = stamp + timedelta(minutes=2)
        await fill(pool, sim.dep_id, "NIFTY26STRAY", 777001, "sell", 75, 40.0, stray, "spurious")
        book = MinuteBook(day, exact_source(sim))
        await D.run_day(pool, day, book, sim.kite)
        row = dict(await pool.fetchrow("SELECT * FROM day_checks WHERE deployment_id = $1", sim.dep_id))
        start = R.divergence(row)
        assert start == f"{stray:%H:%M}", (start, row.get("diff"))
        plan = await R.run(pool, sim.dep_id, day, book, sim.kite, {})
        assert plan["complete"], plan.get("problems")
        mine = [f for f in plan["fills"] if f["symbol"] == entry["symbol"] and f["reason"] == "entry"]
        kept = [r for r in lots if r["symbol"] == entry["symbol"] and r["reason"] == "entry"
                and r["executed_at"].astimezone(IST) < stray]
        assert len(mine) + len(kept) == sum(1 for r in lots if r["symbol"] == entry["symbol"] and r["reason"] == "entry"), \
            f"entry {entry['symbol']} stamped {stamp:%H:%M}: kept {len(kept)}, replayed {len(mine)}"
        res = await R.run(pool, sim.dep_id, day, book, sim.kite, {}, commit=True, expected=plan["fills_key"])
        await assert_book_consistent(pool, sim.dep_id)
        assert len(await sim.open_positions()) <= 1, "a single-leg strategy holds at most one leg"
    pool_run(go)


# The end-of-day check replays the whole day from the open and compares.
# On a day the app traded exactly as its strategy would, it must find
# nothing: every trade matched at the same minute, nothing missed or extra.
# (Fills stamped at their candle's start, not when they happened, made
# every SuperTrend trade look 5 minutes off and the day a mismatch.)
@pytest.mark.parametrize("strategy", STRATEGIES)
def test_the_day_check_finds_nothing_on_a_day_the_app_traded_as_its_strategy(strategy, pool_run, monkeypatch):
    async def go(pool):
        from app.repair import daycheck as D
        chain_oi(monkeypatch)
        mode = "positional" if strategy in POSITIONAL else "intraday"
        sim = await Sim.create(pool, monkeypatch, strategy, cfg(strategy), mode=mode, spot=base(datetime(2026, 1, 1, 9, 15)))
        day = sim.market.today
        await D.capture_states(pool, day, "open")
        await run_live(sim)
        book = MinuteBook(day, exact_source(sim))
        await D.run_day(pool, day, book, sim.kite)
        row = dict(await pool.fetchrow("SELECT * FROM day_checks WHERE deployment_id = $1", sim.dep_id))
        d = row["diff"] or {}
        assert row["status"] == "ok", (row["status"], row["headline"], d.get("missed"), d.get("extra"))
        assert d["missed"] == [] and d["extra"] == [] and d["matched"], d
        assert all(m["minutes_apart"] == 0 and m["price_diff"] == 0 for m in d["matched"]), d["matched"]
    pool_run(go)

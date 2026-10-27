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
from contextlib import contextmanager
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


@contextmanager
def old_fill_times(monkeypatch):
    """The fill times of the app before the fix (StrategyBase.fill_time):
    a fill made at a candle's close stamped with the candle's start."""
    from app.deployments.strategy_base import StrategyBase
    with monkeypatch.context() as m:
        m.setattr(StrategyBase, "fill_time", lambda self, fallback=None: fallback or self._tick_at)
        yield


async def fix_went_live(pool, day):
    """The mark the fix's migration leaves: here after the simulated day,
    and after any check already run."""
    at = max(datetime.now(IST), datetime.combine(day, time(23, 59), IST)) + timedelta(seconds=1)
    await pool.execute("INSERT INTO app_marks (name, at) VALUES ('fills_stamped_at_tick', $1) "
                       "ON CONFLICT (name) DO UPDATE SET at = $1", at)


# Some strategies stamped a fill with the start of the candle that triggered
# it, though it happened at that candle's close (fixed; their fills from
# before the fix still are). A day repair from a minute in between must not
# keep the fill AND make it again.
@pytest.mark.parametrize("old_stamps", [False, True], ids=["fills timed at their tick", "fills timed before the fix"])
def test_a_day_repair_from_inside_a_signal_candle_does_not_trade_twice(old_stamps, pool_run, monkeypatch):
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
        if old_stamps:
            with old_fill_times(monkeypatch):
                await run_live(sim)
            await fix_went_live(pool, day)
        else:
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
        if old_stamps:
            assert stamp.minute % 5 == 0 and stamp.second == 0, "stamped at its candle's start"
            assert plan["from"] == f"{stamp:%H:%M}", "the repair starts before the fill stamped inside its candle"
        mine = [f for f in plan["fills"] if f["symbol"] == entry["symbol"] and f["reason"] == "entry"]
        cut = datetime.combine(day, time(*map(int, plan["from"].split(":"))), IST)   # what the rewind keeps
        kept = [r for r in lots if r["symbol"] == entry["symbol"] and r["reason"] == "entry"
                and r["executed_at"].astimezone(IST) < cut]
        assert len(mine) + len(kept) == sum(1 for r in lots if r["symbol"] == entry["symbol"] and r["reason"] == "entry"), \
            f"entry {entry['symbol']} stamped {stamp:%H:%M}: kept {len(kept)}, replayed {len(mine)}"
        res = await R.run(pool, sim.dep_id, day, book, sim.kite, {}, commit=True, expected=plan["fills_key"])
        await assert_book_consistent(pool, sim.dep_id)
        assert len(await sim.open_positions()) <= 1, "a single-leg strategy holds at most one leg"
    pool_run(go)


# ST_PV_INV_SENSEX, 27 Oct: the app bought a CE at 10:05 on a SuperTrend
# flip that Kite's candles don't show, and the replay bought one at 12:45
# instead. The app builds its 5-minute candles from its own ticks; the
# replay built them from Kite's minutes, and a flip can hinge on a few
# points of the close. The app records the close of every candle it acted
# on, and from now on every candle its strategy completed
# (strategy_candles): the replay gives the strategy those. Here the app's
# last tick of one candle lands past the band ("across": it flips and
# buys) or just short of it (no flip, but the candle's high and close feed
# every band after), and Kite's minutes don't have that tick. With only
# the closes it acted on (a day before the candles were kept), the replay
# can't know the candle it didn't act on: that one stays a difference.
@pytest.mark.parametrize("kept", [True, False], ids=["candles kept", "closes acted on only"])
@pytest.mark.parametrize("sign", ["across", "short of"])
def test_a_candle_the_app_acted_on_closes_where_its_own_ticks_closed_it(sign, kept, pool_run, monkeypatch):
    async def go(pool):
        from app.repair import daycheck as D
        chain_oi(monkeypatch)
        strategy = "pivot_supertrend_options_inverse"
        sim = await Sim.create(pool, monkeypatch, strategy, {**cfg(strategy), "max_trades_per_day": None},
                               spot=base(datetime(2026, 1, 1, 9, 15)))
        day = sim.market.today
        await pool.execute("INSERT INTO app_marks (name, at) VALUES ('fills_stamped_at_tick', $1) "
                           "ON CONFLICT (name) DO UPDATE SET at = $1", datetime.combine(day, time(0), IST))
        await D.capture_states(pool, day, "open")
        spiked = None
        for m in day_minutes(day):
            for at, spot in minute_ticks(m):
                await sim.tick(at, spot=spot)
                sim.dispatcher.last_prices[VIX] = 14.0
                sim.dispatcher.last_price_times[VIX] = at
            st = sim.strategy.st
            if (spiked is None and m.hour >= 10 and (m.minute + 1) % 5 == 0 and sim.strategy.active_leg_token is None
                    and st.trend is not None and st.final_upper is not None and st.final_lower is not None):
                # the candle's last tick, past the band the way the trend would flip ("short of": a tick that doesn't flip it)
                up = st.trend == "down"
                gap = 20 if sign == "across" else -20
                spike = st.final_upper + gap if up else st.final_lower - gap
                at = m + timedelta(seconds=59, milliseconds=500)
                await sim.tick(at, spot=round(spike, 2))
                spiked = (m + timedelta(minutes=1), "CE" if up else "PE", round(spike, 2))
        assert spiked
        lots = await pool.fetch("SELECT l.reason, l.executed_at, p.symbol FROM position_lots l JOIN positions p ON p.id = l.position_id "
                                "WHERE l.deployment_id = $1 AND l.action = 'buy' ORDER BY l.executed_at", sim.dep_id)
        at_spike = [r for r in lots if r["executed_at"].astimezone(IST).replace(tzinfo=None, second=0, microsecond=0) == spiked[0]]
        if sign == "across":
            assert [r["symbol"][-2:] for r in at_spike] == [spiked[1]], "the app flipped on its own last tick and bought"
        else:
            assert at_spike == [], "short of the band: no flip"
        stored = await pool.fetch("SELECT start, close FROM strategy_candles WHERE deployment_id = $1", sim.dep_id)
        assert len(stored) > 60 and any(float(c["close"]) == spiked[2] for c in stored), "every candle it completed, as it built it"
        if not kept:
            await pool.execute("DELETE FROM strategy_candles WHERE deployment_id = $1", sim.dep_id)
        book = MinuteBook(day, exact_source(sim))     # Kite's minutes: the extra tick isn't in them
        r = await D.check_deployment(pool, dict(await pool.fetchrow("SELECT * FROM deployments WHERE id = $1", sim.dep_id)),
                                     day, book, sim.kite)
        if sign == "short of" and not kept:
            assert r["status"] == "mismatch", "a candle it didn't act on: nothing says where the app closed it"
            return
        assert r["status"] == "ok", (r["headline"], r["diff"].get("missed"), r["diff"].get("extra"))
        notes = [n for n in r["replay"]["notes"] if "from its own ticks" in n]
        start = spiked[0] - timedelta(minutes=5)
        assert any(n.startswith(f"The {start:%H:%M}–{spiked[0]:%H:%M} candle") and f"close {spiked[2]:,.2f}" in n for n in notes), notes
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


# Days traded before the fill-time fix went live. Their stored fills are
# still timed at their candle's start, so the check flagged them; once the
# fix is live (migration 0038's mark) the stored flag is matched again on
# startup without replaying (the contracts may have expired since), and a
# fresh check of the day agrees.
CANDLE_STAMPED = ["pivot_supertrend_options", "pivot_supertrend_options_inverse", "trend_credit_spread", "weekly_ema_st_spread"]


@pytest.mark.parametrize("strategy", CANDLE_STAMPED)
def test_a_day_flagged_only_for_its_old_fill_times_checks_clean_once_the_fix_is_live(strategy, pool_run, monkeypatch):
    async def go(pool):
        from app.repair import daycheck as D
        assert sorted(D.CANDLE_STAMPED) == sorted(CANDLE_STAMPED)
        chain_oi(monkeypatch)
        mode = "positional" if strategy in POSITIONAL else "intraday"
        sim = await Sim.create(pool, monkeypatch, strategy, cfg(strategy), mode=mode, spot=base(datetime(2026, 1, 1, 9, 15)))
        day = sim.market.today
        await D.capture_states(pool, day, "open")
        with old_fill_times(monkeypatch):
            await run_live(sim)
        n = await pool.fetchval("SELECT count(*) FROM position_lots WHERE deployment_id = $1", sim.dep_id)
        book = MinuteBook(day, exact_source(sim))
        # the check as it ran that evening: every candle-close trade one missed, one extra
        await D.run_day(pool, day, book, sim.kite)
        was = dict(await pool.fetchrow("SELECT * FROM day_checks WHERE deployment_id = $1", sim.dep_id))
        assert was["status"] == "mismatch", (was["status"], was["headline"])
        await pool.execute("UPDATE day_checks SET dismissed_at = now() WHERE deployment_id = $1", sim.dep_id)
        # the fix goes live: on startup the stored flag is matched again
        await fix_went_live(pool, day)
        changed = await D.recheck_candle_stamped(pool)
        assert [(c["was"], c["now"]) for c in changed] == [("mismatch", "ok")], changed
        row = dict(await pool.fetchrow("SELECT * FROM day_checks WHERE deployment_id = $1", sim.dep_id))
        assert row["status"] == "ok" and row["headline"].startswith("Traded as its strategy would have")
        assert row["diff"]["missed"] == [] and row["diff"]["extra"] == [] and len(row["diff"]["matched"]) == n
        assert row["replay"]["rechecked"]["was"]["status"] == "mismatch" and row["dismissed_at"] is None
        assert await D.recheck_candle_stamped(pool) == [], "only once"
        # and a check run again from scratch (Check again, after the fix) agrees
        await D.run_day(pool, day, book, sim.kite)
        again = dict(await pool.fetchrow("SELECT * FROM day_checks WHERE deployment_id = $1", sim.dep_id))
        assert again["status"] == "ok", (again["status"], again["headline"], again["diff"].get("missed"), again["diff"].get("extra"))
        assert len(again["diff"]["matched"]) == n
    pool_run(go)


def test_old_fill_times_are_read_across_their_candle_only_for_the_strategies_that_had_them():
    from app.repair import daycheck as D
    day = datetime(2026, 10, 23).date()
    at = datetime.combine(day, time(10, 15), IST)
    old = {"at": at, "until": at + timedelta(minutes=5), "minute": "10:15", "action": "sell", "symbol": "X", "qty": 75.0, "price": 1.0}
    r = {"minute": "10:20", "action": "sell", "symbol": "X", "qty": 75.0, "price": 1.0}
    assert D.compare([old], [r], [], [], day)["matched"][0]["minutes_apart"] == 0
    assert D.compare([{**old, "until": None}], [r], [], [], day)["missed"], "a fill timed at its tick: 5 minutes off"
    assert D.compare([old], [{**r, "minute": "10:23"}], [], [], day)["matched"][0]["minutes_apart"] == 3, "from the candle's end"
    assert D.compare([old], [{**r, "minute": "10:24"}], [], [], day)["missed"], "past its candle and the 3 minutes"
    assert D.candle_of({"strategy_name": "weekly_ema_st_spread", "config": {}}) == timedelta(hours=1)
    assert D.candle_of({"strategy_name": "weekly_ema_st_spread", "config": {"candle_interval_minutes": 30}}) == timedelta(minutes=30)
    assert D.candle_of({"strategy_name": "strangle_monthly_v2", "config": {}}) is None
    # a repair from inside such a candle starts at its start; chained candles move it further
    f2 = {**old, "at": at - timedelta(minutes=5), "until": at}
    assert D.repair_from([old], day, "10:17") == "10:15"
    assert D.repair_from([old, f2], day, "10:17") == "10:10", "f2 really happened at 10:15: it goes too"
    assert D.repair_from([old], day, "10:15") == "10:15" and D.repair_from([old], day, "10:21") == "10:21"


# ST_PV_INV_SENSEX, 27 Oct, once more: with only the close the app logged,
# the check still flagged the day. The replay's SuperTrend had drifted on
# earlier candles (built from Kite's minutes, the app's own not kept), so
# the app's close at 10:05 didn't cross the replay's band. The app logs
# its SuperTrend at each flip (trend, bands, ATR) with the candle: the
# replay's takes those on that candle, and goes on from the app's.
def test_where_the_app_logged_its_supertrend_the_replays_takes_it_on_that_candle():
    import copy
    from app.strategies.pivot_supertrend import CandleAggregator, SuperTrendState

    class Strategy:
        def __init__(self):
            self.aggregator = CandleAggregator(interval_minutes=5)
            self.st = SuperTrendState()
            self.st.seed_from_state("down", 73650.0, 73100.0, 60.0, 73400.0)   # the replay's, drifted

    day = datetime(2026, 10, 27)
    start = day.replace(hour=10)
    logged = {"close": 73620.0, "candle": {"open": 73420.0, "high": 73640.0, "low": 73410.0, "close": 73620.0},
              "new_trend": "up", "prev_trend": "down", "final_upper": 73890.0, "final_lower": 73350.0, "atr": 64.0}

    def run(evidence):
        s = Strategy()
        used, turned = {}, {}
        engine._use_app_candles(s, evidence, used, turned)
        s = copy.deepcopy(s)          # a minute replayed again runs on a copy: the hand-over goes with it
        for i, px in enumerate((73420.0, 73640.0, 73410.0, 73560.0)):    # Kite's minutes close it at 73,560
            assert s.aggregator.add_tick(start + timedelta(minutes=i), px) is None
        done = s.aggregator.add_tick(start + timedelta(minutes=5), 73600.0)
        return s, done, s.st.update(done), used, turned

    # the close alone: 73,620 is below the replay's band (73,650): no flip, as on 27 Oct
    s, done, trend, used, turned = run({start: {"close": 73620.0}})
    assert done["close"] == 73620.0 and trend == "down" and not turned
    # the candle and SuperTrend it logged at the flip: the replay flips with it, and goes on from the app's
    ev = {start: {**logged["candle"], "st": {k: logged[k] for k in ("final_upper", "final_lower", "atr")} | {"trend": "up"}}}
    s, done, trend, used, turned = run(ev)
    assert trend == "up" and (s.st.final_upper, s.st.final_lower, s.st.atr, s.st.prev_close) == (73890.0, 73350.0, 64.0, 73620.0)
    mine, app = turned[start]
    assert mine["trend"] == "down" and app == {"trend": "up", "final_upper": 73890.0, "final_lower": 73350.0, "atr": 64.0}
    assert used[start][1]["close"] == 73620.0 and used[start][0]["close"] == 73560.0
    # the next candle is the replay's own again, from the app's state
    assert s.aggregator.add_tick(start + timedelta(minutes=9, seconds=59), 73700.0) is None
    nxt = s.aggregator.add_tick(start + timedelta(minutes=10), 73710.0)
    assert "app_st" not in nxt and s.st.update(nxt) == "up"

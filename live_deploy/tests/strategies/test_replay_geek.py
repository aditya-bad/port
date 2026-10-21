"""Geek mode's Replay (app/geek/replay.py) on the simulated market: the
episodes, the labelled events, and frames whose P&L adds up to the book
and whose split (time, move, volatility) adds up to the P&L."""

import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app import vix as vixmod
from app.db import queries
from app.geek import common, replay as R
from tests.sim import INDEX, Sim, find_spot, side_sum
from tests.strategies.helpers import assert_book_consistent, cfg

IST = ZoneInfo("Asia/Kolkata")


class Recorder:
    """Every subscribed contract's price at every simulated tick: what the
    app's candle store would hold."""

    def __init__(self, sim):
        self.sim, self.pts = sim, {}

    async def tick(self, at, spot=None):
        await self.sim.tick(at, spot=spot)
        self.sim.dispatcher.publish()          # legs opened on this tick get their price too
        for tok, px in self.sim.dispatcher.last_prices.items():
            self.pts.setdefault(int(tok), []).append((self.sim.market.now.replace(tzinfo=IST), float(px)))


class FakeCandles:
    def __init__(self, rec):
        self.rec = rec

    async def series(self, token, start, end, interval="minute", *, fetch=True, symbol=None):
        rows = [{"t": int(t.timestamp() * 1000), "o": p, "h": p, "l": p, "c": p}
                for t, p in self.rec.pts.get(int(token), []) if start <= t < end]
        return rows, "stored", None


class FakeVix:
    """The VIX service: the index from the simulated ticks, VIX flat."""

    def __init__(self, sim):
        self.sim = sim
        self.token = 264969

    async def candles(self, token, start, end, interval="day", now=None):
        hist = self.sim.market.history.get(int(token), [])
        return [{"t": t.replace(tzinfo=IST), "o": p, "h": p, "l": p, "c": p} for t, p in hist
                if start <= t.date() <= end]

    async def vix(self, start, end, interval="day", now=None):
        idx = await self.candles(INDEX["NIFTY"][0], start, end, interval, now)
        return [{**c, "c": 13.5 + (c["c"] - 25000) / 500} for c in idx]


async def view(sim, rec, monkeypatch, **params):
    monkeypatch.setitem(vixmod.state, "service", FakeVix(sim))
    dep = dict(await queries.get_deployment(sim.pool, sim.dep_id))
    ctx = common.Ctx(pool=sim.pool, dep=dep, runner=sim.runner, now=sim.market.now.replace(tzinfo=IST),
                     candles=FakeCandles(rec), params={k: str(v) for k, v in params.items()})
    return await R.replay(ctx)


async def strangle_with_an_adjustment(pool, monkeypatch):
    sim = await Sim.create(pool, monkeypatch, "strangle_monthly_v2", cfg("strangle_monthly_v2"), mode="positional")
    rec = Recorder(sim)
    await rec.tick("10:00", spot=25000)
    await rec.tick("10:01", spot=25000)
    hit = await find_spot(sim, lambda p: side_sum(p, "PE") < 0.5 * side_sum(p, "CE"), 10)
    for i, s in enumerate(range(25000, int(hit) + 20, 20)):
        await rec.tick((datetime.combine(sim.market.today, datetime.min.time()) + timedelta(hours=11, minutes=i)), spot=s)
    return sim, rec


def test_a_cycle_replays_with_labelled_events_and_frames_that_add_up(pool_run, monkeypatch):
    async def go(pool):
        sim, rec = await strangle_with_an_adjustment(pool, monkeypatch)
        out = await view(sim, rec, monkeypatch)
        assert [e["label"] for e in out["episodes"]] == ["Cycle 1"] and out["selected"] == [1, 1]
        ep = out["episodes"][0]
        assert ep["open"] and ep["adjustments"] >= 1
        kinds = [e["kind"] for e in out["events"]]
        assert kinds[0] == "new" and out["events"][0]["label"] == "New cycle"
        adj = [e for e in out["events"] if e["kind"] == "adjustment"]
        assert adj and adj[0]["rule"] and any(f["opening"] for f in adj[0]["fills"]) and any(not f["opening"] for f in adj[0]["fills"])
        assert adj[0]["since"]["idx"] > 0            # Nifty rose into the adjustment
        assert adj[0]["vix"] is not None
        assert out["interval"] == "minute" and out["frames"]
        # frames: the booked part is exactly what closed, and the split adds up to the P&L
        last = out["frames"][-1]
        closed = [p for p in await sim.all_positions() if p["status"] == "closed"]
        assert last["booked"] == pytest.approx(sum(float(p["realized_pnl"]) for p in closed), abs=0.05)
        opens = await sim.open_positions()
        px = {p["symbol"]: sim.dispatcher.last_prices[int(p["instrument_token"])] for p in opens}
        open_pnl = sum((float(p["avg_entry_price"]) - px[p["symbol"]]) * float(p["qty"]) for p in opens)
        assert last["pnl"] == pytest.approx(last["booked"] + open_pnl, abs=0.1)
        for f in out["frames"]:
            if f["split"] is not None:
                assert sum(f["split"]) == pytest.approx(f["pnl"], abs=0.5)
        assert out["split_legs"] == len(out["legs"]) and out["unpriced"] == []
        # each step shows its own candle (not the next one): the fake candles are flat, so close == open
        moving = [f for f in out["frames"] if f["o"] is not None]
        assert moving and all(f["idx"] == f["o"] == f["h"] == f["l"] for f in moving)
        assert len({f["idx"] for f in moving}) > 5
        # every leg says which event opened it; the adjusted one which closed it
        assert all(l["opened_by"] and l["opened_by"]["label"] for l in out["legs"])
        closed = [l for l in out["legs"] if l["t1"]]
        assert closed and all(l["closed_by"]["label"] == "Adjustment" and l["exit"] is not None and l["realized"] is not None for l in closed)
        assert all(l["s0"] for l in out["legs"])
        # strikes are known for the chart
        assert all(l["strike"] and l["kind"] in ("CE", "PE") for l in out["legs"])
        # each event points at the frame it happened in
        for e in out["events"]:
            assert out["frames"][e["frame"]]["t"] <= e["t"] or e["frame"] == 0
        await assert_book_consistent(pool, sim.dep_id)
    pool_run(go)


def test_a_chain_of_cycles_plays_as_one_and_each_starts_a_new_cycle(pool_run, monkeypatch):
    async def go(pool):
        sim = await Sim.create(pool, monkeypatch, "intraday_dtt_simple", cfg("intraday_dtt_simple"))
        rec = Recorder(sim)
        day1 = sim.market.today
        for d in range(2):
            day = day1 + timedelta(days=d)
            sim.market.today = day
            for m in range(0, 370, 10):     # to 15:15, past the 15:00 square-off
                t = datetime.combine(day, datetime.min.time()) + timedelta(hours=9, minutes=15 + m)
                await rec.tick(t, spot=25000 + 30 * math.sin(m / 40))
        out = await view(sim, rec, monkeypatch, **{"from": 1, "to": 2})
        assert [e["label"] for e in out["episodes"]] == ["Trade 1", "Trade 2"]
        assert out["selected"] == [1, 2]
        starts = [e for e in out["events"] if e["kind"] == "entry"]
        exits = [e for e in out["events"] if e["kind"] == "exit"]
        assert [e["episode"] for e in starts] == [0, 1] and len(exits) == 2
        assert all(e["label"] == "Entry" for e in starts)      # no cycles here: an entry, not a new cycle
        # nights are skipped: steps only during the session
        times = [datetime.fromisoformat(f["t"]) for f in out["frames"]]
        assert all(time_ok(t) for t in times)
        assert out["frames"][-1]["booked"] == pytest.approx(
            sum(float(p["realized_pnl"]) for p in await sim.all_positions()), abs=0.05)
        # one only
        one = await view(sim, rec, monkeypatch, **{"from": 2})
        assert one["selected"] == [2, 2] and {e["episode"] for e in one["events"]} == {0}
        # out of range is clamped
        clamp = await view(sim, rec, monkeypatch, **{"from": 9, "to": 0})
        assert clamp["selected"] == [1, 2]
    pool_run(go)


def time_ok(t):
    t = t.astimezone(IST)
    return t.weekday() < 5 and (9, 15) <= (t.hour, t.minute) <= (15, 30)


def test_without_kite_it_still_replays_the_book_and_says_why_the_index_is_missing(pool_run, monkeypatch):
    async def go(pool):
        sim, rec = await strangle_with_an_adjustment(pool, monkeypatch)
        monkeypatch.setitem(vixmod.state, "service", None)
        dep = dict(await queries.get_deployment(pool, sim.dep_id))
        ctx = common.Ctx(pool=pool, dep=dep, runner=None, now=sim.market.now.replace(tzinfo=IST), candles=FakeCandles(rec))
        out = await R.replay(ctx)
        assert out["no_index"] and "Kite" in out["market_error"]
        assert out["frames"] and out["events"] and all(f["idx"] is None for f in out["frames"])
    pool_run(go)


def test_a_deployment_with_no_trades_has_nothing_to_replay(pool_run, monkeypatch):
    async def go(pool):
        sim = await Sim.create(pool, monkeypatch, "intraday_dtt_simple", cfg("intraday_dtt_simple"))
        out = await view(sim, Recorder(sim), monkeypatch)
        assert out == {"episodes": [], "selected": None}
    pool_run(go)


def test_a_checkpoint_exit_and_re_entry_starts_the_new_cycle_and_names_the_one_it_closed(pool_run, monkeypatch):
    """One action closes cycle 1 and opens cycle 2: replaying cycle 2, its
    first event is "New cycle 2" with only cycle 2's fills, and says it
    closed cycle 1 first (what that booked belongs to cycle 1)."""
    async def go(pool):
        sim = await Sim.create(pool, monkeypatch, "strangle_monthly_v2",
                               cfg("strangle_monthly_v2", checkpoint_profit_pct_of_capital=0.0005), mode="positional")
        rec = Recorder(sim)
        await rec.tick("10:00", spot=25000)
        await rec.tick("10:01", spot=25000)
        sim.market.vol = 0.12                       # premiums fall: the checkpoint is hit
        for m in range(2, 40):
            await rec.tick(f"10:{m:02d}", spot=25000)
            if len({p["metadata"].get("cycle_id") for p in await sim.all_positions()}) > 1:
                break
        out = await view(sim, rec, monkeypatch)
        labels = [e["label"] for e in out["episodes"]]
        assert labels[:2] == ["Cycle 1", "Cycle 2"], labels
        two = await view(sim, rec, monkeypatch, **{"from": 2})
        first = two["events"][0]
        assert first["kind"] == "new" and first["cycle"] == 2
        assert all(f["opening"] for f in first["fills"]) and first["booked"] is None
        cb = first["closed_before"]
        assert cb["cycle"] == 1 and all(not f["opening"] for f in cb["fills"]) and cb["booked"] is not None
        # cycle 2's replay starts from nothing booked
        assert two["frames"][0]["booked"] == 0
        # the chain shows cycle 1's exit within it, not as "before"
        chain = await view(sim, rec, monkeypatch, **{"from": 1, "to": 2})
        reentry = [e for e in chain["events"] if e["cycle"] == 2 and e["kind"] == "new"]
        assert reentry and "closed_before" not in reentry[0] and any(not f["opening"] for f in reentry[0]["fills"])
    pool_run(go)


def test_with_its_future_stored_each_leg_is_valued_off_it_and_the_split_still_adds_up(pool_run, monkeypatch):
    """Options are priced off the future: with the legs' own-month future
    stored, every bar carries it, each leg's entry future and volatility
    come from it (Black-76), and time + move + volatility still equal the
    P&L."""
    async def go(pool):
        sim, rec = await strangle_with_an_adjustment(pool, monkeypatch)
        exp = next(p["metadata"]["contract_expiry"] for p in await sim.all_positions())
        await queries.upsert_contracts(pool, [{"instrument_token": 4242, "tradingsymbol": "NIFTYFUTX", "underlying": "NIFTY",
                                               "expiry": datetime.fromisoformat(exp).date(), "instrument_type": "FUT"}])
        hist = sim.market.history[INDEX["NIFTY"][0]]
        rec.pts[4242] = [(t.replace(tzinfo=IST), p + 120.0) for t, p in hist if t.date() == sim.market.today]
        out = await view(sim, rec, monkeypatch)
        assert out["future"]["symbol"] == "NIFTYFUTX" and out["future"]["bars"] > 0
        moving = [f for f in out["frames"] if f["idx"] is not None and f["fut"] is not None]
        assert moving and all(abs(f["fut"] - f["idx"] - 120) < 0.01 for f in moving)
        # the future at entry is its last finished bar: within one bar's move (20 pts here) of spot + basis
        assert all(l["f0"] and abs(l["f0"] - l["s0"] - 120) <= 20 for l in out["legs"])
        for f in out["frames"]:
            if f["split"] is not None:
                assert sum(f["split"]) == pytest.approx(f["pnl"], abs=0.5)
        assert out["split_legs"] == len(out["legs"])
    pool_run(go)

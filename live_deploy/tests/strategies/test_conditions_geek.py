"""More data per trade, for Geek mode's Conditions view.

Every fill now carries the market it happened in (DeploymentRunner.
market_context: gap, range and move so far today, time of day, strike from
spot, days to expiry) and every signal entry its candle and ATR
(StrategyBase.signal_context). That must be logging only: each strategy
runs the same simulated sessions with and without it and must place
exactly the same orders, and a context that fails to compute never stops
a fill. Then the Conditions view (app/geek/common.conditions) on what
was recorded: trades add up to the book, MAE/MFE bracket every result,
and the buckets count every closed trade."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.deployments.runner import DeploymentRunner
from app.deployments.strategy_base import StrategyBase
from app.geek import common
from app.strategies.registry import list_strategies
from tests.sim import Sim
from tests.strategies.helpers import cfg
from tests.strategies.test_all_strategies_smoke import DAYS, POSITIONAL, market, trading_days

IST = ZoneInfo("Asia/Kolkata")
STRATS = sorted(s["name"] for s in list_strategies())


async def _run(pool, monkeypatch, strategy, name, days=None):
    mode = "positional" if strategy in POSITIONAL else "intraday"
    sim = await Sim.create(pool, monkeypatch, strategy, cfg(strategy), mode=mode, spot=25000, name=name)
    step = 5 if strategy in DAYS else 1
    for i, d in enumerate(trading_days(sim.market.today, days or DAYS.get(strategy, 2))):
        await sim.walk(f"{d} 09:15", f"{d} 15:29", every_min=step, spot_fn=market(i + 1))
    lots = [dict(r) for r in await pool.fetch(
        "SELECT p.symbol, l.action, l.qty, l.price, l.executed_at, l.reason, l.metadata FROM position_lots l "
        "JOIN positions p ON p.id = l.position_id WHERE l.deployment_id = $1 ORDER BY l.executed_at, p.symbol, l.action",
        sim.dep_id)]
    fills = [(f["symbol"], f["action"], float(f["qty"]), round(float(f["price"]), 4), f["executed_at"], f["reason"]) for f in lots]
    return sim, fills, lots


def _opens(f):
    m = f["metadata"] or {}
    return common.opening(m.get("action"), "short" if f["action"] == "sell" else "long", f["action"]) if m.get("action") else False


@pytest.mark.parametrize("strategy", STRATS)
def test_recording_the_market_never_changes_what_a_strategy_trades(strategy, pool_run, monkeypatch):
    async def go(pool):
        _, with_ctx, lots = await _run(pool, monkeypatch, strategy, f"{strategy} with context")
        monkeypatch.setattr(DeploymentRunner, "market_context", lambda self, at, metadata=None: {})
        monkeypatch.setattr(StrategyBase, "signal_context", staticmethod(lambda candle, st=None: {}))
        _, without, _ = await _run(pool, monkeypatch, strategy, f"{strategy} without")
        assert with_ctx, f"{strategy} never traded"
        assert with_ctx == without, f"{strategy}: recording the market changed its orders"
        # what was recorded is well formed
        days = sorted({f["executed_at"].astimezone(IST).date() for f in lots})
        for f in lots:
            c = (f["metadata"] or {}).get("context")
            assert isinstance(c, dict), f
            t = f["executed_at"].astimezone(IST)
            assert c["weekday"] == t.weekday() and c["minute"] == t.hour * 60 + t.minute - 555
            assert c["spot"] and c["spot"] > 20000, c
            assert c["day_low"] <= c["spot"] <= c["day_high"] and c["range_pts"] >= 0
            if t.date() != days[0]:
                assert "gap_pct" in c and abs(c["gap_pct"]) < 5, c
            if f["metadata"].get("expiry"):
                # (the sim pins expiry choice to its first day, so a long walk can hold an expired weekly)
                assert c["dte"] == (datetime.fromisoformat(f["metadata"]["expiry"][:10]).date() - t.date()).days, c
    pool_run(go)


def test_signal_entries_record_their_candle_and_atr(pool_run, monkeypatch):
    async def go(pool):
        _, _, lots = await _run(pool, monkeypatch, "pivot_supertrend_options", "st context", days=3)
        opens = [f for f in lots if _opens(f)]
        assert opens
        for f in opens:
            tv = f["metadata"]["trigger_values"]
            c = tv["candle"]
            assert c["low"] <= min(c["open"], c["close"]) <= max(c["open"], c["close"]) <= c["high"]
            assert tv["atr"] > 0 and tv["band_width"] > 0, tv
    pool_run(go)


def test_signal_context_never_raises():
    assert StrategyBase.signal_context({}) == {"candle": {}}
    assert StrategyBase.signal_context(None) == {}
    assert StrategyBase.signal_context({"open": "x"}) == {}

    class Bad:
        @property
        def atr(self):
            raise RuntimeError("no")
    assert StrategyBase.signal_context({"open": 1, "high": 2, "low": 0.5, "close": 1.5}, Bad())["candle"]["high"] == 2


def test_a_failing_context_never_stops_a_fill(pool_run, monkeypatch):
    async def go(pool):
        def boom(self, at, metadata=None):
            raise RuntimeError("context broke")
        monkeypatch.setattr(DeploymentRunner, "market_context", boom)
        _, fills, lots = await _run(pool, monkeypatch, "intraday_dtt_simple", "dtt broken context")
        assert fills and all("context" not in (f["metadata"] or {}) for f in lots)
    pool_run(go)


# ── the Conditions view ─────────────────────────────────────────────────

@pytest.mark.parametrize("strategy", ["intraday_dtt_simple", "intraday_dtt_adjusted", "pivot_supertrend_options", "strangle_monthly_v2"])
def test_conditions_add_up_to_the_book(strategy, pool_run, monkeypatch):
    async def go(pool):
        sim, _, _ = await _run(pool, monkeypatch, strategy, f"{strategy} conditions", days=3)
        from app.db import queries
        dep = dict(await queries.get_deployment(pool, sim.dep_id))
        ctx = common.Ctx(pool=pool, dep=dep, runner=sim.runner, now=sim.market.now.replace(tzinfo=IST))
        body = await common.conditions(ctx)
        rows, s = body["rows"], body["summary"]
        assert rows and s["trades"] == len(rows) and s["closed"] + s["open"] == len(rows)
        positions = await sim.all_positions()
        # every position is in exactly one trade
        syms = [x for r in rows for x in r["symbols"]]
        assert len(syms) == len(positions)
        closed = [r for r in rows if r["pnl"] is not None]
        in_closed = {x for r in closed for x in r["symbols"]}
        booked = sum(float(p["realized_pnl"]) for p in positions if p["symbol"] in in_closed and p["status"] == "closed")
        assert round(sum(r["pnl"] for r in closed), 2) == round(booked, 2)
        for r in rows:
            assert r["has_context"], r
            if r["pnl"] is not None:
                assert r["mae"] <= min(0, r["pnl"]) and r["mfe"] >= max(0, r["pnl"]), r
                assert r["held_min"] >= 0
        assert rows == sorted(rows, key=lambda r: r["t"], reverse=True), "newest first"
        for g in body["groups"]:
            assert g["covered"] == sum(b["n"] for b in g["buckets"]) <= len(closed)
            for b in g["buckets"]:
                assert 0 <= b["win_rate"] <= 100 and b["avg"] == round(b["pnl"] / b["n"], 2)
        keys = {g["key"]: g for g in body["groups"]}
        if not closed:   # a monthly strangle doesn't close a trade in three days
            assert strategy == "strangle_monthly_v2" and body["groups"] == [] and s["open"] >= 1
            return
        assert keys["hour"]["covered"] == keys["weekday"]["covered"] == len(closed)
        if strategy != "strangle_monthly_v2":
            assert keys["move"]["covered"] == len(closed), "every intraday trade knows the index's move"
    pool_run(go)


T0 = datetime(2026, 10, 19, 9, 20, tzinfo=IST)


def _lot(pid, side, action, t, trigger, opening, qty=75):
    return {"position_id": pid, "position_side": side, "action": action, "executed_at": t, "trigger": trigger,
            "opening": opening, "qty": qty, "price": 100, "pnl": None if opening else 0.0, "metadata": {}}


def _pos(pid, opened, closed):
    return {"id": pid, "opened_at": opened, "closed_at": closed}


def test_adjustments_join_their_trade_and_a_switch_starts_a_new_one():
    m = lambda n: T0 + timedelta(minutes=n)  # noqa: E731
    lots = [_lot("ce", "short", "sell", m(0), "entry", True), _lot("pe", "short", "sell", m(0), "entry", True),
            _lot("adj", "short", "sell", m(60), "adjustment", True),         # while the straddle is open: joins it
            _lot("ce", "short", "buy", m(120), "exit", False), _lot("pe", "short", "buy", m(120), "exit", False),
            _lot("adj", "short", "buy", m(120), "exit", False),
            _lot("ce2", "short", "sell", m(180), "entry", True),             # flat again: a new trade
            _lot("ce2", "short", "buy", m(240), "flip", False), _lot("pe2", "short", "sell", m(240), "flip", True)]
    pos = [_pos("ce", m(0), m(120)), _pos("pe", m(0), m(120)), _pos("adj", m(60), m(120)),
           _pos("ce2", m(180), m(240)), _pos("pe2", m(240), None)]
    trades = common.trades_of({"positions": pos, "actions": common.actions(lots)})
    assert [t["ids"] for t in trades] == [["ce", "pe", "adj"], ["ce2"], ["pe2"]]
    assert [len(t["actions"]) for t in trades] == [2, 1, 1]


def test_excursion_follows_every_leg_at_its_last_mark():
    m = lambda n: T0 + timedelta(minutes=n)  # noqa: E731
    legs = [{"id": "ce", "opened_at": m(0), "closed_at": m(30), "entry": 100.0, "qty": 10, "sign": -1, "realized": 200.0},
            {"id": "pe", "opened_at": m(0), "closed_at": None, "entry": 100.0, "qty": 10, "sign": -1, "realized": 0.0}]
    marks = {"ce": [(m(5), 120.0), (m(10), 90.0), (m(60), 1.0)],    # the 60-minute mark is after it closed: ignored
             "pe": [(m(5), 100.0), (m(10), 130.0), (m(40), 70.0)]}
    mae, mfe, path = common.excursion(legs, marks, m(60))
    # 5m: ce −200, pe 0 → −200 · 10m: ce +100, pe −300 → −200 · 30m: ce booked +200 → −100 · 40m: pe +300 → +500
    assert [v for _, v in path] == [-200, -200, -100, 500], "one point per moment, every leg updated"
    assert (mae, mfe) == (-200, 500)
    assert common.excursion(legs, {}, m(60)) == (None, None, [])


def test_buckets_are_in_their_natural_order():
    assert common._bucket(0, [0.5, 1.5, float("inf")], ["Expiry day", "1 day", "later"]) == "Expiry day"
    assert common._bucket(-0.7, [-0.5, 0, 0.5, float("inf")], ["a", "b", "c", "d"]) == "a"
    assert common._bucket(None, [1], ["x"]) is None
    assert common._hour(datetime(2026, 10, 19, 9, 30)) == "9:15–10"
    assert common._hour(datetime(2026, 10, 19, 15, 10)) == "15–15:30"
    assert common._hour(datetime(2026, 10, 19, 12, 59)) == "12–13"


def test_a_restart_mid_day_keeps_the_days_market_from_the_ticks_ohlc(pool_run, monkeypatch):
    async def go(pool):
        sim = await Sim.create(pool, monkeypatch, "intraday_dtt_simple", cfg("intraday_dtt_simple"), spot=25000, name="restart ctx")
        r, tok = sim.runner, sim.runner.config["instrument_tokens"][0]
        r._session.clear()                               # a fresh process at 11:00
        at = datetime(2026, 10, 19, 11, 0)
        r._note_session({"instrument_token": tok, "last_price": 25100.0, "exchange_timestamp": at})
        c = r.market_context(at)
        assert c["partial"] is True and "gap_pct" not in c, "only what it saw since 11:00, and no previous close"
        # a full-mode tick: the exchange's own open/high/low and previous close
        r._note_session({"instrument_token": tok, "last_price": 25120.0, "exchange_timestamp": at + timedelta(seconds=5),
                         "ohlc": {"open": 24900.0, "high": 25150.0, "low": 24850.0, "close": 25000.0}})
        c = r.market_context(at + timedelta(seconds=5))
        assert "partial" not in c
        assert (c["day_open"], c["day_high"], c["day_low"], c["prev_close"]) == (24900.0, 25150.0, 24850.0, 25000.0)
        assert (c["gap_pct"], c["range_pts"], c["from_open_pct"]) == (-0.4, 300.0, 0.88)
        # a later tick above the exchange's high widens it; a new day starts over
        r._note_session({"instrument_token": tok, "last_price": 25200.0, "exchange_timestamp": at + timedelta(minutes=1)})
        assert r.market_context(at + timedelta(minutes=1))["day_high"] == 25200.0
        nxt = datetime(2026, 10, 20, 9, 15, 30)
        r._note_session({"instrument_token": tok, "last_price": 25210.0, "exchange_timestamp": nxt})
        c = r.market_context(nxt)
        assert "partial" not in c and c["prev_close"] == 25200.0 and c["gap_pct"] == 0.04, "seen from the open: a whole day"
    pool_run(go)


def test_thirds_and_moneyness():
    assert common.thirds([5, 1, 3]) is None, "too few trades to split"
    assert common.thirds([1, 2, 3, 4, 5, 6]) == (2, 4)
    assert common.thirds(list(range(1, 10))) == (3, 6)
    assert common.third_labels((31.25, 45.0)) == ["Low · up to 31.25", "Middle · 31.25–45", "High · above 45"]
    # a call above the index and a put below it are out of the money
    assert common.otm_pct({"strike_from_spot": 250.0, "spot": 25000.0}, "CE") == 1.0
    assert common.otm_pct({"strike_from_spot": 250.0, "spot": 25000.0}, "PE") == -1.0
    assert common.otm_pct({"strike_from_spot": -50.0, "spot": 25000.0}, "PE") == 0.2
    assert common.otm_pct({"spot": 25000.0}, "CE") is None and common.otm_pct({"strike_from_spot": 1, "spot": 1}, None) is None


def test_vix_is_stamped_on_every_fill_once_known(pool_run, monkeypatch):
    from app import vix as V

    async def go(pool):
        sim = await Sim.create(pool, monkeypatch, "intraday_dtt_simple", cfg("intraday_dtt_simple"), spot=25000, name="vix ctx")
        at = datetime(2026, 10, 19, 11, 0)
        V.state["token"] = None
        assert "vix" not in sim.runner.market_context(at), "not looked up yet: nothing, never a guess"
        monkeypatch.setitem(V.state, "token", 264969)
        sim.runner.dispatcher.last_prices[264969] = 14.237
        assert sim.runner.market_context(at)["vix"] == 14.24
    pool_run(go)

"""Shadow mode end to end on the database (app/shadow.py): a fill is
handed over, checked in the background and stored; nothing about the
fill itself waits for it or changes."""

import asyncio
import time
from datetime import date, datetime, timedelta, timezone

import pytest

from app import shadow
from app.db import queries
from tests.conftest import fill

T0 = datetime(2026, 10, 14, 4, 30, tzinfo=timezone.utc)   # 10:00 IST


class Disp:
    def __init__(self, depths=None, prices=None):
        self.last_depths = depths or {}
        self.last_prices = prices or {}
        self.access_token = None


def book(bids=(), asks=()):
    return {"buy": [{"price": p, "quantity": q} for p, q in bids], "sell": [{"price": p, "quantity": q} for p, q in asks]}


def fresh(depth):
    return (depth, datetime.now(timezone.utc))


def svc(pool, disp, quote=None, freeze=None, **kw):
    """Every instrument quotes `quote`; `asked` records each Kite call."""
    asked = []
    async def q(keys):
        asked.append(sorted(keys))
        return {k: quote for k in keys}
    f = shadow.FreezeLimits(pool, freeze if freeze is not None else {"NIFTY": 1800})
    s = shadow.ShadowService(pool, disp, {"shadow": {"limit_buffer_pct": 0.5}}, quotes=q, freeze=f, batch_s=0.05, **kw)
    s.asked = asked
    return s


async def dep(pool, name="S"):
    return (await queries.create_deployment(pool, name, "intraday_dtt_simple", "intraday", 500000, {}))["id"]


async def row(pool, lot_id):
    r = await pool.fetchrow("SELECT * FROM shadow_checks WHERE lot_id = $1", lot_id)
    return shadow.row_out(r) if r else None


def handed(d, r, symbol, token, action, qty, price, at=T0):
    return {"lot_id": r["lot_id"], "deployment_id": d, "executed_at": at, "symbol": symbol,
            "instrument_token": token, "action": action, "qty": qty, "paper_price": price}


QUOTE = {"lower_circuit_limit": 50.0, "upper_circuit_limit": 150.0}


def test_a_fill_is_checked_against_the_book_at_the_fill_and_the_band(pool_run):
    async def go(pool):
        d = await dep(pool)
        disp = Disp({1: fresh(book(bids=[(99.8, 75), (99.6, 500)]))})
        s = svc(pool, disp, QUOTE)
        r = await fill(pool, d, "NIFTY26OCT25800CE", 1, "sell", 150, 100.0, T0)
        s.submit(handed(d, r, "NIFTY26OCT25800CE", 1, "sell", 150, 100.0))
        # the book is taken at the fill: what it does next doesn't count
        disp.last_depths[1] = fresh(book(bids=[(10.0, 1)]))
        await s.drain()
        out = await row(pool, r["lot_id"])
        assert out["verdict"] == "worse" and out["shortfall"] == pytest.approx(45.0)
        assert out["limit_price"] == 99.5 and out["orders"] == 1 and not out["estimated"]
        assert {c["rule"]: c["status"] for c in out["checks"]} == {"lot": "unknown", "freeze": "ok", "band": "ok", "book": "ok"}
        # the fill itself is untouched
        [lot] = await pool.fetch("SELECT price FROM position_lots WHERE id = $1", r["lot_id"])
        assert float(lot["price"]) == 100.0
    pool_run(go)


def test_a_stale_book_is_not_used_the_quotes_is(pool_run):
    async def go(pool):
        d = await dep(pool)
        old = (book(bids=[(10.0, 1)]), datetime.now(timezone.utc) - timedelta(minutes=5))
        s = svc(pool, Disp({1: old}), {**QUOTE, "depth": book(bids=[(100.0, 500)])})
        r = await fill(pool, d, "NIFTY26OCT25800CE", 1, "sell", 75, 100.0, T0)
        s.submit(handed(d, r, "NIFTY26OCT25800CE", 1, "sell", 75, 100.0))
        await s.drain()
        assert (await row(pool, r["lot_id"]))["verdict"] == "fill"
    pool_run(go)


def test_a_resting_limit_is_followed_for_the_next_seconds(pool_run):
    async def go(pool):
        d = await dep(pool)
        disp = Disp({1: fresh(book(asks=[(102.0, 500)]))}, {1: 101.5})
        s = svc(pool, disp, QUOTE, follow_s=2)
        r = await fill(pool, d, "NIFTY26OCT25800CE", 1, "buy", 75, 100.0, T0)
        s.submit(handed(d, r, "NIFTY26OCT25800CE", 1, "buy", 75, 100.0))
        await asyncio.sleep(0.3)
        assert (await row(pool, r["lot_id"]))["verdict"] == "no_fill", "stored at once, then updated"
        disp.last_prices[1] = 100.3     # comes to the limit (100.50)
        await s.drain()
        out = await row(pool, r["lot_id"])
        assert out["verdict"] == "worse" and out["est_price"] == 100.5
        assert out["followup"]["reached"] is True
    pool_run(go)


def test_a_quote_that_hangs_or_fails_leaves_the_band_unchecked(pool_run, monkeypatch):
    monkeypatch.setattr(shadow, "QUOTE_TIMEOUT_S", 0.2)

    async def go(pool):
        d = await dep(pool)
        async def hang(keys):
            await asyncio.sleep(30)
        s = shadow.ShadowService(pool, Disp({1: fresh(book(bids=[(100.0, 500)]))}), {}, quotes=hang,
                                 freeze=shadow.FreezeLimits(pool, {}), batch_s=0.05)
        r = await fill(pool, d, "NIFTY26OCT25800CE", 1, "sell", 75, 100.0, T0)
        s.submit(handed(d, r, "NIFTY26OCT25800CE", 1, "sell", 75, 100.0))
        await s.drain()
        out = await row(pool, r["lot_id"])
        assert out["verdict"] == "fill"
        st = {c["rule"]: c["status"] for c in out["checks"]}
        assert st["band"] == "unknown" and st["freeze"] == "unknown"
    pool_run(go)


def test_a_check_that_breaks_is_stored_as_unknown(pool_run):
    async def go(pool):
        d = await dep(pool)
        s = svc(pool, Disp())
        async def boom(fill):
            raise RuntimeError("boom")
        s.check = boom
        r = await fill(pool, d, "X1CE", 1, "sell", 75, 100.0, T0)
        s.submit(handed(d, r, "X1CE", 1, "sell", 75, 100.0))
        await s.drain()
        out = await row(pool, r["lot_id"])
        assert out["verdict"] == "unknown" and "boom" in out["checks"][0]["detail"]
        assert s.failed == 1
    pool_run(go)


def test_turned_off_in_config_nothing_is_checked(pool_run):
    async def go(pool):
        d = await dep(pool)
        s = shadow.ShadowService(pool, Disp(), {"shadow": {"enabled": False}})
        r = await fill(pool, d, "X1CE", 1, "sell", 75, 100.0, T0)
        s.submit(handed(d, r, "X1CE", 1, "sell", 75, 100.0))
        await s.drain()
        assert await row(pool, r["lot_id"]) is None
    pool_run(go)


def test_a_slow_check_never_holds_up_the_fill(pool_run):
    """The runner and a force-close hand the fill over and carry on."""
    async def go(pool):
        d = await dep(pool)
        started = asyncio.Event()
        async def slow(keys):
            started.set()
            await asyncio.sleep(30)
        s = shadow.ShadowService(pool, Disp(), {}, quotes=slow, freeze=shadow.FreezeLimits(pool, {}), batch_s=0.05)
        shadow.state["service"] = s
        try:
            from app.deployments.runner import DeploymentRunner
            runner = DeploymentRunner(await queries.get_deployment(pool, d), pool, None, Disp(), None)
            await runner._reload_positions()
            t = time.monotonic()
            await runner.sell("NIFTY26OCT25800CE", 1, 75, 100.0, T0, reason="entry")
            [p] = await queries.list_open_positions(pool, d)
            await queries.force_close_position(pool, d, p, 90.0, T0 + timedelta(minutes=5), reason="exit")
            assert time.monotonic() - t < 2, "the fills didn't wait for the checks"
            await asyncio.wait_for(started.wait(), 2)
            assert len([t for t in s.tasks if t.get_name() != "shadow-quotes"]) == 2, "both fills were handed over"
            for task in list(s.tasks):
                task.cancel()
            await asyncio.gather(*s.tasks, return_exceptions=True)
        finally:
            shadow.state["service"] = None
    pool_run(go)


def test_todays_rejections_per_deployment_and_rule(pool_run):
    async def go(pool):
        a, b = await dep(pool, "A"), await dep(pool, "B")
        at = datetime(2026, 10, 14, 5, 0, tzinfo=timezone.utc)
        for d, tok, qty in ((a, 1, 100), (a, 1, 100), (b, 2, 75)):
            r = await fill(pool, d, "NIFTY26OCT25800CE", tok, "sell", qty, 100.0, at)
            f = handed(d, r, "NIFTY26OCT25800CE", tok, "sell", qty, 100.0, at)
            # 100 isn't a whole number of 75-lots: Kite refuses it
            await shadow.save(pool, f, shadow.assess("sell", qty, 100.0, lot=75, tick=0.05, freeze=1800,
                                                     band=(50.0, 150.0), depth=book(bids=[(100.0, 5000)])))
            at += timedelta(minutes=1)
        rej = await shadow.rejections(pool, date(2026, 10, 14))
        assert len(rej) == 1, "B's 75 is a whole lot"
        assert rej[0]["rule"] == "lot" and rej[0]["n"] == 2 and rej[0]["name"] == "A"
        assert "whole lots" in rej[0]["detail"]
        assert await shadow.rejections(pool, date(2026, 10, 15)) == []
    pool_run(go)


def test_freeze_limits_keep_the_last_good_file(pool_run):
    async def go(pool):
        text = "SYMBOL,VOL_FRZ_QTY\n" + "\n".join(f"S{i},{100 + i}" for i in range(6)) + "\nNIFTY,1800\n"
        f = shadow.FreezeLimits(pool, {}, fetch=lambda: text)
        assert await f.refresh(date(2026, 10, 23)) == 7
        assert f.get("NIFTY") == (1800, "NSE file of 23 Oct")
        # a bad day: the file can't be read, the last copy stays
        f.fetch = lambda: "<html>Access denied</html>"
        with pytest.raises(ValueError):
            await f.refresh(date(2026, 10, 24))
        g = shadow.FreezeLimits(pool, {})
        await g.load()
        assert g.get("NIFTY") == (1800, "NSE file of 23 Oct")
    pool_run(go)


def test_the_quotes_for_fills_close_together_are_one_kite_call(pool_run):
    """An entry's legs (and other deployments' at the same minute) mustn't
    each queue a Kite call ahead of the strategies' own."""
    async def go(pool):
        d = await dep(pool)
        disp = Disp({t: fresh(book(bids=[(100.0, 500)])) for t in (1, 2, 3)})
        s = svc(pool, disp, QUOTE)
        for t, sym in ((1, "NIFTY26OCT25800CE"), (2, "NIFTY26OCT24800PE"), (3, "NIFTY26OCT26000CE")):
            r = await fill(pool, d, sym, t, "sell", 75, 100.0, T0)
            s.submit(handed(d, r, sym, t, "sell", 75, 100.0))
        await s.drain()
        assert s.asked == [["NFO:NIFTY26OCT24800PE", "NFO:NIFTY26OCT25800CE", "NFO:NIFTY26OCT26000CE"]]
        rows = await pool.fetch("SELECT checks FROM shadow_checks WHERE deployment_id = $1", d)
        assert len(rows) == 3 and all(next(c for c in r["checks"] if c["rule"] == "band")["status"] == "ok" for r in rows)
    pool_run(go)


def test_fills_from_another_event_loop_are_left_alone(pool_run):
    """The test process runs the app on one loop and strategies on
    others: the service only takes fills from its own pool's loop."""
    holder = {}

    async def go(pool):
        holder["s"] = svc(pool, Disp())
    pool_run(go)

    async def other():
        s = holder["s"]
        s.submit({"lot_id": None, "deployment_id": None, "executed_at": T0, "symbol": "X", "instrument_token": 1,
                  "action": "sell", "qty": 75, "paper_price": 100.0})
        return len(s.tasks)
    assert asyncio.run(other()) == 0

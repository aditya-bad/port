"""Shadow mode end to end on the database (app/shadow.py): a fill is
handed over, checked in the background and stored; nothing about the
fill itself waits for it or changes."""

import asyncio
import time
from datetime import date, datetime, timedelta, timezone

import pytest

from app import shadow
import app.options.resolver  # noqa: F401  (loaded at startup in the app; its first import blocks the loop for a second)
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


async def flat_margin(orders):
    """A toy margin model: ₹1,000 a unit sold, a tenth of that once any
    buy hedges the basket."""
    sold = sum(o["quantity"] for o in orders if o["transaction_type"] == "SELL")
    hedged = any(o["transaction_type"] == "BUY" for o in orders)
    return {"initial": {"total": sold * 1000.0}, "final": {"total": sold * (100.0 if hedged else 1000.0)}}


def svc(pool, disp, quote=None, freeze=None, margins=flat_margin, free=1e9, **kw):
    """Every instrument quotes `quote`; `asked` records each Kite call."""
    asked = []
    async def q(keys):
        asked.append(sorted(keys))
        return {k: quote for k in keys}
    async def funds():
        return free
    f = shadow.FreezeLimits(pool, freeze if freeze is not None else {"NIFTY": 1800})
    s = shadow.ShadowService(pool, disp, {"shadow": {"limit_buffer_pct": 0.5}}, quotes=q, margins=margins, funds=funds,
                             freeze=f, batch_s=0.05, **kw)
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


QUOTE = {"lower_circuit_limit": 1.0, "upper_circuit_limit": 150.0}


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
        assert {c["rule"]: c["status"] for c in out["checks"]} == {"lot": "unknown", "freeze": "ok", "band": "ok", "book": "ok",
                                                                 "rate": "ok", "margin": "ok"}
        m = next(c for c in out["checks"] if c["rule"] == "margin")
        assert m["required"] == 150000 and m["capital"] == 500000, "150 sold, unhedged, against its ₹5L"
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
            assert s.handed == 2, "both fills were handed over"
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
        legs = ((1, "NIFTY26OCT25800CE"), (2, "NIFTY26OCT24800PE"), (3, "NIFTY26OCT26000CE"))
        # the fills are written first and handed over together: writing each
        # between submits could outlast the 50 ms window on a busy machine
        # and split them, which isn't what this test is about
        handed_over = [handed(d, await fill(pool, d, sym, t, "sell", 75, 100.0, T0), sym, t, "sell", 75, 100.0) for t, sym in legs]
        for h in handed_over:
            s.submit(h)
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



# ── stage 3: margin, leg by leg, order rate ─────────────────────────────

async def checks_of(pool, lot_id):
    return {c["rule"]: c for c in (await row(pool, lot_id))["checks"]}


def test_an_entry_needing_more_margin_than_the_deployment_has_is_refused(pool_run):
    async def go(pool):
        d = await dep(pool)    # ₹5,00,000
        s = svc(pool, Disp({1: fresh(book(bids=[(100.0, 5000)]))}), QUOTE)
        r = await fill(pool, d, "NIFTY26OCT25800CE", 1, "sell", 600, 100.0, T0)
        s.submit(handed(d, r, "NIFTY26OCT25800CE", 1, "sell", 600, 100.0))
        await s.drain()
        out = await row(pool, r["lot_id"])
        assert out["verdict"] == "reject"
        m = next(c for c in out["checks"] if c["rule"] == "margin")
        assert m["status"] == "reject" and "₹6,00,000" not in m["detail"] and "₹600,000" in m["detail"] and "₹500,000" in m["detail"]
    pool_run(go)


def test_every_deployment_together_against_the_accounts_free_margin(pool_run):
    async def go(pool):
        a, b = await dep(pool, "A"), await dep(pool, "B")
        s = svc(pool, Disp({1: fresh(book(bids=[(100.0, 5000)])), 2: fresh(book(bids=[(100.0, 5000)]))}), QUOTE, free=500000)
        await fill(pool, a, "NIFTY26OCT25800CE", 1, "sell", 300, 100.0, T0)          # A already holds ₹3L of margin
        r = await fill(pool, b, "NIFTY26OCT24800PE", 2, "sell", 300, 100.0, T0)       # B adds ₹3L: ₹6L > ₹5L free
        s.submit(handed(b, r, "NIFTY26OCT24800PE", 2, "sell", 300, 100.0))
        await s.drain()
        m = (await checks_of(pool, r["lot_id"]))["margin"]
        assert m["status"] == "reject" and "Every deployment's open legs together need ₹600,000; the account has ₹500,000 free." == m["detail"]
        assert m["account_required"] == 600000 and m["account_free"] == 500000
    pool_run(go)


def test_a_short_placed_before_its_hedge_needs_the_naked_margin_until_the_hedge_fills(pool_run):
    async def go(pool):
        d = await dep(pool)
        disp = Disp({1: fresh(book(bids=[(100.0, 5000)])), 2: fresh(book(asks=[(20.0, 5000)]))})
        s = svc(pool, disp, QUOTE)
        sell = await fill(pool, d, "NIFTY26OCT25800CE", 1, "sell", 600, 100.0, T0)    # the short first...
        buy = await fill(pool, d, "NIFTY26OCT26800CE", 2, "buy", 600, 20.0, T0)       # ...then its hedge
        s.submit(handed(d, sell, "NIFTY26OCT25800CE", 1, "sell", 600, 100.0))
        s.submit(handed(d, buy, "NIFTY26OCT26800CE", 2, "buy", 600, 20.0))
        await s.drain()
        ms = (await checks_of(pool, sell["lot_id"]))["margin"]
        assert ms["status"] == "reject" and ms["placing"] and "Place the hedge first" in ms["detail"]
        assert "NIFTY26OCT26800CE" in ms["detail"] and ms["required"] == 600000
        mb = (await checks_of(pool, buy["lot_id"]))["margin"]
        assert mb["status"] == "ok" and mb["required"] == 60000, "hedged, the pair needs a tenth"
        assert (await row(pool, sell["lot_id"]))["verdict"] == "reject"
        assert (await row(pool, buy["lot_id"]))["verdict"] == "fill"
    pool_run(go)


def test_hedge_first_is_fine(pool_run):
    async def go(pool):
        d = await dep(pool)
        s = svc(pool, Disp({1: fresh(book(bids=[(100.0, 5000)])), 2: fresh(book(asks=[(20.0, 5000)]))}), QUOTE)
        buy = await fill(pool, d, "NIFTY26OCT26800CE", 2, "buy", 600, 20.0, T0)
        sell = await fill(pool, d, "NIFTY26OCT25800CE", 1, "sell", 600, 100.0, T0)
        s.submit(handed(d, buy, "NIFTY26OCT26800CE", 2, "buy", 600, 20.0))
        s.submit(handed(d, sell, "NIFTY26OCT25800CE", 1, "sell", 600, 100.0))
        await s.drain()
        ms = (await checks_of(pool, sell["lot_id"]))["margin"]
        assert ms["status"] == "ok" and not ms.get("placing")
    pool_run(go)


def test_an_exit_isnt_margin_checked(pool_run):
    async def go(pool):
        d = await dep(pool)
        s = svc(pool, Disp({1: fresh(book(asks=[(90.0, 5000)]))}), QUOTE)
        await fill(pool, d, "NIFTY26OCT25800CE", 1, "sell", 75, 100.0, T0)
        r = await fill(pool, d, "NIFTY26OCT25800CE", 1, "buy", 75, 90.0, T0 + timedelta(minutes=5))
        s.submit(handed(d, r, "NIFTY26OCT25800CE", 1, "buy", 75, 90.0))
        await s.drain()
        assert "margin" not in await checks_of(pool, r["lot_id"])
    pool_run(go)


def test_no_margin_from_kite_is_unknown_not_a_failure(pool_run):
    async def go(pool):
        d = await dep(pool)
        async def down(orders):
            raise RuntimeError("No Kite session")
        s = svc(pool, Disp({1: fresh(book(bids=[(100.0, 5000)]))}), QUOTE, margins=down)
        r = await fill(pool, d, "NIFTY26OCT25800CE", 1, "sell", 75, 100.0, T0)
        s.submit(handed(d, r, "NIFTY26OCT25800CE", 1, "sell", 75, 100.0))
        await s.drain()
        out = await row(pool, r["lot_id"])
        m = next(c for c in out["checks"] if c["rule"] == "margin")
        assert m["status"] == "unknown" and "No Kite session" in m["detail"] and out["verdict"] == "fill"
    pool_run(go)


def test_margin_can_be_turned_off(pool_run):
    async def go(pool):
        d = await dep(pool)
        s = shadow.ShadowService(pool, Disp({1: fresh(book(bids=[(100.0, 5000)]))}), {"shadow": {"margin": False}},
                                 quotes=lambda keys: asyncio.sleep(0, {}), freeze=shadow.FreezeLimits(pool, {}), batch_s=0.05)
        r = await fill(pool, d, "NIFTY26OCT25800CE", 1, "sell", 75, 100.0, T0)
        s.submit(handed(d, r, "NIFTY26OCT25800CE", 1, "sell", 75, 100.0))
        await s.drain()
        assert "margin" not in await checks_of(pool, r["lot_id"])
    pool_run(go)


def test_legs_placed_one_after_another_meet_the_price_of_then(pool_run):
    async def go(pool):
        d = await dep(pool)
        disp = Disp({1: fresh(book(bids=[(100.0, 5000)])), 2: fresh(book(bids=[(80.0, 5000)]))}, {1: 100.0, 2: 80.0})
        s = svc(pool, disp, QUOTE, leg_gap_s=0.2)
        r1 = await fill(pool, d, "NIFTY26OCT25800CE", 1, "sell", 75, 100.0, T0)
        r2 = await fill(pool, d, "NIFTY26OCT24800PE", 2, "sell", 75, 80.0, T0)
        s.submit(handed(d, r1, "NIFTY26OCT25800CE", 1, "sell", 75, 100.0))
        s.submit(handed(d, r2, "NIFTY26OCT24800PE", 2, "sell", 75, 80.0))
        await asyncio.sleep(0.03)
        disp.last_prices[2] = 79.0      # the PE falls before it's placed: sold ₹1 lower
        await s.drain()
        c1, c2 = await checks_of(pool, r1["lot_id"]), await checks_of(pool, r2["lot_id"])
        assert "legs" not in c1, "the first leg goes at once"
        assert c2["legs"]["status"] == "warn" and c2["legs"]["cost"] == 75.0
        assert "Placed after 1 leg (about 0.2 s later): the price had moved ₹75 against it." == c2["legs"]["detail"]
    pool_run(go)


def test_more_orders_in_a_second_than_kite_takes(pool_run):
    async def go(pool):
        ds = [await dep(pool, f"D{i}") for i in range(4)]
        s = svc(pool, Disp({t: fresh(book(bids=[(100.0, 5000)])) for t in range(1, 13)}), QUOTE)
        rs = []
        for i in range(12):             # four deployments entering three legs each, at once
            d = ds[i % 4]
            r = await fill(pool, d, f"NIFTY26OCT2{i:02d}00CE", i + 1, "sell", 75, 100.0, T0)
            s.submit(handed(d, r, f"NIFTY26OCT2{i:02d}00CE", i + 1, "sell", 75, 100.0))
            rs.append(r)
        await s.drain()
        rates = [(await checks_of(pool, r["lot_id"]))["rate"] for r in rs]
        assert [x["status"] for x in rates[:10]] == ["ok"] * 10
        assert [x["status"] for x in rates[10:]] == ["warn", "warn"]
        assert rates[-1]["second"] == 12 and "Kite takes 10 a second" in rates[-1]["detail"]
    pool_run(go)


def test_shadows_kite_calls_wait_for_the_strategies(monkeypatch):
    """A Kite call in the last second (a strategy's): shadow waits."""
    import time as _t
    from app.options import resolver as R

    async def go():
        monkeypatch.setattr(R, "_kite_call_next_allowed_at", _t.monotonic() + R._KITE_CALL_MIN_INTERVAL)   # one just went out
        t = _t.monotonic()
        await shadow.ShadowService._kite_idle()
        waited = _t.monotonic() - t
        assert 0.8 <= waited < 2.0, waited
        t = _t.monotonic()
        await shadow.ShadowService._kite_idle()
        assert _t.monotonic() - t < 0.1, "quiet now: no wait"
    asyncio.run(go())


# ── stage 4: estimating past fills ──────────────────────────────────────

def test_backfill_estimates_every_fill_without_a_check_once(pool_run):
    async def go(pool):
        a, b = await dep(pool, "A"), await dep(pool, "B")
        r1 = await queries.record_fill(pool, a, "NIFTY26OCT25800CE", 1, "sell", 75, 100.0, T0, book_price=99.9)
        r2 = await queries.record_fill(pool, a, "NIFTY26OCT25800CE", 1, "buy", 75, 90.0, T0 + timedelta(hours=1))
        r3 = await queries.record_fill(pool, b, "NIFTY26OCT24800PE", 2, "sell", 75, 50.0, T0, book_price=50.0)
        f = shadow.FreezeLimits(pool, {"NIFTY": 1800})
        # B's fill was checked as it happened: kept as it is
        await shadow.save(pool, handed(b, r3, "NIFTY26OCT24800PE", 2, "sell", 75, 50.0), shadow.assess("sell", 75, 50.0, depth=book(bids=[(50.0, 99)])))
        dry = await shadow.backfill(pool, f, dry_run=True)
        assert dry == {"fills": 2, "verdicts": {"worse": 1, "unknown": 1}, "written": 0}
        assert await row(pool, r1["lot_id"]) is None, "a dry run writes nothing"
        only_b = await shadow.backfill(pool, f, deployment_id=b)
        assert only_b["fills"] == 0
        out = await shadow.backfill(pool, f)
        assert out["written"] == 2
        e1, e2, kept = await row(pool, r1["lot_id"]), await row(pool, r2["lot_id"]), await row(pool, r3["lot_id"])
        assert e1["estimated"] and e1["verdict"] == "worse" and e1["shortfall"] == pytest.approx(7.5)
        assert e2["estimated"] and e2["verdict"] == "unknown"
        assert not kept["estimated"]
        assert (await shadow.backfill(pool, f))["fills"] == 0, "again: nothing left"
    pool_run(go)

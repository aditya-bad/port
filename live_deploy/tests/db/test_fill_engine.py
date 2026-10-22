"""The fill engine (queries.record_fill / force_close_position): every
rupee shown anywhere starts here."""

from datetime import datetime, timedelta, timezone

import pytest

from app.charges import estimate_options_charges
from app.db import queries
from tests.conftest import fill

T0 = datetime(2026, 10, 14, 4, 30, tzinfo=timezone.utc)   # 10:00 IST


async def dep(pool, capital=100000, name="D", mode="intraday"):
    row = await queries.create_deployment(pool, name, "intraday_dtt_simple", mode, capital, {})
    return row["id"]


async def lots(pool, d):
    return await pool.fetch("SELECT * FROM position_lots WHERE deployment_id = $1 ORDER BY executed_at, id", d)


async def cash(pool, d):
    return float((await queries.get_deployment(pool, d))["current_cash"])


def test_short_open_then_close_books_pnl_charges_and_cash(pool_run):
    async def go(pool):
        d = await dep(pool)
        r1 = await fill(pool, d, "NIFTYCE", 1, "sell", 75, 100.0, T0, reason="entry")
        assert r1["realized_pnl"] is None
        assert await cash(pool, d) == pytest.approx(100000 + 7500)
        [p] = await queries.list_open_positions(pool, d)
        assert p["side"] == "short" and float(p["qty"]) == 75 and float(p["avg_entry_price"]) == 100
        r2 = await fill(pool, d, "NIFTYCE", 1, "buy", 75, 60.0, T0 + timedelta(hours=1), reason="exit")
        assert r2["realized_pnl"] == pytest.approx(3000.0)
        assert await cash(pool, d) == pytest.approx(100000 + 7500 - 4500)
        [c] = await queries.list_positions(pool, d, status="closed")
        assert float(c["qty"]) == 0 and c["closed_at"] is not None
        assert float(c["estimated_charges"]) == pytest.approx(estimate_options_charges(60.0, 100.0, 75), abs=0.01)
        assert await queries.list_open_positions(pool, d) == []
    pool_run(go)


def test_long_round_trip_loss(pool_run):
    async def go(pool):
        d = await dep(pool)
        await fill(pool, d, "X", 7, "buy", 50, 20.0, T0)
        r = await fill(pool, d, "X", 7, "sell", 50, 12.5, T0 + timedelta(minutes=5))
        assert r["realized_pnl"] == pytest.approx(-375.0)
        assert await cash(pool, d) == pytest.approx(100000 - 1000 + 625)
    pool_run(go)


def test_adding_to_a_position_averages_the_entry(pool_run):
    async def go(pool):
        d = await dep(pool)
        await fill(pool, d, "X", 1, "sell", 75, 100.0, T0)
        await fill(pool, d, "X", 1, "sell", 75, 80.0, T0 + timedelta(minutes=1))
        [p] = await queries.list_open_positions(pool, d)
        assert float(p["qty"]) == 150 and float(p["avg_entry_price"]) == pytest.approx(90.0)
        r = await fill(pool, d, "X", 1, "buy", 150, 70.0, T0 + timedelta(minutes=2))
        assert r["realized_pnl"] == pytest.approx(3000.0)
    pool_run(go)


def test_partial_close_is_refused(pool_run):
    async def go(pool):
        d = await dep(pool)
        await fill(pool, d, "X", 1, "sell", 150, 100.0, T0)
        with pytest.raises(queries.ClosingQtyMismatch):
            await fill(pool, d, "X", 1, "buy", 75, 90.0, T0)
        [p] = await queries.list_open_positions(pool, d)
        assert float(p["qty"]) == 150, "a refused fill changes nothing"
    pool_run(go)


def test_buy_beyond_cash_is_refused_and_changes_nothing(pool_run):
    async def go(pool):
        d = await dep(pool, capital=1000)
        with pytest.raises(queries.InsufficientCash):
            await fill(pool, d, "X", 1, "buy", 100, 50.0, T0)
        assert await cash(pool, d) == 1000
        assert await queries.list_positions(pool, d) == []
    pool_run(go)


def test_buying_back_a_short_is_never_refused_for_cash(pool_run):
    """An exit can't wait for cash: a short that ran badly against you is
    bought back even when that takes cash below zero (migration 0034) —
    refused, it stayed open with nothing able to close it. Opening buys
    are still checked."""
    async def go(pool):
        d = await dep(pool, capital=1000)
        await fill(pool, d, "X", 1, "sell", 10, 50.0, T0)              # +500
        await fill(pool, d, "X", 1, "buy", 10, 400.0, T0 + timedelta(minutes=1))   # −4000
        assert await cash(pool, d) == 1000 + 500 - 4000
        assert await queries.list_open_positions(pool, d) == []
        with pytest.raises(queries.InsufficientCash):
            await fill(pool, d, "Y", 2, "buy", 1, 10.0, T0 + timedelta(minutes=2))
    pool_run(go)


def test_reopening_after_a_close_is_a_new_position(pool_run):
    async def go(pool):
        d = await dep(pool)
        await fill(pool, d, "X", 1, "sell", 75, 100.0, T0)
        await fill(pool, d, "X", 1, "buy", 75, 90.0, T0 + timedelta(minutes=1))
        await fill(pool, d, "X", 1, "sell", 75, 95.0, T0 + timedelta(minutes=2))
        rows = await queries.list_positions(pool, d)
        assert len(rows) == 2 and {r["status"] for r in rows} == {"open", "closed"}
    pool_run(go)


def test_tokens_are_independent_positions(pool_run):
    async def go(pool):
        d = await dep(pool)
        await fill(pool, d, "CE", 1, "sell", 75, 100.0, T0)
        await fill(pool, d, "PE", 2, "sell", 75, 90.0, T0)
        assert len(await queries.list_open_positions(pool, d)) == 2
    pool_run(go)


def test_book_price_records_slippage_on_the_lot_and_position(pool_run):
    async def go(pool):
        d = await dep(pool)
        await queries.record_fill(pool, d, "X", 1, "sell", 75, 100.0, T0, book_price=99.5)
        [p] = await queries.list_open_positions(pool, d)
        assert float(p["estimated_slippage"]) == pytest.approx(37.5)    # sold 0.5 below LTP x 75
        await queries.record_fill(pool, d, "X", 1, "buy", 75, 80.0, T0, book_price=80.4)
        [c] = await queries.list_positions(pool, d, status="closed")
        assert float(c["estimated_slippage"]) == pytest.approx(37.5 + 30.0)
        assert sorted(float(l["book_price"]) for l in await lots(pool, d)) == [80.4, 99.5]
    pool_run(go)


def test_force_close_closes_at_the_given_price_either_side(pool_run):
    async def go(pool):
        d = await dep(pool)
        await fill(pool, d, "S", 1, "sell", 75, 100.0, T0)
        await fill(pool, d, "L", 2, "buy", 75, 10.0, T0)
        for p in await queries.list_open_positions(pool, d):
            await queries.force_close_position(pool, d, p, 50.0 if p["side"] == "short" else 15.0, T0, reason="flatten_all")
        closed = {r["symbol"]: float(r["realized_pnl"]) for r in await queries.list_positions(pool, d, status="closed")}
        assert closed == {"S": pytest.approx(3750.0), "L": pytest.approx(375.0)}
    pool_run(go)


def test_lots_keep_reason_and_metadata(pool_run):
    async def go(pool):
        d = await dep(pool)
        await fill(pool, d, "X", 1, "sell", 75, 100.0, T0, reason="entry", metadata={"leg_role": "original", "strike": 25000})
        [lot] = await lots(pool, d)
        assert lot["reason"] == "entry" and lot["metadata"]["leg_role"] == "original"
    pool_run(go)


def test_only_one_open_position_per_instrument_is_allowed_by_the_schema(pool_run):
    import asyncpg

    async def go(pool):
        d = await dep(pool)
        await fill(pool, d, "X", 1, "sell", 75, 100.0, T0)
        with pytest.raises(asyncpg.UniqueViolationError):
            await pool.execute(
                "INSERT INTO positions (deployment_id, symbol, instrument_token, side, status, qty, avg_entry_price, opened_at) "
                "VALUES ($1, 'X', 1, 'short', 'open', 75, 100, now())", d)
    pool_run(go)


def test_naive_tick_times_are_stored_as_ist(pool_run):
    async def go(pool):
        d = await dep(pool)
        naive = datetime(2026, 10, 14, 10, 0, 9)          # what a Kite tick carries
        from app.deployments.runner import DeploymentRunner
        row = await queries.get_deployment(pool, d)
        runner = DeploymentRunner(row, pool, None, type("D", (), {"last_prices": {}, "last_depths": {}})(), None)
        runner.dispatcher.access_token = None
        await runner._reload_positions()
        await runner.sell("X", 1, 75, 100.0, naive, reason="entry")
        [lot] = await lots(pool, d)
        assert lot["executed_at"] == datetime(2026, 10, 14, 4, 30, 9, tzinfo=timezone.utc)
    pool_run(go)

"""intraday_dtt_adjusted and intraday_dtt_advanced on a simulated market:
adjustment on the halved side, reversal unwind, cap, break-even stop,
profit target, square-off, restart with adjustments open."""

from tests.sim import Sim, find_spot, leg_prices, side_sum
from tests.strategies.helpers import assert_book_consistent, cfg

A = "intraday_dtt_adjusted"
V = "intraday_dtt_advanced"


async def start(pool, monkeypatch, strategy=A, **over):
    sim = await Sim.create(pool, monkeypatch, strategy, cfg(strategy, **over))
    await sim.tick("10:00", spot=25000)
    assert len(await sim.open_positions()) == 2
    return sim


def halved_pe(p):     # PE side at or below half the CE: the adjustment trigger
    return side_sum(p, "PE") <= 0.45 * side_sum(p, "CE")


async def legs_by_side(sim):
    legs = await sim.open_positions()
    return {s: [p for p in legs if p["symbol"].endswith(s)] for s in ("CE", "PE")}


def test_quiet_day_squares_off(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch)
        await sim.walk("10:10", "14:50", every_min=20, spot_fn=lambda t: 25000)
        assert len(await sim.open_positions()) == 2
        await sim.tick("15:00")
        assert await sim.open_positions() == []
        await assert_book_consistent(pool, sim.dep_id)
    pool_run(go)


def test_rally_adds_a_put_on_the_halved_side_then_reversal_unwinds_it(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch)
        up = await find_spot(sim, halved_pe, step=10)
        await sim.tick("10:30", spot=up)
        sides = await legs_by_side(sim)
        assert len(sides["CE"]) == 1 and len(sides["PE"]) == 2, "one extra PE sold on the halved side"
        new = max(sides["PE"], key=lambda p: p["opened_at"])
        assert (new["metadata"] or {}).get("leg_role") == "adjustment_1"
        # sized at ~25% of the CE premium
        ce_px = (await leg_prices(sim))[sides["CE"][0]["symbol"]]
        assert abs(float(new["avg_entry_price"]) - 0.25 * ce_px) / (0.25 * ce_px) < 0.5
        # market comes back: PE side total >= CE again -> cheapest PE unwound
        await sim.tick("11:00", spot=25000)
        sides = await legs_by_side(sim)
        assert len(sides["PE"]) == 1
        assert "reversal_unwind" in {f["reason"] for f in await sim.fills() if f["action"] == "buy"}
        await assert_book_consistent(pool, sim.dep_id)
    pool_run(go)


def test_adjustments_stop_at_max_adjustments(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch, max_adjustments=2)
        s = 25000
        for i, t in enumerate(["10:20", "10:40", "11:00", "11:20", "11:40"]):
            s = await find_spot(sim, halved_pe, step=10)
            if s - 25000 > 380:   # stay inside the break-even band
                break
            await sim.tick(t, spot=s)
        roles = [(p["metadata"] or {}).get("leg_role") for p in await sim.all_positions()]
        assert sum(1 for r in roles if (r or "").startswith("adjustment_")) <= 2
        await assert_book_consistent(pool, sim.dep_id)
    pool_run(go)


def test_breakeven_stop_flattens_everything(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch)
        await sim.tick("10:30", spot=25000 + 900)   # far beyond entry +/- combined premium
        assert await sim.open_positions() == []
        assert {f["reason"] for f in await sim.fills() if f["action"] == "buy"} <= {"breakeven_fallback", "breakeven_fallback_sweep"}
        await sim.walk("10:40", "14:40", every_min=60, spot_fn=lambda t: 25000)
        assert await sim.open_positions() == []      # no re-entry after a stop
        await assert_book_consistent(pool, sim.dep_id)
    pool_run(go)


def test_total_profit_target_flattens(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch)
        sim.market.vol = 0.12
        await sim.tick("11:00", spot=25000)
        assert await sim.open_positions() == []
        assert "profit_target_total" in {f["reason"] for f in await sim.fills() if f["action"] == "buy"}
        assert sum(float(p["realized_pnl"]) for p in await sim.all_positions()) > 0
    pool_run(go)


def test_restart_with_an_adjustment_open_resumes_all_legs_and_exits_them(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch)
        up = await find_spot(sim, halved_pe, step=10)
        await sim.tick("10:30", spot=up)
        assert len(await sim.open_positions()) == 3
        await sim.restart()
        assert sim.strategy.adjusted_side == "PE" and sim.strategy.adjustments_used == 1
        await sim.tick("15:00", spot=up)
        assert await sim.open_positions() == []
        await assert_book_consistent(pool, sim.dep_id)
    pool_run(go)


def test_advanced_rolls_at_the_cap_instead_of_adding(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch, strategy=V, max_adjustments=1)
        s = 25000
        for t in ["10:20", "10:40", "11:00"]:
            s = await find_spot(sim, halved_pe, step=10)
            if s - 25000 > 380:
                break
            await sim.tick(t, spot=s)
            sides = await legs_by_side(sim)
            assert len(sides["PE"]) <= 2, "never more than the cap (1 adjustment) open on the side"
        reasons = {f["reason"] for f in await sim.fills()}
        assert reasons & {"adjustment", "roll_open", "roll_close"}
        await sim.tick("15:00", spot=s)
        assert await sim.open_positions() == []
        await assert_book_consistent(pool, sim.dep_id)
    pool_run(go)

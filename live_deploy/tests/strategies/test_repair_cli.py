"""The interactive repair CLI (app/repair/cli.py), driven with scripted
answers: list -> review -> choose a path -> plan -> apply, undo, change a
choice, refuse without a pause."""

from datetime import time

from app.repair import engine
from app.repair.cli import RepairCLI, inr, spark
from app.repair.market import MinuteBook
from tests.repair_market import candles_source
from tests.sim import Sim
from tests.strategies.helpers import assert_book_consistent, cfg


class FakeApi:
    def __init__(self, up=True, fail_pause=False):
        self.up, self.fail_pause, self.calls = up, fail_pause, []
        self.base = "http://fake"

    def reachable(self):
        return self.up

    def pause(self, dep_id):
        if self.fail_pause:
            raise RuntimeError("409 already paused")
        self.calls.append(("pause", dep_id))

    def resume(self, dep_id):
        self.calls.append(("resume", dep_id))


def script(*answers):
    it = iter(answers)

    def inp(prompt):
        try:
            return next(it)
        except StopIteration:
            raise EOFError
    return inp


async def setup(pool, monkeypatch):
    sim = await Sim.create(pool, monkeypatch, "intraday_dtt_simple", cfg("intraday_dtt_simple"))
    await sim.walk("09:15", "12:15", every_min=5)
    path = lambda m: (25000, 25450, 24550, 25000) if f"{m:%H:%M}" == "13:05" else (25000, 25004, 24996, 25000)
    book = MinuteBook(sim.market.today, candles_source(sim, path))
    return sim, book, engine.Window(sim.market.today, time(12, 16))


def cli_for(pool, sim, book, w, api, *answers):
    lines = []
    cli = RepairCLI(pool, book, sim.kite, w, api, inp=script(*answers), out=lines.append)
    return cli, lines


def test_review_choose_a_path_apply_and_it_shows_repaired(pool_run, monkeypatch):
    async def go(pool):
        sim, book, w = await setup(pool, monkeypatch)
        api = FakeApi()
        cli, lines = cli_for(pool, sim, book, w, api, "1", "2", "a", "y", "q")
        await cli.run()
        text = "\n".join(lines)
        assert "STALL REPAIR" in text and "Scanning 1 deployment(s)" in text
        assert "13:05 — the 1-minute candle can't tell" in text
        assert "[1] NIFTY 50 went DOWN first (to 24,550.00, then 25,450.00)" in text
        assert "[2] NIFTY 50 went UP first" in text
        assert "Paths you chose: 13:05 up first" in text
        assert "leg_spike_stop" in text and "Window P&L" in text and "As left by the stall" in text
        assert "Applied: 2 trade(s)" in text
        assert api.calls == [("pause", sim.dep_id), ("resume", sim.dep_id)]
        rep = await engine.live_repair(pool, sim.dep_id, w.day)
        assert rep["choices"] == {"13:05": "up_first"}
        lots = await pool.fetch("SELECT metadata FROM position_lots WHERE deployment_id = $1 AND metadata ? 'repair'", sim.dep_id)
        assert {l["metadata"]["repair"]["phase"] for l in lots} == {"up"}
        assert any("repaired at" in l and "2 trades" in l for l in lines)
        await assert_book_consistent(pool, sim.dep_id)
    pool_run(go)


def test_undo_from_the_list(pool_run, monkeypatch):
    async def go(pool):
        sim, book, w = await setup(pool, monkeypatch)
        cli, _ = cli_for(pool, sim, book, w, FakeApi(), "1", "1", "a", "y", "q")
        await cli.run()
        assert await sim.open_positions() == []
        cli, lines = cli_for(pool, sim, book, w, FakeApi(), "1", "u", "y", "q")
        await cli.run()
        assert any("Undone: 2 fill(s) removed" in l for l in lines)
        assert len(await sim.open_positions()) == 2
        assert await engine.live_repair(pool, sim.dep_id, w.day) is None
        await assert_book_consistent(pool, sim.dep_id)
    pool_run(go)


def test_changing_a_choice_replays_the_other_path(pool_run, monkeypatch):
    async def go(pool):
        sim, book, w = await setup(pool, monkeypatch)
        cli, lines = cli_for(pool, sim, book, w, FakeApi(), "1", "1", "c", "1", "b", "q")
        await cli.run()
        plans = [i for i, l in enumerate(lines) if l.startswith("Paths you chose")]
        assert [lines[i] for i in plans] == ["Paths you chose: 13:05 down first", "Paths you chose: 13:05 up first"]
        fills = [l for l in lines if "leg_spike_stop" in l]
        assert any("↓ low" in l for l in fills) and any("↑ high" in l for l in fills)
        assert await engine.live_repair(pool, sim.dep_id, w.day) is None    # nothing applied
    pool_run(go)


def test_apply_is_refused_when_the_running_app_cant_pause_it(pool_run, monkeypatch):
    async def go(pool):
        sim, book, w = await setup(pool, monkeypatch)
        cli, lines = cli_for(pool, sim, book, w, FakeApi(fail_pause=True), "1", "1", "a", "y", "b", "q")
        await cli.run()
        assert any("Couldn't pause it through the app" in l for l in lines)
        assert await engine.live_repair(pool, sim.dep_id, w.day) is None
        # an unreachable app needs an explicit "it's stopped"
        cli, lines = cli_for(pool, sim, book, w, FakeApi(up=False), "1", "1", "a", "y", "n", "b", "q")
        await cli.run()
        assert any("isn't reachable" in l for l in lines)
        assert await engine.live_repair(pool, sim.dep_id, w.day) is None
    pool_run(go)


def test_the_list_says_nothing_to_repair_for_a_flat_idle_deployment(pool_run, monkeypatch):
    async def go(pool):
        sim = await Sim.create(pool, monkeypatch, "intraday_dtt_simple", cfg("intraday_dtt_simple", catch_up_late_entry=False))
        book = MinuteBook(sim.market.today, candles_source(sim, lambda m: (25000, 25004, 24996, 25000)))
        w = engine.Window(sim.market.today, time(12, 16))
        cli, lines = cli_for(pool, sim, book, w, FakeApi(), "q")
        await cli.run()
        assert any("nothing to repair" in l for l in lines)
    pool_run(go)


def test_formatting_helpers():
    assert inr(1234567.4) == "+12,34,567" and inr(-950) == "−950" and inr(0) == "0" and inr(None) == "—"
    assert inr(24550, sign=False, dp=2) == "24,550.00"
    assert spark([1, 2, 3]) == "▁▄█" and spark([5, 5]) == "▄▄" and spark([]) == ""
    assert len(spark(list(range(500)), width=40)) == 40


def test_a_plan_blind_to_its_legs_is_flagged_in_the_list_and_cant_be_applied(pool_run, monkeypatch):
    async def go(pool):
        sim = await Sim.create(pool, monkeypatch, "intraday_dtt_simple", cfg("intraday_dtt_simple"))
        await sim.walk("09:15", "12:15", every_min=5)
        legs = {int(p["instrument_token"]) for p in await sim.open_positions()}
        good = candles_source(sim, lambda m: (25000, 25004, 24996, 25000))
        book = MinuteBook(sim.market.today, lambda t, a, b: [] if t in legs else good(t, a, b))
        w = engine.Window(sim.market.today, time(12, 16))
        cli, lines = cli_for(pool, sim, book, w, FakeApi(), "1", "a", "b", "q")
        await cli.run()
        text = "\n".join(lines)
        assert "no prices for 2 leg(s): can't be repaired" in text
        assert "CAN'T BE TRUSTED: no prices at all for" in text
        assert "Not applicable: the replay had no prices for" in text
        assert "Applied" not in text and await engine.live_repair(pool, sim.dep_id, w.day) is None
    pool_run(go)

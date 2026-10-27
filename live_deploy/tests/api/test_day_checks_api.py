"""The end-of-day check inside the app: the states kept at the open and
after the close, the check started once the export succeeds (in its own
process: app/repair/launcher.py), what it found announced once, a rule
broken alerting while its legs are still held, a failed check alerting,
and /v2/day-checks."""

import asyncio
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.db import queries
from tests.conftest import add_fill, make_deployment, sql

IST = ZoneInfo("Asia/Kolkata")
WED = date(2026, 10, 14)


def at(hhmm, day=WED):
    h, m = map(int, hhmm.split(":"))
    return datetime.combine(day, time(h, m), IST)


def check(client, when):
    return client.portal.call(client.app.state.ops.check_once, when)


def titles(pushes):
    return [t for t, _ in pushes]


class FakeLauncher:
    def __init__(self, pool):
        self.pool, self.started = pool, []

    async def start(self, day, trigger="auto", deployment=None):
        self.started.append((day, trigger, deployment))
        return await self.pool.fetchval("INSERT INTO day_check_runs (day, trigger, deployment_id) VALUES ($1, $2, $3::uuid) RETURNING id",
                                        day, trigger, deployment)


@pytest.fixture
def launcher(client, monkeypatch):
    f = FakeLauncher(client.app.state.db_pool)
    monkeypatch.setattr(client.app.state.ops, "launcher", f)
    monkeypatch.setattr(client.app.state, "daychecks", f)
    monkeypatch.setattr(client.app.state.deployment_manager, "push_config", {"vapid": "test"})
    return f


def seed_check(client, dep_id, status, day=WED, rules=None, run_status="done", headline="h"):
    run = sql(client, "INSERT INTO day_check_runs (day, status, finished_at) VALUES ($1, $2, now()) RETURNING id",
              day, run_status)[0]["id"]
    sql(client, "INSERT INTO day_checks (run_id, day, deployment_id, status, headline, rules) VALUES ($1, $2, $3::uuid, $4, $5, $6)",
        run, day, dep_id, status, headline, rules or [])
    return run


# ── Keeping the day ───────────────────────────────────────────────────

def test_states_are_kept_at_the_open_and_after_the_close(client, launcher):
    a = make_deployment(client, name="A")
    b = make_deployment(client, name="B")
    sql(client, "INSERT INTO deployment_state (deployment_id, state) VALUES ($1::uuid, $2)", a["id"], {"entered_today": False})
    sql(client, "UPDATE deployments SET status = 'stopped' WHERE id = $1::uuid", b["id"])
    check(client, at("08:55"))
    assert sql(client, "SELECT count(*) AS n FROM deployment_state_days")[0]["n"] == 0
    check(client, at("09:05"))
    rows = sql(client, "SELECT deployment_id, at, state FROM deployment_state_days")
    assert [(str(r["deployment_id"]), r["at"], r["state"]) for r in rows] == [(a["id"], "open", {"entered_today": False})]
    sql(client, "UPDATE deployment_state SET state = $2 WHERE deployment_id = $1::uuid", a["id"], {"entered_today": True})
    check(client, at("09:10"))
    assert sql(client, "SELECT state FROM deployment_state_days WHERE at = 'open'")[0]["state"] == {"entered_today": False}, \
        "the open is kept as first captured"
    check(client, at("12:00"))
    check(client, at("15:40"))
    assert {r["at"] for r in sql(client, "SELECT at FROM deployment_state_days")} == {"open", "close"}
    check(client, at("10:00", WED + timedelta(days=3)))   # Saturday
    assert sql(client, "SELECT count(*) AS n FROM deployment_state_days")[0]["n"] == 2


def test_the_check_starts_once_the_export_has_succeeded(client, launcher):
    pool = client.app.state.db_pool
    check(client, at("15:50"))
    assert launcher.started == [], "no export yet"
    client.portal.call(lambda: queries.set_app_scratch(pool, "candle_backfill_last", {
        "day": WED.isoformat(), "instruments": 3, "fetched": 3, "rows": 900, "seconds": 2.0, "error": None, "complete": True}))
    check(client, at("15:51"))
    check(client, at("15:52"))
    assert launcher.started == [(WED, "auto", None)]


# ── What it found ─────────────────────────────────────────────────────

def test_a_finished_check_says_what_it_found_once(client, launcher, pushes):
    a = make_deployment(client, name="Alpha")
    b = make_deployment(client, name="Beta")
    run = sql(client, "INSERT INTO day_check_runs (day, status, finished_at) VALUES ($1, 'done', now()) RETURNING id", WED)[0]["id"]
    for d, st in ((a, "ok"), (b, "mismatch")):
        sql(client, "INSERT INTO day_checks (run_id, day, deployment_id, status, headline) VALUES ($1, $2, $3::uuid, $4, 'h')",
            run, WED, d["id"], st)
    check(client, at("18:00"))
    check(client, at("18:01"))
    said = [p for p in pushes if p[0].startswith("Day check")]
    assert said == [("Day check 14 Oct: 1 as expected, 1 to review", "Beta: traded differently from its strategy")]


def test_a_rule_broken_alerts_while_its_legs_are_held(client, launcher, pushes):
    d = make_deployment(client, name="DTT")
    add_fill(client, d["id"], "NIFTY26O2125000CE", 11, "sell", 75, 100, at("10:00"))
    rule = {"rule": "flat_after_cutoff", "severity": "critical", "title": "Still holding 1 leg(s) after its 15:00 cut-off",
            "detail": "An intraday deployment...", "legs": ["NIFTY26O2125000CE"]}
    seed_check(client, d["id"], "rule", rules=[rule])
    check(client, at("18:00"))
    [x] = [x for x in client.get("/v2/ops").json()["open"] if x["key"].startswith("eod:")]
    assert x["title"] == "DTT: still holding 1 leg(s) after its 15:00 cut-off" and x["severity"] == "critical"
    assert "Still open: NIFTY26O2125000CE" in x["detail"] and x["deployment_id"] == d["id"]
    sql(client, "UPDATE positions SET status = 'closed', closed_at = now() WHERE deployment_id = $1::uuid", d["id"])
    check(client, at("18:01"))
    assert not [x for x in client.get("/v2/ops").json()["open"] if x["key"].startswith("eod:")]
    assert "Fixed: DTT: still holding 1 leg(s) after its 15:00 cut-off" in titles(pushes)


def test_a_failed_check_is_an_issue_until_a_run_of_that_day_succeeds(client, launcher):
    sql(client, "INSERT INTO day_check_runs (day, status, error, finished_at) VALUES ($1, 'failed', 'No Kite session', now())", WED)
    check(client, at("18:00"))
    x = next(x for x in client.get("/v2/ops").json()["open"] if x["key"] == f"daycheck:{WED}")
    assert "No Kite session" in x["detail"]
    sql(client, "INSERT INTO day_check_runs (day, status, finished_at) VALUES ($1, 'done', now())", WED)
    check(client, at("18:01"))
    assert not [x for x in client.get("/v2/ops").json()["open"] if x["key"].startswith("daycheck:")]


# ── /v2/day-checks ────────────────────────────────────────────────────

def test_day_checks_api_lists_a_day_worst_first(client, launcher):
    assert client.get("/v2/day-checks").json() == {"day": None, "days": [], "run": None, "results": [], "counts": {}, "rechecks": {}}
    names = {}
    for name, st in (("Calm", "ok"), ("Broken", "rule"), ("Drift", "mismatch"), ("Maybe", "ambiguous")):
        names[st] = make_deployment(client, name=name)
    run = sql(client, "INSERT INTO day_check_runs (day, status, finished_at) VALUES ($1, 'done', now()) RETURNING id", WED)[0]["id"]
    for st, d in names.items():
        sql(client, "INSERT INTO day_checks (run_id, day, deployment_id, status, headline, diff) VALUES ($1, $2, $3::uuid, $4, $5, $6)",
            run, WED, d["id"], st, f"{st} headline", {"missed": [], "extra": [], "end": {"same": st == "ok"}})
    sql(client, "INSERT INTO day_check_runs (day, status) VALUES ($1, 'done')", WED - timedelta(days=1))
    r = client.get("/v2/day-checks").json()
    assert r["day"] == WED.isoformat() and r["days"] == [WED.isoformat(), (WED - timedelta(days=1)).isoformat()]
    assert [x["status"] for x in r["results"]] == ["rule", "mismatch", "ambiguous", "ok"]
    assert r["counts"] == {"rule": 1, "mismatch": 1, "ambiguous": 1, "ok": 1} and r["run"]["status"] == "done"
    assert r["results"][0]["name"] == "Broken" and r["results"][0]["diff"]["end"]["same"] is False
    assert client.get(f"/v2/day-checks?day={WED - timedelta(days=1)}").json()["results"] == []
    assert client.get("/v2/day-checks?day=yesterday").status_code == 422


def test_running_the_check_by_hand(client, launcher):
    r = client.post("/v2/day-checks/run", json={"day": WED.isoformat()})
    assert r.status_code == 202 and r.json()["status"] == "running" and r.json()["trigger"] == "manual"
    d = make_deployment(client, name="One")
    client.post("/v2/day-checks/run", json={"day": WED.isoformat(), "deployment_id": d["id"]})
    assert launcher.started == [(WED, "manual", None), (WED, "manual", d["id"])]
    future = (datetime.now(IST).date() + timedelta(days=2)).isoformat()
    assert client.post("/v2/day-checks/run", json={"day": future}).status_code == 422


# ── The launcher ──────────────────────────────────────────────────────

def test_the_launcher_runs_the_check_as_its_own_process_and_notices_it_dying(client, tmp_path):
    from app.repair.launcher import DayCheckLauncher
    pool = client.app.state.db_pool
    dead = tmp_path / "dead.py"
    dead.write_text("import sys\nprint('loading…')\nprint('boom: no such module')\nsys.exit(3)\n")
    ok = tmp_path / "ok.py"
    ok.write_text("import sys\nprint(' '.join(sys.argv[1:]))\n")
    dep = make_deployment(client, name="One")["id"]

    async def go():
        L = DayCheckLauncher(pool, script=dead)
        rid = await L.start(WED)
        assert await L.start(WED) == rid, "one run of a day at a time"
        await L.tasks[rid]
        failed = await pool.fetchrow("SELECT * FROM day_check_runs WHERE id = $1", rid)
        L2 = DayCheckLauncher(pool, script=ok)
        rid2 = await L2.start(WED + timedelta(days=1), "manual", dep)
        await L2.tasks[rid2]
        still = await pool.fetchrow("SELECT * FROM day_check_runs WHERE id = $1", rid2)
        return failed, still
    failed, still = client.portal.call(go)
    assert failed["status"] == "failed" and "exit 3" in failed["error"] and "boom: no such module" in failed["error"]
    assert still["status"] == "running", "a worker that exits cleanly reports for itself (this fake doesn't)"


# ── Checking one deployment again ─────────────────────────────────────
# After a fix: its day checked again on its own, without replaying every
# deployment. It shows on its row; it isn't the day's run.

def test_checking_one_deployment_again_shows_on_its_row_not_as_the_days_run(client, launcher):
    a, b = make_deployment(client, name="A"), make_deployment(client, name="B")
    day_run = seed_check(client, a["id"], "mismatch")
    sql(client, "INSERT INTO day_checks (run_id, day, deployment_id, status, headline) VALUES ($1, $2, $3::uuid, 'ok', 'h')",
        day_run, WED, b["id"])
    r = client.post("/v2/day-checks/run", json={"day": WED.isoformat(), "deployment_id": a["id"]})
    assert r.status_code == 202 and r.json()["deployment_id"] == a["id"] and r.json()["status"] == "running"
    got = client.get(f"/v2/day-checks?day={WED}").json()
    assert got["run"]["id"] == day_run and got["run"]["deployment_id"] is None, "the day's run stays the day's"
    assert list(got["rechecks"]) == [a["id"]] and got["rechecks"][a["id"]]["status"] == "running"
    # it finishes: the row has the new result, nothing left running
    sql(client, "UPDATE day_check_runs SET status = 'done', finished_at = now() WHERE id = $1", r.json()["id"])
    sql(client, "UPDATE day_checks SET status = 'ok', checked_at = now(), run_id = $1 WHERE deployment_id = $2::uuid",
        r.json()["id"], a["id"])
    got = client.get(f"/v2/day-checks?day={WED}").json()
    assert got["rechecks"] == {} and {x["name"]: x["status"] for x in got["results"]} == {"A": "ok", "B": "ok"}
    # one that fails says so on its row until a later check of it succeeds
    r2 = client.post("/v2/day-checks/run", json={"day": WED.isoformat(), "deployment_id": b["id"]}).json()
    sql(client, "UPDATE day_check_runs SET status = 'failed', error = 'No Kite session', finished_at = now() WHERE id = $1", r2["id"])
    got = client.get(f"/v2/day-checks?day={WED}").json()
    assert got["rechecks"][b["id"]]["status"] == "failed" and got["rechecks"][b["id"]]["error"] == "No Kite session"


def test_one_deployments_check_is_not_the_days_for_the_health_monitor(client, launcher, pushes):
    a = make_deployment(client, name="A")
    pool = client.app.state.db_pool
    sql(client, "INSERT INTO day_check_runs (day, status, finished_at, deployment_id) VALUES ($1, 'done', now(), $2::uuid)", WED, a["id"])
    sql(client, "INSERT INTO day_check_runs (day, status, error, finished_at, deployment_id) VALUES ($1, 'failed', 'x', now(), $2::uuid)",
        WED, a["id"])
    client.portal.call(lambda: queries.set_app_scratch(pool, "candle_backfill_last", {
        "day": WED.isoformat(), "instruments": 3, "fetched": 3, "rows": 900, "seconds": 2.0, "error": None, "complete": True}))
    check(client, at("15:51"))
    assert launcher.started == [(WED, "auto", None)], "the day's own check still starts after the export"
    assert not [p for p in pushes if p[0].startswith("Day check")], "no phone alert for checking one again"
    assert not [x for x in client.get("/v2/ops").json()["open"] if x["key"].startswith("daycheck:")]


def test_the_launcher_reuses_only_a_run_that_covers_the_deployment(client, tmp_path):
    from app.repair.launcher import DayCheckLauncher
    pool = client.app.state.db_pool
    slow = tmp_path / "slow.py"
    slow.write_text("import time\ntime.sleep(0.5)\n")
    a, b, c = (make_deployment(client, name=n)["id"] for n in "ABC")

    async def go():
        L = DayCheckLauncher(pool, script=slow)
        ra = await L.start(WED, "manual", a)
        assert await L.start(WED, "manual", a) == ra, "A is already being checked"
        rb = await L.start(WED, "manual", b)
        assert rb != ra, "B isn't: its own run"
        day = await L.start(WED, "manual")
        assert day not in (ra, rb), "one deployment's run doesn't cover the whole day"
        assert await L.start(WED, "manual", c) == day, "the whole day's covers C"
        rows = {r["id"]: r for r in await pool.fetch("SELECT id, deployment_id FROM day_check_runs")}
        await asyncio.gather(*L.tasks.values())
        return rows[ra]["deployment_id"], rows[day]["deployment_id"]
    dep_a, dep_day = client.portal.call(go)
    assert str(dep_a) == a and dep_day is None

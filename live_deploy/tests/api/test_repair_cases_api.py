"""Repairing a flagged day from the Repair page: the case, the paths
chosen kept as a draft and planned by the repair worker, pushed only as
planned with the deployment paused meanwhile, undone — the API around
app/repair/dayrepair.py (the worker itself: tests/strategies/
test_repair_worker.py)."""

import asyncio
from datetime import date

import pytest

from tests.conftest import make_deployment, sql

DAY = date(2026, 10, 14)
D = DAY.isoformat()


class FakeWorker:
    def __init__(self):
        self.ensured = 0

    async def ensure(self):
        self.ensured += 1


@pytest.fixture
def worker(client, monkeypatch):
    w = FakeWorker()
    monkeypatch.setattr(client.app.state, "repair_worker", w)
    return w


def checked(client, name="Stalled", status="rule", start="paused"):
    d = make_deployment(client, name=name, start=start)
    sql(client, "INSERT INTO day_checks (day, deployment_id, status, headline, diff) VALUES ($1, $2::uuid, $3, 'h', $4)",
        DAY, d["id"], status, {"missed": [{"minute": "15:00"}], "extra": [], "end": {"same": False}})
    return d


def finish(client, job_id, result=None, status="done", error=None):
    sql(client, "UPDATE repair_jobs SET status = $2, result = $3, error = $4, finished_at = now() WHERE id = $1",
        job_id, status, result, error)


GOOD = {"from": "15:00", "can_apply": True, "why_not": [], "fills_key": "k1", "complete": True, "fills": []}


def test_a_case_and_its_draft_planned(client, worker):
    assert client.get(f"/v2/repair-cases/{D}/00000000-0000-0000-0000-000000000001").status_code == 404
    d = checked(client)
    other = make_deployment(client, name="Unchecked")
    assert client.get(f"/v2/repair-cases/{D}/{other['id']}").status_code == 404
    c = client.get(f"/v2/repair-cases/{D}/{d['id']}").json()
    assert c["check"]["status"] == "rule" and c["draft"]["choices"] == {} and c["plan"] is None and c["applied"] is None
    r = client.post(f"/v2/repair-cases/{D}/{d['id']}/plan", json={"choices": {"13:05": "up_first"}})
    assert r.status_code == 202 and r.json()["status"] == "queued" and worker.ensured == 1
    c = client.get(f"/v2/repair-cases/{D}/{d['id']}").json()
    assert c["draft"]["choices"] == {"13:05": "up_first"} and c["plan"]["status"] == "queued" and not c["plan_is_draft"]
    finish(client, r.json()["id"], GOOD)
    c = client.get(f"/v2/repair-cases/{D}/{d['id']}").json()
    assert c["plan_is_draft"] and c["plan"]["result"]["from"] == "15:00"
    assert client.get(f"/v2/repair-jobs/{r.json()['id']}").json()["status"] == "done"
    for bad in ({"13:5": "up_first"}, {"13:05": "sideways"}):
        assert client.post(f"/v2/repair-cases/{D}/{d['id']}/plan", json={"choices": bad}).status_code == 422
    assert client.post(f"/v2/repair-cases/{D}/{other['id']}/plan", json={"choices": {}}).status_code == 404


def test_push_needs_a_plan_of_the_draft_that_can_be_applied(client, worker):
    d = checked(client)
    assert client.post(f"/v2/repair-cases/{D}/{d['id']}/push").status_code == 409
    j = client.post(f"/v2/repair-cases/{D}/{d['id']}/plan", json={"choices": {}}).json()
    finish(client, j["id"], {**GOOD, "can_apply": False, "why_not": ["It has traded 3 time(s) since that day."]})
    r = client.post(f"/v2/repair-cases/{D}/{d['id']}/push")
    assert r.status_code == 409 and "traded 3 time(s)" in r.json()["detail"]
    j = client.post(f"/v2/repair-cases/{D}/{d['id']}/plan", json={"choices": {"13:05": "down_first"}}).json()
    finish(client, j["id"], GOOD)
    client.post(f"/v2/repair-cases/{D}/{d['id']}/plan", json={"choices": {"13:05": "up_first"}})   # draft moved on
    assert client.post(f"/v2/repair-cases/{D}/{d['id']}/push").status_code == 409


def test_push_pauses_a_running_deployment_until_the_repair_is_done(client, worker):
    d = checked(client, start="now")
    j = client.post(f"/v2/repair-cases/{D}/{d['id']}/plan", json={"choices": {}}).json()
    finish(client, j["id"], GOOD)
    r = client.post(f"/v2/repair-cases/{D}/{d['id']}/push")
    assert r.status_code == 202 and r.json()["kind"] == "apply" and r.json()["paused"] is True
    job = sql(client, "SELECT * FROM repair_jobs WHERE id = $1", r.json()["id"])[0]
    assert job["expected"] == "k1" and job["status"] == "queued"
    status = lambda: sql(client, "SELECT status FROM deployments WHERE id = $1::uuid", d["id"])[0]["status"]
    assert status() == "paused"
    assert client.post(f"/v2/repair-cases/{D}/{d['id']}/push").status_code == 409, "already being applied"
    finish(client, job["id"], {"applied": {"id": "x"}})

    async def wait():
        for t in list(client.app.state.repair_resume_tasks):
            await asyncio.wait_for(t, 5)
    client.portal.call(wait)
    assert status() == "active", "resumed once the repair was done"


def test_undo_pauses_around_it_and_reports(client, worker, monkeypatch):
    d = checked(client, start="now")
    rid = sql(client, "INSERT INTO stall_repairs (deployment_id, day, window_from, window_to, undo) "
                      "VALUES ($1::uuid, $2, now(), now(), '{}') RETURNING id", d["id"], DAY)[0]["id"]
    from app.repair import engine
    seen = []

    async def fake_undo(pool, repair_id):
        seen.append(await pool.fetchval("SELECT status FROM deployments WHERE id = $1::uuid", d["id"]))
        return {"deployment_id": d["id"], "lots_removed": 2, "positions_removed": 0}
    monkeypatch.setattr(engine, "undo", fake_undo)
    r = client.post(f"/v2/repairs/{rid}/undo")
    assert r.status_code == 200 and r.json()["lots_removed"] == 2
    assert seen == ["paused"]
    assert sql(client, "SELECT status FROM deployments WHERE id = $1::uuid", d["id"])[0]["status"] == "active"

    async def refused(pool, repair_id):
        raise engine.PlanChanged("the deployment traded 1 time(s) after the repaired window")
    monkeypatch.setattr(engine, "undo", refused)
    r = client.post(f"/v2/repairs/{rid}/undo")
    assert r.status_code == 409 and "traded 1 time" in r.json()["detail"]
    assert sql(client, "SELECT status FROM deployments WHERE id = $1::uuid", d["id"])[0]["status"] == "active"
    assert client.post("/v2/repairs/00000000-0000-0000-0000-000000000009/undo").status_code == 404


def test_an_applied_repair_shows_on_its_case(client, worker):
    d = checked(client)
    sql(client, "INSERT INTO stall_repairs (deployment_id, day, window_from, window_to, choices, summary, undo) "
                "VALUES ($1::uuid, $2, now(), now(), $3, $4, '{}')", d["id"], DAY, {"13:05": "up_first"},
        {"fills": 2, "kind": "day", "fills_detail": [{"minute": "15:00"}], "replaced": [{"minute": "15:02"}]})
    a = client.get(f"/v2/repair-cases/{D}/{d['id']}").json()["applied"]
    assert a["choices"] == {"13:05": "up_first"} and a["summary"]["fills"] == 2 and "fills_detail" not in a["summary"]
    assert a["fills"] == [{"minute": "15:00"}] and a["replaced"] == [{"minute": "15:02"}]


def test_the_launcher_starts_the_worker_and_fails_its_running_job_if_it_dies(client, tmp_path):
    from app.repair.launcher import RepairWorkerLauncher
    pool = client.app.state.db_pool
    d = checked(client)
    dead = tmp_path / "dead.py"
    dead.write_text("import sys\nprint('took a job')\nsys.exit(2)\n")

    async def go():
        jid = await pool.fetchval(
            "INSERT INTO repair_jobs (deployment_id, day, kind, status) VALUES ($1::uuid, $2, 'plan', 'running') RETURNING id",
            d["id"], DAY)
        L = RepairWorkerLauncher(pool, script=dead)
        await L.ensure()
        first = L.task
        await L.ensure()
        assert L.task is first, "one worker at a time"
        await first
        return await pool.fetchrow("SELECT * FROM repair_jobs WHERE id = $1", jid)
    r = client.portal.call(go)
    assert r["status"] == "failed" and "exit 2" in r["error"] and "took a job" in r["error"]

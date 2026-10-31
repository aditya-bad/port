"""Videos through the real app (app/routers/videos.py, app/video/jobs.py):
asked for from a replay, queued, made one at a time (here with a stand-in
voice at two frames a second), served as an MP4 with its chapters, made
again after a restart, held back without memory to spare, and deleted
with its file."""

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.video import jobs as J
from tests.api.test_replay_api import no_kite
from tests.conftest import add_fill, default_config, make_deployment, sql

IST = ZoneInfo("Asia/Kolkata")
pytest.importorskip("playwright")


@pytest.fixture
def videos(client, monkeypatch, tmp_path):
    monkeypatch.setenv("LINCK_VIDEO_FAKE_VOICE", "1")
    monkeypatch.setenv("LINCK_VIDEO_FPS", "2")
    monkeypatch.setenv("LINCK_VIDEO_DIR", str(tmp_path))
    no_kite(monkeypatch, client)
    return client.app.state.videos


def straddle_day(client, name="vid dtt"):
    cfg = {**default_config("intraday_dtt_simple"), "instrument_tokens": [256265]}
    d = make_deployment(client, name=name, config=cfg)
    at = lambda h, m: datetime(2024, 5, 8, h, m, tzinfo=IST)  # noqa: E731
    meta = {"trigger": "entry", "context": {"spot": 22400.0, "vix": 14.1}}
    add_fill(client, d["id"], "NIFTY2450922400CE", 121, "sell", 75, 120, at(10, 0), "entry", meta)
    add_fill(client, d["id"], "NIFTY2450922400PE", 122, "sell", 75, 110, at(10, 0), "entry", meta)
    add_fill(client, d["id"], "NIFTY2450922400CE", 121, "buy", 75, 90, at(15, 0), "force_exit", {"trigger": "force_exit"})
    add_fill(client, d["id"], "NIFTY2450922400PE", 122, "buy", 75, 100, at(15, 0), "force_exit", {"trigger": "force_exit"})
    return d


def test_a_video_is_made_served_with_chapters_and_deleted(client, videos, tmp_path):
    info = client.get("/v2/videos/info").json()
    assert info["can_make"] is True and info["default_voice"] == "af_heart" and len(info["voices"]) == 4
    d = straddle_day(client)
    r = client.post(f"/v2/deployments/{d['id']}/videos", json={"voice": "bm_george"})
    assert r.status_code == 200, r.text
    v = r.json()
    assert (v["status"], v["title"], v["live"], v["voice"], v["ep_from"], v["ep_to"]) == ("queued", "Trade 1", False, "bm_george", 1, 1)
    again = client.post(f"/v2/deployments/{d['id']}/videos", json={"from": 1, "to": 1})
    assert again.status_code == 409 and "already being made" in again.json()["detail"]
    assert client.get(f"/v2/deployments/{d['id']}/videos").json()[0]["ahead"] == 0

    assert client.portal.call(lambda: videos.run_once(check_memory=False)) is True
    v = client.get(f"/v2/videos/{v['id']}").json()
    assert v["status"] == "done" and v["ready"] and v["progress"] == 1 and v["note"] is None, v
    assert v["seconds"] > 30 and v["bytes"] > 1000
    titles = [c["title"] for c in v["chapters"]]
    assert titles[:3] == ["Trade 1", "How it plays", "The market"] and titles[-1] == "That's it"
    assert [c["t"] for c in v["chapters"]] == sorted(c["t"] for c in v["chapters"])
    assert sorted(p.name for p in (tmp_path / v["id"]).iterdir()) == ["video.mp4"]     # the rest cleared away
    f = client.get(f"/v2/videos/{v['id']}/file")
    assert f.status_code == 200 and f.headers["content-type"] == "video/mp4" and f.content[4:8] == b"ftyp"
    assert "inline" in f.headers["content-disposition"]
    dl = client.get(f"/v2/videos/{v['id']}/file?download=1")
    assert dl.headers["content-disposition"].startswith("attachment") and "vid-dtt-Trade-1.mp4" in dl.headers["content-disposition"]
    part = client.get(f"/v2/videos/{v['id']}/file", headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and len(part.content) == 100           # a player can seek

    assert client.delete(f"/v2/videos/{v['id']}").json() == {"ok": True}
    assert not (tmp_path / v["id"]).exists()
    assert client.get(f"/v2/videos/{v['id']}").status_code == 404


def test_one_caught_half_made_starts_again_and_one_waits_for_memory(client, videos, monkeypatch):
    d = straddle_day(client, "vid wait")
    v = client.post(f"/v2/deployments/{d['id']}/videos", json={}).json()
    sql(client, "UPDATE videos SET status = 'drawing', progress = 0.5 WHERE id = $1", __import__("uuid").UUID(v["id"]))
    assert client.portal.call(videos.requeue) == 1
    v = client.get(f"/v2/videos/{v['id']}").json()
    assert (v["status"], v["progress"]) == ("queued", 0) and "restarted" in v["note"]
    monkeypatch.setattr(J, "memory_mb", lambda: (180, 300))
    monkeypatch.setattr(J, "WAIT_MEMORY_S", 0)
    assert client.portal.call(videos.run_once) is False
    v = client.get(f"/v2/videos/{v['id']}").json()
    assert v["status"] == "queued" and v["note"].startswith("Waiting for memory: 180 MB free, 300 MB of swap.")


def test_it_says_why_it_cant(client, videos, monkeypatch):
    d = straddle_day(client, "vid cant")
    monkeypatch.setattr(J, "can_make", lambda: (False, "The voice model isn't installed on this server."))
    r = client.post(f"/v2/deployments/{d['id']}/videos", json={})
    assert r.status_code == 503 and r.json()["detail"] == "The voice model isn't installed on this server."
    monkeypatch.undo()


def test_nothing_to_tell_before_it_trades(client, videos):
    d = make_deployment(client, name="vid none", config={**default_config("intraday_dtt_simple"), "instrument_tokens": [256265]})
    r = client.post(f"/v2/deployments/{d['id']}/videos", json={})
    assert r.status_code == 409 and "hasn't traded" in r.json()["detail"]
    assert client.get(f"/v2/deployments/{d['id']}/videos").json() == []

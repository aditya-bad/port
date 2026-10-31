"""Videos API: a replay told as a narrated, animated MP4 (app/video/).

POST   /v2/deployments/{id}/videos   {from, to, voice}: tells the story of
       those episodes (the replay page's choice; the latest by default) as
       it stands now, and queues it. A cycle still running is told up to now.
GET    /v2/deployments/{id}/videos   its videos, newest first
GET    /v2/videos/info               whether this server can make them, the voices, the queue
GET    /v2/videos/{vid}              one, with its progress
GET    /v2/videos/{vid}/file         the MP4 (?download=1 to save it)
DELETE /v2/videos/{vid}              stops it if it's being made, and deletes it
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from .. import clock
from ..db import queries
from ..video import jobs as J
from ..video.voice import DEFAULT_VOICE, VOICES

router = APIRouter(tags=["videos"])
COLS = ("id, deployment_id, title, ep_from, ep_to, live, as_of, voice, status, progress, note, seconds, bytes, chapters, "
        "created_at, started_at, finished_at")


class MakeVideo(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_: Optional[int] = Field(None, alias="from")
    to: Optional[int] = None
    voice: Optional[str] = None


def _out(r) -> dict:
    d = dict(r)
    for k in ("id", "deployment_id"):
        d[k] = str(d[k])
    if isinstance(d.get("chapters"), str):
        d["chapters"] = json.loads(d["chapters"])
    for k in ("as_of", "created_at", "started_at", "finished_at"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    d["ready"] = d["status"] == "done" and J.file_of(d["id"]).exists()
    if d["status"] == "done" and not d["ready"]:
        d["note"] = "The file is gone (the videos folder was cleared, or isn't kept across redeploys). Make it again."
    return d


async def _queue(pool) -> list[str]:
    return [str(r["id"]) for r in await pool.fetch("SELECT id FROM videos WHERE status NOT IN ('done', 'failed') ORDER BY created_at")]


@router.get("/v2/videos/info")
async def info(request: Request):
    ok, why = J.can_make()
    ram, swap = J.memory_mb()
    return {"can_make": ok, "why": why, "voices": [{"id": k, "label": v} for k, v in VOICES.items()],
            "default_voice": DEFAULT_VOICE, "queue": len(await _queue(request.app.state.db_pool)),
            "memory": {"ram_mb": ram, "swap_mb": swap, "needs_mb": J.MIN_FREE_MB}}


@router.post("/v2/deployments/{deployment_id}/videos")
async def make(deployment_id: UUID, body: MakeVideo, request: Request):
    pool = request.app.state.db_pool
    dep = await queries.get_deployment(pool, deployment_id)
    if dep is None:
        raise HTTPException(404, "No such deployment")
    ok, why = J.can_make()
    if not ok:
        raise HTTPException(503, why)
    from ..geek import module_for
    from ..geek import replay as R
    from ..v2.catalog import STRATEGIES as META
    from ..video import story as V
    mod = module_for(dep["strategy_name"])
    if mod is None:
        raise HTTPException(404, f"Replays aren't available for {dep['strategy_name']}")
    params = {k: str(v) for k, v in (("from", body.from_), ("to", body.to)) if v is not None}
    now = clock.now_ist()
    ctx = mod.Ctx(pool=pool, dep=dict(dep), runner=request.app.state.deployment_manager.get_runner(deployment_id), now=now,
                  candles=getattr(request.app.state, "candles", None), params=params)
    D = await R.replay(ctx)
    if not D.get("selected"):
        raise HTTPException(409, "Nothing to tell yet: this deployment hasn't traded.")
    rows = await pool.fetch(
        "SELECT decided_at, rule, outcome, summary FROM strategy_decisions WHERE deployment_id = $1 "
        "AND decided_at >= $2 AND decided_at <= $3 ORDER BY decided_at LIMIT 500",
        deployment_id, datetime.fromisoformat(D["start"]), datetime.fromisoformat(D["end"])) if D.get("frames") else []
    try:
        story = V.build(D, dep=dict(dep), strategy_label=(META.get(dep["strategy_name"]) or {}).get("label") or dep["strategy_name"],
                        decisions=[{"t": r["decided_at"], "rule": r["rule"], "outcome": r["outcome"], "summary": r["summary"]} for r in rows],
                        now=now)
    except V.NoStory as e:
        raise HTTPException(409, f"{e} {D.get('market_error') or ''}".strip())
    a, z = D["selected"]
    busy = await pool.fetchval("SELECT id FROM videos WHERE deployment_id = $1 AND ep_from = $2 AND ep_to = $3 "
                               "AND status NOT IN ('done', 'failed')", deployment_id, a, z)
    if busy:
        raise HTTPException(409, "A video of these episodes is already being made.")
    voice = body.voice if body.voice in VOICES else DEFAULT_VOICE
    r = await pool.fetchrow(
        f"INSERT INTO videos (deployment_id, title, ep_from, ep_to, live, as_of, voice, story) "
        f"VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb) RETURNING {COLS}",
        deployment_id, story["episode"], a, z, story["live"], now, voice, json.dumps(story, ensure_ascii=False))
    jobs = getattr(request.app.state, "videos", None)
    if jobs is not None:
        jobs.poke()
    return _out(r)


@router.get("/v2/deployments/{deployment_id}/videos")
async def list_for(deployment_id: UUID, request: Request):
    pool = request.app.state.db_pool
    rows = await pool.fetch(f"SELECT {COLS} FROM videos WHERE deployment_id = $1 ORDER BY created_at DESC", deployment_id)
    q = await _queue(pool)
    out = []
    for r in rows:
        d = _out(r)
        d["ahead"] = q.index(d["id"]) if d["id"] in q else None
        out.append(d)
    return out


async def _get(request: Request, vid: UUID):
    r = await request.app.state.db_pool.fetchrow(f"SELECT {COLS} FROM videos WHERE id = $1", vid)
    if r is None:
        raise HTTPException(404, "No such video")
    return r


@router.get("/v2/videos/{vid}")
async def one(vid: UUID, request: Request):
    d = _out(await _get(request, vid))
    q = await _queue(request.app.state.db_pool)
    d["ahead"] = q.index(d["id"]) if d["id"] in q else None
    return d


@router.get("/v2/videos/{vid}/file")
async def file(vid: UUID, request: Request, download: int = 0):
    r = await _get(request, vid)
    f = J.file_of(str(vid))
    if r["status"] != "done" or not f.exists():
        raise HTTPException(404, "This video isn't ready")
    dep = await queries.get_deployment(request.app.state.db_pool, r["deployment_id"])
    name = re.sub(r"[^A-Za-z0-9]+", "-", f"{dep['deployment_name'] if dep else 'video'} {r['title']}").strip("-")[:80] + ".mp4"
    return FileResponse(f, media_type="video/mp4", filename=name,
                        content_disposition_type="attachment" if download else "inline")


@router.delete("/v2/videos/{vid}")
async def delete(vid: UUID, request: Request):
    await _get(request, vid)
    jobs = getattr(request.app.state, "videos", None)
    if jobs is not None:
        await jobs.cancel(str(vid))
    await request.app.state.db_pool.execute("DELETE FROM videos WHERE id = $1", vid)
    shutil.rmtree(J.video_dir() / str(vid), ignore_errors=True)
    return {"ok": True}

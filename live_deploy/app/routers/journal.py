"""The trade journal API (/v2/journal): notes on a day, optionally on one
deployment and one of its trades, with tags. Each note comes back with
the P&L booked that day (by its deployment, or by every deployment for a
note about the whole day) so a note sits next to how the day went.
Never read by a strategy."""

from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from .. import clock
from ..db import queries

router = APIRouter(prefix="/v2/journal", tags=["journal"])

MAX_BODY, MAX_TAGS, MAX_TAG = 4000, 10, 30
# Offered when writing a note; any other tag works too.
SUGGESTED = ["event day", "expiry day", "gap open", "trend day", "range day", "high vix", "news",
             "manual intervention", "kite down", "data gap", "rule change", "lesson"]


def clean_tags(tags: list) -> list[str]:
    """Lower case, single spaces, no duplicates, in the order given."""
    out = []
    for t in tags or []:
        t = " ".join(str(t).strip().lower().split())
        if not t:
            continue
        if len(t) > MAX_TAG:
            raise ValueError(f"A tag can be at most {MAX_TAG} characters: {t[:40]!r}")
        if t not in out:
            out.append(t)
    if len(out) > MAX_TAGS:
        raise ValueError(f"At most {MAX_TAGS} tags on a note")
    return out


class EntryIn(BaseModel):
    day: Optional[date] = None
    body: str = Field(min_length=1, max_length=MAX_BODY)
    tags: list[str] = Field(default_factory=list)
    deployment_id: Optional[UUID] = None
    position_id: Optional[UUID] = None

    @field_validator("body")
    @classmethod
    def _body(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Write something in the note")
        return v

    @field_validator("tags")
    @classmethod
    def _tags(cls, v: list) -> list:
        return clean_tags(v)


class EntryPatch(BaseModel):
    day: Optional[date] = None
    body: Optional[str] = Field(default=None, min_length=1, max_length=MAX_BODY)
    tags: Optional[list[str]] = None

    @field_validator("body")
    @classmethod
    def _body(cls, v):
        if v is not None and not v.strip():
            raise ValueError("Write something in the note")
        return v.strip() if v is not None else v

    @field_validator("tags")
    @classmethod
    def _tags(cls, v):
        return clean_tags(v) if v is not None else v


def _out(r) -> dict:
    return {"id": str(r["id"]), "day": r["day"].isoformat(), "body": r["body"], "tags": list(r["tags"] or []),
            "deployment_id": str(r["deployment_id"]) if r["deployment_id"] else None,
            "deployment_name": r["deployment_name"], "strategy_name": r["strategy_name"],
            "position_id": str(r["position_id"]) if r["position_id"] else None, "position_symbol": r["position_symbol"],
            "author": r["author"], "created_at": r["created_at"].isoformat(), "updated_at": r["updated_at"].isoformat(),
            "day_pnl": round(float(r["day_pnl"]), 2), "day_closed": int(r["day_closed"])}


async def _check_links(pool, deployment_id: Optional[UUID], position_id: Optional[UUID]):
    if deployment_id is not None and await queries.get_deployment(pool, deployment_id) is None:
        raise HTTPException(404, "No such deployment")
    if position_id is not None:
        row = await pool.fetchrow("SELECT deployment_id FROM positions WHERE id = $1", position_id)
        if row is None:
            raise HTTPException(404, "No such trade")
        if deployment_id is None or row["deployment_id"] != deployment_id:
            raise HTTPException(400, "A note on a trade needs that trade's deployment")


@router.get("")
async def list_entries(request: Request, deployment_id: Optional[UUID] = None, day_from: Optional[date] = None,
                       day_to: Optional[date] = None, tag: Optional[str] = None, q: Optional[str] = None,
                       position_id: Optional[UUID] = None, general: bool = False, limit: int = 500):
    """Notes, newest day first. `general` adds the whole-day notes to a
    deployment's own."""
    pool = request.app.state.db_pool
    rows = await queries.list_journal(pool, deployment_id, day_from, day_to,
                                      " ".join(tag.strip().lower().split()) if tag else None,
                                      q.strip() if q and q.strip() else None, position_id, general, max(1, min(limit, 2000)))
    return {"entries": [_out(r) for r in rows]}


@router.get("/tags")
async def tags(request: Request, deployment_id: Optional[UUID] = None):
    """Every tag in use with its notes, days and those days' P&L, and the
    suggested ones."""
    rows = await queries.journal_tags(request.app.state.db_pool, deployment_id)
    used = [{"tag": r["tag"], "notes": int(r["notes"]), "days": int(r["days"]), "pnl": round(float(r["pnl"]), 2),
             "avg": round(float(r["pnl"]) / int(r["days"]), 2) if r["days"] else None} for r in rows]
    return {"tags": used, "suggested": [t for t in SUGGESTED if t not in {u["tag"] for u in used}]}


@router.post("", status_code=201)
async def create_entry(payload: EntryIn, request: Request):
    pool = request.app.state.db_pool
    await _check_links(pool, payload.deployment_id, payload.position_id)
    author = request.session.get("username") if "session" in request.scope else None   # none for X-API-Key calls
    day = payload.day or clock.now_ist().date()
    eid = await queries.create_journal_entry(pool, day, payload.body, payload.tags, payload.deployment_id,
                                             payload.position_id, author)
    return _out(await queries.get_journal_entry(pool, eid))


@router.patch("/{entry_id}")
async def update_entry(entry_id: UUID, payload: EntryPatch, request: Request):
    pool = request.app.state.db_pool
    if await queries.get_journal_entry(pool, entry_id) is None:
        raise HTTPException(404, "No such note")
    await queries.update_journal_entry(pool, entry_id, payload.model_dump(exclude_none=True))
    return _out(await queries.get_journal_entry(pool, entry_id))


@router.delete("/{entry_id}")
async def delete_entry(entry_id: UUID, request: Request):
    if not await queries.delete_journal_entry(request.app.state.db_pool, entry_id):
        raise HTTPException(404, "No such note")
    return {"deleted": str(entry_id)}

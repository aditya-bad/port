"""
live_deploy — the predefined tag catalog (Settings -> Tags): a small,
admin-managed list of label names a deployment can be tagged with (see
migration 0010's own comment for why this is a curated catalog rather
than freeform per-deployment text).

Deliberately does NOT include "Excluded from reports" — that one is
synthesized by the frontend straight from a deployment's own
include_in_reports field (0009), never stored here.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from ..db import queries
from ..deployments.schemas import TagCreate, TagOut

router = APIRouter(prefix="/tags", tags=["tags"])

# Reserved purely to stop a confusing DUPLICATE of the synthetic
# "Excluded from reports" chip from ever entering the real catalog --
# a second, independently-toggleable tag with the same name as the
# built-in one would be genuinely confusing on a deployment's own chip
# row (which one turned it on?). Compared case-insensitively since the
# confusion is about the NAME colliding, not the exact casing.
_RESERVED_NAME = "excluded from reports"


@router.get("", response_model=list[TagOut])
async def list_tags(request: Request):
    rows = await queries.list_tags(request.app.state.db_pool)
    return [dict(r) for r in rows]


@router.post("", response_model=TagOut, status_code=201)
async def create_tag(payload: TagCreate, request: Request):
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "Tag name cannot be blank")
    if name.lower() == _RESERVED_NAME:
        raise HTTPException(
            400,
            '"Excluded from reports" is a built-in tag, toggled per-deployment from '
            "Edit — it's not part of this catalog.",
        )
    pool = request.app.state.db_pool
    existing = await queries.get_tag_by_name(pool, name)
    if existing is not None:
        raise HTTPException(409, f"A tag named '{name}' already exists")
    row = await queries.create_tag(pool, name)
    return dict(row)


@router.delete("/{tag_id}", status_code=204)
async def delete_tag(tag_id: UUID, request: Request):
    """Also strips this tag's name out of every deployment currently
    carrying it (see queries.delete_tag's own docstring) -- deleting a
    tag here means it's genuinely gone, not silently orphaned on
    whichever deployments already had it applied."""
    deleted = await queries.delete_tag(request.app.state.db_pool, tag_id)
    if not deleted:
        raise HTTPException(404, "No such tag")

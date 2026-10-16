"""Per-user UI preferences, stored as one JSON object per user.

The server doesn't interpret the keys: the UI's preference registry
(static/linck/js/core/prefs.js) defines them and their defaults, so a
new preference is a UI-only change. Values are limited to small scalars
so the table can't be used as general storage.
"""

from __future__ import annotations

import json
from typing import Any, Optional
from uuid import UUID

import asyncpg

MAX_KEYS = 64


def clean(prefs: dict) -> dict[str, Any]:
    """Keep short string keys with scalar values only."""
    out: dict[str, Any] = {}
    for k, v in prefs.items():
        if len(out) >= MAX_KEYS:
            break
        if isinstance(k, str) and 0 < len(k) <= 40 and (v is None or isinstance(v, (bool, int, float)) or (isinstance(v, str) and len(v) <= 80)):
            out[k] = v
    return out


async def get(pool: asyncpg.Pool, user_id: Optional[UUID]) -> dict[str, Any]:
    if user_id is None:
        return {}
    raw = await pool.fetchval("SELECT prefs FROM user_preferences WHERE user_id = $1", user_id)
    if raw is None:
        return {}
    return json.loads(raw) if isinstance(raw, str) else dict(raw)


async def merge(pool: asyncpg.Pool, user_id: UUID, patch: dict) -> dict[str, Any]:
    current = await get(pool, user_id)
    current.update(clean(patch))
    current = {k: v for k, v in current.items() if v is not None}
    await pool.execute(
        """
        INSERT INTO user_preferences (user_id, prefs, updated_at) VALUES ($1, $2, now())
        ON CONFLICT (user_id) DO UPDATE SET prefs = EXCLUDED.prefs, updated_at = now()
        """,
        user_id, current,   # the pool's jsonb codec encodes it (app/db/pool.py)
    )
    return current

"""Alert dismissals, per user, so a dismissal on one device holds on all.

The alerts themselves are worked out in the browser from the current
state (static/linck/js/data/alerts.js). Each has a key (which alert) and
a token (what it said: a streak's length, an error's text, a day). A
dismissal stores both; the browser hides the alert while its token is
unchanged, so it comes back when the situation changes. Dismissals older
than KEEP are dropped on read: by then the alert has either changed or
gone.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Iterable, Optional
from uuid import UUID

import asyncpg

KEEP = timedelta(days=30)
MAX_ITEMS = 200
MAX_LEN = 160


def _clean(items: Iterable) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for x in items or []:
        if len(out) >= MAX_ITEMS:
            break
        if not isinstance(x, dict):
            continue
        k, t = x.get("key"), x.get("token")
        if isinstance(k, str) and isinstance(t, str) and 0 < len(k) <= MAX_LEN and len(t) <= MAX_LEN:
            out.append((k, t))
    return out


async def get(pool: asyncpg.Pool, user_id: Optional[UUID]) -> dict[str, str]:
    """{key: token} of this user's dismissals."""
    if user_id is None:
        return {}
    await pool.execute("DELETE FROM alert_dismissals WHERE user_id = $1 AND dismissed_at < now() - $2::interval",
                       user_id, KEEP)
    rows = await pool.fetch("SELECT key, token FROM alert_dismissals WHERE user_id = $1", user_id)
    return {r["key"]: r["token"] for r in rows}


async def dismiss(pool: asyncpg.Pool, user_id: UUID, items: Iterable) -> dict[str, str]:
    """Dismiss alerts; dismissing one again with a new token replaces it."""
    pairs = _clean(items)
    if pairs:
        await pool.executemany(
            "INSERT INTO alert_dismissals (user_id, key, token) VALUES ($1, $2, $3) "
            "ON CONFLICT (user_id, key) DO UPDATE SET token = EXCLUDED.token, dismissed_at = now()",
            [(user_id, k, t) for k, t in pairs])
    return await get(pool, user_id)


async def restore(pool: asyncpg.Pool, user_id: UUID, keys: Iterable) -> dict[str, str]:
    """Bring dismissed alerts back."""
    ks = [k for k in (keys or []) if isinstance(k, str) and 0 < len(k) <= MAX_LEN][:MAX_ITEMS]
    if ks:
        await pool.execute("DELETE FROM alert_dismissals WHERE user_id = $1 AND key = ANY($2::text[])", user_id, ks)
    return await get(pool, user_id)

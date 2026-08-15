"""
live_deploy — the RBAC extension seam.

There is no role-based access control today, by explicit product
decision: every authenticated user can see and do everything (all
deployments are shared, not per-user). What this module exists for is
to make sure that when RBAC IS wanted later, it's a change confined to
this one file plus call sites that already exist — not a rearchitecture.

`can()` is the single choke point every "should this user be allowed to
do X" decision is meant to go through. Call sites that matter later
(e.g. `routers/deployments.py`'s mutating endpoints, `routers/auth.py`'s
user-management endpoints) can start calling `rbac.can(user, "...")` and
raising a 403 on `False` at any point — today `can()` always returns
`True`, so adding those calls now would be a no-op; the point is that
the SHAPE is already here for that later work to slot into.

`user` is whatever `request.state.user` is dict — see app/auth.py's
AuthMiddleware, which populates it from the session on every
authenticated request. Includes `id`, `username`, `role`. `role` is
already a real column on the `users` table (defaults to 'member') for
exactly this reason: adding a real check here later is "read the
column", not "add the column".

`action` is a short free-form string naming what's being attempted
(e.g. "deployment:pause", "user:create") — no fixed enum yet since
there's nothing to enumerate against; a real implementation would
likely map roles to a permission set keyed by these same strings.
"""

from typing import Optional


def can(user: Optional[dict], action: str) -> bool:
    # No RBAC yet — every authenticated user can do everything. `user`
    # and `action` are accepted (not ignored via `*args`) so call sites
    # read naturally today and don't need to change shape when a real
    # check lands here.
    return True

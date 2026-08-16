"""
live_deploy — authentication + user management.

The actual gate is app/auth.py's AuthMiddleware; this router is where
sessions get created/cleared and where the `users` table (migration
0005) gets managed — login, logout, change-password, create-user,
list-users, "who am I", and a read view over the audit log.

No RBAC yet, by explicit product decision — every logged-in user can
manage users and read the audit log, same as they can see every
deployment. See app/rbac.py's docstring for the extension seam meant to
make adding real role checks later NOT a rearchitecture; the call sites
that would gate `POST /auth/users` etc. behind a role are noted inline
below with the exact `rbac.can(...)` check they'd add.

/auth/login is the one endpoint allowlisted in AuthMiddleware (has to
be, to be reachable before logging in at all). Everything else here is
protected like any other router — including /auth/logout: with no
session, there's nothing to log out of, and a 401 is a reasonable
answer to "log me out of a session I don't have."
"""

import logging
from uuid import UUID

import bcrypt
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, field_validator

from ..db import queries

logger = logging.getLogger("live_deploy.auth")

router = APIRouter(prefix="/auth", tags=["auth"])

# Generic on purpose — "unknown username" and "wrong password" return
# the identical message and status so a login attempt can't be used to
# enumerate valid usernames.
_BAD_CREDENTIALS = "Incorrect username or password"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        # Malformed/foreign hash (shouldn't happen — every row is written
        # by hash_password above) — fail closed, not with a 500.
        return False


class LoginIn(BaseModel):
    username: str
    password: str


class ChangePasswordIn(BaseModel):
    old_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("New password must be at least 8 characters")
        return v


class CreateUserIn(BaseModel):
    username: str
    password: str

    @field_validator("username")
    @classmethod
    def _username_shape(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Username cannot be blank")
        return v

    @field_validator("password")
    @classmethod
    def _min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


def _user_out(row) -> dict:
    return {
        "id": str(row["id"]),
        "username": row["username"],
        "role": row["role"],
        "is_active": row["is_active"],
        "created_at": row["created_at"].isoformat(),
        "last_login_at": row["last_login_at"].isoformat() if row["last_login_at"] else None,
    }


@router.post("/login")
async def login(payload: LoginIn, request: Request):
    pool = request.app.state.db_pool
    user = await queries.get_user_by_username(pool, payload.username)
    # Always run bcrypt.checkpw even on a missing user (against a fixed
    # dummy hash) so a nonexistent-username request takes the same time
    # as a wrong-password one — bcrypt's own cost factor already makes
    # timing differences here impractical to exploit, but there's no
    # reason to leave the short-circuit in when it costs nothing to close.
    password_hash = user["password_hash"] if user else \
        "$2b$12$C6UzMDM.H6dfI/f/IKcEeO/o9lqM9AYibXStSflhHzOoOoiHYaP4y"  # bcrypt("") — not a real user's hash
    ok = verify_password(payload.password, password_hash)
    if not user or not ok or not user["is_active"]:
        raise HTTPException(401, _BAD_CREDENTIALS)

    request.session["user_id"] = str(user["id"])
    request.session["username"] = user["username"]
    # See migration 0006 + AuthMiddleware._session_ok: this session
    # only keeps working for as long as it matches the user's CURRENT
    # session_version — embedding it here is what makes a later bump
    # (change-password, logout-everywhere) actually revoke THIS session
    # too, not just future ones.
    request.session["session_version"] = user["session_version"]
    await queries.update_user_last_login(pool, user["id"])
    logger.info("Login successful: %s", user["username"])
    return {"ok": True, "username": user["username"]}


@router.post("/logout")
async def logout(request: Request):
    username = request.session.get("username")
    request.session.clear()
    logger.info("Logged out: %s", username)
    return {"ok": True}


@router.get("/me")
async def me(request: Request):
    user_id = request.session.get("user_id")
    if not user_id:
        # Reachable when the caller authenticated via X-API-Key rather
        # than a session cookie — that path has no associated user.
        return {"authenticated_via": "api_key", "username": None}
    pool = request.app.state.db_pool
    user = await queries.get_user_by_id(pool, UUID(user_id))
    if not user:
        raise HTTPException(401, "Session refers to a user that no longer exists")
    return {"authenticated_via": "session", **_user_out(user)}


@router.post("/change-password")
async def change_password(payload: ChangePasswordIn, request: Request):
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(400, "Log in with a user account to change its password "
                                  "(not available for X-API-Key access)")
    pool = request.app.state.db_pool
    user = await queries.get_user_by_id(pool, UUID(user_id))
    if not user or not verify_password(payload.old_password, user["password_hash"]):
        raise HTTPException(401, "Current password is incorrect")
    await queries.update_user_password(pool, user["id"], hash_password(payload.new_password))
    # A password change is exactly the moment a leaked/stolen session
    # should stop working — bump invalidates every session for this
    # user, including whichever one might have been compromised.
    # refresh_now() makes that immediate rather than waiting out the
    # cache's periodic interval (see main.py's registration of this
    # key). The one exception: THIS request's own session gets
    # re-stamped with the new version right after, so the person who
    # just changed their own password isn't logged out by their own
    # action — every OTHER session for this user is still dead the
    # instant they next make a request.
    new_version = await queries.bump_session_version(pool, user["id"])
    await request.app.state.cache.refresh_now("user_session_versions")
    request.session["session_version"] = new_version
    logger.info("Password changed: %s (all other sessions invalidated)", user["username"])
    return {"ok": True}


@router.post("/logout-everywhere")
async def logout_everywhere(request: Request):
    """Unlike change-password, this deliberately does NOT re-stamp the
    current session afterward — "log out everywhere" means everywhere,
    this browser included. Useful when you just want every session
    gone (lost/stolen device, shared computer, general paranoia)
    without also having to pick a new password to get there."""
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(400, "Log in with a user account to use this "
                                  "(not available for X-API-Key access)")
    pool = request.app.state.db_pool
    username = request.session.get("username")
    await queries.bump_session_version(pool, UUID(user_id))
    await request.app.state.cache.refresh_now("user_session_versions")
    request.session.clear()
    logger.info("Logged out everywhere: %s", username)
    return {"ok": True}


@router.post("/users")
async def create_user(payload: CreateUserIn, request: Request):
    # rbac.can(current_user, "user:create") would gate this once real
    # roles exist — every logged-in user may create users today.
    pool = request.app.state.db_pool
    existing = await queries.get_user_by_username(pool, payload.username)
    if existing:
        raise HTTPException(409, f"Username '{payload.username}' already exists")
    user = await queries.create_user(pool, payload.username, hash_password(payload.password))
    logger.info("User created: %s", user["username"])
    return _user_out(user)


@router.get("/users")
async def list_users(request: Request):
    pool = request.app.state.db_pool
    rows = await queries.list_users(pool)
    return [_user_out(r) for r in rows]


@router.get("/audit-log")
async def audit_log(request: Request, offset: int = 0, limit: int = 200):
    pool = request.app.state.db_pool
    limit = max(1, min(limit, 500))
    rows = await queries.list_audit_log(pool, offset=offset, limit=limit)
    return [
        {
            "id": str(r["id"]),
            "occurred_at": r["occurred_at"].isoformat(),
            "user_id": str(r["user_id"]) if r["user_id"] else None,
            "username": r["username"],
            "method": r["method"],
            "path": r["path"],
            "status_code": r["status_code"],
            "request_body": r["request_body"],
            "remote_addr": r["remote_addr"],
        }
        for r in rows
    ]

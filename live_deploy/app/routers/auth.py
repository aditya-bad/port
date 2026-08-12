"""
live_deploy — POST /auth/login and POST /auth/logout.

The actual enforcement is app/auth.py's AuthMiddleware; this router only
sets/clears the session — both handlers write to `request.session`
(Starlette's SessionMiddleware, wired up in main.py, is what turns that
into a signed Set-Cookie on the way out). Nothing here touches cookies
directly.

/auth/login is the one router endpoint that's allowlisted in
AuthMiddleware (has to be, to be reachable at all before logging in).
/auth/logout is NOT allowlisted — it's protected like everything else,
which is fine: with no session, there's nothing to log out of, and a
401 here is a reasonable answer to "log me out of a session I don't
have."
"""

import logging
import secrets

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger("live_deploy.auth")

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    password: str


@router.post("/login")
async def login(payload: LoginIn, request: Request):
    secret = request.app.state.app_auth_secret
    if not secrets.compare_digest(payload.password, secret):
        raise HTTPException(401, "Incorrect password")
    request.session["authed"] = True
    logger.info("Login successful")
    return {"ok": True}


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    logger.info("Logged out")
    return {"ok": True}

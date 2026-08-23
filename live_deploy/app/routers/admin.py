"""
live_deploy — app-level admin actions about the SOFTWARE itself
(redeploying a new release), not about any trading-strategy deployment.
Kept in its own router (`/admin`), separate from deployments.py's own
"deployment" domain — otherwise "deployment" would mean two completely
different things depending on which endpoint you're reading.
"""

import logging
import secrets
import time
from pathlib import Path

from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger("live_deploy.admin_router")

router = APIRouter(prefix="/admin", tags=["admin"])

# Same "re-enter the app password AND type this exact phrase" gate
# Clear All uses (deployments.py's own CLEAR_ALL_CONFIRM_PHRASE) —
# consistent security posture for every irreversible/outward-facing
# admin action in this app, not a new pattern invented just for this.
REDEPLOY_CONFIRM_PHRASE = "REDEPLOY"

# Written here, read by redeploy_watcher.sh — which runs ON THE HOST,
# never inside this container. This is NOT a stylistic choice; it's the
# only thing that actually works. Three real constraints rule out doing
# the redeploy from inside this process directly:
#
#   1. This container's own image (see Dockerfile) has no `git`, no
#      `docker` CLI, and no access to the host's Docker daemon socket —
#      redeploy.sh's own `git pull`/`docker build`/`docker stop`/
#      `docker run` commands would simply fail with "command not
#      found" if run from here as-is.
#   2. Even granting all of that (installing git+docker-cli in the
#      image, mounting /var/run/docker.sock) doesn't fix the deeper
#      problem: redeploy.sh's own `docker stop live-deploy` targets
#      THIS EXACT CONTAINER. Stopping a container tears down its whole
#      PID namespace at once — every process inside it, not just PID 1
#      — so the very shell script running that command would be killed
#      mid-execution, before it ever reached `docker build`/`docker
#      run`. A detached/backgrounded child process doesn't escape this
#      either; it's still inside the same container being torn down.
#   3. Mounting the Docker socket into this container at all is a real
#      security tradeoff (it's effectively root-equivalent access to
#      the whole host) that a background trading process shouldn't
#      need or carry, regardless of point 2.
#
# The fix: this process does the ONE thing it safely CAN do — write a
# plain marker file to a directory bind-mounted from the host (see
# redeploy.sh's own `-v $(pwd)/control:/app/control` mount) — and a
# tiny watcher running directly on the host (never in a container,
# so it's never at risk of being torn down by the redeploy it's
# performing) notices the file and runs redeploy.sh for real. See
# RUN_GUIDE.md's "Redeploy from the UI" section for the one-time setup
# this needs on the host before this endpoint does anything.
REDEPLOY_TRIGGER_PATH = Path("control") / "redeploy.trigger"


class RedeployIn(BaseModel):
    password: str
    confirm: str


@router.post("/redeploy")
async def trigger_redeploy(payload: RedeployIn, request: Request):
    """
    Signals redeploy_watcher.sh (host-side, see REDEPLOY_TRIGGER_PATH's
    own comment for why this can't just run redeploy.sh directly from
    here) to pull the latest code, rebuild, and restart. Gated behind
    the same two checks Clear All uses (deployments.py's
    clear_all_deployments): re-entering the app password and typing the
    literal confirmation phrase, both required and checked server-side
    — a stray click or a replayed request can't trigger this by itself.

    Deliberately does NOT wait for the redeploy to actually finish —
    by the time git pull/docker build/docker stop/docker run complete,
    THIS process (running inside the container about to be replaced)
    will itself have been killed partway through. Returns immediately
    once the trigger file is written; the frontend's own polling of
    /health (see api.js/account.js) is what actually confirms the new
    version came back up, since this response can't.

    Returns 503 (not a silent no-op) if `control/` isn't a real
    bind-mounted directory from the host — the most likely reason being
    the currently-running container predates the `-v control:/app/
    control` mount this feature needs (i.e. redeploy.sh hasn't been run
    with the updated mount yet). Writing the trigger file into this
    container's own ephemeral filesystem instead would silently do
    nothing (no host-side watcher is looking at it there) while telling
    the admin it worked — worse than refusing outright.
    """
    app_auth_secret = request.app.state.app_auth_secret
    if not secrets.compare_digest(payload.password, app_auth_secret):
        raise HTTPException(401, "Incorrect password")
    if payload.confirm != REDEPLOY_CONFIRM_PHRASE:
        raise HTTPException(400, f"Type {REDEPLOY_CONFIRM_PHRASE!r} exactly to confirm")

    if not REDEPLOY_TRIGGER_PATH.parent.is_dir():
        raise HTTPException(
            503,
            "control/ isn't mounted into this container yet -- redeploy once by hand "
            "(./redeploy.sh on the host) after pulling the update that adds this mount, "
            "then this button will work on every redeploy after that.",
        )

    REDEPLOY_TRIGGER_PATH.write_text(f"requested_at={time.time()}\n")
    logger.warning(
        "Redeploy triggered via admin UI -- wrote %s for the host-side watcher to pick up",
        REDEPLOY_TRIGGER_PATH,
    )
    return {"triggered": True}

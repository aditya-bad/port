"""/v2 endpoints for the Linck UI. See app/v2/__init__.py."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..db import queries
from ..deployments.schemas import DeploymentCreate
from ..strategies.registry import is_registered
from . import actions, book, catalog, preferences, session

router = APIRouter(prefix="/v2", tags=["v2"])

_BOOK_TTL = 15.0   # seconds; the book only changes when something closes


async def _refresh_caches(request: Request) -> None:
    cache = request.app.state.cache
    await asyncio.gather(
        cache.refresh_now("deployments"), cache.refresh_now("positions_open"), cache.refresh_now("trades_recent"),
    )
    request.app.state.v2_book = None


@router.get("/book")
async def get_book(request: Request, since: Optional[datetime] = None):
    """Closed trades, daily realized P&L and repaired or flagged days for every deployment."""
    cached = getattr(request.app.state, "v2_book", None)
    if since is None and cached and time.monotonic() - cached[0] < _BOOK_TTL:
        return cached[1]
    pool = request.app.state.db_pool
    trades, daily, repairs = await asyncio.gather(
        book.list_trades(pool, since), book.list_daily(pool, since), book.list_repairs(pool))
    out = {"trades": trades, "daily": daily, "repairs": repairs}
    if since is None:
        request.app.state.v2_book = (time.monotonic(), out)
    return out


@router.get("/session")
async def get_session(request: Request):
    """Today's P&L lane and event markers for every deployment."""
    return await session.build_session(request.app.state.db_pool)


@router.get("/strategy-health")
async def get_strategy_health(request: Request, fresh: bool = False):
    """Deployments drifting from how they normally trade (app/strategy_health.py):
    {checked_at, deployments: {id: [findings]}}. The background check's
    last result, or a fresh one (`fresh`, or before the first check)."""
    from .. import strategy_health
    cached = getattr(request.app.state, "strategy_health", None)
    if fresh or not cached:
        now = datetime.now(strategy_health.IST)
        cached = {"checked_at": now.isoformat(), "deployments": await strategy_health.check_all(request.app.state.db_pool, now)}
        request.app.state.strategy_health = cached
    return cached


@router.get("/vix")
async def get_vix(request: Request, start: Optional[datetime] = None, end: Optional[datetime] = None,
                  interval: str = "5minute"):
    """India VIX from Kite (app/vix.py), kept in memory, never stored:
    candles for [start, end] (default: today), and the live value."""
    from .. import vix
    from ..options import NoKiteSession
    if interval not in vix.SPAN:
        raise HTTPException(422, f"interval must be one of {', '.join(vix.SPAN)}")
    svc = getattr(request.app.state, "vix", None)
    if svc is None:
        raise HTTPException(503, "VIX isn't available")
    now = datetime.now(vix.IST)
    a = (start or now).astimezone(vix.IST).date()
    b = (end or now).astimezone(vix.IST).date()
    if (b - a).days > 800:
        raise HTTPException(422, "At most 800 days at a time")
    candles, error = [], None
    try:
        rows = await svc.vix(a, b, interval)
        if not rows and not start:   # nothing today yet (before the open, a holiday): the last session
            rows = (await svc.vix(a - timedelta(days=7), a, interval))[-1:]
        candles = [{"t": int(r["t"].timestamp() * 1000), "o": r["o"], "h": r["h"], "l": r["l"], "c": r["c"]} for r in rows]
    except NoKiteSession:
        error = "Kite isn't logged in, so VIX history can't be fetched."
    return {"symbol": vix.SYMBOL, "token": svc.token, "last": svc.last(), "candles": candles,
            "error": error or svc.last_error}


@router.get("/vix/spike")
async def get_vix_spike(request: Request, fresh: bool = False):
    """The latest VIX spike check (app/vix.py): {vix, prev_close, change,
    change_pct, spiking, exposed}, or {spiking: false} outside market hours."""
    from .. import vix
    from ..options import NoKiteSession
    s = vix.state.get("spike")
    svc = getattr(request.app.state, "vix", None)
    if (fresh or s is None) and svc is not None:
        try:
            s = await svc.spike()
        except NoKiteSession:
            s = None
        if s and s["spiking"]:
            s["exposed"] = await vix.exposed(request.app.state.db_pool)
    return s or {"spiking": False}


@router.get("/vix/exposure")
async def get_vix_exposure(request: Request):
    """What open option legs gain or lose if VIX rises one point now
    (app/vix.exposure), per deployment and in total."""
    from .. import vix
    return await vix.exposure(request.app.state.db_pool, request.app.state.dispatcher, getattr(request.app.state, "vix", None))


@router.get("/deployments/{deployment_id}/underlying")
async def get_underlying(deployment_id: UUID, request: Request):
    """The open legs against the index's spot and its future: the basis,
    each leg's distance from both, delta per point of the future, and what
    a move of the future would do (app/v2/underlying.py)."""
    from . import underlying
    pool = request.app.state.db_pool
    dep = await queries.get_deployment(pool, deployment_id)
    if dep is None:
        raise HTTPException(404, "No such deployment")
    return await underlying.build(pool, dict(dep), request.app.state.dispatcher)


@router.get("/deployments/{deployment_id}/repairs")
async def get_repairs(deployment_id: UUID, request: Request):
    """The stall repairs applied to this deployment (custom_scripts/
    repair_stall.py, app/repair), newest first: the window replayed, the
    paths chosen, what it did and what it changed. Undone ones are left out."""
    pool = request.app.state.db_pool
    if await queries.get_deployment(pool, deployment_id) is None:
        raise HTTPException(404, "No such deployment")
    rows = await pool.fetch(
        "SELECT * FROM stall_repairs WHERE deployment_id = $1 AND undone_at IS NULL ORDER BY window_from DESC", deployment_id)
    out = []
    for r in rows:
        s = r["summary"] or {}
        out.append({
            "id": str(r["id"]), "day": r["day"].isoformat(), "from": r["window_from"].isoformat(), "to": r["window_to"].isoformat(),
            "applied_at": r["applied_at"].isoformat(), "choices": r["choices"] or {},
            "fills": s.get("fills", 0), "pnl": s.get("pnl"), "left": s.get("left"), "change": s.get("change"),
            "booked": s.get("booked"), "reasons": s.get("reasons") or {},
            "trades": [{k: f.get(k) for k in ("minute", "phase", "action", "symbol", "qty", "price", "reason", "realized")}
                       for f in s.get("fills_detail") or []],
        })
    return {"repairs": out}


@router.get("/review")
async def get_review(request: Request, week: Optional[str] = None):
    """The weekly review (app/v2/review.py): ?week=any day of the week
    (default: this week once something closed in it, else last week)."""
    from datetime import date as _date

    from . import review
    try:
        wk = _date.fromisoformat(week) if week else None
    except ValueError:
        raise HTTPException(422, "week must be a date, YYYY-MM-DD")
    health = (getattr(request.app.state, "strategy_health", None) or {}).get("deployments")
    return await review.build(request.app.state.db_pool, wk, health=health)


@router.get("/strategies")
async def get_strategies(request: Request):
    enabled = await queries.get_strategy_enabled_map(request.app.state.db_pool)
    out = catalog.catalog()
    for s in out:
        s["enabled"] = enabled.get(s["name"], True)
    return out


def _user_id(request: Request) -> Optional[UUID]:
    uid = request.session.get("user_id") if "session" in request.scope else None
    return UUID(uid) if uid else None


@router.get("/preferences")
async def get_preferences(request: Request):
    return await preferences.get(request.app.state.db_pool, _user_id(request))


@router.put("/preferences")
async def put_preferences(payload: dict, request: Request):
    uid = _user_id(request)
    if uid is None:
        raise HTTPException(400, "Preferences are saved per signed-in user")
    return await preferences.merge(request.app.state.db_pool, uid, payload)


class CloseEverythingIn(BaseModel):
    confirm: str


@router.post("/close-everything")
async def close_everything(payload: CloseEverythingIn, request: Request):
    """Close every open leg everywhere, shorts before longs, then pause
    every running deployment. Needs the typed confirmation the UI asks for."""
    if payload.confirm.strip().upper() != "CLOSE ALL":
        raise HTTPException(400, "Type CLOSE ALL to confirm")
    result = await actions.close_everything(request.app.state.deployment_manager)
    await _refresh_caches(request)
    return result


class CreateIn(DeploymentCreate):
    start: Literal["paused", "now"] = "paused"
    tags: list[str] = Field(default_factory=list)


@router.post("/deployments", status_code=201)
async def create_deployment(payload: CreateIn, request: Request):
    """Same checks as POST /deployments, plus a start mode and tags."""
    pool = request.app.state.db_pool
    if await queries.get_deployment_by_name(pool, payload.deployment_name) is not None:
        raise HTTPException(409, f"A deployment named {payload.deployment_name!r} already exists")
    if is_registered(payload.strategy_name) and not await queries.is_strategy_enabled(pool, payload.strategy_name):
        raise HTTPException(400, f"Strategy {payload.strategy_name!r} is disabled")
    if payload.tags:
        known = {t["name"] for t in await queries.list_tags(pool)}
        missing = [t for t in payload.tags if t not in known]
        if missing:
            raise HTTPException(400, f"Unknown tag(s): {', '.join(missing)}")
    base = DeploymentCreate(**payload.model_dump(exclude={"start", "tags"}))
    try:
        row, registered = await actions.create_deployment(
            request.app.state.deployment_manager, base, payload.start, payload.tags,
        )
    except Exception as e:
        raise HTTPException(400, str(e))
    await _refresh_caches(request)
    return {"id": str(row["id"]), "status": row["status"], "strategy_registered": registered}


@router.post("/deployments/{deployment_id}/restart")
async def restart_deployment(deployment_id: UUID, request: Request):
    """Start the strategy of a deployment that's active but not running
    (see DeploymentManager.find_not_running). 409 with the strategy's own
    error if it still can't start."""
    manager = request.app.state.deployment_manager
    try:
        await manager.restart(deployment_id)
    except KeyError:
        raise HTTPException(404, "No such deployment")
    except ValueError as e:
        raise HTTPException(409, str(e))
    await _refresh_caches(request)
    return {"ok": True, "running": True}


# ── Health monitor (app/ops_health.py) ───────────────────────────────

class SnoozeIn(BaseModel):
    period: str = Field("30m", alias="for", description="30m, 2h (any number of m or h), today — or clear")

    model_config = {"populate_by_name": True}


def _ops(request: Request):
    ops = getattr(request.app.state, "ops", None)
    if ops is None:
        raise HTTPException(503, "The health monitor isn't running")
    return ops


@router.get("/ops")
async def get_ops(request: Request):
    """What the health monitor sees: open issues, the last 50 cleared
    ones, and the option-history export (last result, or running)."""
    from .. import ops_health
    pool = request.app.state.db_pool
    ops = getattr(request.app.state, "ops", None)
    open_rows = await pool.fetch("SELECT * FROM ops_issues WHERE resolved_at IS NULL ORDER BY severity, opened_at")
    recent = await pool.fetch(
        "SELECT * FROM ops_issues WHERE resolved_at IS NOT NULL ORDER BY resolved_at DESC LIMIT 50")
    candles = getattr(request.app.state, "candles", None)
    d = request.app.state.dispatcher
    return {
        "checked_at": ops.checked_at.isoformat() if ops and ops.checked_at else None,
        "open": [ops_health.issue(r) for r in open_rows],
        "recent": [ops_health.issue(r) for r in recent],
        "export": {"last": await queries.get_app_scratch(pool, "candle_backfill_last"),
                   "running": getattr(candles, "progress", None)},
        "kite": {"connected": bool(d.connected) and not d.status.get("needs_login"),
                 "last_tick_at": d.last_tick_at.isoformat() if d.last_tick_at else None,
                 "ticks_stalled_s": request.app.state.deployment_manager.tick_stall(datetime.now().astimezone())},
        "running": len(request.app.state.deployment_manager.runners),
    }


@router.post("/ops/check")
async def run_ops_check(request: Request):
    """Run the health check now instead of waiting for the next minute."""
    await _ops(request).check_once()
    return await get_ops(request)


@router.post("/ops/issues/{issue_id}/snooze")
async def snooze_issue(issue_id: int, payload: SnoozeIn, request: Request):
    """Stop an open issue alerting for a while (it stays open and shown)."""
    from datetime import timezone

    from .. import ops_health
    pool = request.app.state.db_pool
    if payload.period == "clear":
        until = None
    else:
        try:
            until = ops_health.snooze_until(payload.period, datetime.now(timezone.utc))
        except ValueError as e:
            raise HTTPException(422, str(e))
    row = await pool.fetchrow(
        "UPDATE ops_issues SET snoozed_until = $2 WHERE id = $1 AND resolved_at IS NULL RETURNING *", issue_id, until)
    if row is None:
        raise HTTPException(404, "No such open issue")
    if getattr(request.app.state, "ops", None) is not None:
        await request.app.state.ops.refresh_open()
    return ops_health.issue(row)


@router.post("/ops/issues/{issue_id}/dismiss")
async def dismiss_issue(issue_id: int, request: Request):
    """Close an issue by hand (a one-off you've dealt with). If the check
    still finds the problem, it opens again as a new issue."""
    from datetime import timezone

    from .. import ops_health
    row = await request.app.state.db_pool.fetchrow(
        "UPDATE ops_issues SET resolved_at = $2, resolved_by = 'dismissed' WHERE id = $1 AND resolved_at IS NULL RETURNING *",
        issue_id, datetime.now(timezone.utc))
    if row is None:
        raise HTTPException(404, "No such open issue")
    if row["key"].startswith("strat:"):   # a deployment's own report: closed with it, so it doesn't come straight back
        await request.app.state.db_pool.execute(
            "UPDATE strategy_issues SET resolved_at = now(), resolved_by = 'dismissed' WHERE id = $1 AND resolved_at IS NULL",
            int(row["key"].split(":", 1)[1]))
    if getattr(request.app.state, "ops", None) is not None:
        await request.app.state.ops.refresh_open()
    return ops_health.issue(row)


# ── What the deployments report about themselves (app/deployments/health.py) ──

def _strategy_issue_json(r) -> dict:
    return {
        "id": r["id"], "deployment_id": str(r["deployment_id"]), "name": r["deployment_name"], "strategy": r["strategy_name"],
        "key": r["key"], "kind": r["kind"], "severity": r["severity"], "source": r["source"], "title": r["title"],
        "detail": r["detail"], "data": r["data"] or {}, "day": r["day"].isoformat() if r["day"] else None,
        "first_at": r["first_at"].isoformat(), "last_at": r["last_at"].isoformat(), "count": r["count"],
        "expires_at": r["expires_at"].isoformat() if r["expires_at"] else None,
        "resolved_at": r["resolved_at"].isoformat() if r["resolved_at"] else None, "resolved_by": r["resolved_by"],
    }


@router.get("/strategy-issues")
async def get_strategy_issues(request: Request, all: bool = False, deployment: Optional[UUID] = None):
    """What the deployments report about themselves: open ones (worst
    first), or with `all` the recently closed too."""
    rows = await queries.list_strategy_issues(request.app.state.db_pool, open_only=not all, deployment_id=deployment,
                                              limit=200 if all else 500)
    return {"issues": [_strategy_issue_json(r) for r in rows]}


@router.post("/strategy-issues/{issue_id}/dismiss")
async def dismiss_strategy_issue(issue_id: int, request: Request):
    """Close one by hand (you've dealt with it). If the deployment reports
    it again, it comes back as a new one."""
    pool = request.app.state.db_pool
    row = await pool.fetchrow(
        "UPDATE strategy_issues SET resolved_at = now(), resolved_by = 'dismissed' WHERE id = $1 AND resolved_at IS NULL RETURNING id",
        issue_id)
    if row is None:
        raise HTTPException(404, "No such open issue")
    await pool.execute("UPDATE ops_issues SET resolved_at = now(), resolved_by = 'dismissed' "
                       "WHERE key = $1 AND resolved_at IS NULL", f"strat:{issue_id}")
    if getattr(request.app.state, "ops", None) is not None:
        await request.app.state.ops.refresh_open()
    return {"id": issue_id, "dismissed": True}


# ── End-of-day check (app/repair/daycheck.py) ────────────────────────

_CHECK_ORDER = {"rule": 0, "mismatch": 1, "ambiguous": 2, "error": 3, "cant_check": 4, "ok": 5}


def _run_json(r) -> Optional[dict]:
    if r is None:
        return None
    return {"id": r["id"], "day": r["day"].isoformat(), "trigger": r["trigger"], "status": r["status"],
            "progress": r["progress"] or {}, "error": r["error"],
            "started_at": r["started_at"].isoformat(), "finished_at": r["finished_at"].isoformat() if r["finished_at"] else None}


@router.get("/day-checks")
async def get_day_checks(request: Request, day: Optional[str] = None):
    """A day's end-of-day check: its latest run and every deployment's
    result, worst first. Default: the latest day checked."""
    from datetime import date as _date
    pool = request.app.state.db_pool
    days = [r["day"] for r in await pool.fetch(
        "SELECT day FROM (SELECT day FROM day_check_runs UNION SELECT day FROM day_checks) x GROUP BY day ORDER BY day DESC LIMIT 20")]
    try:
        d = _date.fromisoformat(day) if day else (days[0] if days else None)
    except ValueError:
        raise HTTPException(422, "day must be a date, YYYY-MM-DD")
    if d is None:
        return {"day": None, "days": [], "run": None, "results": [], "counts": {}}
    run = await pool.fetchrow("SELECT * FROM day_check_runs WHERE day = $1 ORDER BY id DESC LIMIT 1", d)
    rows = await pool.fetch(
        "SELECT c.*, dp.deployment_name, dp.strategy_name, dp.mode FROM day_checks c "
        "JOIN deployments dp ON dp.id = c.deployment_id WHERE c.day = $1", d)
    results = sorted(({
        "deployment_id": str(r["deployment_id"]), "name": r["deployment_name"], "strategy": r["strategy_name"],
        "mode": r["mode"], "status": r["status"], "headline": r["headline"], "rules": r["rules"] or [],
        "diff": r["diff"] or {}, "replay": r["replay"] or {}, "checked_at": r["checked_at"].isoformat(),
        "dismissed_at": r["dismissed_at"].isoformat() if r["dismissed_at"] else None,
    } for r in rows), key=lambda x: (_CHECK_ORDER.get(x["status"], 9), x["name"]))
    counts: dict = {}
    for x in results:
        counts[x["status"]] = counts.get(x["status"], 0) + 1
    return {"day": d.isoformat(), "days": [x.isoformat() for x in days], "run": _run_json(run),
            "results": results, "counts": counts}


class DayCheckIn(BaseModel):
    day: Optional[str] = None
    deployment_id: Optional[UUID] = None


@router.post("/day-checks/run", status_code=202)
async def run_day_check(payload: DayCheckIn, request: Request):
    """Start the end-of-day check now (its own process). Default: today."""
    from datetime import date as _date

    from ..repair.market import IST
    launcher = getattr(request.app.state, "daychecks", None)
    if launcher is None:
        raise HTTPException(503, "The day check isn't available")
    try:
        d = _date.fromisoformat(payload.day) if payload.day else datetime.now(IST).date()
    except ValueError:
        raise HTTPException(422, "day must be a date, YYYY-MM-DD")
    if d > datetime.now(IST).date():
        raise HTTPException(422, "That day hasn't happened yet")
    rid = await launcher.start(d, "manual", str(payload.deployment_id) if payload.deployment_id else None)
    row = await request.app.state.db_pool.fetchrow("SELECT * FROM day_check_runs WHERE id = $1", rid)
    return _run_json(row)


# ── Repairing a flagged day (app/repair/dayrepair.py) ────────────────

_ORDERS = ("up_first", "down_first")


def _job_json(r, result: bool = True) -> Optional[dict]:
    if r is None:
        return None
    return {"id": r["id"], "kind": r["kind"], "status": r["status"], "choices": r["choices"] or {}, "error": r["error"],
            "created_at": r["created_at"].isoformat(),
            "finished_at": r["finished_at"].isoformat() if r["finished_at"] else None,
            **({"result": r["result"]} if result else {})}


def _day(s: str):
    from datetime import date as _date
    try:
        return _date.fromisoformat(s)
    except ValueError:
        raise HTTPException(422, "day must be a date, YYYY-MM-DD")


class DismissIn(BaseModel):
    undo: bool = False


@router.post("/repair-cases/{day}/{deployment_id}/dismiss")
async def dismiss_flagged_day(day: str, deployment_id: UUID, payload: DismissIn, request: Request):
    """A flagged day you've looked at and are leaving as it is: no longer
    marked as flagged anywhere (undo brings the mark back). A day that's
    been repaired, or wasn't flagged, has nothing to dismiss."""
    pool = request.app.state.db_pool
    d = _day(day)
    row = await pool.fetchrow(
        "UPDATE day_checks SET dismissed_at = CASE WHEN $3 THEN NULL ELSE now() END "
        "WHERE deployment_id = $1 AND day = $2 AND status IN ('rule', 'mismatch', 'ambiguous') "
        "AND NOT EXISTS (SELECT 1 FROM stall_repairs r WHERE r.deployment_id = $1 AND r.day = $2 AND r.undone_at IS NULL) "
        "RETURNING dismissed_at", deployment_id, d, payload.undo)
    if row is None:
        raise HTTPException(409, "Only a flagged day that hasn't been repaired can be dismissed")
    request.app.state.v2_book = None   # the marks come from the book: not 15 seconds late
    return {"day": d.isoformat(), "deployment_id": str(deployment_id),
            "dismissed_at": row["dismissed_at"].isoformat() if row["dismissed_at"] else None}


@router.get("/repair-cases/{day}/{deployment_id}")
async def get_repair_case(day: str, deployment_id: UUID, request: Request):
    """A flagged day of one deployment: what the check found, the paths
    chosen so far (the draft), the latest plan of it with everything to
    judge it by, and the repair if applied."""
    pool = request.app.state.db_pool
    d = _day(day)
    dep = await queries.get_deployment(pool, deployment_id)
    if dep is None:
        raise HTTPException(404, "No such deployment")
    check = await pool.fetchrow("SELECT * FROM day_checks WHERE deployment_id = $1 AND day = $2", deployment_id, d)
    live = await pool.fetchrow(
        "SELECT * FROM stall_repairs WHERE deployment_id = $1 AND day = $2 AND undone_at IS NULL", deployment_id, d)
    reported = await pool.fetch(
        "SELECT i.*, d.deployment_name, d.strategy_name FROM strategy_issues i JOIN deployments d ON d.id = i.deployment_id "
        "WHERE i.deployment_id = $1 AND i.day = $2 ORDER BY i.first_at", deployment_id, d)
    if check is None and live is None and not reported:
        # a repair made by the script, or an issue the deployment reported about the day, still has a case
        raise HTTPException(404, "That day hasn't been checked for this deployment")
    draft = await pool.fetchrow("SELECT * FROM repair_drafts WHERE deployment_id = $1 AND day = $2", deployment_id, d)
    plan = await pool.fetchrow(
        "SELECT * FROM repair_jobs WHERE deployment_id = $1 AND day = $2 AND kind = 'plan' ORDER BY id DESC LIMIT 1", deployment_id, d)
    apply_ = await pool.fetchrow(
        "SELECT * FROM repair_jobs WHERE deployment_id = $1 AND day = $2 AND kind = 'apply' ORDER BY id DESC LIMIT 1", deployment_id, d)
    choices = (draft["choices"] if draft else None) or {}
    return {
        "day": d.isoformat(),
        "deployment": {"id": str(dep["id"]), "name": dep["deployment_name"], "strategy": dep["strategy_name"],
                       "mode": dep["mode"], "status": dep["status"], "capital": float(dep["initial_capital"])},
        "check": {"status": check["status"], "headline": check["headline"], "rules": check["rules"] or [],
                  "diff": check["diff"] or {}, "replay": check["replay"] or {}, "checked_at": check["checked_at"].isoformat(),
                  "dismissed_at": check["dismissed_at"].isoformat() if check["dismissed_at"] else None} if check else None,
        "draft": {"choices": choices, "updated_at": draft["updated_at"].isoformat() if draft else None},
        "reported": [_strategy_issue_json(r) for r in reported],
        "plan": _job_json(plan),
        "plan_is_draft": bool(plan and plan["status"] == "done" and (plan["choices"] or {}) == choices),
        "apply": _job_json(apply_, result=True),
        "applied": {"id": str(live["id"]), "applied_at": live["applied_at"].isoformat(),
                    "from": live["window_from"].isoformat(), "to": live["window_to"].isoformat(),
                    "choices": live["choices"] or {},
                    "summary": {k: v for k, v in (live["summary"] or {}).items() if k not in ("fills_detail", "replaced")},
                    "fills": (live["summary"] or {}).get("fills_detail") or [],
                    "replaced": (live["summary"] or {}).get("replaced") or []} if live else None,
    }


class PlanIn(BaseModel):
    choices: dict[str, str] = Field(default_factory=dict, description='{"HH:MM": "up_first" | "down_first"}')


@router.post("/repair-cases/{day}/{deployment_id}/plan", status_code=202)
async def plan_repair_case(day: str, deployment_id: UUID, payload: PlanIn, request: Request):
    """Keep these paths as the draft and plan the repair with them (a dry
    run in the repair worker; nothing in the book changes)."""
    import re as _re
    pool = request.app.state.db_pool
    d = _day(day)
    for k, v in payload.choices.items():
        if not _re.fullmatch(r"\d\d:\d\d", k) or v not in _ORDERS:
            raise HTTPException(422, f"choices are HH:MM → up_first | down_first, got {k!r}: {v!r}")
    if not await pool.fetchval("SELECT 1 FROM day_checks WHERE deployment_id = $1 AND day = $2", deployment_id, d):
        raise HTTPException(404, "That day hasn't been checked for this deployment")
    await pool.execute(
        "INSERT INTO repair_drafts (deployment_id, day, choices, updated_at) VALUES ($1, $2, $3, now()) "
        "ON CONFLICT (deployment_id, day) DO UPDATE SET choices = $3, updated_at = now()", deployment_id, d, payload.choices)
    job = await pool.fetchrow(
        "INSERT INTO repair_jobs (deployment_id, day, kind, choices) VALUES ($1, $2, 'plan', $3) RETURNING *",
        deployment_id, d, payload.choices)
    await request.app.state.repair_worker.ensure()
    return _job_json(job, result=False)


@router.get("/repair-jobs/{job_id}")
async def get_repair_job(job_id: int, request: Request):
    row = await request.app.state.db_pool.fetchrow("SELECT * FROM repair_jobs WHERE id = $1", job_id)
    if row is None:
        raise HTTPException(404, "No such job")
    return _job_json(row)


async def _paused_while(request: Request, dep_id: UUID, work, wait_job: Optional[int] = None):
    """Pause a running deployment around `work` (and, given a job, until
    it's finished), then resume it."""
    mgr = request.app.state.deployment_manager
    pool = request.app.state.db_pool
    was_active = (await queries.get_deployment(pool, dep_id))["status"] == "active"
    if was_active:
        await mgr.pause(dep_id)
    try:
        res = await work()
    except BaseException:
        if was_active:
            await mgr.resume(dep_id)
        raise
    if wait_job is None and was_active:
        await mgr.resume(dep_id)
    return res, was_active


@router.post("/repair-cases/{day}/{deployment_id}/push", status_code=202)
async def push_repair_case(day: str, deployment_id: UUID, request: Request):
    """Commit the draft to the deployment: the repair worker replays it
    again and commits only if it comes out exactly as the plan shown. The
    deployment is paused until it's done, then resumed."""
    pool = request.app.state.db_pool
    d = _day(day)
    case = await get_repair_case(day, deployment_id, request)
    plan = case["plan"]
    if not case["plan_is_draft"]:
        raise HTTPException(409, "Plan the repair with the paths you chose first")
    res = plan["result"] or {}
    if not res.get("can_apply"):
        raise HTTPException(409, "; ".join(res.get("why_not") or []) or "This plan can't be applied")
    if case["apply"] and case["apply"]["status"] in ("queued", "running"):
        raise HTTPException(409, "It's already being applied")
    author = request.session.get("username") if "session" in request.scope else None

    async def enqueue():
        return await pool.fetchrow(
            "INSERT INTO repair_jobs (deployment_id, day, kind, choices, expected, author) VALUES ($1, $2, 'apply', $3, $4, $5) RETURNING *",
            deployment_id, d, plan["choices"], res["fills_key"], author)
    job, was_active = await _paused_while(request, deployment_id, enqueue, wait_job=True)
    await request.app.state.repair_worker.ensure()

    async def resume_after():
        for _ in range(1800):
            st = await pool.fetchval("SELECT status FROM repair_jobs WHERE id = $1", job["id"])
            if st in ("done", "failed"):
                break
            await asyncio.sleep(0.5)
        if was_active:
            try:
                await request.app.state.deployment_manager.resume(deployment_id)
            except Exception:
                pass
        await _refresh_caches(request)
    request.app.state.repair_resume_tasks = getattr(request.app.state, "repair_resume_tasks", set())
    t = asyncio.create_task(resume_after())
    request.app.state.repair_resume_tasks.add(t)
    t.add_done_callback(request.app.state.repair_resume_tasks.discard)
    return {**_job_json(job, result=False), "paused": was_active}


@router.post("/repairs/{repair_id}/undo")
async def undo_repair(repair_id: UUID, request: Request):
    """Take an applied repair back out (the deployment paused meanwhile)."""
    from ..repair import engine as repair_engine
    pool = request.app.state.db_pool
    r = await pool.fetchrow("SELECT * FROM stall_repairs WHERE id = $1", repair_id)
    if r is None:
        raise HTTPException(404, "No such repair")

    async def work():
        return await repair_engine.undo(pool, repair_id)
    try:
        res, _ = await _paused_while(request, r["deployment_id"], work)
    except repair_engine.PlanChanged as e:
        raise HTTPException(409, str(e))
    await _refresh_caches(request)
    return {"deployment_id": str(res["deployment_id"]), "lots_removed": res["lots_removed"],
            "positions_removed": res["positions_removed"]}

"""
live_deploy — operational health: is everything that has to work, working?

Every minute the monitor looks at Kite, the database, the daily option-
history export and every running deployment, and turns each problem into
an issue (table ops_issues) that stays open until the check passes again:

  kite:login           a market day, 09:05–15:30, and Kite isn't connected
  kite:stall           connected, but no ticks for 90s in market hours
  db                   the database doesn't answer
  dep:<id>:down        marked active, but nothing runs its strategy
  dep:<id>:silent      running, prices flow, but it hasn't acted on one in 3 min
  dep:<id>:errors      its strategy raised in the last 5 minutes
  dep:<id>:stale-legs  open legs with no trade today (after 09:45): the
                       strategy may not act on them, their P&L is old
  export:<day>         the 15:45 option-history export failed, or hadn't
                       run by 16:15
  daycheck:<day>       the end-of-day check (app/repair/daycheck.py) failed
  eod:<id>             a deployment the day check found breaking a rule of
                       a day's end (an intraday one holding legs after its
                       cut-off, a leg held past expiry) still holds them

It also keeps the day: each deployment's strategy state at the open
(09:00–09:15) and after the close (from 15:35), and once the export has
succeeded, starts the end-of-day check (app/repair/launcher.py) and says
what it found when it's done.

While an issue is open and not snoozed, it alerts every minute — a push
to every subscribed phone and a toast in every open Linck tab — and once
more when it clears. "Mark as holiday" (Account -> Notifications) mutes
the pushes and the market-hours checks for the day. The export also
says once when it succeeded.

The watchdog (DeploymentManager.watchdog_loop) still does the recovering
— restarting a dead runner, reconnecting a stalled feed; this only
reports.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from uuid import UUID
from zoneinfo import ZoneInfo

from .db import queries

logger = logging.getLogger("live_deploy.ops_health")

IST = ZoneInfo("Asia/Kolkata")
ALERT_EVERY = timedelta(seconds=55)          # "every minute" for a loop that runs every 60s
SILENT_AFTER = timedelta(minutes=3)
ERRORS_WITHIN = timedelta(minutes=5)
STALE_LEGS_FROM = time(9, 45)
EXPORT_LATE = time(16, 15)
KITE_FROM, MARKET_OPEN, MARKET_CLOSE = time(9, 5), time(9, 15), time(15, 30)


@dataclass
class Problem:
    key: str
    kind: str
    title: str
    detail: str
    severity: str = "critical"
    deployment_id: Optional[str] = None
    data: dict = field(default_factory=dict)


def _ist(dt: datetime) -> datetime:
    return dt.astimezone(IST) if dt.tzinfo else dt.replace(tzinfo=IST)


def market_day(now: datetime) -> bool:
    return _ist(now).weekday() < 5


def in_market(now: datetime, start: time = MARKET_OPEN) -> bool:
    t = _ist(now).time()
    return market_day(now) and start <= t < MARKET_CLOSE


class OpsMonitor:
    def __init__(self, pool, manager, dispatcher, cache=None, event_broadcaster=None, candles=None, launcher=None):
        self.pool = pool
        self.manager = manager
        self.dispatcher = dispatcher
        self.cache = cache
        self.event_broadcaster = event_broadcaster
        self.candles = candles
        self.launcher = launcher            # app/repair/launcher.py: the end-of-day check
        self.checked_at: Optional[datetime] = None
        self.open: list[dict] = []          # the open issues as of the last check, for /health
        self._db_down_alert: Optional[datetime] = None

    # ── what's wrong right now ─────────────────────────────────────────

    async def holiday(self, now: datetime) -> bool:
        """"Mark as holiday" pressed for today (it mutes until 07:00 tomorrow)."""
        if self.cache is None:
            return False
        try:
            until = await self.cache.get("notification_mute_until")
        except Exception:
            return False
        from .routers.notifications import is_marked_holiday
        return is_marked_holiday(until, now)

    async def muted(self) -> bool:
        if self.cache is None:
            return False
        try:
            until = await self.cache.get("notification_mute_until")
        except Exception:
            return False
        return until is not None and datetime.now(timezone.utc) < until

    def _kite(self, now: datetime, holiday: bool) -> list[Problem]:
        d, out = self.dispatcher, []
        connected = bool(getattr(d, "connected", False)) and not d.status.get("needs_login")
        n = len(self.manager.runners)
        if not connected and not holiday and in_market(now, KITE_FROM):
            out.append(Problem("kite:login", "kite_login", "Kite isn't connected",
                               f"{n} running deployment(s) can't trade until you log in to Kite."
                               + (f" Last error: {d.last_error}" if getattr(d, "last_error", None) else "")))
        stalled = self.manager.tick_stall(now) if connected else None
        if stalled:
            last = _ist(d.last_tick_at)
            out.append(Problem("kite:stall", "kite_stall", "Kite prices stopped",
                               f"No ticks since {last:%H:%M} though the connection looks open. Reconnecting "
                               f"automatically; {n} running deployment(s) can't act until prices return.",
                               data={"last_tick_at": last.isoformat(), "seconds": round(stalled)}))
        return out

    def _runners(self, now: datetime, holiday: bool) -> list[Problem]:
        out = []
        for x in getattr(self.manager, "health_issues", []) or []:
            legs = f" It holds {x['open_positions']} open position(s) that nothing will exit." if x.get("open_positions") else ""
            out.append(Problem(f"dep:{x['id']}:down", "not_running", f"{x['name']} isn't running",
                               f"Marked active but its strategy isn't running ({x['reason']}).{legs} "
                               "Retrying automatically every minute — or open it and press Restart.",
                               deployment_id=x["id"], data={"open_positions": x.get("open_positions", 0)}))
        d = self.dispatcher
        last_tick = getattr(d, "last_tick_at", None)
        connected = bool(getattr(d, "connected", False)) and not d.status.get("needs_login")
        flowing = connected and last_tick is not None and now - last_tick < timedelta(seconds=60)
        if holiday or not flowing or not in_market(now, time(9, 16)):
            return out
        for did, r in list(self.manager.runners.items()):
            if not self.manager._is_running(did):
                continue
            marks = [x for x in (getattr(r, "last_tick_at", None), getattr(r, "created_at", None)) if x is not None]
            ref = max(marks) if marks else None
            if ref is None or now - ref < SILENT_AFTER:
                continue
            ago = int((now - ref).total_seconds() // 60)
            out.append(Problem(f"dep:{did}:silent", "silent", f"{r.deployment_name} isn't acting on prices",
                               f"Kite prices are arriving, but its strategy hasn't processed one in {ago} min. "
                               "It won't enter, adjust or exit until it does — check its Activity, or Restart it.",
                               deployment_id=did, data={"minutes": ago}))
        return out

    async def _errors(self, now: datetime) -> list[Problem]:
        rows = await self.pool.fetch(
            "SELECT e.deployment_id, d.deployment_name, count(*) AS n, max(e.created_at) AS last, "
            "(array_agg(e.message ORDER BY e.created_at DESC))[1] AS message "
            "FROM deployment_events e JOIN deployments d ON d.id = e.deployment_id "
            "WHERE e.event_type = 'strategy_error' AND e.created_at >= $1 AND d.status <> 'stopped' "
            "GROUP BY e.deployment_id, d.deployment_name", now - ERRORS_WITHIN)
        return [Problem(f"dep:{r['deployment_id']}:errors", "strategy_error", f"{r['deployment_name']}: strategy errors",
                        f"{r['n']} error(s) in the last 5 minutes, the latest at {_ist(r['last']):%H:%M}: "
                        f"{r['message'] or 'see its Activity'}",
                        deployment_id=str(r["deployment_id"]), data={"count": int(r["n"])})
                for r in rows]

    def _stale_legs(self, now: datetime, holiday: bool) -> list[Problem]:
        d = self.dispatcher
        connected = bool(getattr(d, "connected", False)) and not d.status.get("needs_login")
        if holiday or not connected or not in_market(now, STALE_LEGS_FROM) or self.manager.tick_stall(now):
            return []
        today_open = datetime.combine(_ist(now).date(), MARKET_OPEN, IST)
        prices = getattr(d, "last_prices", {}) or {}
        times = getattr(d, "last_price_times", {}) or {}
        out = []
        for did, r in list(self.manager.runners.items()):
            stale = []
            for tok, p in (getattr(r, "open_positions", {}) or {}).items():
                t = times.get(int(tok))
                if int(tok) not in prices or t is None or _ist(t) < today_open:
                    stale.append(p.get("symbol") or str(tok))
            if stale:
                out.append(Problem(f"dep:{did}:stale-legs", "stale_legs", f"{r.deployment_name}: {len(stale)} leg(s) haven't traded today",
                                   f"{', '.join(sorted(stale))} — no trade on the exchange since the open. Their price is the "
                                   "last traded one, and a strategy that waits for fresh prices (the strangle's stale-price "
                                   "guard) won't take profit, adjust or stop out on them.",
                                   severity="warning", deployment_id=did, data={"legs": sorted(stale)}))
        return out

    async def _export(self, now: datetime) -> tuple[list[Problem], Optional[dict]]:
        """(problems, a success to announce once)."""
        from .candles import BACKFILL_AT
        n = _ist(now)
        if not market_day(now) or n.time() < BACKFILL_AT:
            return [], None
        last = await queries.get_app_scratch(self.pool, "candle_backfill_last")
        today = n.date().isoformat()
        running = getattr(self.candles, "progress", None) if self.candles is not None else None
        if last and last.get("day") == today:
            if last.get("error"):
                return [Problem(f"export:{today}", "export_failed", "Option history export failed",
                                f"The 15:45 export of today's option candles stopped: {last['error']}. It retries every "
                                f"minute; {last.get('fetched', 0)} of {last.get('instruments', 0)} fetched so far. Kite "
                                "drops an expired contract's history, so this matters on expiry days.",
                                data=dict(last))], None
            return [], last
        if n.time() >= EXPORT_LATE and not running:
            return [Problem(f"export:{today}", "export_missing", "Option history export hasn't run",
                            "The 15:45 export of today's option candles hasn't run. Is the app's backfill loop alive? "
                            "Account → History can start it by hand.")], None
        return [], None

    # ── the check ──────────────────────────────────────────────────────

    async def check_once(self, now: Optional[datetime] = None) -> list[dict]:
        now = now or datetime.now(timezone.utc)
        self.checked_at = now
        from .routers.health import check_db_health
        if not await check_db_health(self.pool):
            # nowhere to keep issues: alert straight from here, every minute
            if self._db_down_alert is None or now - self._db_down_alert >= ALERT_EVERY:
                self._db_down_alert = now
                await self._alert("The database isn't answering",
                                  "The app can't read or record anything: no trades, no issues. Check the database.",
                                  {"key": "db", "kind": "db", "severity": "critical"})
            self.open = [{"key": "db", "kind": "db", "severity": "critical", "title": "The database isn't answering"}]
            return self.open
        if self._db_down_alert is not None:
            self._db_down_alert = None
            await self._alert("The database is back", "Reads and writes work again.", {"key": "db", "resolved": True})

        holiday = await self.holiday(now)
        problems: list[Problem] = []
        problems += self._kite(now, holiday)
        problems += self._runners(now, holiday)
        problems += await self._errors(now)
        problems += self._stale_legs(now, holiday)
        exp, success = await self._export(now)
        problems += exp
        if success is not None:
            await self._announce_export(success)
        await self._keep_day(now, success)
        problems += await self._day_check_problems(now)
        await self._sync(now, problems)
        return self.open

    # ── the end-of-day check ───────────────────────────────────────────

    async def _keep_day(self, now: datetime, export_done: Optional[dict]) -> None:
        from .repair import daycheck
        n = _ist(now)
        if not market_day(now):
            return
        try:
            if time(9, 0) <= n.time() < MARKET_OPEN:
                await daycheck.capture_states(self.pool, n.date(), "open")
            elif n.time() >= time(15, 35):
                await daycheck.capture_states(self.pool, n.date(), "close")
        except Exception:
            logger.exception("Keeping the day's strategy states failed")
        if export_done is not None and self.launcher is not None and not await self.pool.fetchval(
                "SELECT 1 FROM day_check_runs WHERE day = $1", n.date()):
            try:
                await self.launcher.start(n.date(), "auto")
            except Exception:
                logger.exception("Starting the day check failed")
        await self._announce_day_checks()

    async def _announce_day_checks(self) -> None:
        for run in await self.pool.fetch(
                "SELECT * FROM day_check_runs WHERE status = 'done' AND finished_at > now() - interval '1 day' ORDER BY id"):
            if not await queries.mark_health_alert_sent(self.pool, None, f"daycheck-done:{run['id']}"):
                continue
            rows = await self.pool.fetch(
                "SELECT c.status, d.deployment_name FROM day_checks c JOIN deployments d ON d.id = c.deployment_id "
                "WHERE c.run_id = $1 ORDER BY d.deployment_name", run["id"])
            await self._alert(f"Day check {run['day']:%d %b}: {summary(rows)}",
                              "; ".join(f"{r['deployment_name']}: {WORDS[r['status']]}" for r in rows if r["status"] != "ok")
                              or "Every deployment traded as its strategy would have and ended the day as expected.",
                              {"key": f"daycheck-done:{run['id']}", "kind": "daycheck_done", "severity": "info"})

    async def _day_check_problems(self, now: datetime) -> list[Problem]:
        out = []
        since = _ist(now).date() - timedelta(days=4)
        for run in await self.pool.fetch(
                "SELECT DISTINCT ON (day) * FROM day_check_runs WHERE day >= $1 ORDER BY day, id DESC", since):
            if run["status"] == "failed":
                out.append(Problem(f"daycheck:{run['day']}", "daycheck_failed", "The day check failed",
                                   f"The end-of-day check of {run['day']:%d %b} stopped: {run['error'] or 'no reason given'}. "
                                   "Run it again from the Repair page.", data={"run_id": run["id"]}))
        for r in await self.pool.fetch(
                "SELECT c.*, d.deployment_name FROM day_checks c JOIN deployments d ON d.id = c.deployment_id "
                "WHERE c.status = 'rule' AND c.day >= $1 ORDER BY c.day", since):
            held = {x["symbol"] for x in await self.pool.fetch(
                "SELECT symbol FROM positions WHERE deployment_id = $1 AND status = 'open'", r["deployment_id"])}
            for rule in r["rules"] or []:
                legs = [x for x in rule.get("legs") or [] if x in held]
                if rule.get("severity") == "critical" and legs:
                    out.append(Problem(f"eod:{r['deployment_id']}:{rule['rule']}", "end_of_day",
                                       f"{r['deployment_name']}: {rule['title'].lower()}",
                                       f"Found by the day check of {r['day']:%d %b}. {rule['detail']} Still open: "
                                       f"{', '.join(legs)}. Review it on the Repair page.",
                                       deployment_id=str(r["deployment_id"]), data={"day": r["day"].isoformat(), "legs": legs}))
        return out

    async def _announce_export(self, r: dict) -> None:
        if not await queries.mark_health_alert_sent(self.pool, None, f"export-ok:{r['day']}"):
            return
        await self._alert("Option history saved",
                          f"Today's export finished: {r.get('instruments', 0)} contract(s), {r.get('fetched', 0)} fetched "
                          f"from Kite, {r.get('rows', 0)} minute(s) stored in {r.get('seconds', 0)}s.",
                          {"key": f"export-ok:{r['day']}", "kind": "export_ok", "severity": "info"})

    async def _sync(self, now: datetime, problems: list[Problem]) -> None:
        seen = {p.key: p for p in problems}
        open_rows = {r["key"]: r for r in await self.pool.fetch("SELECT * FROM ops_issues WHERE resolved_at IS NULL")}
        for key, p in seen.items():
            dep = UUID(p.deployment_id) if p.deployment_id else None
            if dep is not None and not await self.pool.fetchval("SELECT 1 FROM deployments WHERE id = $1", dep):
                dep = None
            row = open_rows.get(key)
            if row is None:
                row = await self.pool.fetchrow(
                    "INSERT INTO ops_issues (key, kind, severity, deployment_id, title, detail, data, opened_at, last_seen_at) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $8) "
                    "ON CONFLICT (key) WHERE resolved_at IS NULL DO UPDATE SET last_seen_at = EXCLUDED.last_seen_at "
                    "RETURNING *", key, p.kind, p.severity, dep, p.title, p.detail, p.data, now)
            else:
                row = await self.pool.fetchrow(
                    "UPDATE ops_issues SET title = $2, detail = $3, data = $4, severity = $5, last_seen_at = $6 "
                    "WHERE id = $1 RETURNING *", row["id"], p.title, p.detail, p.data, p.severity, now)
            snoozed = row["snoozed_until"] is not None and row["snoozed_until"] > now
            due = row["last_alert_at"] is None or now - row["last_alert_at"] >= ALERT_EVERY
            if not snoozed and due:
                await self.pool.execute(
                    "UPDATE ops_issues SET last_alert_at = $2, alerts = alerts + 1 WHERE id = $1", row["id"], now)
                await self._alert(p.title, p.detail, {"key": key, "kind": p.kind, "severity": p.severity,
                                                      "issue_id": row["id"], "deployment_id": p.deployment_id})
        for key, row in open_rows.items():
            if key in seen:
                continue
            await self.pool.execute(
                "UPDATE ops_issues SET resolved_at = $2, resolved_by = 'cleared' WHERE id = $1", row["id"], now)
            if row["alerts"]:
                await self._alert(f"Fixed: {row['title']}",
                                  f"Cleared at {_ist(now):%H:%M}, open since {_ist(row['opened_at']):%H:%M}.",
                                  {"key": key, "kind": row["kind"], "severity": "info", "issue_id": row["id"],
                                   "resolved": True})
        await self.refresh_open()

    async def refresh_open(self) -> list[dict]:
        """Re-read the open issues (after a snooze or a dismiss, for /health)."""
        self.open = [issue(r) for r in await self.pool.fetch(
            "SELECT * FROM ops_issues WHERE resolved_at IS NULL ORDER BY severity, opened_at")]
        return self.open

    async def _alert(self, title: str, body: str, meta: dict) -> None:
        """A push to every subscribed phone (unless muted for the day) and
        a toast in every open tab."""
        if not await self.muted():
            await self.manager._push(title, body)
        if self.event_broadcaster is not None:
            try:
                await self.event_broadcaster.broadcast({
                    "deployment_id": meta.get("deployment_id"), "deployment_name": None, "strategy_name": None,
                    "event_type": "health_resolved" if meta.get("resolved") else "health_issue",
                    "message": f"{title} — {body}", "metadata": meta,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })
            except Exception:
                logger.exception("Broadcasting a health alert failed")

    async def loop(self, every_s: float = 60.0) -> None:
        while True:
            try:
                await self.check_once()
            except Exception:
                logger.exception("Health monitor check failed — continuing")
            await asyncio.sleep(every_s)


WORDS = {"ok": "as expected", "rule": "a rule of the day's end broken", "mismatch": "traded differently from its strategy",
         "ambiguous": "to review: a minute reads two ways", "cant_check": "couldn't be replayed", "error": "the check failed"}


def summary(rows) -> str:
    n = {}
    for r in rows:
        n[r["status"]] = n.get(r["status"], 0) + 1
    bits = [f"{n['ok']} as expected" if n.get("ok") else "",
            f"{n.get('mismatch', 0) + n.get('ambiguous', 0)} to review" if n.get("mismatch") or n.get("ambiguous") else "",
            f"{n['rule']} with a rule broken" if n.get("rule") else "",
            f"{n['cant_check']} not replayed" if n.get("cant_check") else "",
            f"{n['error']} failed" if n.get("error") else ""]
    return ", ".join(b for b in bits if b) or "nothing to check"


def issue(r) -> dict:
    """An ops_issues row for the API."""
    def iso(v):
        return v.isoformat() if v is not None else None
    return {"id": r["id"], "key": r["key"], "kind": r["kind"], "severity": r["severity"],
            "deployment_id": str(r["deployment_id"]) if r["deployment_id"] else None,
            "title": r["title"], "detail": r["detail"], "data": r["data"] or {},
            "opened_at": iso(r["opened_at"]), "last_seen_at": iso(r["last_seen_at"]),
            "resolved_at": iso(r["resolved_at"]), "resolved_by": r["resolved_by"],
            "snoozed_until": iso(r["snoozed_until"]), "last_alert_at": iso(r["last_alert_at"]), "alerts": r["alerts"]}


def snooze_until(choice: str, now: datetime) -> datetime:
    """'30m' / '2h' / 'today' (until 07:00 tomorrow, IST)."""
    if choice == "today":
        tomorrow = _ist(now).date() + timedelta(days=1)
        return datetime.combine(tomorrow, time(7, 0), IST)
    units = {"m": 60, "h": 3600}
    try:
        n, u = int(choice[:-1]), choice[-1]
        return now + timedelta(seconds=n * units[u])
    except (ValueError, KeyError, IndexError):
        raise ValueError("snooze is 30m, 2h (any number of m or h) or today")

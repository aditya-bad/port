"""
live_deploy — strategy health: is a deployment drifting from how it
normally trades?

Each deployment's last RECENT closed trades are compared with the trades
before them (at least MIN_BASE of them, so a young deployment is never
flagged on noise). A finding says what changed, by how much, and since
when. Shown on the Desk with the other things that need you
(GET /v2/strategy-health) and pushed once each (health_alerts_loop).

A trade is one entry with everything that joined it (app/geek/common.
trades_of: both legs of a straddle, its adjustments), at its booked P&L.
Read only: nothing here ever touches a deployment.
"""

import asyncio
import logging
from datetime import date, datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from .db import queries

logger = logging.getLogger("live_deploy.strategy_health")
IST = ZoneInfo("Asia/Kolkata")

RECENT, MIN_BASE = 10, 20
WIN_DROP = 0.25            # win rate down by 25 points or more
LOSS_GROWTH = 1.5          # the average loss half as big again
SLIP_GROWTH, SLIP_MIN = 2.0, 50.0   # slippage per trade doubled, and up by ₹50 at least
DD_GROWTH, DD_MIN_PCT = 1.25, 1.0   # drawdown 25% past the worst before, and over 1% of capital
FEWER = 0.3                # under 30% of the usual trades over the last 10 market days
FEWER_MIN_EXPECTED = 3     # ...when at least 3 were expected
WORST_DAYS = 3             # a record loss stays flagged for 3 days


def _rs(v: float) -> str:
    return f"{'−' if v < 0 else ''}₹{abs(v):,.0f}"


def _weekdays(a: date, b: date) -> int:
    """Market days (Mon–Fri) in [a, b)."""
    n, d = 0, a
    while d < b:
        n += d.weekday() < 5
        d += timedelta(days=1)
    return n


def assess(trades: list[dict], *, capital: float, active: bool, today: date) -> list[dict]:
    """Findings for one deployment. trades: closed trades oldest first,
    each {"opened": datetime, "closed": datetime, "pnl": float,
    "slippage": float}. Pure."""
    if len(trades) < MIN_BASE + RECENT:
        return []
    base, recent = trades[:-RECENT], trades[-RECENT:]
    since = recent[0]["opened"].astimezone(IST).date()
    sig = recent[-1]["closed"].isoformat()
    out = []

    def add(rule, title, body, data):
        out.append({"rule": rule, "key": f"{rule}:{sig}", "title": title, "body": body, "since": since.isoformat(), "data": data})

    wins = lambda ts: sum(1 for t in ts if t["pnl"] > 0) / len(ts)  # noqa: E731
    bw, rw = wins(base), wins(recent)
    if bw >= 0.4 and rw <= bw - WIN_DROP:
        add("win_rate", "Winning less often",
            f"Won {rw:.0%} of the last {RECENT} trades against {bw:.0%} before.",
            {"recent": round(rw * 100, 1), "usual": round(bw * 100, 1)})

    bl, rl = [t["pnl"] for t in base if t["pnl"] < 0], [t["pnl"] for t in recent if t["pnl"] < 0]
    if len(bl) >= 5 and len(rl) >= 3:
        ba, ra = sum(bl) / len(bl), sum(rl) / len(rl)
        if ra <= ba * LOSS_GROWTH:
            add("loss_size", "Losing more per loss",
                f"The last {len(rl)} losses averaged {_rs(ra)}, against {_rs(ba)} before ({ra / ba:.1f}×).",
                {"recent": round(ra, 2), "usual": round(ba, 2)})

    last = trades[-1]
    if last["pnl"] < 0 and last["pnl"] < min(t["pnl"] for t in trades[:-1]) \
            and (today - last["closed"].astimezone(IST).date()).days <= WORST_DAYS:
        prev = min(t["pnl"] for t in trades[:-1])
        out.append({"rule": "worst_loss", "key": f"worst_loss:{sig}", "title": "Its worst trade yet",
                    "body": f"Lost {_rs(last['pnl'])} on {last['closed'].astimezone(IST):%d %b}; the worst before was {_rs(prev)}.",
                    "since": last["closed"].astimezone(IST).date().isoformat(), "data": {"loss": round(last["pnl"], 2), "previous": round(prev, 2)}})

    bs = [t["slippage"] for t in base if t.get("slippage")]
    if len(bs) >= 10:
        ba, ra = sum(bs) / len(bs), sum(t.get("slippage") or 0 for t in recent) / len(recent)
        if ba > 0 and ra >= ba * SLIP_GROWTH and ra - ba >= SLIP_MIN:
            add("slippage", "Paying more in slippage",
                f"About {_rs(ra)} a trade over the last {RECENT}, against {_rs(ba)} before. Thinner books or bigger size?",
                {"recent": round(ra, 2), "usual": round(ba, 2)})

    eq = peak = 0.0
    worst_base = worst_now = 0.0
    for i, t in enumerate(trades):
        eq += t["pnl"]
        peak = max(peak, eq)
        if i < len(base):
            worst_base = max(worst_base, peak - eq)
    worst_now = peak - eq
    if worst_base > 0 and worst_now >= worst_base * DD_GROWTH and worst_now >= capital * DD_MIN_PCT / 100:
        add("drawdown", "Deepest drawdown yet",
            f"{_rs(-worst_now)} below its best, past the worst drawdown before ({_rs(-worst_base)}).",
            {"now": round(worst_now, 2), "worst_before": round(worst_base, 2)})

    if active:
        first = trades[0]["opened"].astimezone(IST).date()
        span = _weekdays(first, since)
        window_start = today - timedelta(days=14)
        while _weekdays(window_start, today) > 10:
            window_start += timedelta(days=1)
        if span >= 10:
            expected = len(base) / span * 10
            got = sum(1 for t in trades if t["opened"].astimezone(IST).date() >= window_start)
            if expected >= FEWER_MIN_EXPECTED and got < expected * FEWER:
                out.append({"rule": "fewer_trades", "key": f"fewer_trades:{today.isoformat()}:{got}", "title": "Trading less than usual",
                            "body": f"{got} trade{'s' if got != 1 else ''} in the last 10 market days, where it usually makes about {expected:.0f}.",
                            "since": window_start.isoformat(), "data": {"recent": got, "usual": round(expected, 1)}})
    return out


async def trades_for(pool, dep: dict) -> list[dict]:
    """A deployment's closed trades, oldest first."""
    from .geek import common
    ctx = common.Ctx(pool=pool, dep=dep)
    b = await common.book(ctx)
    pos = {p["id"]: p for p in b["positions"]}
    out = []
    for tr in common.trades_of(b):
        ps = [pos[i] for i in tr["ids"] if i in pos]
        if not ps or any(p["closed_at"] is None for p in ps):
            continue
        out.append({"opened": tr["t"], "closed": max(p["closed_at"] for p in ps),
                    "pnl": sum(float(p["realized_pnl"] or 0) for p in ps),
                    "slippage": sum(float(p.get("estimated_slippage") or 0) for p in ps)})
    out.sort(key=lambda t: t["closed"])
    return out


async def check_all(pool, now: Optional[datetime] = None) -> dict:
    """{deployment id: [findings]} for every deployment not stopped."""
    now = now or datetime.now(IST)
    out = {}
    for d in await queries.list_deployments(pool):
        d = dict(d)
        if d["status"] == "stopped":
            continue
        try:
            found = assess(await trades_for(pool, d), capital=float(d["initial_capital"]),
                           active=d["status"] == "active", today=now.astimezone(IST).date())
        except Exception:
            logger.exception("Health check of %s failed", d.get("deployment_name"))
            continue
        if found:
            out[str(d["id"])] = [{**f, "deployment_id": str(d["id"]), "name": d["deployment_name"]} for f in found]
    return out


async def push_new(pool, config: dict, findings: dict) -> int:
    """Push each finding not pushed before (by key), unless its
    deployment's notifications are off. Returns how many were sent."""
    from . import notifications
    sent = 0
    for dep_id, fs in findings.items():
        from uuid import UUID
        if not await queries.get_deployment_notifications_enabled(pool, UUID(dep_id)):
            continue
        for f in fs:
            if not await queries.mark_health_alert_sent(pool, UUID(dep_id), f"{dep_id}:{f['key']}"):
                continue
            await notifications.send_push_for_all(pool, config, f"{f['name']}: {f['title'].lower()}", f["body"])
            sent += 1
    return sent


async def push_weekly(pool, config: dict, now: datetime, health: dict) -> bool:
    """Saturday from 09:00: the week just ended in one push, once."""
    if now.weekday() != 5 or now.time() < time(9, 0):
        return False
    from . import notifications
    from .v2 import review
    week = review.monday(now.date())
    if not await queries.mark_health_alert_sent(pool, None, f"weekly:{week.isoformat()}"):
        return False
    title, body = review.push_text(await review.build(pool, week, now=now, health=health))
    await notifications.send_push_for_all(pool, config, title, body)
    return True


async def health_alerts_loop(app, every_s: float = 600.0) -> None:
    """Every 10 minutes: refresh the findings the API serves, and push the
    new ones (after the open, so the morning's first look is the Desk)."""
    while True:
        try:
            now = datetime.now(IST)
            app.state.strategy_health = {"checked_at": now.isoformat(), "deployments": await check_all(app.state.db_pool, now)}
            config = getattr(app.state, "kite_config", None)
            if now.time() >= time(9, 30) and config and notifications_ready(config):
                await push_new(app.state.db_pool, config, app.state.strategy_health["deployments"])
            if config and notifications_ready(config):
                await push_weekly(app.state.db_pool, config, now, app.state.strategy_health["deployments"])
        except Exception:
            logger.exception("Strategy health check failed — continuing")
        await asyncio.sleep(every_s)


def notifications_ready(config: dict) -> bool:
    from .notifications import is_push_configured
    return is_push_configured(config)

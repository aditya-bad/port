"""The weekly review (GET /v2/review): one market week across every
deployment, set against the weeks before it.

* totals and each deployment's week, with its usual week (the average of
  the 8 weeks before, counting only weeks it existed). P&L is what was
  booked that week, every leg closed, so it matches the day bars; trade
  counts and win rates are trades that finished;
* P&L day by day, the week's best and worst trades;
* rules that fired more than usual (fills grouped by the rule that
  triggered them, against the 8 weeks before);
* the strategy health findings, the week's journal notes and what's
  still open.

A trade is a closed episode (app/v2/book.py), so every number matches
the Leaderboard and Reports. Every P&L figure comes gross, after charges
and after charges and slippage (pnl, pnl_net, pnl_net_slip): the UI's
basis switch picks one. Read only.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

import asyncpg

from ..db import queries
from . import book

IST = ZoneInfo("Asia/Kolkata")
BASES = ("pnl", "pnl_net", "pnl_net_slip")
HISTORY_WEEKS = 8
UNUSUAL_MIN, UNUSUAL_X = 3, 2.0   # a rule fired 3+ times, and twice its usual or more


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def default_week(today: date, has_trades_this_week: bool) -> date:
    """This week once something closed in it, else the week before."""
    return monday(today) if has_trades_this_week else monday(today) - timedelta(days=7)


def _at(d: date) -> datetime:
    return datetime.combine(d, time(0), IST)


def _sum(rows: list) -> dict:
    return {b: round(sum(r[b] for r in rows), 2) for b in BASES}


def _week_of(iso: str) -> date:
    return monday(datetime.fromisoformat(iso).astimezone(IST).date())


def summarize(trades: list[dict], daily: list[dict], week: date) -> dict:
    """Pure: the week's totals, per deployment, best/worst, and the usual
    week from the 8 before. P&L is what was booked (daily: every leg
    closed that week, including legs of trades still open, so it matches
    the day bars); trades and wins count trades that finished.
    trades: book.list_trades rows; daily: book.list_daily rows."""
    weeks = [week - timedelta(days=7 * i) for i in range(HISTORY_WEEKS, 0, -1)]
    t_by: dict = defaultdict(list)
    for t in trades:
        t_by[_week_of(t["closed_at"])].append(t)
    d_by: dict = defaultdict(list)
    for d in daily:
        d_by[monday(date.fromisoformat(d["day"]))].append(d)
    first_seen: dict = {}
    for t in trades:
        w = _week_of(t["opened_at"])
        first_seen[t["deployment_id"]] = min(first_seen.get(t["deployment_id"], w), w)
    for d in daily:
        w = monday(date.fromisoformat(d["day"]))
        first_seen[d["deployment_id"]] = min(first_seen.get(d["deployment_id"], w), w)

    def stats(w, dep=None):
        ts = [t for t in t_by.get(w, []) if dep is None or t["deployment_id"] == dep]
        ds = [d for d in d_by.get(w, []) if dep is None or d["deployment_id"] == dep]
        return {**_sum(ds), "trades": len(ts), "wins": sum(1 for t in ts if t["pnl"] > 0)}

    this = t_by.get(week, [])
    deps = {}
    active = {t["deployment_id"] for t in this} | {d["deployment_id"] for d in d_by.get(week, [])}
    active |= {x["deployment_id"] for w in weeks for x in t_by.get(w, []) + d_by.get(w, [])}
    for dep_id in sorted(active):
        mine = [t for t in this if t["deployment_id"] == dep_id]
        lived = [w for w in weeks if w >= first_seen.get(dep_id, week)]
        hist = [stats(w, dep_id) for w in lived]
        usual = {b: round(sum(h[b] for h in hist) / len(hist), 2) for b in BASES} if hist else None
        if usual is not None:
            usual["trades"] = round(sum(h["trades"] for h in hist) / len(hist), 1)
        deps[dep_id] = {**stats(week, dep_id), "usual": usual, "weeks_known": len(hist),
                        "best": max(mine, key=lambda t: t["pnl"]) if mine else None,
                        "worst": min(mine, key=lambda t: t["pnl"]) if mine else None}
    history = [{"week": w.isoformat(), **stats(w)} for w in weeks]
    known = [h for h, w in zip(history, weeks) if t_by.get(w) or d_by.get(w)]
    usual_total = {b: round(sum(h[b] for h in known) / len(known), 2) for b in BASES} if known else None
    ranked = sorted(this, key=lambda t: t["pnl"])
    return {"totals": stats(week), "usual": usual_total, "history": history, "deployments": deps,
            "best": [t for t in ranked[::-1][:3] if t["pnl"] > 0], "worst": [t for t in ranked[:3] if t["pnl"] < 0]}


def unusual_rules(counts: list[dict], week: date) -> list[dict]:
    """Pure: rules that fired more than usual this week. counts: rows of
    {deployment_id, trigger, week (date), n}."""
    weeks = [week - timedelta(days=7 * i) for i in range(1, HISTORY_WEEKS + 1)]
    hist: dict = defaultdict(int)
    first: dict = {}
    for c in counts:
        k = (c["deployment_id"], c["trigger"])
        first[c["deployment_id"]] = min(first.get(c["deployment_id"], c["week"]), c["week"])
        if c["week"] in weeks:
            hist[k] += c["n"]
    out = []
    for c in counts:
        if c["week"] != week or c["n"] < UNUSUAL_MIN:
            continue
        lived = [w for w in weeks if w >= first.get(c["deployment_id"], week)]
        if not lived:
            continue
        usual = hist[(c["deployment_id"], c["trigger"])] / len(lived)
        if usual == 0 or c["n"] >= usual * UNUSUAL_X:
            out.append({"deployment_id": c["deployment_id"], "trigger": c["trigger"], "n": c["n"], "usual": round(usual, 1)})
    return sorted(out, key=lambda x: -(x["n"] / x["usual"] if x["usual"] else 99))


def vix_week(vday: list[dict], week: date) -> Optional[dict]:
    """Pure: VIX through the week and the 8 before, from day candles
    [{t, o, h, l, c}]: {open, close, high, low, change, days, history}."""
    by: dict = defaultdict(list)
    for r in sorted(vday, key=lambda x: x["t"]):
        by[monday(r["t"].astimezone(IST).date())].append(r)
    this = by.get(week)
    if not this:
        return None
    prev_close = None
    weeks = [week - timedelta(days=7 * i) for i in range(HISTORY_WEEKS, -1, -1)]
    hist = []
    for w in weeks:
        rs = by.get(w)
        if rs:
            hist.append({"week": w.isoformat(), "open": rs[0]["o"], "close": rs[-1]["c"], "high": max(r["h"] for r in rs),
                         "change": round(rs[-1]["c"] - (prev_close if prev_close is not None else rs[0]["o"]), 2)})
            prev_close = rs[-1]["c"]
    before = [r for w2, rs in by.items() if w2 < week for r in rs]
    start = before[-1]["c"] if before else this[0]["o"]   # last week's close, else this week's open
    days, last = [], start
    for r in this:
        days.append({"day": r["t"].astimezone(IST).date().isoformat(), "close": r["c"], "change": round(r["c"] - last, 2)})
        last = r["c"]
    return {"open": round(start, 2), "close": this[-1]["c"], "high": max(r["h"] for r in this), "low": min(r["l"] for r in this),
            "change": round(this[-1]["c"] - start, 2), "days": days, "history": hist}


def by_side(deps: list[dict], sides: dict) -> dict:
    """Pure: the week's P&L added up by whether each deployment sells or
    buys premium (net, over its recent opening fills)."""
    out = {}
    for d in deps:
        k = sides.get(d["deployment_id"])
        if k is None:
            continue
        g = out.setdefault(k, {"n": 0, **{b: 0.0 for b in BASES}})
        g["n"] += 1
        for b in BASES:
            g[b] = round(g[b] + d[b], 2)
    return out


async def premium_sides(pool: asyncpg.Pool, start: datetime) -> dict:
    """{deployment id: 'sold' | 'bought'}: net premium of the option legs
    each opened since `start` — what it took in against what it paid."""
    rows = await pool.fetch(
        """
        SELECT l.deployment_id, SUM(CASE WHEN p.side = 'short' THEN 1 ELSE -1 END * l.price * l.qty)::float8 AS net
        FROM position_lots l JOIN positions p ON p.id = l.position_id
        WHERE l.executed_at >= $1 AND (p.symbol LIKE '%CE' OR p.symbol LIKE '%PE')
          AND ((p.side = 'short' AND l.action = 'sell') OR (p.side = 'long' AND l.action = 'buy'))
        GROUP BY 1
        """, start)
    return {str(r["deployment_id"]): "sold" if r["net"] > 0 else "bought" for r in rows if r["net"]}


async def _rule_counts(pool: asyncpg.Pool, start: datetime, end: datetime) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT l.deployment_id, COALESCE(l.metadata->>'trigger', l.reason, 'trade') AS trigger,
               date_trunc('week', l.executed_at AT TIME ZONE 'Asia/Kolkata')::date AS week,
               COUNT(DISTINCT date_trunc('minute', l.executed_at)) AS n
        FROM position_lots l
        WHERE l.executed_at >= $1 AND l.executed_at < $2
        GROUP BY 1, 2, 3
        """,
        start, end,
    )
    return [{"deployment_id": str(r["deployment_id"]), "trigger": r["trigger"], "week": r["week"], "n": r["n"]} for r in rows]


async def build(pool: asyncpg.Pool, week: Optional[date] = None, now: Optional[datetime] = None,
                health: Optional[dict] = None) -> dict[str, Any]:
    now = (now or datetime.now(IST)).astimezone(IST)
    today = now.date()
    trades = await book.list_trades(pool)
    if week is None:
        week = default_week(today, any(_week_of(t["closed_at"]) == monday(today) for t in trades))
    week = monday(week)
    lo, hi = week - timedelta(days=7 * HISTORY_WEEKS), week + timedelta(days=7)
    daily_all = [d for d in await book.list_daily(pool, _at(lo)) if date.fromisoformat(d["day"]) < hi]
    s = summarize([t for t in trades if _at(lo) <= datetime.fromisoformat(t["closed_at"]) < _at(hi)], daily_all, week)
    daily = [d for d in daily_all if date.fromisoformat(d["day"]) >= week]
    days = []
    for i in range(7):
        d = week + timedelta(days=i)
        rows = [x for x in daily if x["day"] == d.isoformat()]
        if i < 5 or rows:
            days.append({"day": d.isoformat(), **_sum(rows), "closes": sum(x["closes"] for x in rows)})
    deps = {str(r["id"]): dict(r) for r in await queries.list_deployments(pool)}
    rules = unusual_rules(await _rule_counts(pool, _at(week - timedelta(days=7 * HISTORY_WEEKS)), _at(week + timedelta(days=7))), week)
    notes = await queries.list_journal(pool, None, week, week + timedelta(days=6), None, None, None, False, 200)
    open_by = defaultdict(int)
    for r in await pool.fetch("SELECT deployment_id, COUNT(*) AS n FROM positions WHERE status = 'open' GROUP BY 1"):
        open_by[str(r["deployment_id"])] = r["n"]
    first_trade = min((_week_of(t["closed_at"]) for t in trades), default=week)
    # India VIX through the week (app/vix.py: from Kite, in memory)
    vix, vix_error = None, None
    from .. import vix as vixmod
    from ..options import NoKiteSession
    svc = vixmod.state.get("service")
    if svc is not None:
        try:
            vix = vix_week(await svc.vix(lo - timedelta(days=7), min(hi, today + timedelta(days=1)), "day", now), week)
        except NoKiteSession:
            vix_error = "Kite isn't logged in, so VIX can't be fetched."
        except Exception as e:   # the review never fails for want of VIX
            vix_error = f"VIX unavailable ({e})"
    sides = await premium_sides(pool, _at(lo))

    def name(dep_id):
        d = deps.get(dep_id)
        return d["deployment_name"] if d else "Deleted deployment"

    rows = []
    for dep_id, x in s["deployments"].items():
        d = deps.get(dep_id) or {}
        rows.append({"deployment_id": dep_id, "name": name(dep_id), "strategy": d.get("strategy_name"), "status": d.get("status"),
                     "side": sides.get(dep_id),
                     "tags": list(d.get("tags") or []), "open_positions": open_by.get(dep_id, 0), **x})
    for t in s["best"] + s["worst"]:
        t["name"] = name(t["deployment_id"])
    for r in rules:
        r["name"] = name(r["deployment_id"])
    repairs = await book.list_repairs(pool, between=(week, week + timedelta(days=7)))
    for r in repairs:
        r["name"] = name(r["deployment_id"])
    return {
        "week": week.isoformat(), "week_end": (week + timedelta(days=4)).isoformat(),
        "complete": today >= week + timedelta(days=5), "today": today.isoformat(),
        "prev": (week - timedelta(days=7)).isoformat() if week > first_trade else None,
        "next": (week + timedelta(days=7)).isoformat() if week + timedelta(days=7) <= monday(today) else None,
        "totals": s["totals"], "usual": s["usual"], "history": s["history"], "days": days,
        "deployments": sorted(rows, key=lambda r: -abs(r["pnl"])), "best": s["best"], "worst": s["worst"],
        "rules": rules, "repairs": repairs,
        "vix": vix, "vix_error": vix_error, "by_side": by_side(rows, sides),
        "health": [f for fs in (health or {}).values() for f in fs],
        "notes": [{"id": str(n["id"]), "day": n["day"].isoformat(), "body": n["body"], "tags": list(n["tags"] or []),
                   "deployment_name": n["deployment_name"], "standalone": bool(n["standalone"])} for n in notes],
    }


def push_text(r: dict) -> tuple[str, str]:
    """The Saturday push: the week in one line, after charges."""
    t = r["totals"]
    a, b = date.fromisoformat(r["week"]), date.fromisoformat(r["week_end"])
    span = f"{a.day}–{b.day} {b:%b}" if a.month == b.month else f"{a.day} {a:%b}–{b.day} {b:%b}"
    pnl = t["pnl_net"]
    money = f"{'−' if pnl < 0 else '+'}₹{abs(pnl):,.0f}"
    if not t["trades"]:
        return f"Your week, {span}", "No trades closed this week."
    body = f"{money} after charges · {t['trades']} trade{'s' if t['trades'] != 1 else ''} · {round(t['wins'] / t['trades'] * 100)}% won"
    if r["deployments"]:
        top = max(r["deployments"], key=lambda d: d["pnl_net"])
        if top["pnl_net"] > 0:
            body += f". Best: {top['name']} +₹{top['pnl_net']:,.0f}"
    if r["health"]:
        body += f". {len(r['health'])} health alert{'s' if len(r['health']) != 1 else ''} to look at"
    return f"Your week, {span}", body + "."

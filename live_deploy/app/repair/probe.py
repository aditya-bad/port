"""
live_deploy — what Kite actually answers for a repair, asked directly.

For every deployment the repair would scan: its index and each open leg,
looked up by name in Kite's instrument master, then Kite's 1-minute
history for the day (with and without open interest) and for the week
before. Printed as asked and as answered, errors and all, so "no
candles" can be told apart from a wrong token, a wrong day or a refusal.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Callable, Optional

from .market import INDEX_LABELS, IST, SESSION_CLOSE, SESSION_OPEN, naive_ist


def _ask(gate, token: int, start: datetime, end: datetime, oi: bool) -> tuple[Optional[list], Optional[str]]:
    try:
        kw = {"oi": True} if oi else {}
        return list(gate.historical_data(token, start, end, "minute", **kw) or []), None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def _said(rows: Optional[list], err: Optional[str]) -> str:
    if err:
        return f"ERROR {err}"
    if not rows:
        return "0 candles"
    a, b = rows[0], rows[-1]
    return (f"{len(rows)} candles, {naive_ist(a['date']):%d %b %H:%M} close {a['close']}"
            f" … {naive_ist(b['date']):%d %b %H:%M} close {b['close']}")


async def probe(pool, gate, day, lookup: Callable, out: Callable = print) -> list[dict]:
    """Ask Kite for every index and open leg of each active/paused
    deployment on `day`; print and return what it said."""
    open_, close = datetime.combine(day, SESSION_OPEN), datetime.combine(day, SESSION_CLOSE)
    week = datetime.combine(day - timedelta(days=7), SESSION_OPEN)
    before = datetime.combine(day, time(0)) - timedelta(seconds=1)
    out(f"KITE PROBE · {day:%a %d %b %Y} · session {open_:%H:%M}–{close:%H:%M}, and the week before from {week:%d %b}")
    out(f"   now: {datetime.now(IST):%a %d %b %Y %H:%M} IST")
    try:
        me = gate.call(gate.kite.profile)
        out(f"   Kite session: {me.get('user_id')} ({me.get('user_name')})")
    except Exception as e:
        out(f"   Kite session: ERROR {type(e).__name__}: {e}")
    report = []
    deps = await pool.fetch("SELECT * FROM deployments WHERE status IN ('active', 'paused') ORDER BY deployment_name")
    seen: dict[int, dict] = {}
    for d in deps:
        cfg = d["config"] if isinstance(d["config"], dict) else {}
        legs = await pool.fetch(
            "SELECT symbol, instrument_token FROM positions WHERE deployment_id = $1 AND status = 'open' ORDER BY symbol", d["id"])
        out(f"\n━━ {d['deployment_name']} ({d['strategy_name']})")
        items = [(int(t), INDEX_LABELS.get(int(t), str(t))) for t in cfg.get("instrument_tokens") or []]
        items += [(int(l["instrument_token"]), l["symbol"]) for l in legs]
        if not items:
            out("   nothing to ask: no index in its config, no open legs")
        for tok, sym in items:
            if tok not in seen:
                row = None
                master = ""
                if tok not in INDEX_LABELS:
                    try:
                        row = lookup(sym)
                        master = (f"master: {row.get('exchange', '?')}:{row['tradingsymbol']} token {row['instrument_token']}"
                                  + ("" if int(row["instrument_token"]) == tok else "  ← DIFFERS from the app's token")
                                  if row else "master: NOT in Kite's instrument list")
                    except Exception as e:
                        master = f"master: ERROR {type(e).__name__}: {e}"
                today_oi = _ask(gate, tok, open_, close, True)
                today = _ask(gate, tok, open_, close, False)
                prior = _ask(gate, tok, week, before, False)
                try:
                    daily = list(gate.historical_data(tok, day - timedelta(days=10), day, "day") or []), None
                except Exception as e:
                    daily = None, f"{type(e).__name__}: {e}"
                seen[tok] = {"token": tok, "symbol": sym, "master": master, "today_oi": today_oi, "today": today,
                             "prior": prior, "daily": daily}
                if row and int(row["instrument_token"]) != tok:
                    seen[tok]["master_today"] = _ask(gate, int(row["instrument_token"]), open_, close, False)
            r = seen[tok]
            report.append({**r, "deployment": d["deployment_name"]})
            out(f"   {sym}  (token {tok})")
            if r["master"]:
                out(f"      {r['master']}")
            out(f"      today, with OI:    {_said(*r['today_oi'])}")
            out(f"      today, without OI: {_said(*r['today'])}")
            if "master_today" in r:
                out(f"      today, master token: {_said(*r['master_today'])}")
            out(f"      week before:       {_said(*r['prior'])}")
            out(f"      daily, 10 days:    {_said(*r['daily'])}")
    return report

"""
live_deploy — India VIX: the 30-day implied volatility of Nifty options,
for Geek mode's volatility views. Never read by a strategy; nothing
stored.

* The instrument is looked up by name in Kite's NSE instrument list
  ("INDIA VIX"), never hard-coded, then subscribed like any index, so
  its live value arrives with the ticks and is stamped on every fill's
  market context (DeploymentRunner.market_context, under "vix").
* A spike — VIX 10% or more above the last session's close, in market
  hours — is checked every minute, shown on the Desk with what holds sold
  premium, and pushed once a day (spike_loop).
* History — VIX's, and each traded index's daily candles — comes from
  Kite's historical API when a view asks, at the interval it needs. Kite
  keeps index history indefinitely, so nothing is stored: finished days
  are kept in memory for the life of the process, today's for a minute,
  and each request spans as many days as Kite allows for its interval.

VIX is quoted annualised over calendar days, so the move it implies over
one day is VIX / √365 per cent (expected_move_pct); it's built from Nifty
options, so for BankNifty, Sensex and Bankex it is a stand-in only.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time as _time
from collections import OrderedDict
from datetime import date, datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from .options import NoKiteSession, OptionsResolver

logger = logging.getLogger("live_deploy.vix")

IST = ZoneInfo("Asia/Kolkata")
EXCHANGE, SYMBOL = "NSE", "INDIA VIX"
# Days of history Kite serves in one request, by interval (kept under its limits).
SPAN = {"minute": 30, "3minute": 90, "5minute": 90, "10minute": 90, "15minute": 180, "30minute": 180,
        "60minute": 360, "day": 1800}
LIVE_TTL_S = 60            # today's candles are asked for again after a minute
CACHE_DAYS = 20000         # finished (token, interval, day) entries kept in memory

# A spike: VIX this far above the last session's close, in per cent.
SPIKE_PCT = 10.0

# The resolved instrument, for the runner's fill context and the API;
# the service; the latest spike check.
state: dict = {"token": None}


async def exposed(pool) -> list[dict]:
    """Deployments not stopped holding open legs that sold premium, net:
    the ones a VIX spike hurts. [{id, name, legs, premium}] most exposed first."""
    rows = await pool.fetch(
        """
        SELECT d.id, d.deployment_name AS name, COUNT(*) AS legs,
               SUM(CASE WHEN p.side = 'short' THEN 1 ELSE -1 END * p.avg_entry_price * p.qty)::float8 AS premium
        FROM positions p JOIN deployments d ON d.id = p.deployment_id
        WHERE p.status = 'open' AND d.status <> 'stopped' AND (p.symbol LIKE '%CE' OR p.symbol LIKE '%PE')
        GROUP BY d.id, d.deployment_name
        HAVING SUM(CASE WHEN p.side = 'short' THEN 1 ELSE -1 END * p.avg_entry_price * p.qty) > 0
        ORDER BY 4 DESC
        """)
    return [{"id": str(r["id"]), "name": r["name"], "legs": r["legs"], "premium": round(r["premium"], 2)} for r in rows]


async def push_spike_once(pool, config: dict, s: dict, now: datetime) -> bool:
    """Push a spike, once a day (strategy_health_sent remembers it)."""
    from . import notifications
    from .db import queries
    if not notifications.is_push_configured(config):
        return False
    if not await queries.mark_health_alert_sent(pool, None, f"vixspike:{now.astimezone(IST).date().isoformat()}"):
        return False
    title, body = spike_text(s)
    await notifications.send_push_for_all(pool, config, title, body)
    return True


async def exposure(pool, dispatcher, svc=None, now: Optional[datetime] = None) -> dict:
    """What open option legs gain or lose if implied volatility — roughly,
    VIX — rises one point now: each leg's vega at its current price, signed
    for side and quantity, per deployment and in total. A leg whose
    strike, expiry, price or index level isn't known is counted, not
    guessed. {total, deployments: [{id, name, legs, per_point}], legs, missing}."""
    from collections import defaultdict as _dd

    from .db import queries
    from .geek.optionmath import implied_vol, vega, years_to
    from .geek.volatility import expiry_of
    from .candles import parse_contract
    now = (now or datetime.now(IST)).astimezone(IST)
    rows = await pool.fetch(
        """
        SELECT p.id, p.symbol, p.instrument_token, p.side, p.qty::float8 AS qty, p.avg_entry_price::float8 AS entry,
               p.metadata, d.id AS dep_id, d.deployment_name AS name, d.config,
               (SELECT l.metadata FROM position_lots l WHERE l.position_id = p.id ORDER BY l.executed_at LIMIT 1) AS first_meta,
               k.expiry AS contract_expiry
        FROM positions p JOIN deployments d ON d.id = p.deployment_id
        LEFT JOIN contracts k ON k.instrument_token = p.instrument_token
        WHERE p.status = 'open' AND d.status <> 'stopped' AND (p.symbol LIKE '%CE' OR p.symbol LIKE '%PE')
        """)
    last = getattr(dispatcher, "last_prices", {}) or {}
    marks = await queries.latest_marks_at(pool, [r["id"] for r in rows])
    spots: dict = {}

    async def spot_of(tok):
        if tok in spots:
            return spots[tok]
        v = last.get(tok)
        if v is None and svc is not None:
            try:
                c = await svc.candles(tok, now.date() - timedelta(days=5), now.date(), "15minute", now)
                v = c[-1]["c"] if c else None
            except Exception:
                v = None
        spots[tok] = v
        return v

    per = _dd(lambda: {"legs": 0, "per_point": 0.0})
    names, missing, n = {}, 0, 0
    for r in rows:
        n += 1
        c = parse_contract(r["symbol"])
        toks = ((r["config"] or {}).get("instrument_tokens") or [])
        exp = expiry_of(r["first_meta"] or r["metadata"] or {}, r["contract_expiry"], r["symbol"])
        px = last.get(r["instrument_token"])
        if px is None and r["id"] in marks:
            px = marks[r["id"]][0]
        s0 = await spot_of(int(toks[0])) if toks else None
        if not c or not c.get("strike") or exp is None or not px or not s0 or not r["qty"]:
            missing += 1
            continue
        t = years_to(exp, now)
        iv = implied_vol(c["instrument_type"], float(px), float(s0), c["strike"], t)
        if iv is None:
            missing += 1
            continue
        pp = vega(float(s0), c["strike"], t, iv) / 100 * r["qty"] * (-1 if r["side"] == "short" else 1)
        dep = str(r["dep_id"])
        names[dep] = r["name"]
        per[dep]["legs"] += 1
        per[dep]["per_point"] += pp
    deps = sorted(({"id": k, "name": names[k], "legs": v["legs"], "per_point": round(v["per_point"], 2)} for k, v in per.items()),
                  key=lambda x: x["per_point"])
    return {"total": round(sum(d["per_point"] for d in deps), 2), "deployments": deps, "legs": n, "missing": missing,
            "at": now.isoformat()}


def spike_text(s: dict) -> tuple[str, str]:
    """The push for a spike: title and body."""
    title = f"VIX up {s['change_pct']:.0f}% today: {s['prev_close']:.2f} → {s['vix']:.2f}"
    ex = s.get("exposed") or []
    if not ex:
        return title, "Nothing open is selling premium."
    names = ", ".join(e["name"] for e in ex[:3]) + (f" and {len(ex) - 3} more" if len(ex) > 3 else "")
    body = f"{len(ex)} deployment{'s' if len(ex) != 1 else ''} hold sold premium that loses as VIX rises: {names}."
    pp = s.get("per_point")
    if pp:
        body += f" Each further point costs about ₹{abs(pp):,.0f}." if pp < 0 else f" Each further point adds about ₹{pp:,.0f}."
    return title, body


def expected_move_pct(vix: float, days: float = 1.0) -> float:
    """The one-standard-deviation move VIX implies over `days` calendar
    days, in per cent of the index."""
    return vix / math.sqrt(365.0) * math.sqrt(days)


def windows(days: list[date], interval: str) -> list[tuple[date, date]]:
    """Days to fetch -> [first, last] runs Kite serves in one request."""
    span, out = SPAN.get(interval, 30), []
    for d in sorted(days):
        if out and (d - out[-1][0]).days < span:
            out[-1] = (out[-1][0], d)
        else:
            out.append((d, d))
    return out


class VixService:
    def __init__(self, pool, dispatcher, broadcaster=None):
        self.pool = pool
        self.dispatcher = dispatcher
        self.token: Optional[int] = state.get("token")
        self.last_error: Optional[str] = None
        self.kite_requests = 0
        # (token, interval, day) -> (fetched at, [candles]); finished days never expire
        self._days: OrderedDict = OrderedDict()
        self._lock = asyncio.Lock()   # one history request at a time, on top of the resolver's throttle

    # ── The instrument ───────────────────────────────────────────────

    async def resolve(self) -> Optional[int]:
        """Look INDIA VIX up in Kite's NSE instruments and subscribe it."""
        if self.token is not None:
            return self.token
        cache = await OptionsResolver(self.dispatcher)._ensure_instruments(EXCHANGE)
        row = next((r for r in cache["data"] if r.get("tradingsymbol") == SYMBOL), None)
        if row is None:
            self.last_error = f"{SYMBOL} isn't in Kite's {EXCHANGE} instrument list"
            logger.warning(self.last_error)
            return None
        self.token = state["token"] = int(row["instrument_token"])
        self.last_error = None
        try:
            self.dispatcher.add_instruments([{"symbol": SYMBOL, "instrument_token": self.token}])
        except Exception:
            logger.exception("Subscribing %s failed — history still works", SYMBOL)
        logger.info("%s is instrument %s", SYMBOL, self.token)
        return self.token

    def last(self) -> Optional[dict]:
        """The latest tick: {price, at}, or None."""
        if self.token is None:
            return None
        px = (getattr(self.dispatcher, "last_prices", {}) or {}).get(self.token)
        at = (getattr(self.dispatcher, "last_price_times", {}) or {}).get(self.token)
        return None if px is None else {"price": float(px), "at": at.isoformat() if at else None}

    # ── History, from Kite, kept in memory ───────────────────────────

    async def candles(self, token: int, start: date, end: date, interval: str = "day",
                      now: Optional[datetime] = None) -> list[dict]:
        """Kite's candles for [start, end] (IST days), oldest first:
        [{t: datetime, o, h, l, c}]. Raises NoKiteSession when Kite isn't
        logged in and something isn't in memory yet."""
        now = (now or datetime.now(IST)).astimezone(IST)
        today, mono = now.date(), _time.monotonic()
        want = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        want = [d for d in want if d.weekday() < 5 and d <= today]
        missing = []
        for d in want:
            hit = self._days.get((token, interval, d))
            if hit is None or (d >= today and mono - hit[0] > LIVE_TTL_S):
                missing.append(d)
        if missing:
            async with self._lock:
                for a, b in windows(missing, interval):
                    self.kite_requests += 1
                    raw = await OptionsResolver(self.dispatcher).get_historical_candles(
                        token, datetime.combine(a, time(0)), datetime.combine(b, time(23, 59)), interval)
                    got: dict = {}
                    for c in raw or []:
                        ts = c["date"]
                        if not isinstance(ts, datetime):
                            ts = datetime.combine(ts, time(0))
                        ts = ts.replace(tzinfo=IST) if ts.tzinfo is None else ts.astimezone(IST)
                        got.setdefault(ts.date(), []).append({"t": ts, "o": float(c["open"]), "h": float(c["high"]),
                                                              "l": float(c["low"]), "c": float(c["close"])})
                    d = a
                    while d <= b:
                        if d.weekday() < 5:
                            self._days[(token, interval, d)] = (_time.monotonic(), got.get(d, []))   # a holiday: []
                            self._days.move_to_end((token, interval, d))
                        d += timedelta(days=1)
                    while len(self._days) > CACHE_DAYS:
                        self._days.popitem(last=False)
        out = []
        for d in want:
            hit = self._days.get((token, interval, d))
            if hit:
                out.extend(hit[1])
        return out

    async def vix(self, start: date, end: date, interval: str = "day", now: Optional[datetime] = None) -> list[dict]:
        token = await self.resolve()
        return [] if token is None else await self.candles(token, start, end, interval, now)

    # ── Spikes ───────────────────────────────────────────────────────

    async def spike(self, now: Optional[datetime] = None) -> Optional[dict]:
        """During market hours: VIX now against the last session's close.
        {vix, prev_close, change, change_pct, spiking, at}, or None when
        either isn't known. Now is the live tick, else Kite's latest
        5-minute candle."""
        now = (now or datetime.now(IST)).astimezone(IST)
        if self.token is None or now.weekday() >= 5 or not (time(9, 15) <= now.time() < time(15, 30)):
            return None
        days = await self.candles(self.token, now.date() - timedelta(days=10), now.date() - timedelta(days=1), "day", now)
        if not days:
            return None
        prev = days[-1]["c"]
        live = self.last()
        cur = live["price"] if live else None
        if cur is None:
            today = await self.candles(self.token, now.date(), now.date(), "5minute", now)
            cur = today[-1]["c"] if today else None
        if cur is None or not prev:
            return None
        pct = (cur / prev - 1) * 100
        return {"vix": round(cur, 2), "prev_close": round(prev, 2), "change": round(cur - prev, 2),
                "change_pct": round(pct, 1), "spiking": pct >= SPIKE_PCT, "at": now.isoformat()}

    async def spike_loop(self, push=None, every_s: float = 60.0) -> None:
        """Every minute in market hours: check for a spike, keep it for the
        API and the Desk, and push it once a day with what's exposed."""
        while True:
            try:
                now = datetime.now(IST)
                s = await self.spike(now)
                if s and s["spiking"]:
                    s["exposed"] = await exposed(self.pool)
                    try:
                        s["per_point"] = (await exposure(self.pool, self.dispatcher, self, now))["total"]
                    except Exception:
                        logger.exception("VIX exposure for the spike failed")
                state["spike"] = s
                if s and s["spiking"] and push is not None:
                    await push(s, now)
            except NoKiteSession:
                pass
            except Exception:
                logger.exception("VIX spike check failed — continuing")
            await asyncio.sleep(every_s)

    async def run_loop(self, every_s: float = 60.0) -> None:
        """Once Kite is logged in, find the instrument and subscribe it, so
        its live value arrives with the ticks. Nothing else runs."""
        while self.token is None:
            try:
                if getattr(self.dispatcher, "access_token", None):
                    await self.resolve()
            except NoKiteSession:
                pass
            except Exception as e:
                self.last_error = str(e)
                logger.exception("Looking up %s failed — retrying in a minute", SYMBOL)
            if self.token is None:
                await asyncio.sleep(every_s)

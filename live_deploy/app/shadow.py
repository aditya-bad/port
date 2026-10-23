"""
live_deploy — shadow mode: for every paper fill, would Kite have taken
the order, and at what price?

Every trade here is paper, filled at the last traded price. Live, the
same order would go to Kite as a LIMIT order at that price plus a small
buffer (0.5% by default, config shadow.limit_buffer_pct), on the price
step, re-priced if it hasn't filled. This module works out, right after
each paper fill and in the background, what that order would have met:

  lot       Kite takes whole lots only.
  freeze    the exchange's largest quantity per order (the freeze
            limit): more goes as several orders. Limits come from the
            daily NSE file (the last good copy is kept), overridden by
            config shadow.freeze_qty; none known means "couldn't check".
  band      today's price band (circuit limits) from a quote: a limit
            beyond it is capped at the band edge, as live would.
  book      the 5-level order book at the fill: what fills at or better
            than the limit, and at what average price.
  follow    whatever didn't fill at once: did the price come to the
            limit in the next 60 s? If not, it wouldn't have filled.

The verdict per fill is stored in shadow_checks (migration 0037).
Nothing here ever changes, delays or blocks a fill: the runner hands
the fill over (submit) and carries on; a check that fails is stored as
"unknown". Margin, order-rate and leg-by-leg checks come later.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import math
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("live_deploy.shadow")

IST = ZoneInfo("Asia/Kolkata")
BUFFER_PCT = 0.5            # the limit's distance from the price, in %
FOLLOW_S = 60               # how long a resting limit is given to fill
WORSE_FROM = 1.0            # ₹: a shortfall under this is "about the paper price"
DEFAULT_TICK = 0.05
QUOTE_TIMEOUT_S = 5.0
QUOTE_BATCH_S = 2.0         # fills' quotes are asked for together, once per this
MAX_BOOK_AGE = timedelta(seconds=30)
NSE_FREEZE_URL = "https://nsearchives.nseindia.com/content/fo/qtyfreeze.csv"

RULES = {"lot": "Lot size", "freeze": "Freeze limit", "band": "Price band", "book": "Order book", "follow": "Within 60 s"}


def _check(rule: str, status: str, detail: str) -> dict:
    return {"rule": rule, "status": status, "detail": detail}


def _r(x: Optional[float], n: int = 2) -> Optional[float]:
    return None if x is None else round(float(x), n)


# ── the checks (pure) ───────────────────────────────────────────────────

def round_tick(p: float, tick: float, up: bool) -> float:
    n = p / tick
    n = math.ceil(n - 1e-9) if up else math.floor(n + 1e-9)
    return round(n * tick, 4)


def limit_for(action: str, ltp: float, tick: float, buffer_pct: float) -> float:
    """The limit live would send: the price plus the buffer for a buy,
    minus it for a sell, on the price step and never past the buffer."""
    if action == "buy":
        return max(round_tick(ltp * (1 + buffer_pct / 100), tick, up=False), round_tick(ltp, tick, up=True))
    return max(tick, min(round_tick(ltp * (1 - buffer_pct / 100), tick, up=True), round_tick(ltp, tick, up=False)))


def check_lot(qty: float, lot: Optional[int]) -> dict:
    if not lot:
        return _check("lot", "unknown", "No lot size known for this contract.")
    lots = qty / lot
    if abs(lots - round(lots)) > 1e-9:
        return _check("lot", "reject", f"Kite takes whole lots only: {qty:g} isn't a multiple of the lot size {lot}.")
    return _check("lot", "ok", f"{round(lots)} × {lot}")


def check_freeze(qty: float, freeze: Optional[int], source: Optional[str] = None) -> tuple[dict, Optional[int]]:
    if not freeze:
        return _check("freeze", "unknown", "No freeze limit known for this underlying (config shadow.freeze_qty)."), None
    orders = math.ceil(qty / freeze - 1e-9)
    src = f" ({source})" if source else ""
    if orders > 1:
        return _check("freeze", "warn", f"Over the freeze limit of {freeze:,}{src}: would go as {orders} orders."), orders
    return _check("freeze", "ok", f"Within the freeze limit of {freeze:,}{src}."), 1


def check_band(action: str, ltp: float, limit: float, lo: Optional[float], hi: Optional[float]) -> tuple[dict, float]:
    """Today's price band: a limit past it is capped at the edge (live
    sends it there); a price already outside it can't be traded."""
    if not lo or not hi:
        return _check("band", "unknown", "No price band in the quote."), limit
    if not lo <= ltp <= hi:
        return _check("band", "reject", f"₹{ltp:,.2f} is outside today's band ₹{lo:,.2f}–₹{hi:,.2f}."), limit
    if action == "buy" and limit > hi:
        return _check("band", "warn", f"Limit capped at the upper band ₹{hi:,.2f}."), hi
    if action == "sell" and limit < lo:
        return _check("band", "warn", f"Limit capped at the lower band ₹{lo:,.2f}."), lo
    return _check("band", "ok", f"Inside today's band ₹{lo:,.2f}–₹{hi:,.2f}."), limit


def walk(depth: Optional[dict], action: str, qty: float, limit: float) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """(quantity that fills at or better than the limit on the visible
    book, its average price, the best price on that side); all None when
    that side of the book is empty. A sell hits the bids, a buy the asks."""
    levels = ((depth or {}).get("buy" if action == "sell" else "sell")) or []
    levels = [lv for lv in levels if (lv.get("price") or 0) > 0 and (lv.get("quantity") or 0) > 0]
    if not levels:
        return None, None, None
    best = float(levels[0]["price"])
    left, got, cost = qty, 0.0, 0.0
    for lv in levels:
        p = float(lv["price"])
        if (action == "sell" and p < limit - 1e-9) or (action == "buy" and p > limit + 1e-9):
            break
        take = min(left, float(lv["quantity"]))
        got += take
        cost += take * p
        left -= take
        if left <= 0:
            break
    return got, (cost / got if got else None), best


def _shortfall(action: str, paper: float, avg: float, qty: float) -> float:
    return ((avg - paper) if action == "buy" else (paper - avg)) * qty


def _verdict(action: str, paper: float, filled: float, qty: float, avg: Optional[float]) -> tuple[str, Optional[float]]:
    if filled >= qty - 1e-9:
        s = _shortfall(action, paper, avg, qty)
        return ("worse" if s >= WORSE_FROM else "fill"), s
    if filled > 0:
        return "partial", _shortfall(action, paper, avg, filled)
    return "no_fill", None


def assess(action: str, qty: float, paper: float, *, lot: Optional[int] = None, tick: Optional[float] = None,
           freeze: Optional[int] = None, freeze_source: Optional[str] = None,
           band: tuple[Optional[float], Optional[float]] = (None, None), depth: Optional[dict] = None,
           buffer_pct: float = BUFFER_PCT) -> dict:
    """What a LIMIT order for this fill would have met, at the moment it
    was made."""
    tick = tick or DEFAULT_TICK
    checks = [check_lot(qty, lot)]
    limit = limit_for(action, paper, tick, buffer_pct)
    fz, orders = check_freeze(qty, freeze, freeze_source)
    checks.append(fz)
    bd, limit = check_band(action, paper, limit, *band)
    checks.append(bd)
    out = {"limit_price": limit, "est_price": None, "filled_qty": None, "shortfall": None, "orders": orders}
    filled, avg, best = walk(depth, action, qty, limit)
    if depth is None:
        checks.append(_check("book", "unknown", "No order book at the fill."))
        verdict = "unknown"
    elif filled is None:
        checks.append(_check("book", "unknown", "That side of the order book was empty."))
        verdict = "unknown"
    else:
        verdict, short = _verdict(action, paper, filled, qty, avg)
        out.update(est_price=_r(avg, 4), filled_qty=filled, shortfall=_r(short))
        if verdict in ("fill", "worse"):
            checks.append(_check("book", "ok", f"Fills on the book at ₹{avg:,.2f} on average (limit ₹{limit:,.2f})."))
        elif verdict == "partial":
            checks.append(_check("book", "warn", f"Only {filled:g} of {qty:g} at or better than the limit ₹{limit:,.2f} on the book; the rest rests."))
        else:
            checks.append(_check("book", "warn", f"Nothing on the book at or better than the limit ₹{limit:,.2f} (best ₹{best:,.2f}); it rests."))
    if any(c["status"] == "reject" for c in checks):
        verdict = "reject"
    out.update(verdict=verdict, checks=checks)
    return out


def follow(res: dict, action: str, qty: float, paper: float, prices: list[tuple[float, float]], seconds: int = FOLLOW_S) -> dict:
    """A limit that rested: if the price came to it within `seconds`
    (prices: [(seconds after the fill, price)]), the rest fills there;
    if not, it wouldn't have filled (live re-prices it)."""
    if res["verdict"] not in ("partial", "no_fill"):
        return res
    res = {**res, "checks": [c for c in res["checks"] if c["rule"] != "follow"]}
    limit = res["limit_price"]
    if not prices:
        res["followup"] = {"reached": None, "seconds": seconds}
        res["checks"].append(_check("follow", "unknown", f"No prices in the {seconds} s after the fill."))
        return res
    hit = next(((s, p) for s, p in prices if (p <= limit + 1e-9 if action == "buy" else p >= limit - 1e-9)), None)
    best = (min if action == "buy" else max)(p for _, p in prices)
    if hit is None:
        res["followup"] = {"reached": False, "seconds": seconds, "best": _r(best)}
        res["checks"].append(_check("follow", "warn", f"Not filled within {seconds} s: the price never came to ₹{limit:,.2f} (best ₹{best:,.2f}). Live would re-price it."))
        return res
    got = float(res["filled_qty"] or 0)
    avg = ((float(res["est_price"] or 0) * got) + limit * (qty - got)) / qty
    verdict, short = _verdict(action, paper, qty, qty, avg)
    res.update(verdict=verdict, est_price=_r(avg, 4), filled_qty=qty, shortfall=_r(short),
               followup={"reached": True, "seconds": round(hit[0]), "best": _r(best)})
    res["checks"].append(_check("follow", "ok", f"The rest filled {round(hit[0])} s later, when the price came to ₹{limit:,.2f}."))
    return res


# ── freeze limits ───────────────────────────────────────────────────────

def parse_freeze_csv(text: str) -> dict[str, int]:
    """NSE's freeze-quantity file: a SYMBOL column and a freeze quantity
    column (VOL_FRZ_QTY). Read leniently: headers are found by name,
    stray spaces ignored, rows that don't parse skipped."""
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    for i, row in enumerate(rows):
        heads = [h.strip().upper() for h in row]
        sym = next((j for j, h in enumerate(heads) if h == "SYMBOL"), None)
        qty = next((j for j, h in enumerate(heads) if "FRZ" in h or "FREEZE" in h), None)
        if sym is not None and qty is not None:
            out: dict[str, int] = {}
            for r in rows[i + 1:]:
                try:
                    s, q = r[sym].strip().upper(), int(float(r[qty].strip().replace(",", "")))
                except (IndexError, ValueError):
                    continue
                if s and q > 0:
                    out[s] = q
            return out
    raise ValueError("no SYMBOL and freeze-quantity columns in the file")


def fetch_nse_freeze(url: str = NSE_FREEZE_URL) -> str:
    import requests   # kiteconnect's own dependency
    r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0", "Accept": "text/csv,*/*"})
    r.raise_for_status()
    return r.text


class FreezeLimits:
    """Per underlying: config first, then the last good NSE file."""

    def __init__(self, pool, override: Optional[dict] = None, fetch: Callable[[], str] = fetch_nse_freeze):
        self.pool = pool
        self.override = {str(k).upper(): int(v) for k, v in (override or {}).items() if v}
        self.rows: dict[str, tuple[int, str, date]] = {}
        self.fetch = fetch
        self.refreshed: Optional[date] = None
        self.last_error: Optional[str] = None

    async def load(self) -> None:
        rows = await self.pool.fetch("SELECT underlying, qty, source, as_of FROM freeze_limits")
        self.rows = {r["underlying"]: (int(r["qty"]), r["source"], r["as_of"]) for r in rows}

    def get(self, underlying: Optional[str]) -> tuple[Optional[int], Optional[str]]:
        u = (underlying or "").upper()
        if u in self.override:
            return self.override[u], "config"
        if u in self.rows:
            q, src, as_of = self.rows[u]
            return q, f"{src.upper()} file of {as_of:%d %b}"
        return None, None

    async def refresh(self, today: Optional[date] = None) -> int:
        today = today or datetime.now(IST).date()
        parsed = parse_freeze_csv(await asyncio.to_thread(self.fetch))
        if len(parsed) < 5:
            raise ValueError(f"the freeze file has only {len(parsed)} rows")
        async with self.pool.acquire() as conn:
            await conn.executemany(
                """INSERT INTO freeze_limits (underlying, qty, source, as_of, fetched_at) VALUES ($1, $2, 'nse', $3, now())
                   ON CONFLICT (underlying) DO UPDATE SET qty = EXCLUDED.qty, source = EXCLUDED.source,
                     as_of = EXCLUDED.as_of, fetched_at = EXCLUDED.fetched_at""",
                [(u, q, today) for u, q in parsed.items()])
        await self.load()
        self.refreshed, self.last_error = today, None
        logger.info("Freeze limits: %d underlyings from the NSE file", len(parsed))
        return len(parsed)

    async def loop(self, dispatcher, every_s: float = 1800.0) -> None:
        """Once a day, after Kite logs in (a trading machine, not a test
        or a laptop without a session): fetch the NSE file. A failure
        keeps the last good copy and tries again later."""
        try:
            await self.load()
        except Exception:
            logger.exception("Couldn't read the stored freeze limits")
        while True:
            now = datetime.now(IST)
            if getattr(dispatcher, "access_token", None) and self.refreshed != now.date() and now.hour >= 8:
                try:
                    await self.refresh(now.date())
                except Exception as e:
                    self.last_error = str(e)
                    logger.warning("Freeze limits: couldn't fetch the NSE file (%s) — keeping the last copy", e)
            await asyncio.sleep(every_s)


# ── instrument data ─────────────────────────────────────────────────────

def instrument_meta(token: int) -> Optional[dict]:
    """The instrument master row for a token, from the options resolver's
    cache (strategies load it when they pick their legs)."""
    from .options.resolver import _INSTRUMENT_CACHE
    for cache in list(_INSTRUMENT_CACHE.values()):
        idx = cache.get("by_token")
        if idx is None:
            idx = cache["by_token"] = {int(r["instrument_token"]): r for r in cache.get("data") or []}
        if int(token) in idx:
            return idx[int(token)]
    return None


def underlying_of(symbol: str) -> str:
    m = re.match(r"[A-Z&-]+?(?=\d)", symbol or "")
    return m.group(0) if m else (symbol or "").upper()


# ── storage ─────────────────────────────────────────────────────────────

_COLS = ("verdict", "limit_price", "est_price", "filled_qty", "shortfall", "orders", "checks", "followup")


async def save(pool, fill: dict, res: dict, estimated: bool = False) -> None:
    await pool.execute(
        """INSERT INTO shadow_checks (lot_id, deployment_id, executed_at, symbol, instrument_token, action, qty, paper_price,
               verdict, limit_price, est_price, filled_qty, shortfall, orders, checks, followup, estimated, checked_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17, now())
           ON CONFLICT (lot_id) DO UPDATE SET verdict = EXCLUDED.verdict, limit_price = EXCLUDED.limit_price,
             est_price = EXCLUDED.est_price, filled_qty = EXCLUDED.filled_qty, shortfall = EXCLUDED.shortfall,
             orders = EXCLUDED.orders, checks = EXCLUDED.checks, followup = EXCLUDED.followup,
             estimated = EXCLUDED.estimated, checked_at = now()""",
        fill["lot_id"], fill["deployment_id"], fill["executed_at"], fill["symbol"], int(fill["instrument_token"]),
        fill["action"], float(fill["qty"]), float(fill["paper_price"]),
        res["verdict"], res.get("limit_price"), res.get("est_price"), res.get("filled_qty"), res.get("shortfall"),
        res.get("orders"), res.get("checks") or [], res.get("followup"), estimated)


def _num(v):
    return None if v is None else float(v)


def row_out(r) -> dict:
    checks = r["checks"]
    followup = r["followup"]
    return {
        "lot_id": str(r["lot_id"]), "deployment_id": str(r["deployment_id"]), "executed_at": r["executed_at"].isoformat(),
        "symbol": r["symbol"], "action": r["action"], "qty": _num(r["qty"]), "paper_price": _num(r["paper_price"]),
        "verdict": r["verdict"], "limit_price": _num(r["limit_price"]), "est_price": _num(r["est_price"]),
        "filled_qty": _num(r["filled_qty"]), "shortfall": _num(r["shortfall"]), "orders": r["orders"],
        "checks": json.loads(checks) if isinstance(checks, str) else checks,
        "followup": json.loads(followup) if isinstance(followup, str) else followup,
        "estimated": r["estimated"], "checked_at": r["checked_at"].isoformat(),
    }


async def list_checks(pool, deployment_id, limit: int = 50) -> list[dict]:
    rows = await pool.fetch(
        "SELECT * FROM shadow_checks WHERE deployment_id = $1 ORDER BY executed_at DESC, lot_id LIMIT $2",
        deployment_id, limit)
    return [row_out(r) for r in rows]


async def by_lots(pool, lot_ids: list) -> dict[str, dict]:
    if not lot_ids:
        return {}
    rows = await pool.fetch("SELECT * FROM shadow_checks WHERE lot_id = ANY($1::uuid[])", list(lot_ids))
    return {str(r["lot_id"]): row_out(r) for r in rows}


async def rejections(pool, day: date) -> list[dict]:
    """Every order Kite would have refused on `day` (IST), per deployment
    and rule: what the Alerts panel raises, once per rule per day."""
    rows = await pool.fetch(
        """SELECT s.deployment_id, d.deployment_name, c->>'rule' AS rule, count(*) AS n,
                  min(s.executed_at) AS first_at, (array_agg(c->>'detail' ORDER BY s.executed_at))[1] AS detail,
                  (array_agg(s.symbol ORDER BY s.executed_at))[1] AS symbol
           FROM shadow_checks s JOIN deployments d ON d.id = s.deployment_id,
                jsonb_array_elements(s.checks) c
           WHERE s.verdict = 'reject' AND c->>'status' = 'reject'
             AND (s.executed_at AT TIME ZONE 'Asia/Kolkata')::date = $1
           GROUP BY s.deployment_id, d.deployment_name, c->>'rule'
           ORDER BY min(s.executed_at)""", day)
    return [{"deployment_id": str(r["deployment_id"]), "name": r["deployment_name"], "rule": r["rule"],
             "n": int(r["n"]), "first_at": r["first_at"].isoformat(), "detail": r["detail"], "symbol": r["symbol"],
             "day": day.isoformat()} for r in rows]


# ── the service ─────────────────────────────────────────────────────────

class ShadowService:
    """Takes fills from the runners (submit) and checks each in the
    background; at most `parallel` at once."""

    def __init__(self, pool, dispatcher, config: Optional[dict] = None, *,
                 quotes: Optional[Callable[[list[str]], Awaitable[dict]]] = None,
                 freeze: Optional[FreezeLimits] = None, follow_s: int = FOLLOW_S, parallel: int = 8,
                 batch_s: float = QUOTE_BATCH_S):
        cfg = (config or {}).get("shadow") or {}
        self.pool = pool
        self.dispatcher = dispatcher
        self.enabled = bool(cfg.get("enabled", True))
        self.buffer_pct = float(cfg.get("limit_buffer_pct", BUFFER_PCT))
        self.freeze = freeze or FreezeLimits(pool, cfg.get("freeze_qty"))
        self.quotes = quotes or self._kite_quotes
        self.batch_s = batch_s
        self._asked: dict[str, list[asyncio.Future]] = {}
        self._batch: Optional[asyncio.Task] = None
        self.follow_s = follow_s
        # fills are only taken on the loop that owns the pool (one in the
        # app; a test process can run several)
        try:
            self.loop: Optional[asyncio.AbstractEventLoop] = asyncio.get_running_loop()
        except RuntimeError:
            self.loop = None
        self.sem = asyncio.Semaphore(parallel)
        self.tasks: set[asyncio.Task] = set()
        self.checked = 0
        self.failed = 0

    def submit(self, fill: dict) -> None:
        """Called as a fill is recorded. The order book is taken now (what
        the order would have met); everything else happens later."""
        if not self.enabled:
            return
        if self.loop is not None and asyncio.get_running_loop() is not self.loop:
            return
        token = int(fill["instrument_token"])
        cached = getattr(self.dispatcher, "last_depths", {}).get(token)
        fresh = cached is not None and datetime.now(timezone.utc) - cached[1] <= MAX_BOOK_AGE
        fill = {**fill, "depth": cached[0] if fresh else None}
        task = asyncio.create_task(self._run(fill), name=f"shadow-{fill['lot_id']}")
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def drain(self) -> None:
        while self.tasks:
            await asyncio.gather(*list(self.tasks), return_exceptions=True)

    async def _run(self, fill: dict) -> None:
        try:
            async with self.sem:
                await self.check(fill)
            self.checked += 1
        except Exception as e:
            self.failed += 1
            logger.exception("Shadow check of %s failed", fill.get("symbol"))
            try:
                await save(self.pool, fill, {"verdict": "unknown", "checks": [_check("book", "unknown", f"The check failed: {e}")]})
            except Exception:
                logger.exception("Couldn't store the failed shadow check")

    async def _kite_quotes(self, keys: list[str]) -> dict:
        from .options import OptionsResolver
        return await OptionsResolver(self.dispatcher).get_quotes(keys)

    async def quote(self, key: str) -> Optional[dict]:
        """One instrument's quote (for its price band). Every fill's ask in
        the same `batch_s` goes in one Kite call: an entry across several
        deployments mustn't queue a call per leg ahead of the strategies'
        own (Kite calls are spaced process-wide)."""
        fut = asyncio.get_running_loop().create_future()
        self._asked.setdefault(key, []).append(fut)
        if self._batch is None or self._batch.done():
            self._batch = asyncio.create_task(self._flush(), name="shadow-quotes")
            self.tasks.add(self._batch)
            self._batch.add_done_callback(self.tasks.discard)
        return await fut

    async def _flush(self) -> None:
        await asyncio.sleep(self.batch_s)
        asked, self._asked = self._asked, {}
        try:
            got = await asyncio.wait_for(self.quotes(list(asked)), QUOTE_TIMEOUT_S)
        except BaseException as e:
            for futs in asked.values():
                for f in futs:
                    if not f.done():
                        f.set_exception(e if isinstance(e, Exception) else RuntimeError("cancelled"))
            if not isinstance(e, Exception):
                raise
            return
        for k, futs in asked.items():
            for f in futs:
                if not f.done():
                    f.set_result(got.get(k))

    async def check(self, fill: dict) -> dict:
        token, symbol, action = int(fill["instrument_token"]), fill["symbol"], fill["action"]
        qty, paper = float(fill["qty"]), float(fill["paper_price"])
        meta = instrument_meta(token) or {}
        exchange = meta.get("exchange") or ("BFO" if underlying_of(symbol) in ("SENSEX", "BANKEX") else "NFO")
        quote = None
        try:
            quote = await asyncio.wait_for(self.quote(f"{exchange}:{meta.get('tradingsymbol') or symbol}"),
                                           self.batch_s + QUOTE_TIMEOUT_S + 1)
        except Exception as e:
            logger.info("Shadow: no quote for %s (%s)", symbol, e)
        quote = quote or {}
        depth = fill.get("depth") or quote.get("depth")
        freeze, src = self.freeze.get(meta.get("name") or underlying_of(symbol))
        res = assess(action, qty, paper, lot=meta.get("lot_size"), tick=meta.get("tick_size"),
                     freeze=freeze, freeze_source=src,
                     band=(quote.get("lower_circuit_limit"), quote.get("upper_circuit_limit")),
                     depth=depth, buffer_pct=self.buffer_pct)
        await save(self.pool, fill, res)
        if res["verdict"] in ("partial", "no_fill"):
            res = follow(res, action, qty, paper, await self._watch(token), self.follow_s)
            await save(self.pool, fill, res)
        return res

    async def _watch(self, token: int, every_s: float = 1.0) -> list[tuple[float, float]]:
        """The price every second for follow_s seconds."""
        out: list[tuple[float, float]] = []
        t0 = asyncio.get_running_loop().time()
        while True:
            await asyncio.sleep(every_s)
            s = asyncio.get_running_loop().time() - t0
            p = self.dispatcher.last_prices.get(token)
            if p:
                out.append((s, float(p)))
            if s >= self.follow_s:
                return out


state: dict[str, Any] = {"service": None}


def submit(fill: dict) -> None:
    """The runners' entry point: never raises, never waits."""
    svc = state["service"]
    if svc is None:
        return
    try:
        svc.submit(fill)
    except Exception:
        logger.exception("Couldn't hand %s to shadow mode", fill.get("symbol"))

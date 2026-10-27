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
QUOTE_QUEUE_S = 30.0   # beyond that, how long a quote may wait its turn behind the strategies' own Kite calls
QUOTE_BATCH_S = 2.0         # fills' quotes are asked for together, once per this
MAX_BOOK_AGE = timedelta(seconds=30)
NSE_FREEZE_URL = "https://nsearchives.nseindia.com/content/fo/qtyfreeze.csv"

LEG_GAP_S = 0.4            # live places a trade's legs one after another, this far apart
RATE_PER_S = 10             # orders Kite takes a second (per API key)
RATE_PER_MIN = 200          # and a minute
KITE_IDLE_S = 1.0           # shadow's own Kite calls wait for this long without a strategy's

RULES = {"lot": "Lot size", "freeze": "Freeze limit", "band": "Price band", "book": "Order book", "follow": "Within 60 s",
         "margin": "Margin", "legs": "Leg by leg", "rate": "Order rate"}


def _check(rule: str, status: str, detail: str, **data) -> dict:
    return {"rule": rule, "status": status, "detail": detail, **data}


def _inr(x: float) -> str:
    return f"₹{x:,.0f}"


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


def book_proof(depth: Optional[dict], action: str, qty: float, limit: float, *, source: Optional[str] = None,
               at: Optional[datetime] = None, fill_at: Optional[datetime] = None) -> dict:
    """What `walk` read, kept with the check so the figure can be shown
    and redone by hand: the side the order meets (a sell the bids, a buy
    the asks) level by level with what it takes from each, the best of
    the other side, and where the book came from and when."""
    def levels(side):
        return [{"price": float(lv["price"]), "qty": float(lv["quantity"]), **({"orders": int(lv["orders"])} if lv.get("orders") else {})}
                for lv in ((depth or {}).get(side) or []) if (lv.get("price") or 0) > 0 and (lv.get("quantity") or 0) > 0]
    side, other = ("bids", "asks") if action == "sell" else ("asks", "bids")
    met = levels("buy" if action == "sell" else "sell")
    left = qty
    for lv in met:
        ok = lv["price"] >= limit - 1e-9 if action == "sell" else lv["price"] <= limit + 1e-9
        take = min(left, lv["qty"]) if ok and left > 0 else 0.0
        lv["take"] = take
        left -= take
    opp = levels("sell" if action == "sell" else "buy")
    out = {"side": side, "levels": met, "other_side": other, "other_best": opp[0]["price"] if opp else None, "source": source}
    if at is not None:
        out["at"] = at.isoformat()
        if fill_at is not None:
            out["after_fill_s"] = round((at - fill_at).total_seconds(), 2)
    return out


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
           buffer_pct: float = BUFFER_PCT, book_from: Optional[dict] = None) -> dict:
    """What a LIMIT order for this fill would have met, at the moment it
    was made. `book_from` ({source, at, fill_at}) says where the book
    came from; the book check keeps it with what it read (book_proof)."""
    tick = tick or DEFAULT_TICK
    checks = [check_lot(qty, lot)]
    limit = limit_for(action, paper, tick, buffer_pct)
    fz, orders = check_freeze(qty, freeze, freeze_source)
    checks.append(fz)
    bd, limit = check_band(action, paper, limit, *band)
    checks.append(bd)
    out = {"limit_price": limit, "est_price": None, "filled_qty": None, "shortfall": None, "orders": orders}
    filled, avg, best = walk(depth, action, qty, limit)
    proof = {} if depth is None else {"proof": book_proof(depth, action, qty, limit, **(book_from or {}))}
    if depth is None:
        checks.append(_check("book", "unknown", "No order book at the fill."))
        verdict = "unknown"
    elif filled is None:
        checks.append(_check("book", "unknown", "That side of the order book was empty.", **proof))
        verdict = "unknown"
    else:
        verdict, short = _verdict(action, paper, filled, qty, avg)
        out.update(est_price=_r(avg, 4), filled_qty=filled, shortfall=_r(short))
        if verdict in ("fill", "worse"):
            checks.append(_check("book", "ok", f"Fills on the book at ₹{avg:,.2f} on average (limit ₹{limit:,.2f}).", **proof))
        elif verdict == "partial":
            checks.append(_check("book", "warn", f"Only {filled:g} of {qty:g} at or better than the limit ₹{limit:,.2f} on the book; the rest rests.", **proof))
        else:
            checks.append(_check("book", "warn", f"Nothing on the book at or better than the limit ₹{limit:,.2f} (best ₹{best:,.2f}); it rests.", **proof))
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


def check_margin(required: Optional[float], capital: float, acc_required: Optional[float] = None,
                 acc_free: Optional[float] = None) -> dict:
    """The margin Kite would block for the deployment's open legs after
    the trade, against its capital: over it, Kite refuses the order.
    Every deployment's open legs together against the Kite account's
    free funds is a warning, not this order's refusal: paper runs many
    deployments the real account was never funded for at once, so a
    shortfall there says nothing about whether this one fits."""
    if required is None:
        return _check("margin", "unknown", "No margin figure from Kite.")
    data = {"required": round(required, 2), "capital": round(capital, 2)}
    if acc_required is not None and acc_free is not None:
        data.update(account_required=round(acc_required, 2), account_free=round(acc_free, 2))
    if required > capital + 0.5:
        return _check("margin", "reject", f"Its open legs need {_inr(required)} of margin; the deployment has {_inr(capital)}.", **data)
    if acc_required is not None and acc_free is not None and acc_required > acc_free + 0.5:
        return _check("margin", "warn", f"Fits this deployment: {_inr(required)} of its {_inr(capital)}. Every deployment's "
                      f"open legs together would need {_inr(acc_required)}; the Kite account has {_inr(acc_free)} free, "
                      f"so all of them live on this account at once would be refused.", **data)
    acc = f" · all deployments {_inr(acc_required)} of {_inr(acc_free)} free" if acc_required is not None and acc_free is not None else ""
    return _check("margin", "ok", f"Needs {_inr(required)} of its {_inr(capital)}{acc}.", **data)


def check_placing(peak: float, capital: float, short: str, hedge: str) -> dict:
    """A short placed before its hedge: Kite blocks the naked short's
    margin until the hedge fills."""
    if peak > capital + 0.5:
        return _check("margin", "reject", f"Placed before its hedge ({hedge}), {short} alone needs {_inr(peak)} of margin; "
                      f"the deployment has {_inr(capital)}. Place the hedge first.", required=round(peak, 2), capital=round(capital, 2), placing=True)
    return _check("margin", "ok", f"Placed before its hedge, it needs {_inr(peak)} of its {_inr(capital)} until the hedge fills.",
                  required=round(peak, 2), capital=round(capital, 2), placing=True)


def leg_cost(action: str, qty: float, paper: float, later: Optional[float]) -> Optional[float]:
    """Rupees worse (positive) or better for a leg placed after the ones
    before it, at `later` instead of the paper price."""
    if later is None:
        return None
    return ((later - paper) if action == "buy" else (paper - later)) * qty


def check_leg(k: int, gap_s: float, cost: Optional[float]) -> dict:
    after = f"Placed after {k} leg{'s' if k > 1 else ''} (about {k * gap_s:.1f} s later)"
    if cost is None:
        return _check("legs", "unknown", f"{after}: no price for then.")
    if cost >= WORSE_FROM:
        return _check("legs", "warn", f"{after}: the price had moved {_inr(cost)} against it.", cost=round(cost, 2))
    if cost <= -WORSE_FROM:
        return _check("legs", "ok", f"{after}: the price had moved {_inr(-cost)} in its favour.", cost=round(cost, 2))
    return _check("legs", "ok", f"{after}: the price hadn't moved.", cost=round(cost, 2))


def check_rate(in_second: int, in_minute: int) -> dict:
    if in_second > RATE_PER_S:
        late = (in_second - RATE_PER_S) / RATE_PER_S
        return _check("rate", "warn", f"{in_second} orders in the same second across deployments; Kite takes {RATE_PER_S} a second, "
                      f"so live would send some about {late:.1f} s late.", second=in_second, minute=in_minute)
    if in_minute > RATE_PER_MIN:
        return _check("rate", "warn", f"{in_minute} orders in the same minute across deployments; Kite takes {RATE_PER_MIN} a minute.",
                      second=in_second, minute=in_minute)
    return _check("rate", "ok", f"{in_second} order{'s' if in_second != 1 else ''} in that second across deployments.",
                  second=in_second, minute=in_minute)


def basket(legs: list[dict], product: str) -> list[dict]:
    """Open legs as the order basket Kite prices the margin of."""
    return [{"exchange": x["exchange"], "tradingsymbol": x["symbol"], "transaction_type": "SELL" if x["side"] == "short" else "BUY",
             "variety": "regular", "product": product, "order_type": "MARKET", "quantity": int(round(float(x["qty"]))),
             "price": 0, "trigger_price": 0} for x in legs if float(x["qty"]) > 0]


def basket_total(resp) -> Optional[float]:
    """Kite's basket margin answer: the total after hedge benefits."""
    try:
        return float((resp.get("final") or resp.get("initial"))["total"])
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


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


# ── fills from before shadow mode (estimated) ───────────────────────────

def estimate(action: str, qty: float, paper: float, book_price: Optional[float], *, lot: Optional[int] = None,
             tick: Optional[float] = None, freeze: Optional[int] = None, freeze_source: Optional[str] = None,
             buffer_pct: float = BUFFER_PCT) -> dict:
    """Pure: a past fill, from what was stored at the time. The order
    book's average for the whole quantity was recorded with each fill
    (position_lots.book_price, since slippage capture began); its levels,
    the price band, the margin and the prices just after were not, so
    those rules say so instead of guessing."""
    tick = tick or DEFAULT_TICK
    checks = [check_lot(qty, lot) if lot else _check("lot", "unknown", "The contract has expired: its lot size isn't in today's instrument list.")]
    limit = limit_for(action, paper, tick, buffer_pct)
    fz, orders = check_freeze(qty, freeze, f"{freeze_source}, today's limit" if freeze_source else None)
    checks.append(fz)
    checks.append(_check("band", "unknown", "Not stored for past fills."))
    out = {"limit_price": limit, "est_price": None, "filled_qty": None, "shortfall": None, "orders": orders}
    if book_price is None:
        checks.append(_check("book", "unknown", "No order book was recorded for this fill."))
        verdict = "unknown"
    else:
        bp = float(book_price)
        inside = bp <= limit + 1e-9 if action == "buy" else bp >= limit - 1e-9
        if inside:
            verdict, short = _verdict(action, paper, qty, qty, bp)
            out.update(est_price=round(bp, 4), filled_qty=qty, shortfall=_r(short))
            checks.append(_check("book", "ok", f"The book recorded at the fill averaged ₹{bp:,.2f} for the whole quantity, inside the limit ₹{limit:,.2f}."))
        else:
            verdict = "partial"
            checks.append(_check("book", "warn", f"The book recorded at the fill averaged ₹{bp:,.2f} for the whole quantity, past the limit "
                                 f"₹{limit:,.2f}: part would have rested. How much isn't known (only the average was stored)."))
    if any(c["status"] == "reject" for c in checks):
        verdict = "reject"
    out.update(verdict=verdict, checks=checks)
    return out


async def backfill(pool, freeze: FreezeLimits, *, deployment_id=None, buffer_pct: float = BUFFER_PCT,
                   dry_run: bool = False) -> dict:
    """Every fill without a shadow check, estimated from what was stored
    (marked estimated). Safe to run again: checked fills are skipped."""
    rows = await pool.fetch(
        """SELECT pl.id, pl.deployment_id, pl.executed_at, pl.action, pl.qty, pl.price, pl.book_price,
                  p.symbol, p.instrument_token
           FROM position_lots pl JOIN positions p ON p.id = pl.position_id
           LEFT JOIN shadow_checks s ON s.lot_id = pl.id
           WHERE s.lot_id IS NULL AND ($1::uuid IS NULL OR pl.deployment_id = $1)
           ORDER BY pl.executed_at""", deployment_id)
    counts: dict[str, int] = {}
    for r in rows:
        meta = instrument_meta(int(r["instrument_token"])) or {}
        fz, src = freeze.get(meta.get("name") or underlying_of(r["symbol"]))
        res = estimate(r["action"], float(r["qty"]), float(r["price"]), r["book_price"], lot=meta.get("lot_size"),
                       tick=meta.get("tick_size"), freeze=fz, freeze_source=src, buffer_pct=buffer_pct)
        counts[res["verdict"]] = counts.get(res["verdict"], 0) + 1
        if not dry_run:
            await save(pool, {"lot_id": r["id"], "deployment_id": r["deployment_id"], "executed_at": r["executed_at"],
                              "symbol": r["symbol"], "instrument_token": r["instrument_token"], "action": r["action"],
                              "qty": float(r["qty"]), "paper_price": float(r["price"])}, res, estimated=True)
    return {"fills": len(rows), "verdicts": counts, "written": 0 if dry_run else len(rows)}


# ── the service ─────────────────────────────────────────────────────────

class ShadowService:
    """Takes fills from the runners (submit) and checks them in the
    background, a trade at a time: a deployment's fills within `batch_s`
    of each other are one trade (its legs), checked together."""

    def __init__(self, pool, dispatcher, config: Optional[dict] = None, *,
                 quotes: Optional[Callable[[list[str]], Awaitable[dict]]] = None,
                 margins: Optional[Callable[[list[dict]], Awaitable[Any]]] = None,
                 funds: Optional[Callable[[], Awaitable[Optional[float]]]] = None,
                 freeze: Optional[FreezeLimits] = None, follow_s: int = FOLLOW_S, parallel: int = 8,
                 batch_s: float = QUOTE_BATCH_S, leg_gap_s: Optional[float] = None):
        cfg = (config or {}).get("shadow") or {}
        self.pool = pool
        self.dispatcher = dispatcher
        self.enabled = bool(cfg.get("enabled", True))
        self.margin_on = bool(cfg.get("margin", True))
        self.buffer_pct = float(cfg.get("limit_buffer_pct", BUFFER_PCT))
        self.leg_gap_s = float(leg_gap_s if leg_gap_s is not None else cfg.get("leg_gap_s", LEG_GAP_S))
        self.freeze = freeze or FreezeLimits(pool, cfg.get("freeze_qty"))
        self.quotes = quotes or self._kite_quotes
        self.margins = margins or self._kite_margins
        self.funds = funds or self._kite_funds
        self.batch_s = batch_s
        self._asked: dict[str, list[asyncio.Future]] = {}
        self._batch: Optional[asyncio.Task] = None
        self._trades: dict[Any, list[dict]] = {}          # deployment -> fills waiting to be checked together
        self._trails: dict[int, list[tuple[float, float]]] = {}   # token -> (loop time, price) just after a fill
        self._sent: list[tuple[float, int]] = []           # (loop time, orders) of every fill, the last minute
        self._acct: Optional[tuple[float, Optional[float], Optional[float]]] = None   # (when, all legs' margin, free)
        self.follow_s = follow_s
        # fills are only taken on the loop that owns the pool (one in the
        # app; a test process can run several)
        try:
            self.loop: Optional[asyncio.AbstractEventLoop] = asyncio.get_running_loop()
        except RuntimeError:
            self.loop = None
        self.sem = asyncio.Semaphore(parallel)
        self.tasks: set[asyncio.Task] = set()
        self.handed = 0
        self.checked = 0
        self.failed = 0

    def _spawn(self, coro, name: str) -> asyncio.Task:
        task = asyncio.create_task(coro, name=name)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    def submit(self, fill: dict) -> None:
        """Called as a fill is recorded. The order book is taken now (what
        the order would have met), and the price followed for a few
        seconds (what a leg placed after it would have met); everything
        else happens later."""
        if not self.enabled:
            return
        loop = asyncio.get_running_loop()
        if self.loop is not None and loop is not self.loop:
            return
        token = int(fill["instrument_token"])
        cached = getattr(self.dispatcher, "last_depths", {}).get(token)
        fresh = cached is not None and datetime.now(timezone.utc) - cached[1] <= MAX_BOOK_AGE
        fill = {**fill, "depth": cached[0] if fresh else None, "book_at": cached[1] if fresh else None, "_t": loop.time()}
        self.handed += 1
        self._sent.append((fill["_t"], 1))    # every deployment's orders, for the rate (freeze splits added later)
        if token not in self._trails:
            self._trails[token] = []
            self._spawn(self._trail(token), f"shadow-trail-{token}")
        dep = fill["deployment_id"]
        if dep not in self._trades:
            self._trades[dep] = []
            self._spawn(self._trade(dep), f"shadow-trade-{dep}")
        self._trades[dep].append(fill)

    async def drain(self) -> None:
        while self.tasks:
            await asyncio.gather(*list(self.tasks), return_exceptions=True)

    async def _trail(self, token: int, every_s: float = 0.1) -> None:
        """The price just after a fill, for the legs placed after it."""
        loop = asyncio.get_running_loop()
        end = loop.time() + self.batch_s + 4 * self.leg_gap_s + 0.5
        try:
            while loop.time() < end:
                p = self.dispatcher.last_prices.get(token)
                if p:
                    self._trails[token].append((loop.time(), float(p)))
                await asyncio.sleep(every_s)
            await asyncio.sleep(self.batch_s + 1)    # kept until the trade that needs it is checked
        finally:
            self._trails.pop(token, None)

    def _price_at(self, token: int, t: float) -> Optional[float]:
        trail = self._trails.get(token) or []
        before = [p for (at, p) in trail if at <= t + 1e-6]
        return before[-1] if before else None

    # ── Kite, politely ──────────────────────────────────────────────────

    @staticmethod
    async def _kite_idle(limit_s: float = 30.0) -> None:
        """Wait until no Kite call has gone out for KITE_IDLE_S: the
        strategies' own calls (entries, quotes) go first."""
        from .options import resolver as R
        loop = asyncio.get_running_loop()
        end = loop.time() + limit_s
        import time as _t
        while loop.time() < end and _t.monotonic() - (R._kite_call_next_allowed_at - R._KITE_CALL_MIN_INTERVAL) < KITE_IDLE_S:
            await asyncio.sleep(0.2)

    async def _kite_quotes(self, keys: list[str]) -> dict:
        from .options import OptionsResolver
        await self._kite_idle()
        return await OptionsResolver(self.dispatcher).get_quotes(keys)

    async def _kite_margins(self, orders: list[dict]):
        from .options import OptionsResolver
        await self._kite_idle()
        r = OptionsResolver(self.dispatcher)
        kite = r._kite()
        return await r._kite_call(lambda: kite.basket_order_margins(orders, consider_positions=False, mode="compact"))

    async def _kite_funds(self) -> Optional[float]:
        from .options import OptionsResolver
        await self._kite_idle()
        r = OptionsResolver(self.dispatcher)
        kite = r._kite()
        m = await r._kite_call(lambda: kite.margins("equity"))
        return float(m["net"]) if m and m.get("net") is not None else None

    async def quote(self, key: str) -> Optional[dict]:
        """One instrument's quote (for its price band). Every fill's ask in
        the same `batch_s` goes in one Kite call: an entry across several
        deployments mustn't queue a call per leg ahead of the strategies'
        own (Kite calls are spaced process-wide)."""
        fut = asyncio.get_running_loop().create_future()
        self._asked.setdefault(key, []).append(fut)
        if self._batch is None or self._batch.done():
            self._batch = self._spawn(self._flush(), "shadow-quotes")
        return await fut

    async def _flush(self) -> None:
        await asyncio.sleep(self.batch_s)
        asked, self._asked = self._asked, {}
        try:
            got = await asyncio.wait_for(self.quotes(list(asked)), QUOTE_TIMEOUT_S + QUOTE_QUEUE_S)
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

    # ── one fill ────────────────────────────────────────────────────────

    async def check(self, fill: dict) -> dict:
        """What the fill's own order would have met (not yet stored)."""
        token, symbol, action = int(fill["instrument_token"]), fill["symbol"], fill["action"]
        qty, paper = float(fill["qty"]), float(fill["paper_price"])
        meta = instrument_meta(token) or {}
        exchange = meta.get("exchange") or ("BFO" if underlying_of(symbol) in ("SENSEX", "BANKEX") else "NFO")
        quote = None
        try:
            quote = await asyncio.wait_for(self.quote(f"{exchange}:{meta.get('tradingsymbol') or symbol}"),
                                           self.batch_s + QUOTE_TIMEOUT_S + QUOTE_QUEUE_S + 1)
        except Exception as e:
            logger.info("Shadow: no quote for %s (%s)", symbol, e)
        quote = quote or {}
        fill_at = fill.get("executed_at") if isinstance(fill.get("executed_at"), datetime) else None
        if fill.get("depth"):
            # the tick's book, as it stood when the fill happened
            depth, book_from = fill["depth"], {"source": "tick", "at": fill.get("book_at"), "fill_at": fill_at}
        else:
            # no fresh tick book: Kite's quote, a few seconds after the fill
            depth, book_from = quote.get("depth"), {"source": "quote", "at": datetime.now(timezone.utc), "fill_at": fill_at}
        freeze, src = self.freeze.get(meta.get("name") or underlying_of(symbol))
        return assess(action, qty, paper, lot=meta.get("lot_size"), tick=meta.get("tick_size"),
                      freeze=freeze, freeze_source=src,
                      band=(quote.get("lower_circuit_limit"), quote.get("upper_circuit_limit")),
                      depth=depth, buffer_pct=self.buffer_pct, book_from=book_from)

    async def _one(self, fill: dict) -> dict:
        try:
            async with self.sem:
                return await self.check(fill)
        except Exception as e:
            self.failed += 1
            logger.exception("Shadow check of %s failed", fill.get("symbol"))
            return {"verdict": "unknown", "checks": [_check("book", "unknown", f"The check failed: {e}")]}

    # ── one trade: its fills together ───────────────────────────────────

    async def _trade(self, dep) -> None:
        await asyncio.sleep(self.batch_s)
        fills = self._trades.pop(dep, [])
        if not fills:
            return
        results = list(await asyncio.gather(*(self._one(f) for f in fills)))
        # the last leg's moment (placed one after another) has to have come
        loop = asyncio.get_running_loop()
        wait = fills[0]["_t"] + (len(fills) - 1) * self.leg_gap_s + 0.15 - loop.time()
        if len(fills) > 1 and wait > 0:
            await asyncio.sleep(wait)
        try:
            self._rate(fills, results)
            self._legs(fills, results)
            if self.margin_on:
                await self._margin(dep, fills, results)
        except Exception:
            logger.exception("Shadow: the trade checks failed for %s", dep)
        for f, res in zip(fills, results):
            if any(c["status"] == "reject" for c in res["checks"]):
                res["verdict"] = "reject"
            try:
                await save(self.pool, f, res)
                self.checked += 1
            except Exception:
                self.failed += 1
                logger.exception("Couldn't store the shadow check of %s", f.get("symbol"))
                continue
            if res["verdict"] in ("partial", "no_fill"):
                self._spawn(self._follow(f, res), f"shadow-follow-{f['lot_id']}")

    async def _follow(self, fill: dict, res: dict) -> None:
        res = follow(res, fill["action"], float(fill["qty"]), float(fill["paper_price"]),
                     await self._watch(int(fill["instrument_token"])), self.follow_s)
        await save(self.pool, fill, res)

    def _rate(self, fills: list[dict], results: list[dict]) -> None:
        """Orders across every deployment in the second (and the minute)
        up to each fill: every fill was recorded as it was handed over,
        and this runs batch_s later, so the others are in."""
        for f, res in zip(fills, results):
            if (res.get("orders") or 1) > 1:
                self._sent.append((f["_t"], int(res["orders"]) - 1))
        now = max(f["_t"] for f in fills)
        self._sent = [x for x in self._sent if now - x[0] <= 61]
        for f, res in zip(fills, results):
            sec = sum(n for t, n in self._sent if f["_t"] - 1.0 < t <= f["_t"] + 1e-6)
            mnt = sum(n for t, n in self._sent if f["_t"] - 60 < t <= f["_t"] + 1e-6)
            res["checks"].append(check_rate(sec, mnt))

    def _legs(self, fills: list[dict], results: list[dict]) -> None:
        """Paper fills a trade's legs at once; live places them one after
        another. The k-th leg meets the price about k × leg_gap_s later."""
        if len(fills) < 2:
            return
        t0 = fills[0]["_t"]
        for k, (f, res) in enumerate(zip(fills, results)):
            if k == 0:
                continue
            p = self._price_at(int(f["instrument_token"]), t0 + k * self.leg_gap_s)
            res["checks"].append(check_leg(k, self.leg_gap_s, leg_cost(f["action"], float(f["qty"]), float(f["paper_price"]), p)))

    async def _open_legs(self, dep=None) -> list[dict]:
        rows = await self.pool.fetch(
            """SELECT p.id, p.deployment_id, p.symbol, p.instrument_token, p.side, p.qty, d.mode
               FROM positions p JOIN deployments d ON d.id = p.deployment_id
               WHERE p.status = 'open' AND d.status <> 'stopped' AND ($1::uuid IS NULL OR p.deployment_id = $1)""", dep)
        out = []
        for r in rows:
            meta = instrument_meta(int(r["instrument_token"])) or {}
            out.append({"id": r["id"], "deployment_id": r["deployment_id"], "symbol": meta.get("tradingsymbol") or r["symbol"],
                        "exchange": meta.get("exchange") or ("BFO" if underlying_of(r["symbol"]) in ("SENSEX", "BANKEX") else "NFO"),
                        "side": r["side"], "qty": r["qty"], "mode": r["mode"]})
        return out

    async def _required(self, legs: list[dict]) -> Optional[float]:
        if not legs:
            return 0.0
        products = {"MIS" if x["mode"] == "intraday" else "NRML" for x in legs}
        orders = [o for prod in products for o in basket([x for x in legs if ("MIS" if x["mode"] == "intraday" else "NRML") == prod], prod)]
        return basket_total(await self.margins(orders))

    async def _account(self) -> tuple[Optional[float], Optional[float]]:
        loop = asyncio.get_running_loop()
        if self._acct and loop.time() - self._acct[0] < 5:
            return self._acct[1], self._acct[2]
        need = await self._required(await self._open_legs())
        free = await self.funds()
        self._acct = (loop.time(), need, free)
        return need, free

    async def _margin(self, dep, fills: list[dict], results: list[dict]) -> None:
        """After a trade that opens legs: the deployment's open legs
        against its capital, everyone's against the account; and a
        short placed before its hedge, at that moment."""
        lot_ids = [f["lot_id"] for f in fills]
        rows = await self.pool.fetch(
            """SELECT pl.id AS lot_id, p.id AS position_id, p.side, pl.action FROM position_lots pl JOIN positions p ON p.id = pl.position_id
               WHERE pl.id = ANY($1::uuid[])""", lot_ids)
        info = {r["lot_id"]: r for r in rows}
        opening = [i for i, f in enumerate(fills) if (r := info.get(f["lot_id"])) is not None
                   and (r["side"] == "short") == (r["action"] == "sell")]
        if not opening:
            return
        capital = float(await self.pool.fetchval("SELECT initial_capital FROM deployments WHERE id = $1", dep) or 0)
        try:
            legs = await self._open_legs(dep)
            need = await self._required(legs)
            acc_need, acc_free = await self._account()
        except Exception as e:
            for i in opening:
                results[i]["checks"].append(_check("margin", "unknown", f"Couldn't get the margin from Kite: {e}"))
            return
        verdict = check_margin(need, capital, acc_need, acc_free)
        # placed one after another: a short before its hedge stands alone until the hedge fills
        placing: dict[int, dict] = {}
        opened = [info[fills[i]["lot_id"]]["position_id"] for i in opening]
        base = [x for x in legs if x["id"] not in opened]
        by_pos = {x["id"]: x for x in legs}
        sells = [i for i in opening if fills[i]["action"] == "sell"]
        buys = [i for i in opening if fills[i]["action"] == "buy"]
        if sells and buys and min(sells) < max(buys):
            so_far = list(base)
            for i in opening:
                leg = by_pos.get(info[fills[i]["lot_id"]]["position_id"])
                if leg is None:
                    continue
                so_far.append(leg)
                hedge_later = any(j > i for j in buys)
                if fills[i]["action"] == "sell" and hedge_later:
                    try:
                        peak = await self._required(so_far)
                    except Exception:
                        peak = None
                    if peak is not None:
                        hedge = fills[next(j for j in buys if j > i)]["symbol"]
                        placing[i] = check_placing(peak, capital, fills[i]["symbol"], hedge)
        for i in opening:
            results[i]["checks"].append(placing.get(i) or dict(verdict))

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


# ── ready for live? ─────────────────────────────────────────────────────

# The bar a deployment's shadow record has to clear (the "Standard" one).
READY = {"sessions": 20, "cycles": 3, "clean_sessions": 10, "shortfall_pct": 1.0}
FILLED = ("fill", "worse", "partial")


def _day(t) -> date:
    t = datetime.fromisoformat(t) if isinstance(t, str) else t
    return t.astimezone(IST).date()


def card(mode: str, checks: list[dict], episodes: list[dict], lots_of: dict, open_cases: int,
         bar: Optional[dict] = None) -> dict:
    """Pure: one deployment's shadow record -> its "ready for live?" card.

    checks:    shadow_checks rows (row_out) of its fills
    episodes:  its cycles: {"positions": [position ids], "closed": bool,
               "pnl": [gross, net, net+slip]}
    lots_of:   {position id: [lot ids]}
    open_cases: days the end-of-day check flagged that nobody repaired or
               dismissed (the Repair page)
    """
    bar = {**READY, **(bar or {})}
    known = [c for c in checks if c["verdict"] != "unknown"]
    # the record (sessions, cycles, recent refusals) is what was checked as
    # it happened: an estimate from stored data couldn't see the band, the
    # margin or the legs (stats below include both)
    live = [c for c in known if not c.get("estimated")]
    verdict = {c["lot_id"]: c["verdict"] for c in checks}
    live_lots = {c["lot_id"] for c in live}
    days = sorted({_day(c["executed_at"]) for c in live})
    counts: dict[str, int] = {}
    for c in checks:
        counts[c["verdict"]] = counts.get(c["verdict"], 0) + 1
    recent = set(days[-bar["clean_sessions"]:])
    recent_rejects = sum(1 for c in live if c["verdict"] == "reject" and _day(c["executed_at"]) in recent)

    filled = [c for c in known if c["verdict"] in FILLED and c.get("filled_qty")]
    value = sum(float(c["paper_price"]) * float(c["filled_qty"]) for c in filled)
    short = sum(float(c["shortfall"] or 0) for c in filled)
    worst = max(filled, key=lambda c: float(c["shortfall"] or 0), default=None)

    # cycles: complete when every fill in it was checked; refused when live
    # would have refused one of its orders, unfilled when one wouldn't fill
    complete, refused, unfilled, total = 0, [0, 0.0, 0.0, 0.0], [0, 0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
    for ep in episodes:
        if not ep["closed"]:
            continue
        lots = [lot for p in ep["positions"] for lot in lots_of.get(p, [])]
        vs = [verdict.get(lot) for lot in lots]
        total = [a + b for a, b in zip(total, ep["pnl"])]
        if lots and all(v not in (None, "unknown") for v in vs) and all(lot in live_lots for lot in lots):
            complete += 1
        hit = refused if "reject" in vs else unfilled if "no_fill" in vs else None
        if hit is not None:
            hit[0] += 1
            for i in range(3):
                hit[i + 1] += ep["pnl"][i]

    intraday = mode == "intraday"
    have = len(days) if intraday else complete
    need = bar["sessions"] if intraday else bar["cycles"]
    avg = short / value * 100 if value else None
    unit = "session" if intraday else "full cycle"
    rules = [
        {"key": "record", "ok": have >= need, "label": f"At least {need} {unit}s in shadow",
         "value": f"{have} of {need}"},
        {"key": "rejects", "ok": recent_rejects == 0, "label": f"Nothing Kite would refuse in the last {bar['clean_sessions']} sessions",
         "value": "none" if not recent_rejects else f"{recent_rejects} refused"},
        {"key": "price", "ok": avg is not None and avg <= bar["shortfall_pct"], "label": f"Fills within {bar['shortfall_pct']:g}% of the paper price on average",
         "value": "no fills yet" if avg is None else f"{avg:.2f}% worse"},
        {"key": "repairs", "ok": open_cases == 0, "label": "No open Repair case",
         "value": "none" if not open_cases else f"{open_cases} open"},
    ]
    n = len(known)
    return {
        "ready": all(r["ok"] for r in rules), "state": "none" if not checks else "ready" if all(r["ok"] for r in rules) else "not_yet",
        "rules": rules, "counts": counts, "checked": len(checks), "sessions": len(days), "cycles": complete,
        "estimated": sum(1 for c in checks if c.get("estimated")),
        "accepted_pct": round((n - counts.get("reject", 0)) / n * 100, 1) if n else None,
        "filled_pct": round(sum(1 for c in known if c["verdict"] in ("fill", "worse")) / n * 100, 1) if n else None,
        "shortfall": round(short, 2), "shortfall_pct": None if avg is None else round(avg, 3),
        "worst": None if worst is None or not worst["shortfall"] else
            {"shortfall": float(worst["shortfall"]), "symbol": worst["symbol"], "at": worst["executed_at"]},
        "splits": sum(1 for c in checks if (c.get("orders") or 1) > 1),
        "margin": _peak_margin(checks),
        "legs_cost": round(sum(float(x.get("cost") or 0) for c in checks for x in c.get("checks") or [] if x["rule"] == "legs"), 2),
        "refused": {"n": refused[0], "pnl": round(refused[1], 2), "pnl_net": round(refused[2], 2), "pnl_net_slip": round(refused[3], 2)},
        "unfilled": {"n": unfilled[0], "pnl": round(unfilled[1], 2), "pnl_net": round(unfilled[2], 2), "pnl_net_slip": round(unfilled[3], 2)},
        "total": {"pnl": round(total[0], 2), "pnl_net": round(total[1], 2), "pnl_net_slip": round(total[2], 2)},
        "bar": bar,
    }


def _peak_margin(checks: list[dict]) -> Optional[dict]:
    """The most margin Kite would have blocked for the deployment at any
    point, against its capital."""
    ms = [x for c in checks for x in c.get("checks") or [] if x["rule"] == "margin" and x.get("required") is not None]
    if not ms:
        return None
    m = max(ms, key=lambda x: float(x["required"]) / float(x["capital"] or 1))
    cap = float(m["capital"] or 0)
    return {"required": float(m["required"]), "capital": cap,
            "pct": round(float(m["required"]) / cap * 100, 1) if cap else None, "placing": bool(m.get("placing"))}


async def scorecards(pool, deployment_id=None) -> dict[str, dict]:
    """Every deployment's card (or one's)."""
    from .db import queries
    from .v2 import book
    one = [deployment_id] if deployment_id is not None else None
    deps = await pool.fetch("SELECT id, mode FROM deployments WHERE $1::uuid[] IS NULL OR id = ANY($1::uuid[])", one)
    rows = await pool.fetch("SELECT * FROM shadow_checks WHERE $1::uuid[] IS NULL OR deployment_id = ANY($1::uuid[])", one)
    lots = await pool.fetch("SELECT id, position_id, deployment_id FROM position_lots WHERE $1::uuid[] IS NULL OR deployment_id = ANY($1::uuid[])", one)
    cases = await pool.fetch(
        """SELECT c.deployment_id, count(*) AS n FROM day_checks c
           WHERE c.status IN ('rule', 'mismatch', 'ambiguous') AND c.dismissed_at IS NULL
             AND ($1::uuid[] IS NULL OR c.deployment_id = ANY($1::uuid[]))
             AND NOT EXISTS (SELECT 1 FROM stall_repairs r WHERE r.deployment_id = c.deployment_id AND r.day = c.day AND r.undone_at IS NULL)
           GROUP BY c.deployment_id""", one)
    async with pool.acquire() as conn:
        positions, reasons = await book._positions(conn)
    checks: dict = {}
    for r in rows:
        checks.setdefault(r["deployment_id"], []).append(row_out(r))
    lots_of: dict = {}
    for r in lots:
        lots_of.setdefault(r["position_id"], []).append(str(r["id"]))
    by_dep = book._by_dep(positions)
    open_cases = {r["deployment_id"]: int(r["n"]) for r in cases}
    out = {}
    for d in deps:
        eps = [{"positions": [r["id"] for r in ep["rows"]], "closed": ep["end"] is not None, "pnl": list(book._bases(ep["rows"]))}
               for ep in queries._group_into_episodes(by_dep.get(d["id"], []), reasons)]
        out[str(d["id"])] = card(d["mode"], checks.get(d["id"], []), eps, lots_of, open_cases.get(d["id"], 0))
    return out

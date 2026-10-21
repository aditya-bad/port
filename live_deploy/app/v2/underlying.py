"""
live_deploy — a deployment's open legs against its index: the spot, the
future that prices them (app/futures.py) and the basis between them, each
leg's distance from both, how much the book makes or loses per point the
future moves (its delta), and what a move of the future would do to it.

For the positions page (GET /v2/deployments/{id}/underlying). Read-only.

Each leg is valued off its own future with Black-76 (app/geek/
optionmath.py): its implied volatility from its price now, its delta and
gamma from that, and every "if the future moves x" figure by revaluing it
at the moved future with the same volatility and time.

Prices, best first: the live tick cache, a Kite quote, the stored
1-minute candles (then marked as of when).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from .. import futures as F
from ..candles import parse_contract
from ..db import queries
from ..geek.optionmath import fwd_greeks, fwd_implied_vol, fwd_price, years_to

IST = ZoneInfo("Asia/Kolkata")
MOVES = [-300, -200, -100, -50, 50, 100, 200, 300]


def leg_view(p: dict, fut: Optional[dict], spot: Optional[float], now: datetime) -> dict:
    """One open leg against the spot and its future: distances, implied
    volatility, delta (₹ per point of the future) and gamma."""
    c = parse_contract(p["symbol"]) or {}
    kind, strike = c.get("instrument_type"), p.get("strike") or c.get("strike")
    qty, sign = float(p["qty"]), (1.0 if p["side"] == "long" else -1.0)
    out = {"symbol": p["symbol"], "kind": kind, "strike": strike, "side": p["side"], "qty": qty,
           "entry": float(p["avg_entry_price"]), "price": p.get("price"), "expiry": p.get("expiry"),
           "future": fut["tradingsymbol"] if fut else None,
           "from_spot": round(strike - spot, 2) if strike and spot else None,
           "from_fut": round(strike - fut["price"], 2) if strike and fut and fut.get("price") else None,
           "iv": None, "delta": None, "gamma": None}
    if kind not in ("CE", "PE") or not strike or not fut or not fut.get("price") or not p.get("price") or not p.get("expiry"):
        return out
    t = years_to(p["expiry"], now)
    iv = fwd_implied_vol(kind, p["price"], fut["price"], strike, t)
    if iv is None:
        return out
    d, g = fwd_greeks(kind, fut["price"], strike, t, iv)
    out.update({"iv": round(iv * 100, 2), "delta": round(d * qty * sign, 2), "gamma": round(g * qty * sign, 4),
                "_t": t, "_iv": iv})
    return out


def scenarios(legs: list[dict], futs: dict) -> list[dict]:
    """If every future moved x points: each valued leg revalued there (same
    volatility and time), the change in the book."""
    out = []
    for x in MOVES:
        total = 0.0
        for l in legs:
            if l.get("_iv") is None:
                continue
            f = futs[l["future"]]["price"]
            now = fwd_price(l["kind"], f, l["strike"], l["_t"], l["_iv"])
            moved = fwd_price(l["kind"], f + x, l["strike"], l["_t"], l["_iv"])
            total += (moved - now) * l["qty"] * (1 if l["side"] == "long" else -1)
        out.append({"move": x, "pnl": round(total, 2)})
    return out


def change(now_px: Optional[float], ref: Optional[float]) -> Optional[float]:
    return round(now_px - ref, 2) if now_px is not None and ref is not None else None


async def build(pool, dep: dict, dispatcher, now: Optional[datetime] = None) -> dict:
    now = (now or datetime.now(IST)).astimezone(IST)
    today = now.date()
    cfg = dict(dep.get("config") or {})
    und = F.underlying_of(cfg)
    if und is None:
        return {"available": False, "why": "This deployment's index isn't one with listed futures."}
    toks = cfg.get("instrument_tokens") or []
    index_token = int(toks[0]) if toks else None

    # the legs, each with its expiry and price now
    rows = [dict(r) for r in await queries.list_open_positions(pool, dep["id"])]
    contract_exp = {r["instrument_token"]: r["expiry"] for r in await pool.fetch(
        "SELECT instrument_token, expiry FROM contracts WHERE instrument_token = ANY($1::bigint[])",
        [int(r["instrument_token"]) for r in rows])} if rows else {}
    from ..geek.volatility import expiry_of
    for r in rows:
        m = r.get("metadata") or {}
        r["expiry"] = expiry_of(m, contract_exp.get(int(r["instrument_token"])), r["symbol"])
        r["strike"] = m.get("strike")
        px = dispatcher.last_prices.get(int(r["instrument_token"])) if dispatcher else None
        r["price"] = float(px) if px is not None else None

    # the futures that price them: known ones, else Kite's list (remembered)
    known = await F.known(pool, und)
    if any(r["expiry"] and F.pick(known, r["expiry"], today) is None for r in rows):
        try:   # a leg whose future isn't known yet: look in Kite's list (remembered)
            await F.remember(pool, await F.listed(dispatcher, und, today))
            known = await F.known(pool, und)
        except Exception:
            pass
    leg_fut = {}
    for r in rows:
        f = F.pick(known, r["expiry"], today) if r["expiry"] else None
        if f:
            leg_fut[r["id"]] = f   # a leg with no future that far out is valued without one
    # the panel's future: the nearest-expiring leg's that has one
    main = next((leg_fut[r["id"]] for r in sorted(rows, key=lambda r: r["expiry"] or date.max) if r["id"] in leg_fut), None)
    used = {f["instrument_token"]: dict(f) for f in list(leg_fut.values()) + ([main] if main else [])}

    # prices: live ticks, a Kite quote (also the day's open and previous close), the stored candles
    from ..options.resolver import options_exchange_for
    ex = options_exchange_for(und)
    keys = [F.spot_key(und)] + [f"{ex}:{f['tradingsymbol']}" for f in used.values()]
    q = await F.quotes(dispatcher, keys)
    sq = q.get(F.spot_key(und)) or {}
    spot = dispatcher.last_prices.get(index_token) if dispatcher and index_token else None
    spot = float(spot) if spot is not None else sq.get("last_price")
    spot_open = (sq.get("ohlc") or {}).get("open")
    spot_prev = (sq.get("ohlc") or {}).get("close")
    if (spot_open is None or spot_prev is None) and index_token:
        from .. import vix as vixmod
        svc = vixmod.state.get("service")
        try:
            days = await svc.candles(index_token, today - timedelta(days=10), today, "day", now) if svc else []
        except Exception:
            days = []
        td = [d for d in days if d["t"].astimezone(IST).date() == today]
        before = [d for d in days if d["t"].astimezone(IST).date() < today]
        spot_open = spot_open if spot_open is not None else (td[0]["o"] if td else None)
        spot_prev = spot_prev if spot_prev is not None else (before[-1]["c"] if before else None)
    for tok, f in used.items():
        fq = q.get(f"{ex}:{f['tradingsymbol']}") or {}
        live = dispatcher.last_prices.get(int(tok)) if dispatcher else None
        stored = await F.stored_day(pool, int(tok), today)
        f["price"] = float(live) if live is not None else fq.get("last_price") or (stored or {}).get("last")
        f["source"] = "live" if live is not None else "quote" if fq.get("last_price") else "stored" if stored and stored.get("last") else None
        f["as_of"] = stored["at"].astimezone(IST).isoformat() if f["source"] == "stored" and stored.get("at") else None
        f["open"] = (fq.get("ohlc") or {}).get("open") or (stored or {}).get("open")
        f["prev_close"] = (fq.get("ohlc") or {}).get("close") or (stored or {}).get("prev_close")
    futs = {f["tradingsymbol"]: f for f in used.values()}

    legs = [leg_view(r, futs.get(leg_fut[r["id"]]["tradingsymbol"]) if r["id"] in leg_fut else None, spot, now) for r in rows]
    fut = futs.get(main["tradingsymbol"]) if main else None
    basis = None
    if fut and fut.get("price") and spot:
        dte = max(0, (fut["expiry"] - today).days)
        b = fut["price"] - spot
        prev_b = fut["prev_close"] - spot_prev if fut.get("prev_close") and spot_prev else None
        basis = {"pts": round(b, 2), "pct": round(b / spot * 100, 3), "dte": dte,
                 "annual_pct": round(b / spot * 365 / max(dte, 1) * 100, 2) if dte else None,
                 "change_today": round(b - prev_b, 2) if prev_b is not None else None}
    valued = [l for l in legs if l.get("_iv") is not None]
    net = {"delta": round(sum(l["delta"] for l in valued), 2), "gamma": round(sum(l["gamma"] for l in valued), 4),
           "legs_valued": len(valued), "legs": len(legs), "scenarios": scenarios(valued, futs)} if valued else None
    for l in legs:
        l.pop("_iv", None)
        l.pop("_t", None)
        if isinstance(l.get("expiry"), date):
            l["expiry"] = l["expiry"].isoformat()
    return {
        "available": True, "underlying": und, "index": F.spot_key(und).split(":", 1)[1],
        "spot": {"price": spot, "open": spot_open, "prev_close": spot_prev,
                 "change_today": change(spot, spot_prev), "change_open": change(spot, spot_open)},
        "future": None if not fut else {
            "symbol": fut["tradingsymbol"], "expiry": fut["expiry"].isoformat(), "price": fut.get("price"),
            "source": fut.get("source"), "as_of": fut.get("as_of"), "open": fut.get("open"), "prev_close": fut.get("prev_close"),
            "change_today": change(fut.get("price"), fut.get("prev_close")), "change_open": change(fut.get("price"), fut.get("open"))},
        "basis": basis, "legs": legs, "net": net,
        "why": None if fut else "No futures contract is known for this index yet: they're listed after a Kite login, and stored after the close.",
    }

"""A made-up Kite account, read through the real LinckLens (app/lens.py),
for the browser tests: what /v2/lens/holdings and /v2/lens/history answer
for it, written to tests/e2e/lens.fixture.json.

    python tests/e2e/lens_fixture.py

tests/unit/test_lens.py checks that the file is what this makes, so the
pages are tested against what the server really sends.
"""

import asyncio
import json
import math
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

from app.lens import NIFTY, Lens, year_answer   # noqa: E402

TODAY = date(2026, 10, 27)
AS_OF = "2026-10-27T11:42:00+05:30"
OUT = HERE / "lens.fixture.json"

# symbol, name, qty, avg cost, beta, drift a year, idiosyncratic swings a year, listed (days ago)
SHARES = [
    ("HDFCBANK", "HDFC BANK", 260, 1480.0, 0.9, 0.10, 0.16, None),
    ("RELIANCE", "RELIANCE INDUSTRIES", 150, 2380.0, 1.0, 0.04, 0.18, None),
    ("INFY", "INFOSYS", 140, 1390.0, 0.8, -0.12, 0.20, None),
    ("ITC", "ITC", 400, 440.0, 0.6, -0.05, 0.14, None),
    ("TATAMOTORS", "TATA MOTORS", 180, 820.0, 1.4, -0.38, 0.30, None),
    ("BEL", "BHARAT ELECTRONICS", 600, 190.0, 1.3, 0.45, 0.32, None),
    ("NIFTYBEES", "NIPPON INDIA ETF NIFTY BEES", 900, 248.0, 1.0, 0.0, 0.01, None),
    ("GOLDBEES", "NIPPON INDIA ETF GOLD BEES", 1500, 54.0, 0.0, 0.30, 0.14, None),
    ("SGBDEC28", "SGB 2028 SERIES", 8, 5400.0, 0.0, 0.30, 0.13, None),
    ("LIQUIDBEES", "NIPPON INDIA ETF LIQUID BEES", 40, 1000.0, 0.0, 0.0, 0.0, None),
    ("EMBASSY", "EMBASSY OFFICE PARKS REIT", 100, 360.0, 0.4, 0.05, 0.15, None),
    ("IDEA", "VODAFONE IDEA", 3000, 14.5, 1.5, -0.45, 0.55, None),
    ("SUZLON", "SUZLON ENERGY", 450, 42.0, 1.6, 0.20, 0.50, None),
    ("MEESHO", "MEESHO", 60, 111.0, 1.2, 0.30, 0.35, 150),
]
LIQUID_PRICE = 1000.0

FUNDS = [
    # isin, fund, amc, scheme type, plan, option, units, avg, nav
    ("INF879O01027", "PARAG PARIKH FLEXI CAP FUND - DIRECT PLAN", "PPFAS Mutual Fund", "equity", "direct", "growth", 3120.4, 62.1, 88.4),
    ("INF846K01131", "AXIS ELSS TAX SAVER FUND - REGULAR PLAN", "Axis Mutual Fund", "elss", "regular", "growth", 1450.2, 68.3, 92.6),
    ("INF789F01XA0", "UTI NIFTY 50 INDEX FUND - DIRECT PLAN", "UTI Mutual Fund", "index", "direct", "growth", 2210.0, 128.5, 163.2),
    ("INF109K01Z48", "ICICI PRUDENTIAL BALANCED ADVANTAGE FUND - REGULAR PLAN", "ICICI Prudential Mutual Fund", "hybrid", "regular", "payout", 1800.0, 21.2, 23.9),
    ("INF179KB1HP9", "HDFC LIQUID FUND - DIRECT PLAN", "HDFC Mutual Fund", "liquid", "direct", "growth", 12.5, 4580.0, 5012.0),
]
SIPS = [
    # id, isin, amount, frequency, day, status, completed, created
    ("881", "INF879O01027", 15000, "monthly", 5, "ACTIVE", 38, "2023-08-05"),
    ("882", "INF789F01XA0", 10000, "monthly", 12, "ACTIVE", 26, "2024-08-12"),
    ("883", "INF846K01131", 2500, "weekly", None, "ACTIVE", 61, "2025-08-04"),
    ("884", "INF109K01Z48", 5000, "monthly", 20, "PAUSED", 14, "2024-03-20"),
]


def _trading_days(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _walk(days, start, ret_of, rnd):
    px, out = start, []
    for d in days:
        r = ret_of(d)
        px *= math.exp(r)
        hi = px * (1 + abs(rnd.gauss(0, 0.006)))
        lo = px * (1 - abs(rnd.gauss(0, 0.006)))
        out.append({"date": datetime(d.year, d.month, d.day), "open": px, "high": round(hi, 2), "low": round(lo, 2),
                    "close": round(px, 2), "volume": 1000})
    return out


def kite():
    """Kite's answers for the made-up account, as `call(what, *args)`."""
    rnd = random.Random(7)
    days = _trading_days(TODAY - timedelta(days=375), TODAY)
    # Nifty: up about 9% with a dip in the spring
    m = {d: rnd.gauss(0.09 / 252, 0.13 / math.sqrt(252)) - (0.004 if date(2026, 3, 1) <= d <= date(2026, 3, 31) else 0)
         for d in days}
    nifty = _walk(days, 23800.0, lambda d: m[d], rnd)
    candles = {NIFTY: nifty}
    holdings = []
    for i, (sym, _name, qty, avg, beta, drift, idio, listed) in enumerate(SHARES):
        token = 1000 + i
        own = [d for d in days if listed is None or d >= TODAY - timedelta(days=listed)]
        if sym == "LIQUIDBEES":
            c = [{"date": datetime(d.year, d.month, d.day), "open": LIQUID_PRICE, "high": LIQUID_PRICE, "low": LIQUID_PRICE,
                  "close": LIQUID_PRICE, "volume": 1} for d in own]
        else:
            c = _walk(own, avg * rnd.uniform(0.9, 1.1),
                      lambda d, beta=beta, drift=drift, idio=idio: beta * (m[d] - 0.09 / 252) + rnd.gauss(drift / 252, idio / math.sqrt(252)), rnd)
        candles[token] = c
        last, prev = c[-1]["close"], c[-2]["close"]
        ltp = round(last * (1 + rnd.gauss(0, 0.004)), 2) if sym != "LIQUIDBEES" else LIQUID_PRICE
        holdings.append({
            "tradingsymbol": sym, "exchange": "NSE", "isin": f"INE{i:03d}A01011", "instrument_token": token,
            "quantity": qty - (20 if sym == "BEL" else 0), "t1_quantity": 20 if sym == "BEL" else 0,
            "collateral_quantity": 100 if sym == "HDFCBANK" else 0,
            "average_price": avg, "last_price": ltp, "close_price": prev, "discrepancy": False,
        })
    names = [{"instrument_token": 1000 + i, "tradingsymbol": s[0], "name": s[1]} for i, s in enumerate(SHARES)]
    mf = [{"tradingsymbol": isin, "fund": fund, "folio": f"{9100 + i}/{i + 3}", "quantity": units, "average_price": avg,
           "last_price": nav, "last_price_date": date(2026, 10, 24), "pledged_quantity": 0}
          for i, (isin, fund, _amc, _t, _p, _o, units, avg, nav) in enumerate(FUNDS)]
    mf_meta = [{"tradingsymbol": isin, "name": fund, "amc": amc, "scheme_type": t, "plan": p, "dividend_type": o}
               for isin, fund, amc, t, p, o, *_ in FUNDS]
    fund_of = {f[0]: f[1] for f in FUNDS}
    sips = [{"sip_id": sid, "tradingsymbol": isin, "fund": fund_of[isin], "instalment_amount": amt, "frequency": freq,
             "instalment_day": day, "status": status, "completed_instalments": done, "created": created,
             "next_instalment": None if status != "ACTIVE" else (
                 "2026-10-31" if freq == "weekly" else f"2026-{11 if day <= 27 else 10}-{day:02d}")}
            for sid, isin, amt, freq, day, status, done, created in SIPS]
    margins = {"equity": {"net": 184250.5, "available": {"live_balance": 142000.0, "cash": 142000.0, "opening_balance": 150000.0,
                                                         "collateral": 61250.5},
                          "utilised": {"debits": 19000.0, "payout": 0}}}
    answers = {"holdings": holdings, "mf_holdings": mf, "instruments": names, "mf_instruments": mf_meta,
               "mf_sips": sips, "margins": margins}

    async def call(what, *args):
        if what == "historical_data":
            return candles[args[0]]
        return answers[what]
    return call, candles


def tradebook(h: dict) -> list[dict]:
    """A made-up tradebook for the account, as app/lens_trades.parse reads
    Console's files: buys that build each share and fund held (most in two
    or three lots, some over a year ago), shares bought and sold out, a
    holding with no trades (a bond bought at issue), and one where a bonus
    left Kite holding more than the trades bought."""
    rnd = random.Random(23)
    out, n = [], [0]

    def add(sym, isin, d, kind, qty, px, seg="EQ", ex="NSE"):
        n[0] += 1
        out.append({"symbol": sym, "isin": isin, "trade_date": d, "exchange": ex, "segment": seg, "series": "EQ" if seg == "EQ" else None,
                    "trade_type": kind, "auction": False, "quantity": qty, "price": round(px, 2), "trade_id": f"T{n[0]:05d}",
                    "order_id": f"O{n[0]:05d}", "executed_at": datetime(d.year, d.month, d.day, 10, 5), "source": "tradebook.csv"})

    for r in h["equity"]:
        if r["symbol"] in ("SGBDEC28",):
            continue                                     # bought at issue: no trade on the exchange
        qty = r["qty"] - (100 if r["symbol"] == "ITC" else 0)     # ITC: a bonus the trades don't show
        k = 1 if qty < 10 else rnd.choice([2, 3])
        lots = [qty // k] * (k - 1) + [qty - qty // k * (k - 1)]
        for i, q in enumerate(lots):
            age = 420 - i * 150 if r["symbol"] not in ("MEESHO", "BEL") else 120 - i * 40
            if r["symbol"] == "HDFCBANK" and i == 0:
                age = 350                               # turns long term in a fortnight
            add(r["symbol"], r["isin"], TODAY - timedelta(days=age), "buy", q, r["avg"] * rnd.uniform(0.93, 1.07))
    for sym, isin, buy_ago, sell_ago, qty, buy, sell in [("YESBANK", "INE528G01035", 610, 200, 2000, 22.4, 17.1),
                                                         ("IRCTC", "INE335Y01020", 540, 90, 40, 690, 905),
                                                         ("ZOMATO", "INE758T01015", 300, 60, 150, 142, 268),
                                                         ("PAYTM", "INE982J01020", 450, 320, 30, 720, 410)]:
        add(sym, isin, TODAY - timedelta(days=buy_ago), "buy", qty, buy)
        add(sym, isin, TODAY - timedelta(days=sell_ago), "sell", qty, sell)
    for f in h["mf"]:
        units, k = f["units"], 12
        each = round(units / k, 3)
        for i in range(k):
            q = each if i < k - 1 else round(units - each * (k - 1), 3)      # allotments add up to the holding
            add(f["fund"], f["isin"], TODAY - timedelta(days=30 * (k - i) + 5), "buy", q, f["avg"] * (0.9 + 0.02 * i),
                seg="MF", ex="BSE")
    return sorted(out, key=lambda t: (t["trade_date"], t["trade_id"]))


def trades_answer(h: dict, candles: dict) -> dict:
    """What /v2/lens/trades answers for the tradebook above, worked out by
    the same code (Lens._held_by_isin, lens_trades.analyse and value_curve)
    from the same prices."""
    from app import lens_trades as T
    trades = tradebook(h)
    a = T.analyse(trades, Lens._held_by_isin(h), TODAY)
    token = {r["isin"]: r["token"] for r in h["equity"]}
    series = {T.key_of(t): [(c["date"].date(), c["close"]) for c in candles.get(token.get(t["isin"])) or []] for t in trades}
    series = {k: v for k, v in series.items() if v}
    c = T.value_curve(trades, series, [(c["date"].date(), c["close"]) for c in candles[NIFTY]], TODAY)
    imports = [{"id": 2, "filename": "tradebook-EQ-2025-26.csv", "rows": sum(1 for t in trades if t["segment"] == "EQ"),
                "added": sum(1 for t in trades if t["segment"] == "EQ"), "skipped": 0, "problems": [],
                "first_day": min(t["trade_date"] for t in trades if t["segment"] == "EQ").isoformat(),
                "last_day": max(t["trade_date"] for t in trades if t["segment"] == "EQ").isoformat(), "imported_at": AS_OF},
               {"id": 1, "filename": "tradebook-MF.csv", "rows": sum(1 for t in trades if t["segment"] == "MF"),
                "added": sum(1 for t in trades if t["segment"] == "MF"), "skipped": 0, "problems": [],
                "first_day": min(t["trade_date"] for t in trades if t["segment"] == "MF").isoformat(),
                "last_day": max(t["trade_date"] for t in trades if t["segment"] == "MF").isoformat(), "imported_at": AS_OF}]
    return {"imports": imports, "analysis": a, "curve": {"state": "ready", **c, "unknown": []}, "holdings_error": None}


async def make() -> dict:
    """What the server answers for the account: the holdings as Lens reads
    them, and the year as it answers once the prices are in (from the
    database there; from the same candles here)."""
    call, candles = kite()
    lens = Lens(None, call=call, today=lambda: TODAY)
    h = await lens.holdings()
    h["as_of"] = AS_OF
    y = year_answer(h, candles, TODAY, total=1 + len(h["equity"]))
    return {"holdings": h, "history": y, "trades": trades_answer(h, candles)}


def main():
    OUT.write_text(json.dumps(asyncio.run(make()), indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()

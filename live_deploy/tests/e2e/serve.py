"""Start the real app for the browser tests: reset the test database,
seed a realistic book, then serve on 127.0.0.1:$E2E_PORT.

    TEST_DATABASE_URL=postgresql://t:t@localhost/t_test python tests/e2e/serve.py

The database name must contain "test" (its schema is dropped). Nothing
reaches Kite or sends a push: no Kite session exists, config.json is
ignored, and VAPID keys are unset. Live prices for open legs are written
straight into the dispatcher's cache (no ticks are broadcast, so no
strategy trades while the browser clicks around).

Prints "E2E READY <port>" once it accepts requests, and writes the seeded
deployment ids to tests/e2e/.ids.json (or $E2E_IDS) for the specs.
"""

import asyncio
import json
import math
import os
import random
import sys
import time as _time
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

DB = os.environ.get("TEST_DATABASE_URL", "postgresql://t:t@localhost/t_test")
if "test" not in urlparse(DB).path:
    raise SystemExit(f"TEST_DATABASE_URL must name a database containing 'test' (got {DB!r})")
PORT = int(os.environ.get("E2E_PORT", "8765"))
PASSWORD = os.environ.get("E2E_PASSWORD", "e2e-secret-123")

os.environ["TZ"] = "Asia/Kolkata"
_time.tzset()
os.environ.update({"DATABASE_URL": DB, "LOCAL_DATABASE_URL": DB, "APP_AUTH_SECRET": PASSWORD,
                   "KITE_API_KEY": "e2e-key", "KITE_API_SECRET": "e2e-secret"})
for k in ("VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY", "VAPID_SUBJECT"):
    os.environ.pop(k, None)

import app.config as _config   # noqa: E402

_real = _config.load_config
_config.load_config = lambda path=None: _real(Path("/nonexistent/config.json"))

from app.db import queries   # noqa: E402
from app.db.migrate import run_migrations   # noqa: E402
from app.db.pool import close_pool, create_pool   # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime.now(IST)
TODAY = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
R = random.Random(11)
LOT = {"NIFTY": 75, "BANKNIFTY": 35, "SENSEX": 20, "BANKEX": 30}
SPOT = {"NIFTY": 25184, "BANKNIFTY": 55920, "SENSEX": 82410, "BANKEX": 63210}
STEP = {"NIFTY": 50, "BANKNIFTY": 100, "SENSEX": 100, "BANKEX": 100}
TOKEN = {"NIFTY": 256265, "BANKNIFTY": 260105, "SENSEX": 265, "BANKEX": 274441}

# name, strategy, mode, capital, index, status, tags, age in days, style
DEPS = [
    ("Nifty Strangle A", "strangle_monthly_v2", "positional", 1_000_000, "NIFTY", "active", ["Strangles"], 200, "pos"),
    ("Nifty Strangle B", "strangle_monthly_v2", "positional", 1_000_000, "NIFTY", "paused", ["Strangles"], 150, "pos"),
    ("BankNifty DTT Adjusted", "intraday_dtt_adjusted", "intraday", 500_000, "BANKNIFTY", "active", ["DTT"], 120, "adj"),
    ("BankNifty DTT Advanced", "intraday_dtt_advanced", "intraday", 500_000, "BANKNIFTY", "paused", ["DTT"], 60, "adv"),
    ("BankNifty DTT Plain", "intraday_dtt_simple", "intraday", 500_000, "BANKNIFTY", "active", ["DTT"], 120, "dtt"),
    ("Sensex Credit Spread", "trend_credit_spread", "positional", 500_000, "SENSEX", "active", ["Spreads"], 90, "pos"),
    ("Pivot Supertrend Options", "pivot_supertrend_options", "intraday", 300_000, "NIFTY", "stopped", [], 60, "st"),
    ("Bankex Strangle", "strangle_monthly_v2", "positional", 500_000, "BANKEX", "active", ["Strategy Lab"], 6, "pos"),
]
OPEN_LEGS = {"Nifty Strangle A": 4, "BankNifty DTT Plain": 2, "Bankex Strangle": 2}
MARKED_ONLY = "Bankex Strangle"   # its legs have no live price, only a recorded mark
_tok = [600000]
GEEK_LIVE: list = []      # open geek legs: their last trade time is kept fresh


def ntok():
    _tok[0] += 1
    return _tok[0]


def sym(ix, d, strike, ot):
    return f"{ix}{d.strftime('%y%b').upper()}{strike}{ot}"


async def fill(pool, dep, s, t, action, qty, price, when, reason):
    price = round(max(0.5, price), 2)
    await queries.record_fill(pool, dep, s, t, action, qty, price, when, reason=reason,
                              book_price=round(price + (0.15 if action == "buy" else -0.15), 2))


async def pair_trade(pool, dep, ix, opened, closed, pnl, reason, adjust=False):
    q, k = LOT[ix], round(SPOT[ix] / STEP[ix]) * STEP[ix]
    legs = [(sym(ix, closed, k + 8 * STEP[ix], "CE"), ntok()), (sym(ix, closed, k - 8 * STEP[ix], "PE"), ntok())]
    entry = R.uniform(80, 160)
    for s, t in legs:
        await fill(pool, dep, s, t, "sell", q, entry, opened, "entry")
    entry_ce = entry
    if adjust:
        mid = opened + (closed - opened) / 2
        rs, rt = sym(ix, closed, k + 10 * STEP[ix], "CE"), ntok()
        await fill(pool, dep, legs[0][0], legs[0][1], "buy", q, entry + 10, mid, "roll")
        await fill(pool, dep, rs, rt, "sell", q, entry * 0.8, mid + timedelta(minutes=3), "roll")
        legs[0], entry_ce = (rs, rt), entry * 0.8
    for (s, t), px in zip(legs, [entry_ce - pnl * 0.6 / q, entry - pnl * 0.4 / q]):
        await fill(pool, dep, s, t, "buy", q, px, closed, reason)


async def seed():
    import asyncpg
    conn = await asyncpg.connect(DB)
    try:
        await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    finally:
        await conn.close()
    pool = await create_pool(DB)
    await run_migrations(pool)
    for tag in ["Strangles", "DTT", "Spreads", "Strategy Lab"]:
        await queries.create_tag(pool, tag)
    ids, prices = {}, {}
    for name, strat, mode, cap, ix, status, tags, age, style in DEPS:
        cfg = {"instrument_tokens": [TOKEN[ix]], "instrument": ix, "options_underlying": ix,
               "entry_time": "10:00", "lots_per_trade": 1}
        d = await queries.create_deployment(pool, name, strat, mode, float(cap), cfg)
        created = TODAY - timedelta(days=age)
        await pool.execute("UPDATE deployments SET created_at=$2, tags=$3 WHERE id=$1", d["id"], created, tags)
        ids[name] = str(d["id"])
        day = created + timedelta(days=1)
        if style == "st":
            await seed_supertrend(pool, d["id"], created)
        elif style == "dtt":
            await seed_dtt(pool, d["id"], created)
        elif style in ("adj", "adv"):
            await seed_adjusted(pool, d["id"], created, rolling=style == "adv")
        elif style == "intra":
            while day < TODAY:
                if day.weekday() < 5 and R.random() < 0.85:
                    o = datetime.combine(day.date(), time(10, 0), IST)
                    pnl = R.uniform(400, 2600) if R.random() < 0.56 else -R.uniform(400, 2400)
                    await pair_trade(pool, d["id"], ix, o, o + timedelta(hours=R.uniform(0.5, 5)), pnl,
                                     "target" if pnl > 0 else "stop loss")
                day += timedelta(days=1)
        else:
            realized = 0.0
            while day < TODAY - timedelta(days=4):
                hold = R.randint(3, 12)
                o = datetime.combine(day.date(), time(9, 21), IST)
                c = min(o + timedelta(days=hold, hours=5), datetime.combine((TODAY - timedelta(days=1)).date(), time(15, 0), IST))
                pnl = R.uniform(4000, 22000) if R.random() < 0.65 else -R.uniform(3000, 18000)
                await pair_trade(pool, d["id"], ix, o, c, pnl, "checkpoint" if pnl > 0 else "cycle stop",
                                 adjust=R.random() < 0.35)
                dip = -abs(pnl) * R.uniform(0.2, 1.1)
                for i in range(hold):
                    f = i / max(1, hold - 1)
                    v = cap + realized + dip * (4 * f * (1 - f)) + pnl * f
                    await pool.execute(
                        "INSERT INTO deployment_snapshots (deployment_id, snapshot_at, cash, open_positions_value, "
                        "total_value, realized_pnl_cumulative) VALUES ($1,$2,$3,0,$3,$4)",
                        d["id"], o + timedelta(days=i, hours=3), v, realized)
                realized += pnl
                day = c + timedelta(days=R.randint(1, 3))
        await pool.execute("UPDATE deployments SET status=$2 WHERE id=$1", d["id"], status)

    for name, n in OPEN_LEGS.items():
        dep = next(x for x in DEPS if x[0] == name)
        ix, q = dep[4], LOT[dep[4]]
        k = round(SPOT[ix] / STEP[ix]) * STEP[ix]
        when = datetime.combine((TODAY if dep[2] == "intraday" else TODAY - timedelta(days=3)).date(), time(9, 21), IST)
        legs = [("CE", 0, "sell"), ("PE", 0, "sell")] if dep[1] == "intraday_dtt_simple" else \
            [("CE", 8, "sell"), ("PE", -8, "sell"), ("CE", 12, "buy"), ("PE", -12, "buy")]   # a straddle sells one strike
        for ot, off, act in legs[:n]:
            t = ntok()
            px = R.uniform(90, 160) if act == "sell" else R.uniform(25, 50)
            if dep[1] == "intraday_dtt_simple":
                await queries.record_fill(pool, ids[name], sym(ix, TODAY + timedelta(days=30), k, ot), t, act, q, round(px, 2), when, reason="entry",
                                          metadata={"trigger": "entry_time_reached", "action": f"sell_open_{ot}", "strike": k,
                                                    "expiry": (TODAY + timedelta(days=(2 - TODAY.weekday()) % 7)).date().isoformat(),
                                                    "entry_spot": SPOT[ix], "leg": ot, "trigger_values": {"late_start_today": False}})
            else:
                await fill(pool, ids[name], sym(ix, TODAY + timedelta(days=30), k + off * STEP[ix], ot), t, act, q, px, when, "entry")
            if name == MARKED_ONLY:
                # never live: only yesterday's last recorded price (a holiday / Kite down)
                pos = await pool.fetchval("SELECT id FROM positions WHERE deployment_id=$1 AND instrument_token=$2", ids[name], t)
                await queries.record_position_marks(pool, [(pos, ids[name], datetime.combine(
                    (TODAY - timedelta(days=1)).date(), time(15, 25), IST), round(px * R.uniform(0.85, 1.12), 2))])
            else:
                prices[t] = round(px * R.uniform(0.85, 1.12), 2)

    # today's lanes: 5-minute snapshots up to now
    for name, _s, _m, cap, _ix, status, *_ in DEPS:
        if status == "stopped":
            continue
        base = cap + await pool.fetchval(
            "SELECT COALESCE(SUM(realized_pnl),0)::float8 FROM positions WHERE deployment_id=$1 AND status='closed'",
            ids[name])
        target, t, i = R.uniform(-1500, 5000), datetime.combine(TODAY.date(), time(9, 10), IST), 0
        while t <= min(NOW, datetime.combine(TODAY.date(), time(15, 30), IST)):
            f = min(1, max(0, (t - datetime.combine(TODAY.date(), time(9, 15), IST)).total_seconds() / 22500))
            v = base + (target * f + abs(target) * 0.35 * math.sin(i / 5) * (1 - f) if t.time() > time(9, 15) else 0)
            await pool.execute(
                "INSERT INTO deployment_snapshots (deployment_id, snapshot_at, cash, open_positions_value, total_value, "
                "realized_pnl_cumulative) VALUES ($1,$2,$3,0,$3,0)", ids[name], t, v)
            t += timedelta(minutes=5)
            i += 1
    ids[GEEK_NAME] = await seed_geek(pool, prices, GEEK_LIVE)
    await seed_futures(pool)
    await seed_repair(pool, ids["BankNifty DTT Plain"])
    await seed_ops(pool, ids)
    await seed_day_checks(pool, ids)
    await seed_repair_cases(pool, ids)
    await seed_strategy_issues(pool, ids)
    await seed_old_repair(pool, ids["BankNifty DTT Advanced"])
    await seed_shadow(pool, ids["BankNifty DTT Plain"])
    await queries.record_event(pool, ids["Sensex Credit Spread"], "strategy_error",
                               message="Order rejected: margin check failed. Hedge leg not placed.", metadata={})
    await close_pool(pool)
    Path(os.environ.get("E2E_IDS") or HERE / ".ids.json").write_text(json.dumps({"ids": ids, "password": PASSWORD, "port": PORT}, indent=1))
    return prices


# Shadow mode (app/shadow.py) on BankNifty DTT Plain's latest fills: today's
# first entry leg Kite would refuse (a lot size the strategy got wrong),
# the next fills ₹ worse on a thin book, the rest at the paper price.
async def seed_shadow(pool, dep):
    from app import shadow
    lots = await pool.fetch(
        "SELECT pl.*, p.symbol, p.instrument_token FROM position_lots pl JOIN positions p ON p.id = pl.position_id "
        "WHERE pl.deployment_id = $1 ORDER BY pl.executed_at DESC, pl.id LIMIT 12", dep)
    today = [x for x in lots if x["executed_at"].astimezone(IST).date() == TODAY.date()]
    assert today, "BankNifty DTT Plain trades today"
    worse = next(x for x in lots if x["id"] != today[-1]["id"])["id"]
    for lot in lots:
        qty, px, act = float(lot["qty"]), float(lot["price"]), lot["action"]
        side = "buy" if act == "sell" else "sell"     # the side the order meets
        if lot["id"] == today[-1]["id"]:
            kw = dict(lot=int(qty) + 5, depth={side: [{"price": px, "quantity": 5000}]})
        elif lot["id"] == worse:
            kw = dict(lot=LOT["BANKNIFTY"], depth={side: [{"price": round(px - 0.4 if act == "sell" else px + 0.4, 2), "quantity": 5000}]})
        else:
            kw = dict(lot=LOT["BANKNIFTY"], depth={side: [{"price": px, "quantity": 5000}]})
        res = shadow.assess(act, qty, px, tick=0.05, freeze=1050, freeze_source="config", band=(0.05, px * 3), **kw)
        await shadow.save(pool, {"lot_id": lot["id"], "deployment_id": dep, "executed_at": lot["executed_at"], "symbol": lot["symbol"],
                                 "instrument_token": lot["instrument_token"], "action": act, "qty": qty, "paper_price": px}, res)


# intraday_dtt_simple the way it really trades: an ATM straddle at
# 10:00 every weekday, both legs bought back at the profit target, on a
# leg's stop or at 15:00, with the metadata its fills carry, the legs'
# and the index's 1-minute candles, and its half-hourly exit checks.
def vix_level(d, minute=0):
    """A deterministic India VIX: 12–19 drifting over weeks, a spike every
    few weeks, a little intraday wobble. Shared with the tests' checks."""
    k = d.toordinal()
    base = 15 + 3 * math.sin(k / 9) + (4 if k % 23 in (0, 1) else 0)
    return round(base + 0.4 * math.sin(minute / 40 + k), 2)


def index_days(tok):
    """Each index's daily candles, moving about what VIX priced, some days
    far more (what Kite's day history would say). Deterministic."""
    ix = next(k for k, v in TOKEN.items() if v == tok)
    out, px, d = {}, SPOT[ix] * 0.94, (TODAY - timedelta(days=400)).date()
    while d <= TODAY.date():
        if d.weekday() < 5:
            k = d.toordinal()
            z = math.sin(k * 1.7) * (2.6 if k % 11 == 0 else 1.0)
            prev, px = px, round(px * (1 + z * vix_level(d) / math.sqrt(365) / 100), 2)
            out[d] = (prev, max(prev, px) * 1.002, min(prev, px) * 0.998, px)
        d += timedelta(days=1)
    return out


VIX_TOKEN = 264969


async def seed_dtt(pool, dep, created):
    ix, q, tok, step = "BANKNIFTY", LOT["BANKNIFTY"], TOKEN["BANKNIFTY"], STEP["BANKNIFTY"]
    rnd = random.Random(11)
    day, n = max(created + timedelta(days=1), TODAY - timedelta(days=45)), 0
    while day < TODAY - timedelta(days=1):
        if day.weekday() >= 5 or n >= 30:
            day += timedelta(days=1)
            continue
        open_ = datetime.combine(day.date(), time(9, 15), IST)
        base, ph, amp = SPOT[ix] + rnd.uniform(-600, 600), rnd.uniform(0, 6.28), rnd.choice([rnd.uniform(40, 120), rnd.uniform(40, 120), rnd.uniform(150, 380)])
        spot = lambda m: base + amp * math.sin((m - open_).total_seconds() / 60 / 70 + ph)  # noqa: E731
        t0 = datetime.combine(day.date(), time(10, 0), IST)
        s0 = spot(t0)
        k = round(s0 / step) * step
        expiry = day + timedelta(days=(2 - day.weekday()) % 7)
        ce_s, pe_s = f"BANKNIFTY{expiry.strftime('%y%b').upper()}{k}CE", f"BANKNIFTY{expiry.strftime('%y%b').upper()}{k}PE"
        ce_t, pe_t = ntok(), ntok()
        ce0, pe0 = round(rnd.uniform(160, 260), 2), round(rnd.uniform(160, 260), 2)
        # premiums: decay with time, CE up when the index rises, PE when it
        # falls, and both up on a big move either way (a short straddle's gamma)
        prem = lambda m, e0, sgn: max(5.0, e0 * (1 - 0.3 * (m - t0).total_seconds() / 18000)  # noqa: E731
                                      + sgn * 0.5 * (spot(m) - s0) + 0.0005 * (spot(m) - s0) ** 2)
        end = datetime.combine(day.date(), time(15, 0), IST)
        m, reason, tv = t0 + timedelta(minutes=1), "force_exit", None
        while m < end:
            ce, pe = prem(m, ce0, 1), prem(m, pe0, -1)
            if ce >= ce0 * 1.4 or pe >= pe0 * 1.4:
                reason = "leg_spike_stop"
                tv = {"ce_now": round(ce, 2), "pe_now": round(pe, 2), "ce_entry_price": ce0, "pe_entry_price": pe0,
                      "per_leg_stop_loss_pct": 0.4, "ce_threshold": round(ce0 * 1.4, 2), "pe_threshold": round(pe0 * 1.4, 2)}
                break
            if ce + pe <= (ce0 + pe0) * 0.9:
                reason = "profit_target_decay"
                tv = {"combined_entry": round(ce0 + pe0, 2), "combined_now": round(ce + pe, 2),
                      "combined_premium_profit_pct": 0.1, "target_combined": round((ce0 + pe0) * 0.9, 2)}
                break
            m += timedelta(minutes=1)
        out = m if reason != "force_exit" else end
        if reason == "force_exit":
            tv = {"tick_time": "15:00:00", "force_exit_time": "15:00:00"}
        tvx = {"tick_time": "10:00:00", "entry_time": "10:00:00", "late_start_today": False, "switched_to_next_week": False}
        for sym_, t_, e0, ot in ((ce_s, ce_t, ce0, "CE"), (pe_s, pe_t, pe0, "PE")):
            await queries.record_fill(pool, dep, sym_, t_, "sell", q, e0, t0, reason="entry", metadata={
                "trigger": "entry_time_reached", "action": f"sell_open_{ot}", "trigger_values": tvx,
                "resulting_state": {ot: {"strike": k, "entry_price": e0}},
                "target_basis": {"selection_basis": "ATM", "selected_strike": k, "underlying_price": round(s0, 2), "fill_premium": e0},
                "strike": k, "expiry": expiry.date().isoformat(), "entry_spot": round(s0, 2), "leg": ot, "exchange": "NFO"})
        for sym_, t_, e0, ot, sgn in ((ce_s, ce_t, ce0, "CE", 1), (pe_s, pe_t, pe0, "PE", -1)):
            await queries.record_fill(pool, dep, sym_, t_, "buy", q, round(prem(out, e0, sgn), 2), out, reason=reason, metadata={
                "trigger": reason, "action": f"buy_close_{ot}", "trigger_values": {**tv, "price_source": "live"},
                "resulting_state": {"position": "flat"}})
        rows = []
        for i in range(int((out - open_).total_seconds() / 60) + 31):
            mm = open_ + timedelta(minutes=i)
            rows.append((tok, mm, *(round(spot(mm), 2),) * 4, None))
            if t0 <= mm <= out:
                rows.append((ce_t, mm, *(round(prem(mm, ce0, 1), 2),) * 4, None))
                rows.append((pe_t, mm, *(round(prem(mm, pe0, -1), 2),) * 4, None))
        await queries.upsert_candles(pool, rows, "kite")
        for hh in (10, 11, 12, 13, 14):
            mm = datetime.combine(day.date(), time(hh, 30), IST)
            if mm < out:
                ce, pe = prem(mm, ce0, 1), prem(mm, pe0, -1)
                await queries.record_decision(pool, dep, mm, "exit_checks", "no_action",
                                              f"Combined {ce + pe:.1f} (entry {ce0 + pe0:.1f}, target {(ce0 + pe0) * 0.9:.1f}) · CE {ce:.1f} / stop {ce0 * 1.4:.1f} · PE {pe:.1f} / stop {pe0 * 1.4:.1f}",
                                              {"combined_entry": round(ce0 + pe0, 2), "combined_now": round(ce + pe, 2),
                                               "target_combined": round((ce0 + pe0) * 0.9, 2), "ce_now": round(ce, 2), "pe_now": round(pe, 2)})
        n += 1
        day += timedelta(days=1)


# intraday_dtt_adjusted / _advanced the way they really trade: the 10:00
# straddle, then legs added to a side that falls to half the other (the
# advanced one rolls the cheapest at its cap), unwound when that side
# recovers, and everything flattened on the day's profit target, a
# break-even breach or at 15:00 — with the strategy's own metadata,
# candles for every leg and the index, and its half-hourly checks.
async def seed_adjusted(pool, dep, created, rolling=False):
    ix, q, tok, step = "BANKNIFTY", LOT["BANKNIFTY"], TOKEN["BANKNIFTY"], STEP["BANKNIFTY"]
    rnd = random.Random(23 if rolling else 17)
    day, n, cap = max(created + timedelta(days=1), TODAY - timedelta(days=40)), 0, 2
    while day < TODAY - timedelta(days=1):
        if day.weekday() >= 5 or n >= 25:
            day += timedelta(days=1)
            continue
        open_ = datetime.combine(day.date(), time(9, 15), IST)
        base, ph, amp = SPOT[ix] + rnd.uniform(-500, 500), rnd.uniform(0, 6.28), rnd.choice([rnd.uniform(60, 160), rnd.uniform(200, 420), rnd.uniform(200, 420)])
        drift = rnd.uniform(-1.2, 1.2)
        spot = lambda m: base + amp * math.sin((m - open_).total_seconds() / 60 / 80 + ph) + drift * (m - open_).total_seconds() / 60  # noqa: E731
        t0 = datetime.combine(day.date(), time(10, 0), IST)
        s0 = spot(t0)
        k = round(s0 / step) * step
        expiry = day + timedelta(days=(2 - day.weekday()) % 7)
        mon = expiry.strftime("%y%b").upper()
        decay = lambda m: 1 - 0.3 * max(0.0, (m - t0).total_seconds()) / 18000  # noqa: E731
        # a leg's premium: its own strike's moneyness at the index now
        prem = lambda strike, side, m, e0, sp0: max(1.0, e0 * decay(m) + (0.5 if side == "CE" else -0.5) * (spot(m) - sp0) * max(0.2, 1 - abs(strike - sp0) / 600) + 0.0004 * (spot(m) - sp0) ** 2)  # noqa: E731
        legs = {"CE": [], "PE": []}
        used, adjusted, realized = 0, None, 0.0
        tv0 = {"tick_time": "10:00:00", "entry_time": "10:00:00", "late_start_today": False, "switched_to_next_week": False}

        async def sell(side, strike, px, at, trigger, role, tvals, target=None):
            t_ = ntok()
            sym_ = f"BANKNIFTY{mon}{strike}{side}"
            meta = {"trigger": trigger, "action": f"sell_open_{side}", "trigger_values": tvals, "leg_role": role, "leg": side,
                    "strike": strike, "expiry": expiry.date().isoformat(), "exchange": "NFO",
                    "resulting_state": {}, "target_basis": {"target_premium": target, "selected_strike": strike, "fill_premium": px} if target else {}}
            if role == "original":
                meta["entry_spot"] = round(s0, 2)
            await queries.record_fill(pool, dep, sym_, t_, "sell", q, round(px, 2), at, reason="entry" if role == "original" else trigger, metadata=meta)
            legs[side].append({"sym": sym_, "tok": t_, "strike": strike, "e0": px, "sp0": spot(at), "role": role, "opened": at})

        async def buy(side, leg, at, trigger, tvals):
            px = round(prem(leg["strike"], side, at, leg["e0"], leg["sp0"]), 2)
            await queries.record_fill(pool, dep, leg["sym"], leg["tok"], "buy", q, px, at, reason=trigger,
                                          metadata={"trigger": trigger, "action": f"buy_close_{side}", "trigger_values": tvals, "resulting_state": {}})
            legs[side].remove(leg)
            return (leg["e0"] - px) * q

        ce0, pe0 = round(rnd.uniform(220, 320), 2), round(rnd.uniform(220, 320), 2)
        await sell("CE", k, ce0, t0, "entry_time_reached", "original", tv0)
        await sell("PE", k, pe0, t0, "entry_time_reached", "original", tv0)
        c_in = ce0 + pe0
        band = c_in * (1.1 if rolling else 1.0)
        lo_be, hi_be, target = s0 - band, s0 + band, 0.1 * c_in * q
        price_of = lambda side, leg, m: prem(leg["strike"], side, m, leg["e0"], leg["sp0"])  # noqa: E731
        m, end = t0 + timedelta(minutes=1), datetime.combine(day.date(), time(15, 0), IST)
        reason = "force_exit"
        while m < end:
            sp = spot(m)
            if sp <= lo_be or sp >= hi_be:
                reason = "breakeven_fallback"
                break
            open_pnl = sum((l["e0"] - price_of(sd, l, m)) * q for sd in ("CE", "PE") for l in legs[sd])
            if realized + open_pnl >= target:
                reason = "profit_target_total"
                break
            tot = {sd: sum(price_of(sd, l, m) for l in legs[sd]) for sd in ("CE", "PE")}
            if adjusted is None:
                small, big = ("PE", "CE") if tot["CE"] >= tot["PE"] else ("CE", "PE")
                bigger_now = tot[big]
            else:
                small, big = adjusted, {"CE": "PE", "PE": "CE"}[adjusted]
                bigger_now = price_of(big, legs[big][0], m)
            if legs[small] and legs[big] and tot[small] <= 0.5 * bigger_now:
                side_target = 0.25 * bigger_now
                held = {l["strike"] for l in legs[small]}
                strike = k + (1 if small == "CE" else -1) * step * rnd.randint(4, 9)
                while strike in held:
                    strike += (1 if small == "CE" else -1) * step
                tvals = {"side": small, "bigger_now": round(bigger_now, 2), "smaller_total": round(tot[small], 2),
                         "adjustment_trigger_ratio": 0.5, "trigger_threshold": round(0.5 * bigger_now, 2)}
                if len(legs[small]) - 1 < cap and (rolling or used < cap):
                    used += 1
                    adjusted = small
                    await sell(small, strike, side_target * rnd.uniform(0.9, 1.1), m, "adjustment", f"adjustment_{used}", tvals, round(side_target, 2))
                elif rolling:
                    cheapest = min(legs[small], key=lambda l: price_of(small, l, m))
                    realized += await buy(small, cheapest, m, "roll_close", {"side": small, "concurrent_legs_before_roll": len(legs[small])})
                    used += 1
                    await sell(small, strike, side_target * rnd.uniform(0.9, 1.1), m + timedelta(seconds=2), "roll_open", f"adjustment_{used}",
                               {"side": small, "bigger_now": round(bigger_now, 2), "concurrent_legs_before_roll_open": len(legs[small])}, round(side_target, 2))
                elif m.minute % 30 == 0:
                    await queries.record_decision(pool, dep, m, "adjustment", "blocked",
                                                  f"{small} side fell to the adjustment trigger but today's {cap} adjustments are used",
                                                  {"side": small, "bigger_now": round(bigger_now, 2), "adjustments_used": used, "max_adjustments": cap})
            elif adjusted and len(legs[adjusted]) > 1 and tot[adjusted] >= bigger_now:
                cheapest = min(legs[adjusted], key=lambda l: price_of(adjusted, l, m))
                realized += await buy(adjusted, cheapest, m, "reversal_unwind",
                                      {"side": adjusted, "smaller_total": round(tot[adjusted], 2), "bigger_now": round(bigger_now, 2)})
            if m.minute in (0, 30) and m.second == 0:
                await queries.record_decision(pool, dep, m, "exit_checks", "no_action",
                                              f"Day P&L {realized + open_pnl:,.0f} of the {target:,.0f} target · spot {sp:.1f} between break-evens {lo_be:.1f} and {hi_be:.1f}",
                                              {"total_profit": round(realized + open_pnl, 2), "target": round(target, 2), "spot": round(sp, 2)})
            m += timedelta(minutes=1)
        out = m if reason != "force_exit" else end
        all_legs = [(sd, l) for sd in ("CE", "PE") for l in list(legs[sd])]
        tv = {"breakeven_fallback": {"spot_price": round(spot(out), 2), "breakeven_lower": round(lo_be, 2), "breakeven_upper": round(hi_be, 2)},
              "profit_target_total": {"total_profit": round(target, 2), "target": round(target, 2), "combined_entry_premium": round(c_in, 2)},
              "force_exit": {"tick_time": "15:00:00", "force_exit_time": "15:00:00"}}[reason]
        for sd, l in all_legs:
            await buy(sd, l, out, reason, {**tv, "price_source": "last_tick"})
        rows = []
        for i in range(int((out - open_).total_seconds() / 60) + 31):
            mm = open_ + timedelta(minutes=i)
            rows.append((tok, mm, *(round(spot(mm), 2),) * 4, None))
        await queries.upsert_candles(pool, rows, "kite")
        for p_ in await pool.fetch("SELECT p.instrument_token, p.symbol, p.opened_at, p.closed_at, l.price, l.metadata FROM positions p "
                                   "JOIN position_lots l ON l.position_id = p.id WHERE p.deployment_id = $1 AND p.opened_at >= $2 AND l.action = 'sell'",
                                   dep, open_):
            md = p_["metadata"] if isinstance(p_["metadata"], dict) else json.loads(p_["metadata"] or "{}")
            sd, strike, e0, at0 = p_["symbol"][-2:], md.get("strike"), float(p_["price"]), p_["opened_at"]
            sp0 = spot(at0.astimezone(IST))
            cr, mm = [], at0.astimezone(IST).replace(second=0)
            while mm <= (p_["closed_at"] or out).astimezone(IST):
                cr.append((p_["instrument_token"], mm, *(round(prem(strike, sd, mm, e0, sp0), 2),) * 4, None))
                mm += timedelta(minutes=1)
            await queries.upsert_candles(pool, cr, "kite")
        n += 1
        day += timedelta(days=1)


# pivot_supertrend_options the way it really trades: one ATM option at a
# time, sold on a pivot break in the SuperTrend's direction and bought
# back on a flip or at 15:00, with the metadata its fills carry, the
# index's 1-minute candles through each trade, the checks it noted and
# the SuperTrend state it saved when it stopped.
async def seed_supertrend(pool, dep, created):
    ix, q, tok = "NIFTY", LOT["NIFTY"], TOKEN["NIFTY"]
    rnd = random.Random(7)
    day, n = created + timedelta(days=1), 0
    last = None
    while day < TODAY - timedelta(days=1):
        if day.weekday() >= 5:
            day += timedelta(days=1)
            continue
        P = 25000 + rnd.uniform(-300, 300)
        piv = {"P": P, "R1": P + 70, "R2": P + 140, "R3": P + 210, "S1": P - 70, "S2": P - 140, "S3": P - 210}
        piv = {k: round(v, 2) for k, v in piv.items()}
        trades = rnd.choice([1, 1, 2, 2, 3, 3]) if n < 22 else 0
        # one smooth index path for the session; every entry reads it
        open_ = datetime.combine(day.date(), time(9, 15), IST)
        ph, amp = rnd.uniform(0, 6.28), rnd.uniform(110, 190)
        path = lambda m: P + amp * math.sin((m - open_).total_seconds() / 60 / 55 + ph) + 25 * math.sin((m - open_).total_seconds() / 60 / 9)  # noqa: E731
        await queries.upsert_candles(pool, [(tok, open_ + timedelta(minutes=i), *(round(path(open_ + timedelta(minutes=i)), 2),) * 4, None)
                                            for i in range(376)], "kite")
        t, done = datetime.combine(day.date(), time(9, 35), IST), 0
        while done < trades and t.time() < time(14, 30):
            close = path(t)
            up = close > piv["R1"]
            if not up and close >= piv["S1"]:
                t += timedelta(minutes=5)      # nothing broken: no entry
                continue
            k_ = max((x for x in ("R1", "R2", "R3") if piv[x] < close), key=lambda x: piv[x]) if up else \
                min((x for x in ("S1", "S2", "S3") if piv[x] > close), key=lambda x: piv[x])
            st = close - rnd.uniform(40, 160) if up else close + rnd.uniform(40, 160)
            ot = "PE" if up else "CE"
            k = round(close / 50) * 50
            s_ = f"NIFTY{day.strftime('%y%b').upper()}{k}{ot}"
            ot_tok = ntok()
            prem = round(rnd.uniform(70, 140), 2)
            hold = rnd.uniform(20, 170)
            force = t + timedelta(minutes=hold) >= datetime.combine(day.date(), time(15, 0), IST) or (done == trades - 1 and rnd.random() < 0.3)
            out = datetime.combine(day.date(), time(15, 0), IST) if force else t + timedelta(minutes=hold)
            win = rnd.random() < 0.6
            exit_px = round(prem * (rnd.uniform(0.45, 0.95) if win else rnd.uniform(1.05, 1.6)), 2)
            tv = {"close": round(close, 2), "trend": "up" if up else "down", "supertrend_value": round(st, 2),
                  "pivot_point": piv["P"], "broken_level_key": k_, "broken_level": piv[k_],
                  ("r_levels" if up else "s_levels"): {x: piv[x] for x in (("R1", "R2", "R3") if up else ("S1", "S2", "S3"))}}
            meta = {"trigger": "pivot_break_long" if up else "pivot_break_short", "action": f"sell_open_{ot}",
                    "trigger_values": tv, "resulting_state": {"leg": ot, "strike": k, "symbol": s_},
                    "target_basis": {"selection_basis": "ATM", "selected_strike": k, "fill_premium": prem},
                    "signal_side": "long" if up else "short", "option_type": ot, "strike": k,
                    "expiry": (day + timedelta(days=(3 - day.weekday()) % 7)).date().isoformat(), "exchange": "NFO", "pivots": piv}
            # the signal candle and ATR, as StrategyBase.signal_context records them
            cv = [path(t - timedelta(minutes=5 - i)) for i in range(6)]
            tv.update({"candle": {"open": round(cv[0], 2), "high": round(max(cv), 2), "low": round(min(cv), 2), "close": round(cv[-1], 2)},
                       "atr": round(abs(close - st) / 3, 2), "band_width": round(abs(close - st) * 2, 2)})
            # the market at the entry, as the runner stamps it on every fill (Geek → Conditions)
            seen = [path(open_ + timedelta(minutes=i)) for i in range(int((t - open_).total_seconds() / 60) + 1)]
            meta["context"] = {"spot": round(close, 2), "minute": int((t - open_).total_seconds() / 60), "weekday": day.weekday(),
                               "day_open": round(seen[0], 2), "day_high": round(max(seen), 2), "day_low": round(min(seen), 2),
                               "range_pts": round(max(seen) - min(seen), 2), "from_open_pct": round((close - seen[0]) / seen[0] * 100, 2),
                               "gap_pct": round((ph - 3.14) / 4, 2), "strike_from_spot": round(k - close, 2),
                               "dte": (day + timedelta(days=(3 - day.weekday()) % 7)).date().toordinal() - day.date().toordinal()}
            await queries.record_fill(pool, dep, s_, ot_tok, "sell", q, prem, t, reason="entry", metadata=meta)
            reason = "force_exit" if force else "st_flip"
            xtv = ({"candle_time": out.time().isoformat(), "force_exit_time": "15:00:00"} if force else
                   {"prev_trend": "up" if up else "down", "new_trend": "down" if up else "up", "close": round(path(out), 2),
                    "final_upper": round(path(out) + 200, 2), "final_lower": round(path(out) + 3, 2)})
            await queries.record_fill(pool, dep, s_, ot_tok, "buy", q, exit_px, out, reason=reason,
                                      metadata={"trigger": reason, "action": f"buy_close_{ot}",
                                                "trigger_values": {**xtv, "price_source": "ltp"}, "resulting_state": {"position": "flat"}})
            mins_ = max(1, int((out - t).total_seconds() / 60))
            await queries.upsert_candles(pool, [(ot_tok, t + timedelta(minutes=i), *(round(prem + (exit_px - prem) * i / mins_ + 1.5 * math.sin(i / 7), 2),) * 4, None)
                                                for i in range(mins_ + 1)], "kite")
            n += 1
            done += 1
            t = out + timedelta(minutes=rnd.choice([5, 25, 60]))
        if trades == 3 and done == 3:
            await queries.record_decision(pool, dep, datetime.combine(day.date(), time(14, 10), IST), "max_trades", "blocked",
                                          f"R1 broken at {piv['R1'] + 12:.2f} (would sell a PE) but today's 3 entries are used",
                                          {"close": piv["R1"] + 12, "broken_level_key": "R1", "side": "long", "trades_today": 3, "max_trades_per_day": 3})
        await queries.record_decision(pool, dep, datetime.combine(day.date(), time(15, 0), IST), "force_exit", "no_action",
                                      "15:00 square-off: nothing open", {})
        if rnd.random() < 0.4:
            await queries.record_decision(pool, dep, datetime.combine(day.date(), time(12, 35), IST), "st_flip", "no_action",
                                          f"SuperTrend flipped up → down at {P - 20:.2f}: nothing open to close",
                                          {"prev_trend": "up", "new_trend": "down", "close": round(P - 20, 2)})
        last = (piv, P)
        day += timedelta(days=1)
    piv, P = last
    await queries.save_deployment_state(pool, dep, {
        "version": 1, "supertrend": {"period": 7, "multiplier": 3, "atr_method": "wilder", "atr": 31.4,
                                     "final_upper": round(P + 160, 2), "final_lower": round(P - 95, 2), "trend": "up",
                                     "prev_close": round(P + 20, 2), "tr_buffer": []},
        "prev_trend": "up", "prev_day_ohlc": {"high": P + 120, "low": P - 110, "close": P + 15}, "pivots": piv,
        "pivots_source_day": (TODAY - timedelta(days=2)).date().isoformat(), "today": None,
        "today_high": None, "today_low": None, "today_last_close": round(P + 20, 2), "trades_today": 0})


# A strangle_monthly_v2 deployment with the history Geek mode reads:
# fills carrying the strategy's own metadata (cycles, rolls, triggers),
# 5-minute-style price marks, decisions that didn't trade, and candles
# for legs rolls closed. Its open cycle is priced live by the loop below.
# NIFTY's futures (app/futures.py): this month's and the next two, stored
# 1-minute candles for the last weeks (the index's synthetic path plus a
# basis that shrinks to expiry), and a live price kept at spot + basis.
FUT_LIVE: dict = {}


def last_tuesday(y, m):
    d = date(y + m // 12, m % 12 + 1, 1) - timedelta(days=1)
    while d.weekday() != 1:
        d -= timedelta(days=1)
    return d


def fut_path(t, basis):
    e = t.timestamp()
    return 25000 + 300 * math.sin(e / 200000.0) + 60 * math.sin(e / 9000.0) + basis


async def seed_futures(pool):
    y, m = TODAY.year, TODAY.month
    exps, k = [], 0
    while len(exps) < 3:
        e = last_tuesday(y + (m - 1 + k) // 12, (m - 1 + k) % 12 + 1)
        if e >= TODAY.date():
            exps.append(e)
        k += 1
    rows = []
    for i, e in enumerate(exps):
        tok = 990001 + i
        sym = f"NIFTY{e.strftime('%y%b').upper()}FUT"
        await queries.upsert_contracts(pool, [{"instrument_token": tok, "tradingsymbol": sym, "exchange": "NFO", "underlying": "NIFTY",
                                               "expiry": e, "instrument_type": "FUT"}])
        FUT_LIVE[tok] = (e - TODAY.date()).days * 3.2 + 8
        for back in range(1, 16):
            d = (TODAY - timedelta(days=back)).date()
            if d.weekday() >= 5:
                continue
            for mm in range(0, 375):
                t = datetime.combine(d, time(9, 15), IST) + timedelta(minutes=mm)
                b = (e - d).days * 3.2 + 8
                v = round(fut_path(t, b), 2)
                rows.append((tok, t, v, v + 2, v - 2, v, 100))
    await queries.upsert_candles(pool, rows, "kite")


async def seed_repair(pool, dep):
    """A stall repair (app/repair) on the DTT's latest closed straddle: its
    closing fills marked as the repair CLI writes them, and the repair."""
    last = await pool.fetchval(
        "SELECT max(closed_at) FROM positions WHERE deployment_id = $1 AND status = 'closed'", dep)
    lots = await pool.fetch(
        "SELECT l.id, l.price, l.qty, p.symbol, p.realized_pnl, l.executed_at FROM position_lots l JOIN positions p ON p.id = l.position_id "
        "WHERE l.deployment_id = $1 AND p.closed_at = $2 AND l.executed_at = p.closed_at", dep, last)
    day = last.astimezone(IST)
    rep = {"run": "e2e", "source": "replay-1m", "day": day.date().isoformat(), "from": "12:16", "to": "15:30",
           "minute": day.strftime("%H:%M"), "phase": "open"}
    for l in lots:
        await pool.execute("UPDATE position_lots SET metadata = metadata || $2::jsonb WHERE id = $1", l["id"], {"repair": rep})
    trades = [{"minute": day.strftime("%H:%M"), "phase": "open", "action": "buy", "symbol": l["symbol"], "qty": float(l["qty"]),
               "price": float(l["price"]), "reason": "force_exit", "realized": float(l["realized_pnl"])} for l in lots]
    booked = round(sum(t["realized"] for t in trades), 2)
    await pool.execute(
        "INSERT INTO stall_repairs (deployment_id, day, window_from, window_to, choices, summary, undo) VALUES ($1,$2,$3,$4,$5,$6,$7)",
        dep, day.date(), day.replace(hour=12, minute=16, second=0, microsecond=0), day.replace(hour=15, minute=30, second=0, microsecond=0),
        {"13:05": "up_first"},
        {"fills": len(trades), "pnl": booked, "left": round(booked / 2, 2), "change": round(booked / 2, 2), "booked": booked,
         "reasons": {"force_exit": len(trades)}, "fills_detail": trades}, {})


async def seed_ops(pool, ids):
    """The health monitor's issues (app/ops_health.py): Kite not connected
    (critical), a strangle's legs with no trade today (a warning), and one
    already cleared. The monitor's own loop is stopped in this server."""
    now = datetime.now(timezone.utc)
    dep = ids[GEEK_NAME]
    await pool.execute(
        "INSERT INTO ops_issues (key, kind, severity, title, detail, opened_at, last_seen_at, last_alert_at, alerts) "
        "VALUES ('kite:login', 'kite_login', 'critical', 'Kite isn''t connected', "
        "'6 running deployment(s) can''t trade until you log in to Kite.', $1, $2, $2, 14)",
        now - timedelta(minutes=14), now)
    await pool.execute(
        "INSERT INTO ops_issues (key, kind, severity, deployment_id, title, detail, data, opened_at, last_seen_at, last_alert_at, alerts) "
        "VALUES ($1, 'stale_legs', 'warning', $2, $3, $4, $5, $6, $7, $7, 3)",
        f"dep:{dep}:stale-legs", dep, f"{GEEK_NAME}: 1 leg(s) haven't traded today",
        "NIFTY26NOV27000CE — no trade on the exchange since the open.", {"legs": ["NIFTY26NOV27000CE"]},
        now - timedelta(minutes=3), now)
    await pool.execute(
        "INSERT INTO ops_issues (key, kind, severity, title, detail, opened_at, last_seen_at, resolved_at, resolved_by, alerts) "
        "VALUES ('kite:stall', 'kite_stall', 'critical', 'Kite prices stopped', 'No ticks since 12:15.', $1, $2, $2, 'cleared', 5)",
        now - timedelta(hours=3), now - timedelta(hours=2, minutes=55))


async def seed_old_repair(pool, dep):
    """A repair three weeks back on a deployment with nothing flagged: its
    cycle is long over, so the deployment itself isn't marked; the cycle is,
    wherever it's listed (ui/repairs.js)."""
    day = datetime.now(IST).date() - timedelta(days=20)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    await pool.execute(
        "INSERT INTO stall_repairs (deployment_id, day, window_from, window_to, choices, summary, undo) "
        "VALUES ($1, $2, $3, $4, '{}', $5, '{}')", dep, day, datetime.combine(day, time(12, 0), IST),
        datetime.combine(day, time(15, 30), IST), {"fills": 0, "pnl": 0.0, "left": 0.0, "change": 0.0, "fills_detail": []})


async def seed_strategy_issues(pool, ids):
    """What deployments reported about themselves (app/deployments/
    health.py): a strangle short left without its hedge (open, critical),
    yesterday's late DTT entry that went through in the end (cleared),
    and a stretch with no prices yesterday (a note)."""
    now = datetime.now(timezone.utc)
    yday = datetime.now(IST).date() - timedelta(days=1)
    rows = [
        (ids["Nifty Strangle A"], "unhedged:1", "unhedged", "critical", "strategy", "A PE short is open without its hedge",
         "The protective leg for the short at 22400 couldn't be bought (ConnectionError: Kite is down).", datetime.now(IST).date(),
         now - timedelta(minutes=20), 1, None, None),
        (ids["BankNifty DTT Plain"], f"entry:{yday}", "entry_delayed", "warning", "strategy", "The 10:00 entry hasn't gone through yet",
         "Kite didn't answer the option chain lookup. Trying again every minute until 10:30.", yday,
         datetime.combine(yday, time(10, 0), IST), 3, datetime.combine(yday, time(10, 3), IST), "cleared"),
        (ids["Sensex Credit Spread"], f"feed_gap:{yday}:1100", "feed_gap", "info", "runner", "No prices from 11:00 to 11:20",
         "20 minutes without a tick for the underlying.", yday, datetime.combine(yday, time(11, 20), IST), 1, None, None),
    ]
    for dep, key, kind, sev, src, title, detail, day, first, count, resolved, by in rows:
        await pool.execute(
            "INSERT INTO strategy_issues (deployment_id, key, kind, severity, source, title, detail, day, first_at, last_at, count, resolved_at, resolved_by) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $9, $10, $11, $12)",
            dep, key, kind, sev, src, title, detail, day, first, count, resolved, by)


async def seed_day_checks(pool, ids):
    """Yesterday's end-of-day check (app/repair/daycheck.py): one rule
    broken, one traded differently, one to review, the rest as expected;
    and an older day all as expected."""
    today = datetime.now(IST).date()
    day = today - timedelta(days=1)
    older = today - timedelta(days=2)
    run = await pool.fetchval(
        "INSERT INTO day_check_runs (day, trigger, status, started_at, finished_at, progress) "
        "VALUES ($1, 'auto', 'done', $2, $3, $4) RETURNING id", day,
        datetime.combine(day, time(15, 52), IST), datetime.combine(day, time(16, 3), IST), {"total": 9, "done": 9})

    def f(minute, action, symbol, price, reason="", qty=75):
        return {"minute": minute, "action": action, "symbol": symbol, "qty": qty, "price": price, "reason": reason}
    rows = {
        "BankNifty DTT Plain": ("rule", "Still holding 2 leg(s) after its 15:00 cut-off — and the replay found 2 trade(s) the strategy would have made that the app didn't",
            [{"rule": "flat_after_cutoff", "severity": "critical", "title": "Still holding 2 leg(s) after its 15:00 cut-off",
              "detail": "An intraday deployment that exits by 15:00 ended the day holding BANKNIFTY26OCT55700CE, BANKNIFTY26OCT55700PE.",
              "legs": ["BANKNIFTY26OCT55700CE", "BANKNIFTY26OCT55700PE"]}],
            {"matched": [{"replay": f("10:00", "sell", "BANKNIFTY26OCT55700CE", 412.0, "entry", 35), "app": f("10:00", "sell", "BANKNIFTY26OCT55700CE", 413.2, "entry", 35), "minutes_apart": 0, "price_diff": 1.2}],
             "missed": [f("15:00", "buy", "BANKNIFTY26OCT55700CE", 241.0, "force_exit", 35), f("15:00", "buy", "BANKNIFTY26OCT55700PE", 899.15, "force_exit", 35)],
             "extra": [], "end": {"app": [{"symbol": "BANKNIFTY26OCT55700CE", "side": "short", "qty": 35}, {"symbol": "BANKNIFTY26OCT55700PE", "side": "short", "qty": 35}],
                                  "replay": [], "same": False}}),
        "Nifty Strangle A": ("mismatch", "The replay found 2 trade(s) the strategy would have made that the app didn't", [],
            {"matched": [], "missed": [f("13:31", "buy", "NIFTY26NOV22400PE", 243.5, "continuous_50pct_trigger"), f("13:31", "sell", "NIFTY26NOV22800PE", 151.2, "continuous_50pct_trigger")],
             "extra": [], "end": {"app": [{"symbol": "NIFTY26NOV22400PE", "side": "short", "qty": 75}], "replay": [{"symbol": "NIFTY26NOV22800PE", "side": "short", "qty": 75}], "same": False}}),
        "Nifty Monthly Strangle": ("ambiguous", "The replay found 1 trade(s) the app made that it wouldn't have — but 1 minute(s) could be read two ways (13:05): choose on the Repair page", [],
            {"matched": [], "missed": [], "extra": [f("13:05", "buy", "NIFTY26NOV27000CE", 18.4, "continuous_50pct_trigger")],
             "end": {"app": [], "replay": [], "same": True}}),
    }
    for name, d in ids.items():
        status, headline, rules, diff = rows.get(name, ("ok", "Traded as its strategy would have, and ended the day as expected", [],
                                                        {"matched": [], "missed": [], "extra": [], "end": {"app": [], "replay": [], "same": True}}))
        replay = {"start": f"captured at the open of {day:%d %b}", "auto": [{"minute": "13:05"}] if status == "ambiguous" else [],
                  "notes": [], "estimated": [], "blind": [], "fills": []}
        await pool.execute(
            "INSERT INTO day_checks (run_id, day, deployment_id, status, headline, rules, diff, replay) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
            run, day, d, status, headline, rules, diff, replay)
    run2 = await pool.fetchval("INSERT INTO day_check_runs (day, status, finished_at) VALUES ($1, 'done', now()) RETURNING id", older)
    for d in ids.values():
        await pool.execute("INSERT INTO day_checks (run_id, day, deployment_id, status, headline) VALUES ($1, $2, $3, 'ok', 'As expected')",
                           run2, older, d)


def _case_series(day, legs):
    """A day's minutes as the repair worker returns them: the index's
    candles, VIX, and each leg's price."""
    t, ohlc, vix, out = [], [], [], {k: [] for k in legs}
    for i in range(375):
        m = datetime.combine(day, time(9, 15)) + timedelta(minutes=i)
        t.append(f"{m:%H:%M}")
        v = 25000 + 120 * math.sin(i / 40) + (380 if 230 <= i < 300 else 0) * (1 - abs(i - 265) / 35)
        o, c = v - 3 * math.sin(i), v + 3 * math.cos(i)
        hi, lo = max(o, c) + 6, min(o, c) - 6
        if f"{m:%H:%M}" == "13:05":
            hi, lo = v + 420, v - 410
        ohlc.append([round(o, 2), round(hi, 2), round(lo, 2), round(c, 2)])
        vix.append(round(14 + 0.4 * math.sin(i / 50), 2))
        for k, (base, beta) in legs.items():
            out[k].append(round(max(0.05, base + beta * (v - 25000) / 10), 2))
    return {"t": t, "index": {"label": "NIFTY 50", "ohlc": ohlc}, "vix": vix, "legs": out}


async def seed_repair_cases(pool, ids):
    """Repairs of yesterday's flagged days (app/repair/dayrepair.py), as the
    repair worker leaves them: one planned and ready to push, one waiting
    for a path at 13:05, and one applied (undoable)."""
    day = datetime.now(IST).date() - timedelta(days=1)

    def f(minute, action, symbol, price, reason, qty=75, phase="open", trigger=None):
        return {"minute": minute, "phase": phase, "at": f"{day}T{minute}:00+05:30", "action": action, "symbol": symbol,
                "token": 1, "qty": qty, "price": price, "reason": reason, "realized": None,
                "meta": {"trigger": trigger} if trigger else {}}
    pe, pe2, ce = "NIFTY26NOV22400PE", "NIFTY26NOV22800PE", "NIFTY26NOV23550CE"
    ready = {
        "from": "13:31", "start": f"captured at the open of {day:%d %b}, the morning replayed to 13:31", "choices": {},
        "complete": True, "ambiguity": None, "fills_key": "e2e-ready", "can_apply": True, "why_not": [],
        "fills": [f("13:31", "buy", pe, 243.5, "continuous_50pct_trigger", trigger={"ratio": 0.48, "threshold": 0.5, "ce": 117.2, "pe": 243.5}),
                  f("13:31", "sell", pe2, 151.2, "continuous_50pct_trigger", trigger={"target_premium": 150.0, "band": "0.80–0.95"})],
        "app_fills": [f("10:00", "sell", pe, 138.95, "initial_entry"), f("10:00", "sell", ce, 161.85, "initial_entry")],
        "replaced": [],
        "decisions": [{"at": "13:12:00", "rule": "continuous_50pct_trigger", "outcome": "no_action",
                       "summary": "PE at 2.1× the CE: not yet past the 50% trigger", "data": {"ratio": 0.53}},
                      {"at": "15:13:00", "rule": "eod_check", "outcome": "no_action", "summary": "Gap fine at the end-of-day check", "data": {}}],
        "auto": [], "notes": [], "problems": [], "blind": [], "estimated": [],
        "end": {"app": [{"symbol": pe, "side": "short", "qty": 75.0}, {"symbol": ce, "side": "short", "qty": 75.0}],
                "after": [{"symbol": pe2, "side": "short", "qty": 75.0}, {"symbol": ce, "side": "short", "qty": 75.0}]},
        "summary": {"pnl": -1840.0, "left": -6796.0, "change": 4956.0, "booked": -6967.5, "charges": 64.0, "fills": 2},
        "curve": [], "series": _case_series(day, {pe: (180, -0.9), pe2: (110, -0.7), ce: (150, 0.8)}),
    }
    amb = {**ready, "from": "13:05", "complete": False, "can_apply": False, "fills_key": "e2e-amb",
           "fills": [], "replaced": [f("13:05", "buy", "NIFTY26NOV27000CE", 18.4, "continuous_50pct_trigger")],
           "ambiguity": {"minute": "13:05", "natural": "down_first",
                         "candles": [{"label": "NIFTY 50", "token": 256265, "open": 25010.0, "high": 25430.0, "low": 24600.0, "close": 25020.0},
                                     {"label": "NIFTY26NOV27000CE", "token": 2, "open": 18.0, "high": 31.0, "low": 9.5, "close": 18.4}],
                         "paths": {"up_first": {"fills": [f("13:05", "buy", "NIFTY26NOV27000CE", 31.0, "continuous_50pct_trigger", phase="up")],
                                                "pnl": -980.0, "open": [{"symbol": "NIFTY26NOV24000PE"}]},
                                   "down_first": {"fills": [], "pnl": 120.0, "open": [{"symbol": "NIFTY26NOV24000PE"}, {"symbol": "NIFTY26NOV27000CE"}]}}},
           "end": {"app": [{"symbol": "NIFTY26NOV24000PE", "side": "short", "qty": 75.0}], "after": []},
           "series": _case_series(day, {"NIFTY26NOV27000CE": (18, 0.25), "NIFTY26NOV24000PE": (60, -0.4)})}
    for name, res in (("Nifty Strangle A", ready), ("Nifty Monthly Strangle", amb)):
        await pool.execute("INSERT INTO repair_drafts (deployment_id, day, choices) VALUES ($1, $2, '{}')", ids[name], day)
        await pool.execute("INSERT INTO repair_jobs (deployment_id, day, kind, status, result, finished_at) "
                           "VALUES ($1, $2, 'plan', 'done', $3, now())", ids[name], day, res)
    # applied: undoing it changes nothing (it added nothing here) but takes it off
    dep = ids["Bankex Strangle"]
    cash = await pool.fetchval("SELECT current_cash::text FROM deployments WHERE id = $1", dep)
    state = await pool.fetchrow("SELECT state FROM deployment_state WHERE deployment_id = $1", dep)
    await pool.execute(
        "INSERT INTO stall_repairs (deployment_id, day, window_from, window_to, choices, summary, undo) VALUES ($1, $2, $3, $4, '{}', $5, $6)",
        dep, day, datetime.combine(day, time(14, 2), IST), datetime.combine(day, time(15, 30), IST),
        {"fills": 1, "kind": "day", "pnl": 410.0, "left": -220.0, "change": 630.0,
         "fills_detail": [f("14:02", "buy", "BANKEX26NOV66300CE", 61.0, "checkpoint_target", qty=30)],
         "replaced": [f("14:40", "buy", "BANKEX26NOV66300CE", 74.0, "stop", qty=30)]},
        {"cash": cash, "had_state": state is not None, "state": state["state"] if state else None, "positions": [],
         "added": {"positions": [], "lots": [], "events": [], "decisions": []},
         "journal": "00000000-0000-0000-0000-000000000000", "event": "00000000-0000-0000-0000-000000000000"})


GEEK_NAME = "Nifty Monthly Strangle"
GEEK_CAP = 600_000


async def seed_geek(pool, prices, live_times):
    from app.strategies.registry import list_strategies
    defaults = next(x for x in list_strategies() if x["name"] == "strangle_monthly_v2")["default_config"]
    cfg = {**defaults, "instrument_tokens": [TOKEN["NIFTY"]], "instrument": "NIFTY", "convergence_stop_pct": 0.08}
    d = await queries.create_deployment(pool, GEEK_NAME, "strangle_monthly_v2", "positional", float(GEEK_CAP), cfg)
    dep = d["id"]
    await pool.execute("UPDATE deployments SET created_at=$2 WHERE id=$1", dep, TODAY - timedelta(days=80))
    Q, G = 75, random.Random(5)

    def at(days, hh, mm):
        return datetime.combine((TODAY - timedelta(days=days)).date(), time(hh, mm), IST)

    def meta(trigger, action, leg, strike, cycle, expiry, role=None, tv=None, tb=None):
        m = {"trigger": trigger, "action": action, "leg": leg, "strike": strike, "cycle_id": cycle,
             "contract_expiry": expiry, "trigger_values": tv or {}, "target_basis": tb or {}, "exchange": "NFO"}
        if role:
            m["leg_role"] = role
        return m

    async def f(leg, action, px, when, reason, m):
        await queries.record_fill(pool, dep, leg["sym"], leg["tok"], action, Q, round(px, 2), when, reason=reason,
                                  metadata=m, book_price=round(px + (0.2 if action == "buy" else -0.2), 2))

    def new_leg(expiry, strike, ot, px, role):
        e = datetime.fromisoformat(expiry)
        return {"sym": f"NIFTY{e.strftime('%y%b').upper()}{strike}{ot}", "tok": ntok(), "strike": strike,
                "entry": px, "role": role, "opt": ot}

    cycles = [
        (75, 70, "checkpoint_target", 3150, []),
        (70, 60, "checkpoint_target", 3080, [(65, 11, 20, "continuous_50pct_trigger", "PE"), (62, 15, 13, "eod_gap_check", "PE")]),
        (60, 52, "contract_expiry_backstop", 1820, [(56, 15, 13, "eod_gap_check", "CE")]),
        (52, 49, "checkpoint_target", 3210, []),
        (49, 40, "convergence_stop", -3852, [(45, 10, 40, "continuous_50pct_trigger", "PE"), (43, 15, 13, "eod_rollout", "CE")]),
        (40, 30, "checkpoint_target", 3040, [(35, 15, 13, "eod_gap_check", "PE")]),
        (30, 21, "checkpoint_target", 3120, [(25, 12, 5, "continuous_50pct_trigger", "CE")]),
        (21, 17, "checkpoint_target", 3186, []),
        (17, 9, "checkpoint_target", 3060, [(13, 15, 13, "eod_gap_check", "CE")]),
        (9, None, None, None, [(4, 15, 13, "eod_gap_check", "PE")]),
    ]
    marks, prev_close = [], None

    async def price_rolled_away(pending, exits):
        """A rolled-away leg's price when its replacement closed (or now),
        chosen so rolls sometimes beat holding and sometimes don't."""
        for old, close_px, nl in pending:
            px, when = exits[nl["tok"]]
            new_pnl = (nl["entry"] - px) * Q
            held = max(0.5, close_px - (new_pnl - G.uniform(-1200, 1800)) / Q)
            m = when.replace(second=0, microsecond=0) - timedelta(minutes=1)
            await queries.upsert_candles(pool, [(old["tok"], m, held, held, held, held, None)], "kite")

    def mark_path(leg, start, end, exit_px):
        t = start.replace(minute=30, second=0, microsecond=0) + timedelta(hours=1)
        span = max(1.0, (end - start).total_seconds())
        while t < end:
            if t.weekday() < 5 and time(9, 30) <= t.time() <= time(15, 30):
                fr = (t - start).total_seconds() / span
                bump = leg["entry"] * 0.18 * math.sin(math.pi * fr) * (1 if leg["opt"] == "CE" else -1)
                marks.append((leg["tok"], t, max(1.0, leg["entry"] + (exit_px - leg["entry"]) * fr + bump), cid))
            t += timedelta(hours=1)

    for cid, (start, end, why, target, rolls) in enumerate(cycles, start=1):
        # a checkpoint flattens and re-enters straight away
        opened = prev_close + timedelta(seconds=5) if prev_close else at(start, 10, 0)
        expiry = (opened + timedelta(days=42)).date().isoformat()
        k = 25000 + G.choice([-100, -50, 0, 50, 100])
        tv = {"day_of_month": opened.day, "rotation_selector": "NEXT_MONTH", "days_to_expiry": 42,
              "long_days_override": True, "strike_selection_capital_pct": 0.05}
        tb = {"target_premium": 230.77}
        reason = "initial_entry" if cid == 1 else "checkpoint_target"
        legs = {"CE": new_leg(expiry, k + 650, "CE", G.uniform(224, 236), "original"),
                "PE": new_leg(expiry, k - 600, "PE", G.uniform(224, 236), "original")}
        for ot, leg in legs.items():
            await f(leg, "sell", leg["entry"], opened, reason, meta(reason, "open", ot, leg["strike"], cid, expiry, "original", tv, tb))
        booked, n_adj, pending, exits = 0.0, 0, [], {}
        for days, hh, mm, trig, side in rolls:
            when = at(days, hh, mm)
            other = "PE" if side == "CE" else "CE"
            old = legs[side]
            if trig == "eod_rollout":          # the tested (bigger) side rolls out
                big, small = G.uniform(270, 290), G.uniform(140, 155)
                close_px, new_px = big, small / 0.875
                tv2 = {"bigger_side": side, "smaller_side": other, "bigger_sum": round(big, 2), "smaller_sum": round(small, 2),
                       "eod_gap_floor": 0.8, "rolled_side": side,
                       "why": "moving the untested side in would have created a straddle before the overnight gap"}
                step = 200 if side == "CE" else -200
            else:
                big = G.uniform(255, 300)
                small = big * (G.uniform(0.42, 0.49) if trig.startswith("continuous") else G.uniform(0.6, 0.75))
                close_px, new_px = small, 0.875 * big
                tv2 = {"bigger_side": other, "smaller_side": side, "bigger_sum": round(big, 2), "smaller_sum": round(small, 2)}
                tv2.update({"ratio": 0.5} if trig.startswith("continuous") else {"eod_gap_floor": 0.8, "check_time": f"{hh:02d}:{mm:02d}:00"})
                step = -150 if side == "CE" else 150
            mark_path(old, at(days + 3, 10, 0) if old["role"] == "original" else when - timedelta(days=2), when, close_px)
            await f(old, "buy", close_px, when, trig, meta(trig, "close", side, old["strike"], cid, expiry, tv=tv2))
            exits[old["tok"]] = (close_px, when)
            booked += (old["entry"] - close_px) * Q
            n_adj += 1
            nl = new_leg(expiry, old["strike"] + step, side, new_px, f"adjustment_{n_adj}")
            await f(nl, "sell", new_px, when + timedelta(seconds=4), trig,
                    meta(trig, "open", side, nl["strike"], cid, expiry, nl["role"], tv2, {"target_premium": round(new_px, 2)}))
            legs[side] = nl
            pending.append((old, close_px, nl))
        if end is None:
            # Priced so the cycle sits about a third of the way to its
            # checkpoint with puts at 70% of calls (the EOD check armed).
            ce = (booked / Q + legs["CE"]["entry"] + legs["PE"]["entry"] - 1000 / Q) / 1.7
            now_px = {"CE": round(ce, 2), "PE": round(0.7 * ce, 2)}
            for ot, leg in legs.items():
                mark_path(leg, opened if leg["role"] == "original" else at(4, 15, 13), NOW, now_px[ot])
            prices[legs["CE"]["tok"]], prices[legs["PE"]["tok"]] = now_px["CE"], now_px["PE"]
            for leg in legs.values():
                exits[leg["tok"]] = (now_px[leg["opt"]], NOW - timedelta(minutes=1))
            await price_rolled_away(pending, exits)
            live_times.extend([legs["CE"]["tok"], legs["PE"]["tok"]])
            open_cycle_legs = legs
            continue
        closed = at(end, 11, 5) if why == "checkpoint_target" else at(end, 9, 20) if why == "contract_expiry_backstop" else at(end, 13, 40)
        each = (target - booked) / 2 / Q
        tvx = {"checkpoint_target": {"total_cycle_profit": target, "checkpoint_target": GEEK_CAP * 0.005, "cycle_realized_pnl": round(booked, 2)},
               "contract_expiry_backstop": {"contract_expiry": expiry},
               "convergence_stop": {"convergence_premium": 420.0, "combined_now": 455.0, "stop_level": 453.6, "convergence_mode": "fixed_stop"}}[why]
        for ot, leg in legs.items():
            px = max(0.5, leg["entry"] - each)
            mark_path(leg, opened if leg["role"] == "original" else closed - timedelta(days=3), closed, px)
            await f(leg, "buy", px, closed, why, meta(why, "close", ot, leg["strike"], cid, expiry, tv=tvx))
            exits[leg["tok"]] = (px, closed)
        await price_rolled_away(pending, exits)
        prev_close = closed

    pid = {r["instrument_token"]: r["id"] for r in await pool.fetch(
        "SELECT id, instrument_token FROM positions WHERE deployment_id=$1", dep)}
    # Marks only began part-way: cycles 1-2 have no prices stored at all
    # (their journeys are what each leg booked), cycle 3 only the 1-minute
    # candles the candle store backfilled.
    await queries.record_position_marks(pool, [(pid[t], dep, w, round(px, 2)) for t, w, px, c in marks if t in pid and c > 3])
    await queries.upsert_candles(pool, [(t, w, round(px, 2), round(px, 2), round(px, 2), round(px, 2), None)
                                        for t, w, px, c in marks if c == 3], "kite")
    for days in (8, 7, 6, 5):
        await queries.record_decision(pool, dep, at(days, 15, 13), "eod_gap_check", "no_action",
                                      f"PE/CE 0.{G.randint(82, 93)} ≥ floor 0.80: gap small enough to hold overnight",
                                      {"eod_gap_floor": 0.8})
    await queries.record_decision(pool, dep, at(2, 9, 16), "stale_price_guard", "blocked",
                                  f"Holding all management: {open_cycle_legs['CE']['sym']} not traded yet this session",
                                  {"legs": [open_cycle_legs["CE"]["sym"]]})
    await queries.record_decision(pool, dep, at(1, 11, 4), "continuous_50pct_trigger", "blocked",
                                  "Not acted on: live quotes don't confirm the tick prices",
                                  {"tick": {open_cycle_legs["PE"]["sym"]: 151.2}, "quote": {open_cycle_legs["PE"]["sym"]: 166.0}})
    await pool.execute("UPDATE deployments SET status='active' WHERE id=$1", dep)
    return str(dep)


def main():
    prices = asyncio.run(seed())
    import uvicorn
    from app.main import app
    from app.options.resolver import OptionsResolver

    # Kite isn't logged in here: indices' history (what a chart asks Kite
    # for) is synthesised instead, at whatever interval is asked for.
    days_by = {}

    async def index_history(self, token, start, end, interval):
        if token == VIX_TOKEN:   # India VIX, at any interval (app/vix.py)
            out, d = [], start.date()
            while d <= end.date():
                if d.weekday() < 5:
                    if interval == "day":
                        v = vix_level(d)
                        out.append({"date": datetime.combine(d, time(0)), "open": v, "high": v + 0.5, "low": v - 0.5, "close": vix_level(d, 374)})
                    else:
                        step = {"minute": 1, "5minute": 5, "15minute": 15, "60minute": 60}.get(interval, 15)
                        for m in range(0, 375, step):
                            t = datetime.combine(d, time(9, 15)) + timedelta(minutes=m)
                            if t.replace(tzinfo=IST) > NOW:
                                break
                            v = vix_level(d, m)
                            out.append({"date": t, "open": v, "high": v + 0.05, "low": v - 0.05, "close": v})
                d += timedelta(days=1)
            return out
        if interval == "day" and token in TOKEN.values():   # an index's days
            if token not in days_by:
                days_by[token] = index_days(token)
            return [{"date": datetime.combine(d, time(0)), "open": o, "high": h, "low": lo, "close": c}
                    for d, (o, h, lo, c) in days_by[token].items() if start.date() <= d <= end.date()]
        if token not in TOKEN.values():
            # an option contract: a little slow, like Kite, so a backfill's
            # progress is visible (E2E_HISTORY_DELAY seconds per request)
            await asyncio.sleep(float(os.environ.get("E2E_HISTORY_DELAY", "1.0")))
        step = {"minute": 1, "5minute": 5, "15minute": 15, "60minute": 60}.get(interval, 1)
        out, t = [], start
        while t <= end:
            if t.weekday() < 5 and time(9, 15) <= t.time() < time(15, 30):
                e = t.timestamp()
                v = 25000 + 300 * math.sin(e / 200000.0) + 60 * math.sin(e / 9000.0)
                if token not in TOKEN.values():
                    v = 120 + (v - 25000) / 4          # an option's premium, not the index
                out.append({"date": t, "open": v, "high": v + 3, "low": v - 3, "close": v})
            t += timedelta(minutes=step)
        return out
    OptionsResolver.get_historical_candles = index_history
    # The health monitor checks the real clock: here its issues are seeded
    # (seed_ops) and its every-minute loop does nothing.
    from app.ops_health import OpsMonitor

    async def no_loop(self, every_s=60.0):
        return None
    OpsMonitor.loop = no_loop
    # Kite isn't logged in to look INDIA VIX up by name: it's known here
    from app import vix as _vix
    _vix.state["token"] = VIX_TOKEN

    @app.on_event("startup")
    async def _prices():
        async def loop():
            k = 0
            while True:
                k += 1
                if k == 1 and getattr(app.state, "ops", None) is not None:
                    await app.state.ops.refresh_open()   # the seeded issues, for /health
                d = app.state.dispatcher
                for ix, tok in TOKEN.items():
                    d.last_prices[tok] = SPOT[ix] + (k % 11 - 5) * 3.1
                for tok, px in prices.items():
                    d.last_prices[tok] = round(px * (1 + 0.01 * ((k + tok) % 9 - 4) / 4), 2)
                for tok in GEEK_LIVE:
                    d.last_price_times[tok] = datetime.now(IST)
                for tok, basis in FUT_LIVE.items():
                    d.last_prices[tok] = round(d.last_prices[TOKEN["NIFTY"]] + basis, 2)
                await asyncio.sleep(1.0)
        asyncio.create_task(loop())
        print(f"E2E READY {PORT}", flush=True)

    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()

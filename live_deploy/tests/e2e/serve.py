"""Start the real app for the browser tests: reset the test database,
seed a realistic book, then serve on 127.0.0.1:$E2E_PORT.

    TEST_DATABASE_URL=postgresql://t:t@localhost/t_test python tests/e2e/serve.py

The database name must contain "test" (its schema is dropped). Nothing
reaches Kite or sends a push: no Kite session exists, config.json is
ignored, and VAPID keys are unset. Live prices for open legs are written
straight into the dispatcher's cache (no ticks are broadcast, so no
strategy trades while the browser clicks around).

Prints "E2E READY <port>" once it accepts requests, and writes the seeded
deployment ids to tests/e2e/.ids.json for the specs.
"""

import asyncio
import json
import math
import os
import random
import sys
import time as _time
from datetime import datetime, time, timedelta
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
    ("BankNifty DTT Adjusted", "intraday_dtt_adjusted", "intraday", 500_000, "BANKNIFTY", "active", ["DTT"], 120, "intra"),
    ("BankNifty DTT Plain", "intraday_dtt_simple", "intraday", 500_000, "BANKNIFTY", "active", ["DTT"], 120, "intra"),
    ("Sensex Credit Spread", "trend_credit_spread", "positional", 500_000, "SENSEX", "active", ["Spreads"], 90, "pos"),
    ("Pivot Supertrend Options", "pivot_supertrend_options", "intraday", 300_000, "NIFTY", "stopped", [], 60, "intra"),
    ("Bankex Strangle", "strangle_monthly_v2", "positional", 500_000, "BANKEX", "active", ["Strategy Lab"], 6, "pos"),
]
OPEN_LEGS = {"Nifty Strangle A": 4, "BankNifty DTT Plain": 2, "Bankex Strangle": 2}
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
        if style == "intra":
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
        for ot, off, act in [("CE", 8, "sell"), ("PE", -8, "sell"), ("CE", 12, "buy"), ("PE", -12, "buy")][:n]:
            t = ntok()
            px = R.uniform(90, 160) if act == "sell" else R.uniform(25, 50)
            await fill(pool, ids[name], sym(ix, TODAY + timedelta(days=30), k + off * STEP[ix], ot), t, act, q, px, when, "entry")
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
    await queries.record_event(pool, ids["Sensex Credit Spread"], "strategy_error",
                               message="Order rejected: margin check failed. Hedge leg not placed.", metadata={})
    await close_pool(pool)
    (HERE / ".ids.json").write_text(json.dumps({"ids": ids, "password": PASSWORD, "port": PORT}, indent=1))
    return prices


# A strangle_monthly_v2 deployment with the history Geek mode reads:
# fills carrying the strategy's own metadata (cycles, rolls, triggers),
# 5-minute-style price marks, decisions that didn't trade, and candles
# for legs rolls closed. Its open cycle is priced live by the loop below.
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
        t = start.replace(minute=30) + timedelta(hours=1)
        span = max(1.0, (end - start).total_seconds())
        while t < end:
            if t.weekday() < 5 and time(9, 30) <= t.time() <= time(15, 30):
                fr = (t - start).total_seconds() / span
                bump = leg["entry"] * 0.18 * math.sin(math.pi * fr) * (1 if leg["opt"] == "CE" else -1)
                marks.append((leg["tok"], t, max(1.0, leg["entry"] + (exit_px - leg["entry"]) * fr + bump)))
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
    await queries.record_position_marks(pool, [(pid[t], dep, w, round(px, 2)) for t, w, px in marks if t in pid])
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
    async def index_history(self, token, start, end, interval):
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

    @app.on_event("startup")
    async def _prices():
        async def loop():
            k = 0
            while True:
                k += 1
                d = app.state.dispatcher
                for ix, tok in TOKEN.items():
                    d.last_prices[tok] = SPOT[ix] + (k % 11 - 5) * 3.1
                for tok, px in prices.items():
                    d.last_prices[tok] = round(px * (1 + 0.01 * ((k + tok) % 9 - 4) / 4), 2)
                for tok in GEEK_LIVE:
                    d.last_price_times[tok] = datetime.now(IST)
                await asyncio.sleep(1.0)
        asyncio.create_task(loop())
        print(f"E2E READY {PORT}", flush=True)

    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()

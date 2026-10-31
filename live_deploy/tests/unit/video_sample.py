"""A replay (app/geek/replay.replay's answer) of two strangle cycles over
three days, for the video's tests: an entry, a 50% roll, an end-of-day gap
check, then a checkpoint exit with a re-entry that's still running. Prices
are made up but move the way the trades say they did."""

import math
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
DAYS = [datetime(2026, 10, 27, tzinfo=IST), datetime(2026, 10, 28, tzinfo=IST), datetime(2026, 10, 29, tzinfo=IST)]


def at(d, h, m):
    return DAYS[d].replace(hour=h, minute=m)


def _path():
    """The index every 5 minutes: up on day 1, down on day 2, up on day 3."""
    rnd = random.Random(7)
    pts, v = [], 24900.0
    goals = {0: 25130.0, 1: 24840.0, 2: 25020.0}
    for d in range(3):
        t = at(d, 9, 15)
        n = 75
        for i in range(n):
            v += (goals[d] - v) / (n - i + 8) + rnd.gauss(0, 9)
            pts.append((t, round(v, 2)))
            t += timedelta(minutes=5)
    return pts


def sample(live=True):
    path = _path()
    legs = [
        {"symbol": "NIFTY26NOV25300CE", "side": "short", "qty": 75, "entry": 121.0, "kind": "CE", "strike": 25300.0, "t0": at(0, 10, 0), "t1": at(1, 15, 13), "exit": 105.0, "realized": 1200.0},
        {"symbol": "NIFTY26NOV24500PE", "side": "short", "qty": 75, "entry": 119.5, "kind": "PE", "strike": 24500.0, "t0": at(0, 10, 0), "t1": at(0, 13, 40), "exit": 61.0, "realized": 4387.5},
        {"symbol": "NIFTY26NOV24800PE", "side": "short", "qty": 75, "entry": 118.0, "kind": "PE", "strike": 24800.0, "t0": at(0, 13, 40), "t1": at(2, 11, 20), "exit": 95.0, "realized": 1725.0},
        {"symbol": "NIFTY26NOV25100CE", "side": "short", "qty": 75, "entry": 148.0, "kind": "CE", "strike": 25100.0, "t0": at(1, 15, 13), "t1": at(2, 11, 20), "exit": 120.0, "realized": 2100.0},
        {"symbol": "NIFTY26DEC25400CE", "side": "short", "qty": 75, "entry": 118.0, "kind": "CE", "strike": 25400.0, "t0": at(2, 11, 20), "t1": None if live else at(2, 15, 20), "exit": None if live else 104.0, "realized": None if live else 1050.0},
        {"symbol": "NIFTY26DEC24600PE", "side": "short", "qty": 75, "entry": 116.0, "kind": "PE", "strike": 24600.0, "t0": at(2, 11, 20), "t1": None if live else at(2, 15, 20), "exit": None if live else 103.0, "realized": None if live else 975.0},
    ]

    def mark(l, t, s):
        """A rough option price: what it was sold at, moved by the index and decayed by time."""
        days = (t - l["t0"]).total_seconds() / 86400
        d = (s - l["strike"]) if l["kind"] == "CE" else (l["strike"] - s)
        s0 = next(v for tt, v in path if tt >= l["t0"])
        d0 = (s0 - l["strike"]) if l["kind"] == "CE" else (l["strike"] - s0)
        return max(2.0, l["entry"] * math.exp((d - d0) / 260) * (1 - 0.02 * days))

    frames = []
    for t, s in path:
        booked, openp, held = 0.0, 0.0, []
        for j, l in enumerate(legs):
            if l["t0"] > t:
                continue
            if l["t1"] is not None and l["t1"] <= t:
                booked += l["realized"]
                continue
            m = round(mark(l, t, s), 2)
            pnl = (l["entry"] - m) * l["qty"]
            openp += pnl
            held.append([j, m, round(pnl, 2), False, None])
        frames.append({"t": t.isoformat(), "idx": s, "vix": round(13.4 - 0.5 * len(frames) / len(path) + 0.1 * math.sin(len(frames) / 9), 2),
                       "booked": round(booked, 2), "pnl": round(booked + openp, 2), "legs": held,
                       "split": None})
    tot = frames[-1]["pnl"]
    frames[-1]["split"] = [round(tot * 0.74, 2), round(-tot * 0.12, 2), round(tot - tot * 0.74 + tot * 0.12, 2)]

    def frame_of(t):
        return max(i for i, f in enumerate(frames) if datetime.fromisoformat(f["t"]) <= t)

    def fill(action, sym, price, opening, pnl=None):
        return {"action": action, "symbol": sym, "qty": 75.0, "price": price, "opening": opening, "pnl": pnl}

    def ev(t, kind, label, trig, values, fills, booked=None, **more):
        i = frame_of(t)
        return {"t": t.isoformat(), "frame": i, "kind": kind, "label": label, "trigger": trig, "values": values,
                "fills": fills, "booked": booked, "idx": frames[i]["idx"], "vix": frames[i]["vix"], "pnl": frames[i]["pnl"], **more}

    events = [
        ev(at(0, 10, 0), "new", "New cycle", "initial_entry", {"target_premium": 120.5, "days_to_expiry": 30},
           [fill("sell", "NIFTY26NOV25300CE", 121.0, True), fill("sell", "NIFTY26NOV24500PE", 119.5, True)]),
        ev(at(0, 13, 40), "roll", "Roll", "continuous_50pct_trigger",
           {"bigger_side": "CE", "smaller_side": "PE", "bigger_sum": 127.0, "smaller_sum": 61.0, "ratio": 0.5},
           [fill("buy", "NIFTY26NOV24500PE", 61.0, False, 4387.5), fill("sell", "NIFTY26NOV24800PE", 118.0, True)], 4387.5),
        ev(at(1, 15, 13), "adjustment", "Adjustment", "eod_gap_check",
           {"bigger_side": "PE", "smaller_side": "CE", "bigger_sum": 150.0, "smaller_sum": 105.0, "eod_gap_floor": 0.8},
           [fill("buy", "NIFTY26NOV25300CE", 105.0, False, 1200.0), fill("sell", "NIFTY26NOV25100CE", 148.0, True)], 1200.0),
        ev(at(2, 11, 20), "new", "New cycle", "checkpoint_target",
           {"total_cycle_profit": 9412.5, "cycle_realized_pnl": 5587.5, "unrealized": 3825.0, "checkpoint_target": 5000.0, "target_premium": 117.0},
           [fill("sell", "NIFTY26DEC25400CE", 118.0, True), fill("sell", "NIFTY26DEC24600PE", 116.0, True)], None,
           closed_before={"fills": [fill("buy", "NIFTY26NOV24800PE", 95.0, False, 1725.0), fill("buy", "NIFTY26NOV25100CE", 120.0, False, 2100.0)],
                          "booked": 3825.0, "cycle": 7}),
    ]
    prev = None
    for e in events:
        if prev:
            e["since"] = {"idx": round(e["idx"] - prev["idx"], 2), "vix": None, "pnl": None, "t": prev["t"]}
        prev = e
    out_legs = [{**{k: v for k, v in l.items() if k != "realized"}, "t0": l["t0"].isoformat(),
                 "t1": l["t1"].isoformat() if l["t1"] else None, "realized": l["realized"]} for l in legs]
    return {
        "episodes": [{"n": 7, "label": "Cycle 7"}, {"n": 8, "label": "Cycle 8"}], "selected": [1, 2],
        "frames": frames, "legs": out_legs, "events": events,
        "start": at(0, 10, 0).isoformat(), "end": frames[-1]["t"], "index_name": "NIFTY 50", "interval": "5minute",
    }


DEP = {"id": "11111111-1111-1111-1111-111111111111", "strategy_name": "strangle_monthly_v2", "deployment_name": "Nifty Strangle A",
       "initial_capital": 1000000, "config": {"adjustment_trigger_ratio": 0.5, "eod_gap_floor": 0.8, "eod_check_time": "15:13",
                                              "checkpoint_profit_pct_of_capital": 0.005, "convergence_mode": "fixed_stop"}}
DECISIONS = [
    {"t": at(0, 9, 30), "rule": "stale_price_hold", "outcome": "blocked", "summary": "24500PE hasn't traded yet today: management held until it does"},
    {"t": at(0, 15, 13), "rule": "eod_gap_check", "outcome": "no_action", "summary": "PE/CE 0.92 ≥ floor 0.80: gap small enough to hold overnight."},
]
NOW = at(2, 15, 25)

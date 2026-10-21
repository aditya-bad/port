"""GET /v2/deployments/{id}/geek/volatility through real fills, stored VIX,
index days and daily equity (the arithmetic: tests/unit/test_volatility.py).
Days in 2024, which no other test uses."""

from datetime import date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from tests.conftest import add_fill, default_config, make_deployment

IST = ZoneInfo("Asia/Kolkata")
T = datetime.fromisoformat
START = date(2024, 3, 4)   # a Monday


def days(n):
    out, d = [], START
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def kite_history(monkeypatch, client, vix, idx):
    """Kite's history as the view asks for it: VIX in 15-minute and daily
    candles (drifting up 0.4 within each day), and the index's days."""
    from app import vix as V

    async def history(resolver, token, start, end, interval):
        out, d = [], start.date()
        while d <= end.date():
            if token == 264969 and d in vix:
                if interval == "day":
                    out.append({"date": datetime.combine(d, time(0)), "open": vix[d], "high": vix[d] + 0.4, "low": vix[d], "close": vix[d] + 0.4})
                else:
                    for m in range(0, 375, 15):
                        v = vix[d] + (m + 15) / 375 * 0.4
                        out.append({"date": datetime.combine(d, time(9, 15)) + timedelta(minutes=m), "open": v, "high": v, "low": v, "close": round(v - 15 / 375 * 0.4, 4)})
            elif token != 264969 and d in idx and interval == "day":
                p = idx[d]
                out.append({"date": datetime.combine(d, time(0)), "open": p, "high": p, "low": p, "close": p})
            d += timedelta(days=1)
        return out
    monkeypatch.setattr(V.OptionsResolver, "get_historical_candles", history)
    svc = client.app.state.vix
    monkeypatch.setattr(svc, "token", 264969)
    svc._days.clear()


def test_volatility_view(client, monkeypatch):
    pool = client.app.state.db_pool
    cfg = {**default_config("intraday_dtt_simple"), "instrument_tokens": [256265]}
    d = make_deployment(client, name="vol dtt", config=cfg)       # NIFTY: the real VIX, not a stand-in
    did = UUID(d["id"])
    ds = days(14)
    vix = {day: 13 + i * 0.5 + (0.8 if i % 3 == 0 else 0) for i, day in enumerate(ds)}   # climbing, unevenly
    px, idx = 22000.0, {}
    for i, day in enumerate(ds):
        px = px * (1.02 if i == 5 else 1.001)                       # one day moves far more than priced
        idx[day] = px
    kite_history(monkeypatch, client, vix, idx)
    eq, prev = 500000.0, None
    for i, day in enumerate(ds):
        close = vix[day] + 0.4                                       # the day's last minute
        if prev is not None:
            eq += -2000 * (close - prev) + (60 if i % 2 else -60)    # about ₹2,000 lost per point VIX rises
        prev = close
        snap = datetime.combine(day, time(15, 40), IST)
        client.portal.call(lambda: pool.execute(
            "INSERT INTO deployment_snapshots (deployment_id, snapshot_at, cash, open_positions_value, total_value, realized_pnl_cumulative) "
            "VALUES ($1, $2, $3, 0, $3, 0)", did, snap, eq))
    # two short straddle legs on day 2, closed the same afternoon at a loss
    t0 = datetime.combine(ds[1], time(10, 0), IST)
    for sym, tok in (("NIFTY24MAR22000CE", 9501), ("NIFTY24MAR22000PE", 9502)):
        add_fill(client, d["id"], sym, tok, "sell", 75, 100.0, t0, "entry", {"trigger": "entry_time_reached", "action": "sell_open"})
        add_fill(client, d["id"], sym, tok, "buy", 75, 104.0, t0 + timedelta(hours=4), "force_exit", {"trigger": "force_exit", "action": "buy_close"})
    r = client.get(f"/v2/deployments/{d['id']}/geek/volatility")
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["has_vix"] and v["has_index"] and v["proxy"] is False and v["side"] == "sold"
    (t,) = v["rows"]
    assert t["pnl"] == -600.0 and t["side"] == "sold"
    assert t["vix_in"] == round(vix[ds[1]] + 30 / 375 * 0.4, 2), "VIX at 10:00: the close of the 09:45–10:00 candle, the last one finished"
    assert t["dvix"] > 0, "and higher by 14:00"
    g = {x["key"]: x for x in v["groups"]}
    assert [b["label"] for b in g["band"]["buckets"]] == ["12–15"] and g["change"]["buckets"][0]["label"] == "Within a point"
    pp = v["per_point"]
    assert pp["n"] == 13 and -2200 < pp["slope"] < -1800 and pp["r"] < -0.9, "about ₹2,000 lost per VIX point"
    assert v["summary"]["days_moved_more"] >= 1 and v["summary"]["days_compared"] == 13
    big = next(x for x in v["days"] if x["day"] == ds[5].isoformat())
    assert big["ratio"] > 2, "the 2% day moved more than twice what VIX priced"
    assert [w["week"] for w in v["weeks"]] == ["2024-03-04", "2024-03-11", "2024-03-18"]
    assert v["weeks"][1]["dvix"] > 0 and v["weeks"][0]["trades"] == 1
    assert v["now"]["vix"] is not None and v["now"]["expected_move_pct"] > 0
    assert set(v["here"]) >= {"n", "low", "high"} and v["filters"] == [], "one trade: too few for what-ifs"


def test_without_kite_it_says_so(client, monkeypatch):
    from app import vix as V
    from app.options import NoKiteSession

    async def no_session(resolver, *a, **k):
        raise NoKiteSession("no")
    monkeypatch.setattr(V.OptionsResolver, "get_historical_candles", no_session)
    svc = client.app.state.vix
    monkeypatch.setattr(svc, "token", 264969)
    svc._days.clear()
    cfg = {**default_config("intraday_dtt_simple"), "instrument_tokens": [256265]}
    d = make_deployment(client, name="vol no kite", config=cfg)
    v = client.get(f"/v2/deployments/{d['id']}/geek/volatility").json()
    assert v["has_vix"] is False and "Kite isn't logged in" in v["error"]


def test_volatility_says_when_vix_is_a_stand_in(client):
    cfg = {**default_config("intraday_dtt_simple"), "instrument_tokens": [260105]}
    d = make_deployment(client, name="vol bank", config=cfg)       # BANKNIFTY
    r = client.get(f"/v2/deployments/{d['id']}/geek/volatility").json()
    assert r["proxy"] is True
    assert r["rows"] == [] and r["groups"] == [] and r["per_point"] is None


def test_without_equity_snapshots_it_uses_what_was_booked(client, monkeypatch):
    cfg = {**default_config("intraday_dtt_simple"), "instrument_tokens": [256265]}
    d = make_deployment(client, name="vol booked", config=cfg)
    kite_history(monkeypatch, client, {day: 14.0 for day in days(12)}, {day: 22000.0 for day in days(12)})
    for i, day in enumerate(days(12)):
        t0 = datetime.combine(day, time(10, 0), IST)
        add_fill(client, d["id"], f"NIFTY24MAR{21000 + i * 50}CE", 9600 + i, "sell", 75, 100.0, t0, "entry", {"trigger": "entry", "action": "sell_open"})
        add_fill(client, d["id"], f"NIFTY24MAR{21000 + i * 50}CE", 9600 + i, "buy", 75, 100.0 + (i % 3) * 4, t0 + timedelta(hours=3), "exit", {"trigger": "exit", "action": "buy_close"})
    v = client.get(f"/v2/deployments/{d['id']}/geek/volatility").json()
    assert v["pnl_source"] == "booked"
    booked = {x["day"]: x["pnl"] for x in v["days"] if x["pnl"] is not None}
    assert booked[days(12)[2].isoformat()] == -600.0, "a day's P&L: what closed that day"

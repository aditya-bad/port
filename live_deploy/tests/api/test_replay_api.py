"""GET /v2/deployments/{id}/geek/replay through the real app: every kind of
deployment has it, a straddle day replays with its entry and exit, and
without Kite it still replays the book and says why the index is missing
(the arithmetic: tests/strategies/test_replay_geek.py)."""

from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from tests.conftest import add_fill, default_config, make_deployment

IST = ZoneInfo("Asia/Kolkata")


def no_kite(monkeypatch, client):
    from app import vix as V
    from app.options import NoKiteSession

    async def nope(resolver, *a, **k):
        raise NoKiteSession("no")
    monkeypatch.setattr(V.OptionsResolver, "get_historical_candles", nope)
    monkeypatch.setattr(client.app.state.vix, "token", 264969)
    client.app.state.vix._days.clear()


def test_every_strategy_has_replay_and_an_empty_one_says_so(client):
    from app.strategies.registry import list_strategies
    for s in list_strategies():
        d = make_deployment(client, name=f"rp {s['name']}", strategy=s["name"],
                            mode="positional" if s["name"] in ("strangle_monthly_v2", "calendar_btst", "trend_credit_spread", "weekly_ema_st_spread") else "intraday",
                            config=default_config(s["name"]))
        r = client.get(f"/v2/deployments/{d['id']}/geek/replay")
        assert r.status_code == 200, s["name"]
        assert r.json() == {"episodes": [], "selected": None}


def test_a_straddle_day_replays_from_its_entry_to_its_exit(client, monkeypatch):
    no_kite(monkeypatch, client)
    cfg = {**default_config("intraday_dtt_simple"), "instrument_tokens": [256265]}
    d = make_deployment(client, name="rp dtt", config=cfg)
    at = lambda h, m: datetime(2024, 5, 6, h, m, tzinfo=IST)  # noqa: E731
    meta = {"trigger": "entry", "context": {"spot": 22400.0, "vix": 14.1}}
    add_fill(client, d["id"], "NIFTY2450922400CE", 101, "sell", 75, 120, at(10, 0), "entry", meta)
    add_fill(client, d["id"], "NIFTY2450922400PE", 102, "sell", 75, 110, at(10, 0), "entry", meta)
    add_fill(client, d["id"], "NIFTY2450922400CE", 101, "buy", 75, 90, at(15, 0), "force_exit", {"trigger": "force_exit"})
    add_fill(client, d["id"], "NIFTY2450922400PE", 102, "buy", 75, 100, at(15, 0), "force_exit", {"trigger": "force_exit"})
    v = client.get(f"/v2/deployments/{d['id']}/geek/replay").json()
    assert [e["label"] for e in v["episodes"]] == ["Trade 1"]
    assert v["episodes"][0]["booked"] == (120 - 90) * 75 + (110 - 100) * 75
    assert [e["kind"] for e in v["events"]] == ["entry", "exit"]
    assert v["events"][0]["idx"] == 22400.0 and v["events"][0]["vix"] == 14.1      # from the fill's own context
    assert v["events"][1]["booked"] == 3000 and v["events"][1]["since"]["t"] == v["events"][0]["t"]
    assert v["no_index"] and "Kite" in v["market_error"]
    assert v["frames"] and v["frames"][-1]["booked"] == 3000
    assert {l["strike"] for l in v["legs"]} == {22400.0}
    assert client.get(f"/v2/deployments/{d['id']}/geek/replay?from=x&to=-3").status_code == 200   # bad params clamp

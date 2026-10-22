"""What the UI shows of stall repairs (app/repair): GET
/v2/deployments/{id}/repairs, and the repaired mark on the strangle's
cycles, the adjusted straddle's sessions and the trades book — set by a
fill carrying metadata.repair, as the repair CLI writes them."""

from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from app.db import queries
from tests.conftest import add_fill, default_config, make_deployment

IST = ZoneInfo("Asia/Kolkata")
D = datetime(2024, 6, 3, tzinfo=IST)          # a Monday no other test uses


def at(days, h, m):
    return (D + timedelta(days=days)).replace(hour=h, minute=m)


def repair_meta(**m):
    return {**m, "repair": {"run": "r1", "source": "replay-1m", "day": "2024-06-04", "from": "12:16", "to": "15:30",
                            "minute": "15:13", "phase": "up"}}


def test_repairs_listed_and_the_repaired_cycle_marked(client):
    pool = client.app.state.db_pool
    cfg = {**default_config("strangle_monthly_v2"), "instrument": "NIFTY", "instrument_tokens": [256265]}
    d = make_deployment(client, name="rep strangle", strategy="strangle_monthly_v2", mode="positional", config=cfg)
    m = lambda c, **x: {"cycle_id": c, "trigger": "initial_entry", "leg_role": "original", **x}  # noqa: E731
    # cycle 1: live start to finish
    add_fill(client, d["id"], "NIFTY24JUN23000PE", 11, "sell", 75, 100, at(0, 10, 0), "initial_entry", m(1))
    add_fill(client, d["id"], "NIFTY24JUN23000PE", 11, "buy", 75, 80, at(0, 14, 0), "checkpoint_target", m(1, trigger="checkpoint_target"))
    # cycle 2: closed by a repair
    add_fill(client, d["id"], "NIFTY24JUN23100PE", 12, "sell", 75, 100, at(1, 10, 0), "initial_entry", m(2))
    add_fill(client, d["id"], "NIFTY24JUN23100PE", 12, "buy", 75, 70, at(1, 15, 13), "checkpoint_target", repair_meta(**m(2, trigger="checkpoint_target")))
    summary = {"fills": 1, "pnl": 2250.0, "left": 900.0, "change": 1350.0, "booked": 2250.0, "reasons": {"checkpoint_target": 1},
               "fills_detail": [{"minute": "15:13", "phase": "up", "action": "buy", "symbol": "NIFTY24JUN23100PE", "qty": 75.0,
                                 "price": 70.0, "reason": "checkpoint_target", "realized": 2250.0, "position_id": "x"}]}
    client.portal.call(lambda: pool.execute(
        "INSERT INTO stall_repairs (deployment_id, day, window_from, window_to, choices, summary, undo) VALUES ($1,$2,$3,$4,$5,$6,$7)",
        UUID(d["id"]), date(2024, 6, 4), at(1, 12, 16), at(1, 15, 30), {"13:05": "up_first"}, summary, {}))

    r = client.get(f"/v2/deployments/{d['id']}/repairs").json()["repairs"]
    assert len(r) == 1 and r[0]["fills"] == 1 and r[0]["choices"] == {"13:05": "up_first"} and r[0]["change"] == 1350
    assert r[0]["trades"][0]["symbol"] == "NIFTY24JUN23100PE" and "position_id" not in r[0]["trades"][0]
    cyc = {c["cycle_id"]: c["repaired"] for c in client.get(f"/v2/deployments/{d['id']}/geek/cycles").json()["rows"]}
    assert cyc == {1: None, 2: "2024-06-04"}
    trades = [t for t in client.get("/v2/book").json()["trades"] if t["deployment_id"] == d["id"]]
    assert sorted(t["repaired"] or "" for t in trades) == ["", "2024-06-04"], "the day it repaired, to link to"
    # an undone repair isn't listed
    client.portal.call(lambda: pool.execute("UPDATE stall_repairs SET undone_at = now() WHERE deployment_id = $1", UUID(d["id"])))
    assert client.get(f"/v2/deployments/{d['id']}/repairs").json() == {"repairs": []}
    assert client.get("/v2/deployments/00000000-0000-0000-0000-000000000000/repairs").status_code == 404


def test_the_repaired_session_is_marked(client):
    cfg = {**default_config("intraday_dtt_adjusted"), "instrument_tokens": [260105]}
    d = make_deployment(client, name="rep adj", strategy="intraday_dtt_adjusted", config=cfg)
    m = {"leg_role": "original", "trigger": "entry", "strike": 48000}
    for day, close_meta in ((0, {"trigger": "force_exit"}), (1, repair_meta(trigger="force_exit"))):
        for sym, tok in (("BANKNIFTY24JUN48000CE", 21 + day * 2), ("BANKNIFTY24JUN48000PE", 22 + day * 2)):
            add_fill(client, d["id"], sym, tok, "sell", 35, 200, at(day, 10, 0), "entry", m)
            add_fill(client, d["id"], sym, tok, "buy", 35, 180, at(day, 15, 0), "force_exit", close_meta)
    rows = client.get(f"/v2/deployments/{d['id']}/geek/sessions").json()["sessions"]
    assert {r["day"]: r["repaired"] for r in rows} == {"2024-06-03": None, "2024-06-04": "2024-06-04"}

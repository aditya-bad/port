"""Shadow mode over the API: a deployment's checked fills, today's
would-be rejections for the Alerts panel, and the verdict on each fill
of a cycle."""

from datetime import datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app import shadow
from tests.conftest import add_fill, call_in_app, make_deployment

IST = ZoneInfo("Asia/Kolkata")
TODAY = datetime.now(IST).date()


def at(day, hhmm):
    h, m = map(int, hhmm.split(":"))
    return datetime.combine(day, time(h, m), IST)


def check(client, d, r, symbol, token, action, qty, price, when, **kw):
    """Store a shadow check for a recorded fill, as the service would."""
    pool = client.app.state.db_pool
    f = {"lot_id": r["lot_id"], "deployment_id": UUID(d["id"]), "executed_at": when, "symbol": symbol,
         "instrument_token": token, "action": action, "qty": qty, "paper_price": price}
    res = shadow.assess(action, qty, price, tick=0.05, freeze=1800, band=(1.0, 500.0), **kw)
    call_in_app(client, lambda: shadow.save(pool, f, res))
    return res


BOOK = {"buy": [{"price": 99.6, "quantity": 500}], "sell": [{"price": 100.4, "quantity": 500}]}


def test_a_deployments_checked_fills_newest_first_with_counts(client):
    d = make_deployment(client)
    r1 = add_fill(client, d["id"], "NIFTY26OCT25000CE", 11, "sell", 75, 100, at(TODAY, "09:30"), reason="entry")
    r2 = add_fill(client, d["id"], "NIFTY26OCT25000CE", 11, "buy", 75, 100, at(TODAY, "10:30"), reason="exit")
    check(client, d, r1, "NIFTY26OCT25000CE", 11, "sell", 75, 100.0, at(TODAY, "09:30"), lot=75, depth=BOOK)
    check(client, d, r2, "NIFTY26OCT25000CE", 11, "buy", 75, 100.0, at(TODAY, "10:30"), lot=75, depth=None)
    body = client.get(f"/v2/deployments/{d['id']}/shadow").json()
    assert body["enabled"] is True and body["buffer_pct"] == shadow.BUFFER_PCT
    assert [c["verdict"] for c in body["checks"]] == ["unknown", "worse"]
    assert body["counts"] == {"worse": 1, "unknown": 1}
    w = body["checks"][1]
    assert w["shortfall"] == pytest.approx(30.0) and w["limit_price"] == 99.5 and w["lot_id"] == str(r1["lot_id"])
    assert {c["rule"] for c in w["checks"]} == {"lot", "freeze", "band", "book"}
    assert client.get(f"/v2/deployments/{d['id']}/shadow?limit=1").json()["checks"][0]["verdict"] == "unknown"
    assert client.get("/v2/deployments/00000000-0000-0000-0000-000000000000/shadow").status_code == 404


def test_todays_would_be_rejections(client):
    d = make_deployment(client, name="Odd Lots")
    r = add_fill(client, d["id"], "NIFTY26OCT25000CE", 11, "sell", 100, 100, at(TODAY, "09:30"), reason="entry")
    check(client, d, r, "NIFTY26OCT25000CE", 11, "sell", 100, 100.0, at(TODAY, "09:30"), lot=75, depth=BOOK)
    body = client.get("/v2/shadow/rejections").json()
    assert body["day"] == TODAY.isoformat()
    [x] = body["rejections"]
    assert x["name"] == "Odd Lots" and x["rule"] == "lot" and x["n"] == 1 and "whole lots" in x["detail"]
    assert client.get(f"/v2/shadow/rejections?day={(TODAY - timedelta(days=1)).isoformat()}").json()["rejections"] == []
    assert client.get("/v2/shadow/rejections?day=nope").status_code == 400


def test_each_fill_of_a_cycle_carries_its_verdict(client):
    d = make_deployment(client)
    r1 = add_fill(client, d["id"], "X", 11, "sell", 75, 100, at(TODAY, "09:30"), reason="entry")
    add_fill(client, d["id"], "X", 11, "buy", 75, 70, at(TODAY, "10:30"), reason="exit")
    check(client, d, r1, "X", 11, "sell", 75, 100.0, at(TODAY, "09:30"), lot=75, depth=BOOK)
    [c] = client.get(f"/deployments/{d['id']}/cycles").json()
    ch = client.get(f"/deployments/{d['id']}/cycles/{c['key']}/chart").json()
    fills = [f for e in ch["events"] for f in e["fills"]]
    assert [f["shadow"]["verdict"] if f["shadow"] else None for f in fills] == ["worse", None], "the exit wasn't checked"
    assert fills[0]["lot_id"] == str(r1["lot_id"])


def test_ready_for_live_card_per_deployment_and_for_all(client):
    from tests.conftest import sql
    d = make_deployment(client, name="Card")
    y = TODAY - timedelta(days=1)
    # trade 1: Kite would refuse its entry (100 isn't a whole 75-lot); it made ₹3,000
    r1 = add_fill(client, d["id"], "NIFTY26OCT25000CE", 11, "sell", 100, 100, at(y, "09:30"), reason="entry")
    r2 = add_fill(client, d["id"], "NIFTY26OCT25000CE", 11, "buy", 100, 70, at(y, "10:30"), reason="exit")
    check(client, d, r1, "NIFTY26OCT25000CE", 11, "sell", 100, 100.0, at(y, "09:30"), lot=75, depth=BOOK)
    check(client, d, r2, "NIFTY26OCT25000CE", 11, "buy", 100, 70.0, at(y, "10:30"), lot=None, depth={"sell": [{"price": 70.0, "quantity": 500}]})
    # an open Repair case
    sql(client, "INSERT INTO day_checks (day, deployment_id, status, headline) VALUES ($1, $2::uuid, 'mismatch', 'h')", y, d["id"])
    card = client.get(f"/v2/deployments/{d['id']}/shadow").json()["card"]
    assert card["state"] == "not_yet" and card["sessions"] == 1
    assert card["refused"]["n"] == 1 and card["refused"]["pnl"] == pytest.approx(3000)
    assert card["total"]["pnl"] == pytest.approx(3000)
    rules = {r["key"]: r for r in card["rules"]}
    assert rules["record"]["value"] == "1 of 20" and rules["rejects"]["value"] == "1 refused"
    assert rules["repairs"]["value"] == "1 open"
    allc = client.get("/v2/shadow/scorecards").json()
    assert allc[d["id"]] == card
    other = make_deployment(client, name="Nothing yet")
    assert client.get("/v2/shadow/scorecards").json()[other["id"]]["state"] == "none"

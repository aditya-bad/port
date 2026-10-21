"""The trade journal (/v2/journal): notes on a day, a deployment or one of
its trades, with tags; each note carries that day's booked P&L, and the
tags add up the P&L of the days they mark."""

from datetime import datetime

from tests.conftest import add_fill, make_deployment

T = datetime.fromisoformat


def note(client, **body):
    r = client.post("/v2/journal", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _trade(client, dep_id, sym, tok, sell, buy, opened, closed):
    add_fill(client, dep_id, sym, tok, "sell", 75, sell, T(opened), "entry", {"trigger": "entry", "action": "open"})
    add_fill(client, dep_id, sym, tok, "buy", 75, buy, T(closed), "stop", {"trigger": "stop", "action": "close"})


def test_a_note_on_a_deployments_day_carries_that_days_pnl(client):
    d = make_deployment(client, name="journal dtt")
    _trade(client, d["id"], "NIFTY26OCT25000CE", 7301, 100, 90, "2026-10-19T10:00:00+05:30", "2026-10-19T11:00:00+05:30")   # +750
    _trade(client, d["id"], "NIFTY26OCT25000PE", 7302, 100, 120, "2026-10-19T10:00:00+05:30", "2026-10-19T14:00:00+05:30")  # −1500
    n = note(client, day="2026-10-19", deployment_id=d["id"], body="  RBI policy at 10: stayed out of the way  ",
             tags=["Event Day", "event   day", " News "])
    assert n["body"] == "RBI policy at 10: stayed out of the way", "trimmed"
    assert n["tags"] == ["event day", "news"], "lower case, single spaces, no duplicates"
    assert (n["deployment_name"], n["day_pnl"], n["day_closed"]) == ("journal dtt", -750.0, 2)
    assert n["author"] == "admin"
    other = note(client, day="2026-10-20", deployment_id=d["id"], body="quiet")
    assert other["day_pnl"] == 0 and other["day_closed"] == 0
    got = client.get(f"/v2/journal?deployment_id={d['id']}").json()["entries"]
    assert [e["day"] for e in got] == ["2026-10-20", "2026-10-19"], "newest day first"


def test_a_whole_day_note_counts_every_deployment(client):
    a, b = make_deployment(client, name="journal a"), make_deployment(client, name="journal b")
    _trade(client, a["id"], "BANKNIFTY26OCT56000CE", 7311, 200, 150, "2026-10-16T10:00:00+05:30", "2026-10-16T12:00:00+05:30")
    _trade(client, b["id"], "BANKNIFTY26OCT56000PE", 7312, 200, 210, "2026-10-16T10:00:00+05:30", "2026-10-16T12:00:00+05:30")
    n = note(client, day="2026-10-16", body="Kite down 11:00–11:20", tags=["kite down"])
    assert n["deployment_id"] is None and n["day_closed"] >= 2
    day = client.get("/v2/journal?day_from=2026-10-16&day_to=2026-10-16").json()["entries"]
    assert n["id"] in [e["id"] for e in day]
    # a deployment's page can include the whole-day notes
    own = client.get(f"/v2/journal?deployment_id={a['id']}").json()["entries"]
    assert n["id"] not in [e["id"] for e in own]
    both = client.get(f"/v2/journal?deployment_id={a['id']}&general=true").json()["entries"]
    assert n["id"] in [e["id"] for e in both]


def test_a_note_on_a_trade_belongs_to_its_deployment(client):
    d, other = make_deployment(client, name="journal trade"), make_deployment(client, name="journal other")
    _trade(client, d["id"], "NIFTY26OCT24900PE", 7321, 80, 60, "2026-10-14T10:00:00+05:30", "2026-10-14T13:00:00+05:30")
    pos = client.get(f"/deployments/{d['id']}/positions?status=closed").json()[0]
    n = note(client, day="2026-10-14", deployment_id=d["id"], position_id=pos["id"], body="took profit early", tags=["lesson"])
    assert n["position_symbol"] == "NIFTY26OCT24900PE"
    assert client.get(f"/v2/journal?position_id={pos['id']}").json()["entries"][0]["id"] == n["id"]
    r = client.post("/v2/journal", json={"day": "2026-10-14", "deployment_id": other["id"], "position_id": pos["id"], "body": "x"})
    assert r.status_code == 400 and "that trade's deployment" in r.json()["detail"]
    r = client.post("/v2/journal", json={"day": "2026-10-14", "position_id": pos["id"], "body": "x"})
    assert r.status_code == 400


def test_bad_notes_are_refused(client):
    d = make_deployment(client, name="journal bad")
    for body, why in [({"body": "   "}, "empty"), ({"body": "x" * 4001}, "too long"),
                      ({"body": "ok", "tags": ["t"] * 0 + [f"tag{i}" for i in range(11)]}, "too many tags"),
                      ({"body": "ok", "tags": ["x" * 31]}, "tag too long"), ({"body": "ok", "day": "not a day"}, "bad day")]:
        r = client.post("/v2/journal", json=body)
        assert r.status_code == 422, (why, r.text)
    r = client.post("/v2/journal", json={"body": "ok", "deployment_id": "00000000-0000-0000-0000-000000000000"})
    assert r.status_code == 404
    today = note(client, body="no day given", deployment_id=d["id"])
    from app import clock
    assert today["day"] == clock.now_ist().date().isoformat(), "today, IST"


def test_edit_search_and_delete(client):
    d = make_deployment(client, name="journal edit")
    n = note(client, day="2026-10-13", deployment_id=d["id"], body="first draft", tags=["range day"])
    r = client.patch(f"/v2/journal/{n['id']}", json={"body": "Gap up, then a range all day", "tags": ["Gap Open", "range day"]})
    assert r.status_code == 200
    e = r.json()
    assert e["body"] == "Gap up, then a range all day" and e["tags"] == ["gap open", "range day"] and e["updated_at"] >= e["created_at"]
    assert client.patch(f"/v2/journal/{n['id']}", json={"body": " "}).status_code == 422
    found = client.get("/v2/journal?q=RANGE all").json()["entries"]
    assert [x["id"] for x in found] == [n["id"]], "search is case-insensitive"
    assert [x["id"] for x in client.get("/v2/journal?tag=Gap%20Open").json()["entries"]] == [n["id"]]
    assert client.delete(f"/v2/journal/{n['id']}").status_code == 200
    assert client.delete(f"/v2/journal/{n['id']}").status_code == 404
    assert client.patch(f"/v2/journal/{n['id']}", json={"body": "x"}).status_code == 404


def test_tags_add_up_the_days_they_mark(client):
    d = make_deployment(client, name="journal tags")
    _trade(client, d["id"], "NIFTY26SEP25000CE", 7331, 100, 80, "2026-09-07T10:00:00+05:30", "2026-09-07T11:00:00+05:30")   # +1500
    _trade(client, d["id"], "NIFTY26SEP25100CE", 7332, 100, 110, "2026-09-08T10:00:00+05:30", "2026-09-08T11:00:00+05:30")  # −750
    note(client, day="2026-09-07", deployment_id=d["id"], body="a", tags=["trend day"])
    note(client, day="2026-09-07", deployment_id=d["id"], body="b", tags=["trend day"])          # same day: counted once
    note(client, day="2026-09-08", deployment_id=d["id"], body="c", tags=["trend day", "lesson"])
    body = client.get(f"/v2/journal/tags?deployment_id={d['id']}").json()
    t = {x["tag"]: x for x in body["tags"]}
    assert (t["trend day"]["notes"], t["trend day"]["days"], t["trend day"]["pnl"], t["trend day"]["avg"]) == (3, 2, 750.0, 375.0)
    assert (t["lesson"]["days"], t["lesson"]["pnl"]) == (1, -750.0)
    assert "lesson" not in body["suggested"] and "event day" in body["suggested"]
    # a deleted deployment takes its notes with it
    client.post(f"/deployments/{d['id']}/stop?force_close=true")
    r = client.post(f"/deployments/{d['id']}/delete")
    assert r.status_code in (200, 204), r.text
    assert client.get(f"/v2/journal?deployment_id={d['id']}").json()["entries"] == []

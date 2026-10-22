"""The repair marks every page shows (Phase 4 of the Repair page): the
days each deployment was repaired, or flagged by the end-of-day check and
not repaired yet, in /v2/book (app/v2/book.py: list_repairs) and the
weekly review; and a repair made by the script, with no check of its day,
still opening its case."""

from datetime import date, datetime, timedelta

from tests.conftest import make_deployment, sql

from app.clock import IST

TODAY = datetime.now(IST).date()


def check(client, dep, day, status, headline="h"):
    sql(client, "INSERT INTO day_checks (day, deployment_id, status, headline) VALUES ($1, $2::uuid, $3, $4)",
        day, dep["id"], status, headline)


def repair(client, dep, day, undone=False):
    return sql(client, "INSERT INTO stall_repairs (deployment_id, day, window_from, window_to, undo, undone_at) "
                       "VALUES ($1::uuid, $2, now(), now(), '{}', $3) RETURNING id",
               dep["id"], day, datetime.now(IST) if undone else None)[0]["id"]


def marks(client, dep):
    client.app.state.v2_book = None
    return [(m["day"], m["state"]) for m in client.get("/v2/book").json()["repairs"] if m["deployment_id"] == dep["id"]]


def test_the_book_marks_repaired_and_flagged_days(client):
    d = make_deployment(client, name="Marked")
    y, old = TODAY - timedelta(days=1), TODAY - timedelta(days=30)
    check(client, d, y, "mismatch", "Traded differently from 13:31")
    check(client, d, old, "rule")                                     # flagged a month ago: no longer marked
    check(client, d, TODAY - timedelta(days=2), "ok")                 # as expected: nothing
    check(client, d, TODAY - timedelta(days=3), "cant_check")         # couldn't be replayed: not a finding
    check(client, d, TODAY - timedelta(days=4), "rule")
    repair(client, d, TODAY - timedelta(days=4))                      # flagged, then repaired: one mark, repaired
    repair(client, d, old)                                            # a repair is marked however old
    repair(client, d, TODAY - timedelta(days=5), undone=True)         # undone: gone
    assert marks(client, d) == [(old.isoformat(), "repaired"), ((TODAY - timedelta(days=4)).isoformat(), "repaired"),
                                (y.isoformat(), "flagged")]
    client.app.state.v2_book = None
    f = next(m for m in client.get("/v2/book").json()["repairs"] if m["deployment_id"] == d["id"] and m["state"] == "flagged")
    assert f["status"] == "mismatch" and f["headline"] == "Traded differently from 13:31"


def test_the_week_in_review_lists_its_repairs(client):
    d = make_deployment(client, name="Reviewed")
    monday = TODAY - timedelta(days=TODAY.weekday() + 14)            # a week two weeks back
    check(client, d, monday + timedelta(days=1), "rule", "Still holding after 15:15")
    repair(client, d, monday + timedelta(days=2))
    repair(client, d, monday + timedelta(days=8))                     # the week after
    w = client.get(f"/v2/review?week={monday.isoformat()}").json()
    mine = [(r["day"], r["state"], r["name"]) for r in w["repairs"] if r["deployment_id"] == d["id"]]
    # flagged two weeks ago and never repaired: still in that week's review
    assert mine == [((monday + timedelta(days=1)).isoformat(), "flagged", "Reviewed"),
                    ((monday + timedelta(days=2)).isoformat(), "repaired", "Reviewed")]


def test_a_repair_with_no_check_of_its_day_still_has_its_case(client):
    d = make_deployment(client, name="Script repaired")
    day = date(2026, 9, 9)
    assert client.get(f"/v2/repair-cases/{day}/{d['id']}").status_code == 404
    rid = repair(client, d, day)
    c = client.get(f"/v2/repair-cases/{day}/{d['id']}").json()
    assert c["check"] is None and c["applied"]["id"] == str(rid)
    # nothing to plan without a check: that still says so
    assert client.post(f"/v2/repair-cases/{day}/{d['id']}/plan", json={"choices": {}}).status_code == 404
    sql(client, "UPDATE stall_repairs SET undone_at = now() WHERE id = $1", rid)
    assert client.get(f"/v2/repair-cases/{day}/{d['id']}").status_code == 404

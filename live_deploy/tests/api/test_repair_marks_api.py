"""The repair marks every page shows (Phase 4 of the Repair page): the
days each deployment was repaired, or flagged by the end-of-day check and
not repaired yet, in /v2/book (app/v2/book.py: list_repairs) and the
weekly review; and a repair made by the script, with no check of its day,
still opening its case."""

from datetime import date, datetime, timedelta

from tests.conftest import add_fill, make_deployment, sql

from app.clock import IST

TODAY = datetime.now(IST).date()


def check(client, dep, day, status, headline="h"):
    sql(client, "INSERT INTO day_checks (day, deployment_id, status, headline) VALUES ($1, $2::uuid, $3, $4)",
        day, dep["id"], status, headline)


def repair(client, dep, day, undone=False):
    return sql(client, "INSERT INTO stall_repairs (deployment_id, day, window_from, window_to, undo, undone_at) "
                       "VALUES ($1::uuid, $2, now(), now(), '{}', $3) RETURNING id",
               dep["id"], day, datetime.now(IST) if undone else None)[0]["id"]


def marks(client, dep, full=False):
    client.app.state.v2_book = None
    ms = [m for m in client.get("/v2/book").json()["repairs"] if m["deployment_id"] == dep["id"]]
    return ms if full else [(m["day"], m["state"]) for m in ms]


def test_the_book_marks_repaired_and_flagged_days(client):
    d = make_deployment(client, name="Marked")
    y, old = TODAY - timedelta(days=1), TODAY - timedelta(days=30)
    check(client, d, y, "mismatch", "Traded differently from 13:31")
    stale = TODAY - timedelta(days=25)
    check(client, d, stale, "rule")                                   # flagged weeks ago, never dealt with: still marked
    check(client, d, old, "rule")
    check(client, d, TODAY - timedelta(days=2), "ok")                 # as expected: nothing
    check(client, d, TODAY - timedelta(days=3), "cant_check")         # couldn't be replayed: not a finding
    check(client, d, TODAY - timedelta(days=4), "rule")
    repair(client, d, TODAY - timedelta(days=4))                      # flagged, then repaired: one mark, repaired
    repair(client, d, old)                                            # a repair is marked however old
    repair(client, d, TODAY - timedelta(days=5), undone=True)         # undone: gone
    assert marks(client, d) == [(old.isoformat(), "repaired"), (stale.isoformat(), "flagged"),
                                ((TODAY - timedelta(days=4)).isoformat(), "repaired"), (y.isoformat(), "flagged")]
    client.app.state.v2_book = None
    f = next(m for m in client.get("/v2/book").json()["repairs"] if m["deployment_id"] == d["id"] and m["day"] == y.isoformat())
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


# ── by cycle ──────────────────────────────────────────────────────────

def test_an_intraday_repair_is_its_day_and_current_until_the_next_session(client):
    d = make_deployment(client, name="Intraday")
    repair(client, d, TODAY - timedelta(days=30))
    repair(client, d, TODAY)
    old, now = marks(client, d, full=True)
    assert old["cycle"] == {"from": old["day"], "to": old["day"], "current": False}, "a month ago: long over"
    assert now["cycle"] == {"from": now["day"], "to": now["day"], "current": True}, "today's: until tomorrow's open"


def test_a_positional_repair_is_its_whole_cycle_current_until_a_new_entry(client):
    d = make_deployment(client, name="Positional", strategy="strangle_monthly_v2", mode="positional")
    at = lambda days, h=10: datetime.combine(TODAY - timedelta(days=days), datetime.min.time().replace(hour=h), IST)  # noqa: E731
    # cycle 1: entry 20 days ago, a roll 18 days ago, exit 15 days ago
    add_fill(client, d["id"], "CE1", 101, "sell", 75, 100, at(20))
    add_fill(client, d["id"], "PE1", 102, "sell", 75, 100, at(20))
    add_fill(client, d["id"], "PE1", 102, "buy", 75, 60, at(18))
    add_fill(client, d["id"], "PE2", 103, "sell", 75, 120, at(18))
    add_fill(client, d["id"], "CE1", 101, "buy", 75, 50, at(15))
    add_fill(client, d["id"], "PE2", 103, "buy", 75, 70, at(15))
    repair(client, d, TODAY - timedelta(days=18))
    [m] = marks(client, d, full=True)
    assert m["cycle"] == {"from": (TODAY - timedelta(days=20)).isoformat(), "to": (TODAY - timedelta(days=15)).isoformat(),
                          "current": True}, "the whole cycle; still the latest one"
    # a new entry starts the next cycle: the repaired one isn't current any more
    add_fill(client, d["id"], "CE3", 104, "sell", 75, 90, at(3))
    [m] = marks(client, d, full=True)
    assert m["cycle"]["current"] is False and m["cycle"]["to"] == (TODAY - timedelta(days=15)).isoformat()


def test_a_flagged_day_can_be_dismissed_from_its_case_and_brought_back(client):
    d = make_deployment(client, name="Dismissed")
    y = TODAY - timedelta(days=1)
    check(client, d, y, "mismatch", "Traded differently from 13:31")
    assert marks(client, d) == [(y.isoformat(), "flagged")]
    r = client.post(f"/v2/repair-cases/{y}/{d['id']}/dismiss", json={})
    assert r.status_code == 200 and r.json()["dismissed_at"]
    assert marks(client, d) == [], "no longer marked anywhere"
    case = client.get(f"/v2/repair-cases/{y}/{d['id']}").json()
    assert case["check"]["dismissed_at"], "the case says so"
    assert next(x for x in client.get(f"/v2/day-checks?day={y}").json()["results"] if x["deployment_id"] == d["id"])["dismissed_at"]
    r = client.post(f"/v2/repair-cases/{y}/{d['id']}/dismiss", json={"undo": True})
    assert r.json()["dismissed_at"] is None
    assert marks(client, d) == [(y.isoformat(), "flagged")]


def test_only_a_flagged_unrepaired_day_can_be_dismissed(client):
    d = make_deployment(client, name="Nothing to dismiss")
    ok, fixed = TODAY - timedelta(days=1), TODAY - timedelta(days=2)
    check(client, d, ok, "ok")
    check(client, d, fixed, "rule")
    repair(client, d, fixed)
    assert client.post(f"/v2/repair-cases/{ok}/{d['id']}/dismiss", json={}).status_code == 409
    assert client.post(f"/v2/repair-cases/{fixed}/{d['id']}/dismiss", json={}).status_code == 409
    assert client.post(f"/v2/repair-cases/{TODAY}/{d['id']}/dismiss", json={}).status_code == 409, "never checked"

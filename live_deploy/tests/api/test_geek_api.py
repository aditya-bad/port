"""GET /v2/deployments/{id}/geek/{view}: every strategy has the shared
views (engine, decisions, settings); strangle_monthly_v2's own views all
answer on a deployment with no history and on one with
fills (the views themselves: tests/strategies/test_strangle_geek.py)."""

from datetime import datetime
from uuid import uuid4

import pytest

from tests.conftest import add_fill, make_deployment

T = datetime.fromisoformat
VIEWS = ["engine", "decisions", "cycles", "cycle", "adjustments", "entries", "premium", "settings"]


def test_other_strategies_have_the_shared_views_only(client):
    d = make_deployment(client, name="dtt")
    for view in ("engine", "decisions", "settings"):
        r = client.get(f"/v2/deployments/{d['id']}/geek/{view}")
        assert r.status_code == 200, (view, r.text)
    e = client.get(f"/v2/deployments/{d['id']}/geek/engine").json()
    assert e["running"] is False and e["legs"] == [] and e["rules"] == []
    assert e["next"]["text"].startswith("Paused")
    r = client.get(f"/v2/deployments/{d['id']}/geek/cycles")
    assert r.status_code == 404 and "No such Geek view" in r.json()["detail"]


def _decide(client, dep_id, at, rule, outcome, summary, data):
    from uuid import UUID

    from app.db import queries
    pool = client.app.state.db_pool
    client.portal.call(lambda: queries.record_decision(pool, UUID(str(dep_id)), at, rule, outcome, summary, data))


def test_shared_decisions_group_fills_into_actions_and_merge_the_log(client):
    d = make_deployment(client, name="dtt decisions")
    t0 = T("2026-10-19T09:20:00+05:30")
    tv = {"close": 25010.5, "prev_high": 24990.0}
    for sym, tok, px in (("NIFTY26OCT25000CE", 7101, 120.0), ("NIFTY26OCT25000PE", 7102, 110.0)):
        add_fill(client, d["id"], sym, tok, "sell", 75, px, t0, "entry",
                 {"trigger": "straddle_entry", "action": "open", "trigger_values": tv})
    t1 = T("2026-10-19T11:05:00+05:30")
    add_fill(client, d["id"], "NIFTY26OCT25000CE", 7101, "buy", 75, 150.0, t1, "leg_stop",
             {"trigger": "leg_stop", "action": "close", "trigger_values": {"ce": 150.0, "stop": 148.0},
              "price_source": "ltp"})
    _decide(client, d["id"], T("2026-10-19T10:00:00+05:30"), "exit_checks", "no_action", "Both legs inside their stops",
            {"ce": 131.0, "pe": 101.5})
    body = client.get(f"/v2/deployments/{d['id']}/geek/decisions").json()
    rows = body["rows"]
    assert [r["kind"] for r in rows] == ["exit", "check", "entry"]
    assert body["counts"] == {"acted": 2, "no_action": 1, "blocked": 0}
    stop, check, entry = rows
    assert stop["label"] == "Leg stop" and stop["outcome"] == "acted"
    assert "Bought NIFTY26OCT25000CE ×75 @ 150" in stop["summary"] and "booked −₹2,250" in stop["summary"]
    assert stop["why"] == "ce 150 · stop 148 · price from ltp"
    assert entry["summary"].count("Sold") == 2 and entry["why"] == "close 25,010.5 · prev high 24,990"
    assert check["label"] == "Exit checks" and check["why"] == "ce 131 · pe 101.5"


def test_shared_settings_against_the_defaults(client):
    from tests.conftest import default_config
    cfg = default_config("intraday_dtt_simple")
    key = next(k for k, v in cfg.items() if isinstance(v, (int, float)) and not isinstance(v, bool) and k != "instrument_tokens")
    cfg[key] = cfg[key] + 1
    d = make_deployment(client, name="dtt settings", config=cfg)
    body = client.get(f"/v2/deployments/{d['id']}/geek/settings").json()
    rows = {r["key"]: r for r in body["rows"]}
    assert "instrument_tokens" not in rows
    assert rows[key]["changed"] is True and rows[key]["value"] == cfg[key]
    assert sum(r["changed"] for r in body["rows"]) == 1
    assert all(r["label"] for r in body["rows"])
    assert body["rules"] == []


def test_unknown_view_and_unknown_deployment_are_404(client):
    d = make_deployment(client, name="strangle", strategy="strangle_monthly_v2", mode="positional")
    assert client.get(f"/v2/deployments/{d['id']}/geek/nope").status_code == 404
    assert client.get(f"/v2/deployments/{uuid4()}/geek/engine").status_code == 404


@pytest.mark.parametrize("view", VIEWS)
def test_every_view_answers_with_no_history(client, view):
    d = make_deployment(client, name=f"empty {view}", strategy="strangle_monthly_v2", mode="positional")
    r = client.get(f"/v2/deployments/{d['id']}/geek/{view}")
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("view", VIEWS)
def test_every_view_answers_with_a_cycle_and_a_roll(client, view):
    d = make_deployment(client, name=f"hist {view}", strategy="strangle_monthly_v2", mode="positional")
    base = {"cycle_id": 1, "contract_expiry": "2026-11-24"}
    entry = {**base, "trigger": "initial_entry", "action": "open", "leg_role": "original",
             "trigger_values": {"day_of_month": 12, "rotation_selector": "NEXT_MONTH", "days_to_expiry": 43,
                                "long_days_override": True, "strike_selection_capital_pct": 0.05},
             "target_basis": {"target_premium": 230.8}}
    add_fill(client, d["id"], "NIFTY26NOV25700CE", 9001, "sell", 65, 228, T("2026-10-12T10:00:00+05:30"),
             "initial_entry", {**entry, "leg": "CE", "strike": 25700})
    add_fill(client, d["id"], "NIFTY26NOV24500PE", 9002, "sell", 65, 232, T("2026-10-12T10:00:00+05:30"),
             "initial_entry", {**entry, "leg": "PE", "strike": 24500})
    tv = {"bigger_side": "CE", "smaller_side": "PE", "bigger_sum": 263.0, "smaller_sum": 160.5, "eod_gap_floor": 0.8}
    add_fill(client, d["id"], "NIFTY26NOV24500PE", 9002, "buy", 65, 160.5, T("2026-10-15T15:13:00+05:30"),
             "eod_gap_check", {**base, "trigger": "eod_gap_check", "action": "close", "leg": "PE", "strike": 24500,
                               "trigger_values": tv})
    add_fill(client, d["id"], "NIFTY26NOV24750PE", 9003, "sell", 65, 205, T("2026-10-15T15:13:05+05:30"),
             "eod_gap_check", {**base, "trigger": "eod_gap_check", "action": "open", "leg": "PE", "strike": 24750,
                               "leg_role": "adjustment_1", "trigger_values": tv})
    r = client.get(f"/v2/deployments/{d['id']}/geek/{view}")
    assert r.status_code == 200, r.text
    body = r.json()
    if view == "decisions":
        assert [x["kind"] for x in body["rows"]] == ["roll", "entry"]
        assert body["rows"][0]["summary"].startswith("Rolled PE in: closed PE 24500 @160.5")
    if view == "adjustments":
        row = body["rows"][0]
        assert row["rule"] == "eod" and abs(row["ratio_before"] - 160.5 / 263) < 1e-3
        assert abs(row["ratio_after"] - 205 / 263) < 1e-3
    if view == "cycles":
        assert body["rows"][0]["cycle_id"] == 1 and body["rows"][0]["status"] == "open"
        assert body["rows"][0]["rolls_by_kind"]["eod"] == 1
    if view == "entries":
        assert body["rows"][0]["target"] == 230.8 and body["rows"][0]["long_days"] is True
    if view == "cycle":
        assert body["found"] and body["cycle_id"] == 1
        assert [x["kind"] for x in body["timeline"]] == ["entry", "roll"]
        assert body["rolls"][0]["side"] == "PE" and body["rolls"][0]["direction"] == "in"
        assert len(body["legs"]) == 3
        # the cycle is picked by ?c=
        assert client.get(f"/v2/deployments/{d['id']}/geek/cycle?c=1").json()["cycle_id"] == 1
        assert client.get(f"/v2/deployments/{d['id']}/geek/cycle?c=7").json()["found"] is False

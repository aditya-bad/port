"""GET /v2/deployments/{id}/geek/{view}: offered for strangle_monthly_v2
only, every view answers on a deployment with no history and on one with
fills (the views themselves: tests/strategies/test_strangle_geek.py)."""

from datetime import datetime
from uuid import uuid4

import pytest

from tests.conftest import add_fill, make_deployment

T = datetime.fromisoformat
VIEWS = ["engine", "decisions", "cycles", "adjustments", "entries", "premium", "settings"]


def test_other_strategies_have_no_geek_mode(client):
    d = make_deployment(client, name="dtt")
    r = client.get(f"/v2/deployments/{d['id']}/geek/engine")
    assert r.status_code == 404 and "isn't available for intraday_dtt_simple" in r.json()["detail"]


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

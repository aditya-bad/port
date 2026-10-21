"""GET /v2/deployments/{id}/geek/{view}: every strategy has the shared
views (engine, decisions, settings); strangle_monthly_v2's own views all
answer on a deployment with no history and on one with
fills (the views themselves: tests/strategies/test_strangle_geek.py)."""

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from tests.conftest import add_fill, make_deployment

T = datetime.fromisoformat
VIEWS = ["engine", "decisions", "cycles", "cycle", "adjustments", "entries", "premium", "settings", "conditions"]


def test_other_strategies_have_the_shared_views_only(client):
    d = make_deployment(client, name="spread", strategy="trend_credit_spread", mode="positional")
    for view in ("engine", "decisions", "settings", "conditions"):
        r = client.get(f"/v2/deployments/{d['id']}/geek/{view}")
        assert r.status_code == 200, (view, r.text)
    e = client.get(f"/v2/deployments/{d['id']}/geek/engine").json()
    assert e["running"] is False and e["legs"] == [] and e["rules"] == []
    assert e["next"]["text"].startswith("Paused")
    r = client.get(f"/v2/deployments/{d['id']}/geek/cycles")
    assert r.status_code == 404 and "No such Geek view" in r.json()["detail"]
    assert client.get(f"/v2/deployments/{d['id']}/geek/straddles").status_code == 404


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


ST_VIEWS = ["engine", "decisions", "settings", "signals", "trade", "conditions"]


@pytest.mark.parametrize("view", ST_VIEWS)
def test_supertrend_options_views_answer_with_no_history(client, view):
    d = make_deployment(client, name=f"st empty {view}", strategy="pivot_supertrend_options")
    r = client.get(f"/v2/deployments/{d['id']}/geek/{view}")
    assert r.status_code == 200, r.text
    if view == "signals":
        assert r.json()["trades"] == [] and r.json()["summary"]["trades"] == 0
    if view == "trade":
        assert r.json() == {"trade": None, "n": 0}
    if view == "engine":
        assert r.json()["supertrend"]["source"] == "unavailable" and r.json()["rules"] == []


def test_supertrend_options_one_trade_through_the_api(client):
    d = make_deployment(client, name="st one trade", strategy="pivot_supertrend_options")
    piv = {"P": 25000.0, "R1": 25070.0, "R2": 25140.0, "R3": 25210.0, "S1": 24930.0, "S2": 24860.0, "S3": 24790.0}
    tv = {"close": 25092.5, "trend": "up", "supertrend_value": 24990.0, "pivot_point": 25000.0,
          "broken_level_key": "R1", "broken_level": 25070.0}
    add_fill(client, d["id"], "NIFTY26OCT25100PE", 8101, "sell", 75, 120.0, T("2026-10-19T10:05:00+05:30"), "entry",
             {"trigger": "pivot_break_long", "action": "sell_open_PE", "trigger_values": tv, "signal_side": "long",
              "option_type": "PE", "strike": 25100, "expiry": "2026-10-22", "pivots": piv})
    add_fill(client, d["id"], "NIFTY26OCT25100PE", 8101, "buy", 75, 90.0, T("2026-10-19T11:35:00+05:30"), "st_flip",
             {"trigger": "st_flip", "action": "buy_close_PE",
              "trigger_values": {"prev_trend": "up", "new_trend": "down", "close": 24985.0, "price_source": "ltp"}})
    s = client.get(f"/v2/deployments/{d['id']}/geek/signals").json()
    t = s["trades"][0]
    assert (t["level"], t["past_level"], t["from_st"]) == ("R1", 22.5, 102.5)
    assert (t["exit_reason"], t["hold_min"], t["kept_pct"], t["pnl"]) == ("st_flip", 90.0, 25.0, 2250.0)
    assert t["price_source"] == "ltp" and "price_source" not in t["exit_values"]
    assert s["by_exit"]["st_flip"]["label"] == "SuperTrend flip" and s["summary"]["win_rate"] == 100.0
    one = client.get(f"/v2/deployments/{d['id']}/geek/trade?p={t['id']}").json()
    assert one["no"] == one["n"] == 1 and one["trade"]["pivots"] == piv
    dec = client.get(f"/v2/deployments/{d['id']}/geek/decisions").json()["rows"]
    assert [(r["kind"], r["label"]) for r in dec] == [("exit", "SuperTrend flip"), ("entry", "Pivot break · sold a PE")]


DTT_VIEWS = ["engine", "decisions", "settings", "straddles", "straddle", "conditions"]


@pytest.mark.parametrize("view", DTT_VIEWS)
def test_dtt_views_answer_with_no_history(client, view):
    d = make_deployment(client, name=f"dtt empty {view}")
    r = client.get(f"/v2/deployments/{d['id']}/geek/{view}")
    assert r.status_code == 200, r.text
    if view == "straddles":
        assert r.json()["straddles"] == [] and r.json()["summary"]["straddles"] == 0
    if view == "straddle":
        assert r.json() == {"straddle": None, "n": 0}
    if view == "engine":
        e = r.json()
        assert e["rules"] == [] and e["straddle"]["state"] is None and e["next"]["text"].startswith("Paused")


def test_dtt_one_straddle_through_the_api(client):
    d = make_deployment(client, name="dtt one straddle")
    t0, t1 = T("2026-10-19T10:00:00+05:30"), T("2026-10-19T11:42:00+05:30")
    tvx = {"tick_time": "10:00:00", "entry_time": "10:00:00", "late_start_today": False, "switched_to_next_week": False}
    for sym, tok, px in (("NIFTY26OCT25000CE", 9101, 100.0), ("NIFTY26OCT25000PE", 9102, 120.0)):
        add_fill(client, d["id"], sym, tok, "sell", 75, px, t0, "entry",
                 {"trigger": "entry_time_reached", "action": f"sell_open_{sym[-2:]}", "trigger_values": tvx,
                  "strike": 25000, "expiry": "2026-10-20", "entry_spot": 25012.4, "leg": sym[-2:]})
    tv = {"ce_now": 141.0, "pe_now": 70.0, "ce_threshold": 140.0, "pe_threshold": 168.0, "per_leg_stop_loss_pct": 0.4}
    for sym, tok, px in (("NIFTY26OCT25000CE", 9101, 141.0), ("NIFTY26OCT25000PE", 9102, 70.0)):
        add_fill(client, d["id"], sym, tok, "buy", 75, px, t1, "leg_spike_stop",
                 {"trigger": "leg_spike_stop", "action": f"buy_close_{sym[-2:]}", "trigger_values": {**tv, "price_source": "live"}})
    body = client.get(f"/v2/deployments/{d['id']}/geek/straddles").json()
    (r,) = body["straddles"]
    assert (r["strike"], r["dte"], r["weekday"], r["combined_entry"], r["combined_exit"]) == (25000.0, 1, "Mon", 220.0, 211.0)
    assert (r["reason"], r["stop_leg"], r["held_min"], r["decay_pct"]) == ("leg_spike_stop", "CE", 102.0, 4.1)
    assert r["pnl"] == round((100 - 141) * 75 + (120 - 70) * 75, 2)
    assert body["by_exit"]["leg_spike_stop"]["label"] == "Leg stop" and body["by_exit"]["leg_spike_stop"]["median_exit"] == "11:42"
    assert body["stop_leg"] == {"CE": 1, "PE": 0, "both": 0}
    one = client.get(f"/v2/deployments/{d['id']}/geek/straddle?s={r['pe']['id']}").json()
    assert one["no"] == 1 and one["lines"] == {"target": 198.0, "stops": {"CE": 140.0, "PE": 168.0}}
    rows = client.get(f"/v2/deployments/{d['id']}/geek/decisions").json()["rows"]
    assert [(x["kind"], x["label"]) for x in rows] == [("exit", "Leg stop"), ("entry", "Entry")]


ADJ_VIEWS = ["engine", "decisions", "settings", "sessions", "moves", "session", "conditions"]


@pytest.mark.parametrize("strategy", ["intraday_dtt_adjusted", "intraday_dtt_advanced"])
@pytest.mark.parametrize("view", ADJ_VIEWS)
def test_adjusting_straddle_views_answer_with_no_history(client, strategy, view):
    d = make_deployment(client, name=f"{strategy} {view}", strategy=strategy)
    r = client.get(f"/v2/deployments/{d['id']}/geek/{view}")
    assert r.status_code == 200, r.text
    body = r.json()
    if view == "sessions":
        assert body["sessions"] == [] and body["summary"]["rolling"] == (strategy == "intraday_dtt_advanced")
    if view == "session":
        assert body == {"session": None, "n": 0}
    if view == "engine":
        assert body["rules"] == [] and body["adjusted"]["legs"] == {"CE": [], "PE": []}


def _mark(client, dep_id, symbol, at, price):
    from uuid import UUID

    from app.db import queries
    pool = client.app.state.db_pool

    async def go():
        pid = await pool.fetchval("SELECT id FROM positions WHERE deployment_id = $1 AND symbol = $2", UUID(str(dep_id)), symbol)
        await queries.record_position_marks(pool, [(pid, UUID(str(dep_id)), at, price)])
    client.portal.call(go)


def test_conditions_one_trade_with_its_market_and_its_excursion(client):
    d = make_deployment(client, name="spread conditions", strategy="trend_credit_spread", mode="positional")
    ctx = {"spot": 25010.0, "minute": 65, "weekday": 0, "day_open": 24900.0, "day_high": 25050.0, "day_low": 24880.0,
           "range_pts": 170.0, "from_open_pct": 0.44, "prev_close": 25100.0, "gap_pct": -0.8, "dte": 1}
    t0 = T("2026-10-19T10:20:00+05:30")
    add_fill(client, d["id"], "NIFTY26OCT24900PE", 7401, "sell", 75, 100.0, t0, "entry",
             {"trigger": "trend_flip_up", "action": "sell_open_PE", "context": ctx,
              "trigger_values": {"candle": {"open": 24990, "high": 25030, "low": 24980, "close": 25020}, "atr": 41.5}})
    _mark(client, d["id"], "NIFTY26OCT24900PE", T("2026-10-19T10:30:00+05:30"), 130.0)   # −2,250
    _mark(client, d["id"], "NIFTY26OCT24900PE", T("2026-10-19T11:00:00+05:30"), 70.0)    # +2,250
    add_fill(client, d["id"], "NIFTY26OCT24900PE", 7401, "buy", 75, 90.0, T("2026-10-19T11:20:00+05:30"), "target",
             {"trigger": "target", "action": "buy_close_PE"})                                       # +750
    body = client.get(f"/v2/deployments/{d['id']}/geek/conditions").json()
    (r,) = body["rows"]
    assert (r["pnl"], r["mae"], r["mfe"], r["held_min"], r["path"]) == (750.0, -2250.0, 2250.0, 60.0, True)
    assert r["label"] == "Trend flip up" and r["opt"] == "Puts" and r["candle_pts"] == 50 and r["atr"] == 41.5
    assert r["dims"] == {"hour": "10–11", "weekday": "Mon", "legs": "Puts", "dte": "1 day", "gap": "Gap down > 0.5%",
                         "range": "0.6–1%", "move": "Up 0–0.5%", "held": "30 min–2 h"}
    assert body["summary"] == {"trades": 1, "closed": 1, "open": 0, "wins": 1, "with_context": 1, "avg_mae": -2250.0,
                               "avg_mfe": 2250.0, "gave_back": 1500.0, "losers_were_up": 0, "with_path": 1}
    g = {x["key"]: x for x in body["groups"]}
    assert g["gap"]["buckets"] == [{"label": "Gap down > 0.5%", "n": 1, "wins": 1, "pnl": 750.0, "avg": 750.0, "win_rate": 100.0}]


def test_conditions_fall_back_to_the_contracts_candles_and_order_the_hours(client):
    from uuid import UUID  # noqa: F401

    from app.db import queries
    d = make_deployment(client, name="dtt conditions candles")
    pool = client.app.state.db_pool
    # 09:20 → 09:40 sold at 100, bought back at 95: up to 130 (−2,250) and down to 80 (+1,500) on the way
    t0 = T("2026-10-19T09:20:00+05:30")
    add_fill(client, d["id"], "NIFTY26OCT25000CE", 7501, "sell", 75, 100.0, t0, "entry", {"trigger": "entry", "action": "open"})
    client.portal.call(lambda: queries.upsert_candles(pool, [(7501, t0 + timedelta(minutes=i), c, c, c, c, None)
                                                            for i, c in enumerate([105, 130, 110, 80, 95])], "kite"))
    add_fill(client, d["id"], "NIFTY26OCT25000CE", 7501, "buy", 75, 95.0, T("2026-10-19T09:40:00+05:30"), "exit", {"trigger": "exit", "action": "close"})
    # 11:00, no prices at all on the way
    add_fill(client, d["id"], "NIFTY26OCT25100CE", 7502, "sell", 75, 100.0, T("2026-10-19T11:00:00+05:30"), "entry", {"trigger": "entry", "action": "open"})
    add_fill(client, d["id"], "NIFTY26OCT25100CE", 7502, "buy", 75, 110.0, T("2026-10-19T11:30:00+05:30"), "exit", {"trigger": "exit", "action": "close"})
    body = client.get(f"/v2/deployments/{d['id']}/geek/conditions").json()
    late, early = body["rows"]
    assert (early["mae"], early["mfe"], early["pnl"], early["path"]) == (-2250.0, 1500.0, 375.0, True)
    assert late["path"] is False and late["pnl"] == -750.0
    assert body["summary"]["with_path"] == 1 and body["summary"]["avg_mae"] == -2250.0
    hours = next(g for g in body["groups"] if g["key"] == "hour")
    assert [b["label"] for b in hours["buckets"]] == ["9:15–10", "11–12"], "in the day's order"

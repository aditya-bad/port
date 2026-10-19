"""Query semantics on real Postgres: episodes, P&L digests and ranges,
leaderboard, report, adjustment histogram, trade removal, and the small
stores (deployments, users, presets, tags, push, Kite session, mute,
scratch, snapshots, events, audit)."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.db import queries
from tests.conftest import fill

IST = ZoneInfo("Asia/Kolkata")


def at(day, hh=10, mm=0):
    return datetime(2026, 10, day, hh, mm, tzinfo=IST)


async def dep(pool, name="D", strategy="intraday_dtt_simple", mode="intraday", capital=500000, config=None):
    return (await queries.create_deployment(pool, name, strategy, mode, capital, config or {}))["id"]


async def trade(pool, d, sym, tok, opened, closed, entry=100.0, exit_=80.0, qty=75, meta=None):
    """A short round trip."""
    await fill(pool, d, sym, tok, "sell", qty, entry, opened, reason="entry", metadata=meta or {})
    if closed:
        await fill(pool, d, sym, tok, "buy", qty, exit_, closed, reason="exit")


# ── Deployments ───────────────────────────────────────────────────────

def test_deployment_crud_status_fields_and_state(pool_run):
    async def go(pool):
        d = await dep(pool, config={"entry_time": "10:00"})
        row = await queries.get_deployment(pool, d)
        assert row["status"] == "active" or row["status"] in ("paused", "active")
        assert float(row["current_cash"]) == 500000
        assert (await queries.get_deployment_by_name(pool, "D"))["id"] == d
        await queries.set_status(pool, d, "paused")
        assert (await queries.get_deployment(pool, d))["status"] == "paused"
        upd = await queries.update_deployment_fields(pool, d, deployment_name="D2", notes="why", include_in_reports=False,
                                                     notifications_enabled=False, config={"entry_time": "10:15"})
        assert upd["deployment_name"] == "D2" and upd["notes"] == "why"
        assert upd["include_in_reports"] is False and upd["notifications_enabled"] is False
        assert upd["config"]["entry_time"] == "10:15"
        # None leaves a field alone
        upd = await queries.update_deployment_fields(pool, d, notes=None)
        assert upd["notes"] == "why"
        await queries.save_deployment_state(pool, d, {"version": 1, "today": "2026-10-14"})
        assert (await queries.load_deployment_state(pool, d))["today"] == "2026-10-14"
        assert [r["id"] for r in await queries.list_deployments(pool, status="paused")] == [d]
        await queries.delete_deployment(pool, d)
        assert await queries.get_deployment(pool, d) is None
    pool_run(go)


def test_clear_all_deletes_every_deployment_and_its_history(pool_run):
    async def go(pool):
        d = await dep(pool)
        await trade(pool, d, "X", 1, at(14), at(14, 11))
        n = await queries.clear_all_deployments(pool)
        assert n == 1
        assert await pool.fetchval("SELECT count(*) FROM positions") == 0
        assert await pool.fetchval("SELECT count(*) FROM position_lots") == 0
    pool_run(go)


# ── Episodes ──────────────────────────────────────────────────────────

def test_overlapping_legs_form_one_episode_and_a_gap_starts_another(pool_run):
    async def go(pool):
        d = await dep(pool, mode="positional", strategy="strangle_monthly_v2")
        await trade(pool, d, "CE1", 1, at(1), at(3))
        await trade(pool, d, "PE1", 2, at(1), at(5))
        await trade(pool, d, "CE2", 3, at(3, 11), at(5))       # opened while PE1 open: same episode
        await trade(pool, d, "CE3", 4, at(8), None)              # after everything closed: new episode
        rows = await queries.list_positions_with_episode(pool, d)
        ep = {r["symbol"]: r["episode_opened_at"] for r in rows}
        assert ep["CE1"] == ep["PE1"] == ep["CE2"]
        assert ep["CE3"] != ep["CE1"]
    pool_run(go)


# ── P&L digests and ranges ────────────────────────────────────────────

def test_daily_digest_sums_realized_pnl_per_ist_day(pool_run):
    async def go(pool):
        d = await dep(pool)
        await trade(pool, d, "A", 1, at(13), at(13, 15), 100, 80)    # +1500
        await trade(pool, d, "B", 2, at(14), at(14, 15), 100, 110)   # -750
        await trade(pool, d, "C", 3, at(14, 11), at(14, 14), 50, 40)  # +750
        rows = await queries.list_pnl_digest(pool, period="day", limit=10)
        by = {r["period_start"].astimezone(IST).date().day: float(r["realized_pnl"]) for r in rows}
        assert by[13] == pytest.approx(1500) and by[14] == pytest.approx(0)
        day14 = next(r for r in rows if r["period_start"].astimezone(IST).date().day == 14)
        assert day14["positions_closed"] == 2 and day14["wins"] == 1 and day14["losses"] == 1
        assert float(day14["realized_pnl_net"]) < float(day14["realized_pnl"])   # charges come off
    pool_run(go)


def test_deployment_digest_reports_mtm_best_and_worst(pool_run):
    async def go(pool):
        d = await dep(pool)
        await trade(pool, d, "A", 1, at(14), at(14, 15))
        rows = await queries.list_pnl_digest_for_deployment(pool, d, "intraday", period="day", limit=10)
        assert rows and float(rows[0]["realized_pnl"]) == pytest.approx(1500)
        assert "max_profit" in rows[0] and "max_loss" in rows[0]
    pool_run(go)


def test_range_summaries_by_strategy_and_deployment(pool_run):
    async def go(pool):
        a = await dep(pool, name="A")
        b = await dep(pool, name="B", strategy="strangle_monthly_v2", mode="positional")
        await trade(pool, a, "X", 1, at(14), at(14, 12), 100, 80)    # +1500
        await trade(pool, b, "Y", 2, at(14), at(14, 12), 100, 120)   # -1500
        await trade(pool, b, "Z", 3, at(20), at(20, 12), 100, 90)    # outside the range
        s, e = at(14, 0), at(15, 0)
        summ = await queries.pnl_summary_for_range(pool, s, e)
        assert float(summ["realized_pnl"]) == pytest.approx(0)
        by_s = {r["strategy_name"]: float(r["realized_pnl"]) for r in await queries.pnl_by_strategy_for_range(pool, s, e)}
        assert by_s == {"intraday_dtt_simple": pytest.approx(1500), "strangle_monthly_v2": pytest.approx(-1500)}
        by_d = {r["deployment_name"]: float(r["realized_pnl"]) for r in await queries.pnl_by_deployment_for_range(pool, s, e)}
        assert by_d == {"A": pytest.approx(1500), "B": pytest.approx(-1500)}
    pool_run(go)


def test_excluded_from_reports_is_left_out_of_totals(pool_run):
    async def go(pool):
        a = await dep(pool, name="A")
        b = await dep(pool, name="B")
        await trade(pool, a, "X", 1, at(14), at(14, 12), 100, 80)
        await trade(pool, b, "Y", 2, at(14), at(14, 12), 100, 80)
        await queries.update_deployment_fields(pool, b, include_in_reports=False)
        summ = await queries.pnl_summary_for_range(pool, at(14, 0), at(15, 0))
        assert float(summ["realized_pnl"]) == pytest.approx(1500)
    pool_run(go)


def test_strategy_leaderboard_counts_wins_and_losses(pool_run):
    async def go(pool):
        a = await dep(pool, name="A")
        await trade(pool, a, "X", 1, at(14), at(14, 12), 100, 80)
        await trade(pool, a, "Y", 2, at(14), at(14, 12), 100, 120)
        [row] = [r for r in await queries.list_strategy_leaderboard(pool) if r["strategy_name"] == "intraday_dtt_simple"]
        assert row["positions_closed"] == 2 and row["wins"] == 1 and row["losses"] == 1
        assert float(row["realized_pnl"]) == pytest.approx(0)
    pool_run(go)


def test_report_and_realized_totals(pool_run):
    async def go(pool):
        a = await dep(pool)
        await trade(pool, a, "X", 1, at(14), at(14, 12), 100, 80)
        await trade(pool, a, "Y", 2, at(14), None)
        rep = await queries.build_report(pool, a)
        assert (rep["closed_positions"], rep["open_positions"]) == (1, 1)
        assert rep["total_realized_pnl"] == pytest.approx(1500) and rep["win_rate_pct"] == 100.0
        # cash: +7500 (X sold) - 6000 (X bought back) + 7500 (Y sold)
        assert rep["current_cash"] == pytest.approx(509000.0)
        assert (await queries.realized_pnl_by_deployment(pool))[str(a)] == pytest.approx(1500)
        assert await queries.realized_pnl_total(pool, a) == pytest.approx(1500)
        net = await queries.realized_pnl_total_net(pool, a)
        assert net < 1500 and await queries.realized_pnl_total_net(pool, a, with_slippage=True) <= net
    pool_run(go)


# ── Adjustments and trade removal ─────────────────────────────────────

def test_adjustment_histogram_counts_adjustment_legs_per_day(pool_run):
    async def go(pool):
        d = await dep(pool, strategy="intraday_dtt_adjusted")
        await trade(pool, d, "CE", 1, at(13), at(13, 15), meta={"leg_role": "original"})
        await trade(pool, d, "PE", 2, at(13), at(13, 15), meta={"leg_role": "original"})
        await trade(pool, d, "CE", 3, at(14), at(14, 15), meta={"leg_role": "original"})
        await trade(pool, d, "PE", 4, at(14), at(14, 15), meta={"leg_role": "original"})
        await trade(pool, d, "PE2", 5, at(14, 11), at(14, 15), meta={"leg_role": "adjustment_1"})
        buckets = {b["adjustments"]: b["units"] for b in await queries.get_adjustment_histogram(pool, d, "day")}
        assert buckets == {0: 1, 1: 1}
    pool_run(go)


def test_remove_trade_scope_erases_the_open_cycle_and_restores_cash(pool_run):
    async def go(pool):
        d = await dep(pool)
        await trade(pool, d, "OLD", 1, at(13), at(13, 15), 100, 80)
        before = float((await queries.get_deployment(pool, d))["current_cash"])
        await trade(pool, d, "NEW", 2, at(14), None)
        scope = await queries.resolve_current_trade_scope(pool, d, None)
        assert scope["position_ids"]
        await queries.remove_trade_scope(pool, d, scope["position_ids"], scope["min_opened_at"])
        assert [p["symbol"] for p in await queries.list_positions(pool, d)] == ["OLD"]
        assert float((await queries.get_deployment(pool, d))["current_cash"]) == pytest.approx(before)
    pool_run(go)


def test_remove_trade_scope_also_drops_the_open_cycles_decisions(pool_run):
    async def go(pool):
        d = await dep(pool)
        await trade(pool, d, "OLD", 1, at(13), at(13, 15), 100, 80)
        await queries.record_decision(pool, d, at(13, 12), "eod_gap_check", "no_action", "old cycle")
        await trade(pool, d, "NEW", 2, at(14), None)
        await queries.record_decision(pool, d, at(14, 12), "eod_gap_check", "no_action", "new cycle")
        scope = await queries.resolve_current_trade_scope(pool, d, None)
        await queries.remove_trade_scope(pool, d, scope["position_ids"], scope["min_opened_at"])
        assert [r["summary"] for r in await queries.list_decisions(pool, d)] == ["old cycle"]
    pool_run(go)


def test_decisions_round_trip_newest_first(pool_run):
    async def go(pool):
        d = await dep(pool)
        await queries.record_decision(pool, d, at(10), "a", "no_action", "first", {"x": 1})
        await queries.record_decision(pool, d, at(11), "b", "blocked", "second")
        rows = await queries.list_decisions(pool, d)
        assert [(r["rule"], r["outcome"], r["summary"]) for r in rows] == [
            ("b", "blocked", "second"), ("a", "no_action", "first")]
        assert rows[1]["data"] == {"x": 1}
        assert [r["summary"] for r in await queries.list_decisions(pool, d, since=at(10, 12))] == ["second"]
        with pytest.raises(Exception):
            await queries.record_decision(pool, d, at(12), "c", "acted", "trades aren't decisions")
    pool_run(go)


# ── Snapshots, events, marks ──────────────────────────────────────────

def test_snapshots_events_and_marks_round_trip(pool_run):
    async def go(pool):
        d = await dep(pool)
        await queries.record_snapshot(pool, d, at(14, 10), 500000, 0, 500000, 0)
        await queries.record_snapshot(pool, d, at(14, 11), 500100, 50, 500150, 100)
        rows = await queries.list_snapshots_between(pool, d, at(14, 0), at(15, 0))
        assert [float(r["total_value"]) for r in rows] == [500000, 500150]
        curve = await queries.list_portfolio_equity_curve(pool)
        assert curve
        await queries.record_event(pool, d, "paused", message="by hand")
        ev = await queries.list_events(pool, d, limit=10)
        assert ev[0]["event_type"] == "paused"
        await trade(pool, d, "X", 1, at(14), None)
        [p] = await queries.list_open_positions(pool, d)
        await queries.record_position_marks(pool, [(p["id"], d, at(14, 10, 1), 95.0), (p["id"], d, at(14, 10, 1), 96.0)])
        marks = await queries.list_marks_for_positions(pool, [p["id"]], at(14, 0), at(15, 0))
        assert [float(m["price"]) for m in marks] == [95.0], "a repeated minute is ignored"
    pool_run(go)


def test_index_candles_merge_within_a_minute(pool_run):
    async def go(pool):
        m = at(14, 10)
        await queries.record_index_candles(pool, [(256265, m, 100, 101, 99, 100.5)])
        await queries.record_index_candles(pool, [(256265, m, 100.5, 103, 98, 102)])
        [c] = await queries.list_index_candles(pool, 256265, at(14, 0), at(15, 0))
        assert (c["o"], c["h"], c["l"], c["c"]) == (100, 103, 98, 102)
    pool_run(go)


# ── Small stores ──────────────────────────────────────────────────────

def test_users_sessions_and_audit(pool_run):
    async def go(pool):
        u = await queries.create_user(pool, "riya", "hash")
        assert (await queries.get_user_by_username(pool, "riya"))["id"] == u["id"]
        v0 = (await queries.get_all_session_versions(pool))[str(u["id"])]
        await queries.bump_session_version(pool, u["id"])
        assert (await queries.get_all_session_versions(pool))[str(u["id"])] == v0 + 1
        await queries.update_user_password(pool, u["id"], "hash2")
        assert (await queries.get_user_by_id(pool, u["id"]))["password_hash"] == "hash2"
        await queries.record_audit_log(pool, u["id"], "riya", "POST", "/x", 200, {"a": 1}, "1.2.3.4")
        [a] = await queries.list_audit_log(pool, offset=0, limit=10)
        assert a["path"] == "/x" and a["username"] == "riya"
    pool_run(go)


def test_presets_tags_push_subscriptions(pool_run):
    async def go(pool):
        p = await queries.create_preset(pool, "intraday_dtt_simple", "Wide", {"lots_per_trade": 2})
        assert [x["preset_name"] for x in await queries.list_presets(pool, "intraday_dtt_simple")] == ["Wide"]
        await queries.delete_preset(pool, p["id"])
        assert await queries.list_presets(pool, "intraday_dtt_simple") == []
        t = await queries.create_tag(pool, "Strangles")
        assert (await queries.get_tag_by_name(pool, "Strangles"))["id"] == t["id"]
        await queries.delete_tag(pool, t["id"])
        await queries.save_push_subscription(pool, "https://push/1", "p256", "auth")
        await queries.save_push_subscription(pool, "https://push/1", "p256", "auth")
        assert len(await queries.list_push_subscriptions(pool)) == 1, "same endpoint is upserted"
        await queries.delete_push_subscription(pool, "https://push/1")
        assert await queries.list_push_subscriptions(pool) == []
    pool_run(go)


def test_kite_session_mute_strategy_settings_and_scratch(pool_run):
    async def go(pool):
        await queries.set_kite_session(pool, "tok", datetime.now(timezone.utc))
        assert (await queries.get_kite_session(pool))["access_token"] == "tok"
        until = datetime(2026, 10, 15, 1, 30, tzinfo=timezone.utc)
        await queries.set_notification_mute_until(pool, until)
        assert await queries.get_notification_mute_until(pool) == until
        await queries.set_notification_mute_until(pool, None)
        assert await queries.get_notification_mute_until(pool) is None
        await queries.ensure_strategy_settings(pool, ["intraday_dtt_simple"])
        await queries.set_strategy_enabled(pool, "intraday_dtt_simple", False)
        assert await queries.is_strategy_enabled(pool, "intraday_dtt_simple") is False
        assert (await queries.get_strategy_enabled_map(pool))["intraday_dtt_simple"] is False
        await queries.set_strategy_enabled(pool, "intraday_dtt_simple", True)
        await queries.set_app_scratch(pool, "k", {"v": 1})
        assert (await queries.get_app_scratch(pool, "k"))["v"] == 1
        await queries.delete_app_scratch(pool, "k")
        assert await queries.get_app_scratch(pool, "k") is None
    pool_run(go)

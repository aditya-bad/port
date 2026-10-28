"""strangle_monthly_v2 on a simulated market: contract rotation and
sizing, the continuous 50% roll, the 15:13 EOD check, checkpoint
re-entry, the expiry backstop, the stale-price guard, hedging, restart."""

import dataclasses
from datetime import date, timedelta

from tests.sim import Sim, find_spot, leg_prices, side_sum
from tests.strategies.helpers import assert_book_consistent, cfg

S = "strangle_monthly_v2"


async def start(pool, monkeypatch, **over):
    sim = await Sim.create(pool, monkeypatch, S, cfg(S, **over), mode="positional")
    await sim.tick("10:00", spot=25000)
    return sim


async def shorts(sim):
    return [p for p in await sim.open_positions() if p["side"] == "short"]


def closes(fills):
    return [f for f in fills if f["action"] == "buy"]


def test_entry_sells_the_monthly_contract_at_the_capital_sized_premium(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch)
        legs = await shorts(sim)
        assert sorted(p["symbol"][-2:] for p in legs) == ["CE", "PE"]
        # 14 Oct: day > 10 -> NEXT_MONTH (Nov monthly, 24 Nov); 41 days out
        # and not the entry month -> long_days_target_percentage (5%):
        # 500000 * 0.05 / 75 / 2 = 166.7 per leg
        assert all("26NOV24" in p["symbol"] for p in legs)
        for p in legs:
            assert abs(float(p["avg_entry_price"]) - 166.7) / 166.7 < 0.15, p
        ce = next(p for p in legs if p["symbol"].endswith("CE"))
        pe = next(p for p in legs if p["symbol"].endswith("PE"))
        assert float(ce["metadata"]["strike"]) > 25000 > float(pe["metadata"]["strike"])
        assert float(ce["qty"]) == float(pe["qty"]) == 75
        await assert_book_consistent(pool, sim.dep_id)
    pool_run(go)


def test_no_entry_before_entry_time(pool_run, monkeypatch):
    async def go(pool):
        sim = await Sim.create(pool, monkeypatch, S, cfg(S), mode="positional")
        await sim.walk("09:15", "09:55", every_min=10)
        assert await sim.open_positions() == []
    pool_run(go)


def test_rally_rolls_the_decayed_put_up_to_87_5_pct_of_the_call(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch)
        old_pe = next(p for p in await shorts(sim) if p["symbol"].endswith("PE"))
        up = await find_spot(sim, lambda p: side_sum(p, "PE") <= 0.48 * side_sum(p, "CE"), step=25)
        await sim.tick("11:00", spot=up)
        legs = await shorts(sim)
        pes = [p for p in legs if p["symbol"].endswith("PE")]
        assert len(pes) == 1 and pes[0]["symbol"] != old_pe["symbol"], "the PE is replaced, never added to"
        assert float(pes[0]["metadata"]["strike"]) > float(old_pe["metadata"]["strike"]), "rolled closer to the market"
        prices = await leg_prices(sim)
        assert 0.75 <= side_sum(prices, "PE") / side_sum(prices, "CE") <= 1.0
        assert {f["reason"] for f in closes(await sim.fills())} == {"continuous_50pct_trigger"}
        await assert_book_consistent(pool, sim.dep_id)
    pool_run(go)


def test_eod_check_rebalances_a_gap_below_80_pct_at_15_13_only(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch)
        mid = await find_spot(sim, lambda p: 0.58 <= side_sum(p, "PE") / side_sum(p, "CE") <= 0.75, step=10)
        await sim.tick("14:00", spot=mid)
        assert closes(await sim.fills()) == [], "between 50% and 80% nothing happens during the day"
        await sim.tick("15:13", spot=mid)
        reasons = {f["reason"] for f in closes(await sim.fills())}
        assert reasons and reasons <= {"eod_gap_check", "eod_rollout"}
        prices = await leg_prices(sim)
        r = side_sum(prices, "PE") / side_sum(prices, "CE")
        assert 0.78 <= r <= 1.15, r
        await sim.tick("15:20", spot=mid)
        assert len(closes(await sim.fills())) == 1, "the EOD check runs once a day"
        await assert_book_consistent(pool, sim.dep_id)
    pool_run(go)


def test_nothing_is_traded_on_the_ticks_after_the_close(pool_run, monkeypatch):
    """DTT BankNifty Strangle, 23 Oct: its 15:13 check was held back until
    a 15:36 tick, after the close, then rolled a leg at closing prices no
    order could have got. A check that missed its window is skipped."""
    async def go(pool):
        sim = await start(pool, monkeypatch)
        mid = await find_spot(sim, lambda p: 0.58 <= side_sum(p, "PE") / side_sum(p, "CE") <= 0.75, step=10)
        await sim.tick("14:00", spot=mid)
        n = len(await sim.fills())
        await sim.tick("15:36", spot=mid)
        await sim.tick("15:38", spot=mid * 1.01)
        assert len(await sim.fills()) == n, "no EOD roll, no adjustment after 15:30"
        rows = await pool.fetch("SELECT rule, outcome, summary FROM strategy_decisions WHERE deployment_id = $1 "
                                "AND rule = 'market_closed'", sim.dep_id)
        assert len(rows) == 1 and "end-of-day check didn't run before the close: skipped" in rows[0]["summary"]
        d2 = sim.market.today + timedelta(days=1)
        await sim.tick(f"{d2} 15:13", spot=mid)
        assert len(await sim.fills()) > n, "the next day's check runs as usual"
    pool_run(go)


def test_checkpoint_profit_flattens_and_re_enters_a_new_cycle(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch)
        first = {p["symbol"] for p in await shorts(sim)}
        sim.market.vol = 0.13          # premiums fall: cycle profit passes 0.5% of capital
        await sim.tick("11:00", spot=25000)
        assert "checkpoint_target" in {f["reason"] for f in closes(await sim.fills())}
        legs = await shorts(sim)
        assert len(legs) == 2 and {p["symbol"] for p in legs} != first, "re-entered immediately"
        assert {int(p["metadata"]["cycle_id"]) for p in legs} == {2}
        await assert_book_consistent(pool, sim.dep_id)
    pool_run(go)


def test_checkpoint_gate_holds_until_the_configured_time(pool_run, monkeypatch):
    async def go(pool):
        sim = await Sim.create(pool, monkeypatch, S, cfg(S, checkpoint_exit_not_before="12:00"), mode="positional")
        await sim.tick("10:00", spot=25000)
        sim.market.vol = 0.13
        await sim.tick("11:00", spot=25000)
        assert closes(await sim.fills()) == []
        await sim.tick("12:00", spot=25000)
        assert "checkpoint_target" in {f["reason"] for f in closes(await sim.fills())}
    pool_run(go)


def test_contract_expiry_backstop_closes_everything_on_expiry_day(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch)
        await sim.tick(f"{date(2026, 11, 24)} 09:30", spot=25000)
        assert "contract_expiry_backstop" in {f["reason"] for f in closes(await sim.fills())}
        assert not [p for p in await shorts(sim) if "26NOV24" in p["symbol"]]
        await assert_book_consistent(pool, sim.dep_id)
    pool_run(go)


def test_no_decisions_until_every_leg_has_traded_this_session(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch)
        up = await find_spot(sim, lambda p: side_sum(p, "PE") <= 0.45 * side_sum(p, "CE"), step=25)
        d2 = sim.market.today + timedelta(days=1)
        stale_token = int(next(p for p in await shorts(sim) if p["symbol"].endswith("CE"))["instrument_token"])
        sim.market.now = sim.market.now.replace(day=d2.day)
        orig = sim.dispatcher.publish

        def publish_with_stale_call():
            orig()
            sim.dispatcher.last_price_times[stale_token] = sim.dispatcher.last_price_times[stale_token] - timedelta(days=1)
        sim.dispatcher.publish = publish_with_stale_call
        await sim.tick(f"{d2} 09:16", spot=up)
        assert closes(await sim.fills()) == [], "the CE hasn't traded today: hold"
        sim.dispatcher.publish = orig
        await sim.tick(f"{d2} 09:20", spot=up)
        assert closes(await sim.fills()), "acts once every leg has a fresh price"
    pool_run(go)


def test_restart_resumes_the_position_and_its_contract(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch)
        before = {p["symbol"] for p in await shorts(sim)}
        await sim.restart()
        assert sim.strategy.contract_expiry == date(2026, 11, 24)
        assert {l["symbol"] for side in sim.strategy.legs.values() for l in side} == before
        await sim.tick("11:00", spot=25000)
        assert {p["symbol"] for p in await shorts(sim)} == before, "no re-entry after restart"
    pool_run(go)


def test_hedging_buys_a_wing_per_short_and_flattens_with_them(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch, enable_hedging=True)
        legs = await sim.open_positions()
        assert len([p for p in legs if p["side"] == "short"]) == 2
        assert len([p for p in legs if p["side"] == "long"]) == 2, "one protective long per short"
        await sim.tick(f"{date(2026, 11, 24)} 09:30", spot=25000)
        assert not [p for p in await sim.open_positions() if "26NOV24" in p["symbol"]]
        await assert_book_consistent(pool, sim.dep_id)
    pool_run(go)


# ── Decision log (Geek mode's Decisions view) ───────────────────────────

async def decisions(sim):
    from app.db import queries
    return [dict(r) for r in await queries.list_decisions(sim.pool, sim.dep_id)]


def test_eod_check_that_finds_nothing_to_do_is_noted_once_a_day(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch)
        await sim.tick("15:13", spot=25000)
        await sim.tick("15:20", spot=25000)
        assert closes(await sim.fills()) == []
        rows = await decisions(sim)
        assert [(r["rule"], r["outcome"]) for r in rows] == [("eod_gap_check", "no_action")]
        assert rows[0]["data"]["eod_gap_floor"] == 0.8
        assert rows[0]["data"]["smaller_sum"] <= rows[0]["data"]["bigger_sum"]
        assert "hold overnight" in rows[0]["summary"]
        assert rows[0]["decided_at"].astimezone(sim.market.now.tzinfo).strftime("%H:%M") == "15:13"
    pool_run(go)


def test_a_trade_is_not_noted_as_a_decision(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch)
        up = await find_spot(sim, lambda p: side_sum(p, "PE") <= 0.48 * side_sum(p, "CE"), step=25)
        await sim.tick("11:00", spot=up)
        assert closes(await sim.fills())
        assert await decisions(sim) == [], "fills explain themselves; the log holds only checks that didn't trade"
    pool_run(go)


def test_checkpoint_held_by_the_time_gate_is_noted_once_a_day(pool_run, monkeypatch):
    async def go(pool):
        sim = await Sim.create(pool, monkeypatch, S, cfg(S, checkpoint_exit_not_before="12:00"), mode="positional")
        await sim.tick("10:00", spot=25000)
        sim.market.vol = 0.13
        await sim.tick("11:00", spot=25000)
        await sim.tick("11:30", spot=25000)
        rows = await decisions(sim)
        assert [(r["rule"], r["outcome"]) for r in rows] == [("checkpoint_target", "no_action")]
        assert "12:00" in rows[0]["summary"]
    pool_run(go)


def test_stale_price_hold_is_noted_as_blocked(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch)
        d2 = sim.market.today + timedelta(days=1)
        stale_token = int(next(p for p in await shorts(sim) if p["symbol"].endswith("CE"))["instrument_token"])
        sim.market.now = sim.market.now.replace(day=d2.day)
        orig = sim.dispatcher.publish

        def publish_with_stale_call():
            orig()
            sim.dispatcher.last_price_times[stale_token] = sim.dispatcher.last_price_times[stale_token] - timedelta(days=1)
        sim.dispatcher.publish = publish_with_stale_call
        await sim.tick(f"{d2} 09:16", spot=25000)
        await sim.tick(f"{d2} 09:17", spot=25000)
        rows = await decisions(sim)
        assert [(r["rule"], r["outcome"]) for r in rows] == [("stale_price_guard", "blocked")], "noted once, not every tick"
        assert rows[0]["data"]["legs"] and rows[0]["data"]["legs"][0].endswith("CE")
    pool_run(go)


async def next_day_untraded_call(sim, last=None):
    """The next day, with the CE leg not traded yet: its cached price is
    the previous session's (`last`, if given, in place of the model's)."""
    d2 = sim.market.today + timedelta(days=1)
    tok = int(next(p for p in await shorts(sim) if p["symbol"].endswith("CE"))["instrument_token"])
    sim.market.now = sim.market.now.replace(day=d2.day)
    orig = sim.dispatcher.publish

    def publish():
        orig()
        sim.dispatcher.last_price_times[tok] = sim.dispatcher.last_price_times[tok] - timedelta(days=1)
        if last is not None:
            sim.dispatcher.last_prices[tok] = last
    sim.dispatcher.publish = publish
    return d2, tok


def test_an_untraded_leg_is_priced_from_its_live_quote_from_09_20(pool_run, monkeypatch):
    """DTT Bankex Strangle, 28 Oct: a monthly BANKEX call that didn't trade
    all morning held every rule from 09:15, with the checkpoint met. From
    09:20 the leg is priced from its bid/ask mid; the checkpoint fires,
    and the leg closes at the quote, not at yesterday's last trade."""
    async def go(pool):
        sim = await start(pool, monkeypatch)
        call = next(p for p in await shorts(sim) if p["symbol"].endswith("CE"))
        d2, tok = await next_day_untraded_call(sim, last=400.0)
        sim.market.vol = 0.13          # premiums fall: the checkpoint is met on the quotes
        await sim.tick(f"{d2} 09:18", spot=25000)
        assert closes(await sim.fills()) == [], "before 09:20 it still holds"
        await sim.tick(f"{d2} 09:21", spot=25000)
        fills = closes(await sim.fills())
        assert "checkpoint_target" in {f["reason"] for f in fills}
        ce = next(f for f in fills if f["position_id"] == call["id"])
        assert float(ce["price"]) < 300, "closed at the quote's mid, not the 400 from yesterday"
        rows = [r for r in await decisions(sim) if r["rule"] == "stale_price_guard" and r["outcome"] == "no_action"]
        assert len(rows) == 1 and rows[0]["summary"] == "Priced from a live quote: not traded yet today"
        assert set(rows[0]["data"]["tokens"]) == {str(tok)}
    pool_run(go)


def test_a_one_sided_book_still_holds_and_says_so_once(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch)
        d2, tok = await next_day_untraded_call(sim)
        strat = sim.runner.strategy

        async def no_bids(keys):
            return {k: {"last_price": 150.0, "depth": {"buy": [], "sell": [{"price": 160.0}]}} for k in keys}
        monkeypatch.setattr(strat.resolver, "get_quotes", no_bids)
        sim.market.vol = 0.13
        await sim.tick(f"{d2} 09:21", spot=25000)
        await sim.tick(f"{d2} 09:40", spot=25000)
        assert closes(await sim.fills()) == []
        rows = [r for r in await decisions(sim) if "no bid and ask" in r["summary"]]
        assert len(rows) == 1 and rows[0]["outcome"] == "blocked"
    pool_run(go)


def test_the_quote_is_asked_for_at_most_every_15s_and_kept_90s_if_kite_fails(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch)
        d2, tok = await next_day_untraded_call(sim)
        strat = sim.runner.strategy
        asked, fail = [], [False]
        real = strat.resolver.get_quotes

        async def counted(keys):
            asked.append(sim.market.now)
            if fail[0]:
                raise RuntimeError("Kite quote timed out")
            return await real(keys)
        monkeypatch.setattr(strat.resolver, "get_quotes", counted)
        t0 = ist_at(sim, d2, "09:30")
        for s in (0, 5, 10, 16):
            await sim.tick(t0 + timedelta(seconds=s), spot=25000)
        assert len(asked) == 2, "09:30:00 and 09:30:16"
        assert strat._stale_quotes.get(tok)
        fail[0] = True
        await sim.tick(t0 + timedelta(seconds=90), spot=25000)
        assert strat._manage_ts == t0 + timedelta(seconds=90)
        assert await strat._quote_stale(sim.runner, t0 + timedelta(seconds=90), [strat.legs["CE"][0]["symbol"]]) is not None, \
            "a quote 74s old is kept while Kite fails"
        assert await strat._quote_stale(sim.runner, t0 + timedelta(seconds=120), [strat.legs["CE"][0]["symbol"]]) is None, \
            "one over 90s old isn't"
    pool_run(go)


def ist_at(sim, day, hhmm):
    h, m = map(int, hhmm.split(":"))
    return sim.market.now.replace(year=day.year, month=day.month, day=day.day, hour=h, minute=m, second=0, microsecond=0)


def test_trigger_refuted_by_quotes_is_noted_as_blocked(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch)
        up = await find_spot(sim, lambda p: side_sum(p, "PE") <= 0.45 * side_sum(p, "CE"), step=25)
        strat = sim.runner.strategy
        legs = [l for side in strat.legs.values() for l in side]

        async def flat_quotes(keys):   # live quotes say both sides are equal: no roll
            return {k: {"last_price": 100.0} for k in keys}
        monkeypatch.setattr(strat.resolver, "get_quotes", flat_quotes)
        await sim.tick("11:00", spot=up)
        await sim.tick("11:01", spot=up)
        assert closes(await sim.fills()) == []
        rows = await decisions(sim)
        assert [(r["rule"], r["outcome"]) for r in rows] == [("continuous_50pct_trigger", "blocked")], \
            "noted at most once per 5 minutes"
        assert set(rows[0]["data"]["quote"]) == {l["symbol"] for l in legs}
    pool_run(go)


# ── Convergence survives a restart ─────────────────────────────────────
# DTT Nifty Strangle, 28 Oct: converged into a straddle (trailing stop),
# then the strategy was rebuilt from the database (a restart, the
# runner's re-sync from the book, an automatic restart). Nothing saved
# that it had converged, so it came back a strangle: at 10:21 the 50%
# roll closed 22850CE and sold 22850CE again, and the trailing stop
# started over.

async def converge(sim):
    """A rally of 25 points a minute from 10:00 rolls the put up to the
    call's strike (the strike-cross cap): a straddle by about 11:14."""
    s = sim.runner.strategy
    t, spot = sim.market.now, sim.market.spot[sim.underlying]
    for _ in range(150):
        t += timedelta(minutes=1)
        spot += 25
        await sim.tick(t, spot=spot)
        if s.converged:
            return t, spot
    raise AssertionError("never converged")


def converged_state(s):
    return s.converged, s.convergence_premium, s._trailing_floor


def test_a_straddle_comes_back_converged_with_its_trailing_stop_after_a_restart(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch, convergence_mode="trailing_stop")
        t, spot = await converge(sim)
        strike = sim.runner.strategy.legs["CE"][0]["strike"]
        for _ in range(3):             # back toward the strike: the combined premium falls, the stop trails down
            t += timedelta(minutes=1)
            spot -= 10 if spot > strike else 0
            await sim.tick(t, spot=spot)
        before = converged_state(sim.runner.strategy)
        assert before[0] and before[2] < before[1], "the stop trailed down before the restart"
        await sim.restart()
        assert converged_state(sim.runner.strategy) == before
    pool_run(go)


def test_a_straddle_saved_before_this_was_saved_is_rebuilt_from_its_legs(pool_run, monkeypatch):
    """The live case: nothing saved (the deployment's state from before
    this fix). One CE and one PE at one strike is a straddle; its
    premium at convergence comes back from the fill that made it. The
    50% roll that re-sold the same strike doesn't happen."""
    async def go(pool):
        sim = await start(pool, monkeypatch, convergence_mode="trailing_stop")
        t, spot = await converge(sim)
        premium = sim.runner.strategy.convergence_premium
        await pool.execute("DELETE FROM deployment_state WHERE deployment_id = $1", sim.dep_id)
        await sim.restart()
        s = sim.runner.strategy
        assert s.converged and abs(s.convergence_premium - premium) < 0.01 and s._trailing_floor == s.convergence_premium
        n = len(await sim.fills())
        down = await find_spot(sim, lambda p: side_sum(p, "CE") <= 0.48 * side_sum(p, "PE"), step=-25)
        await sim.tick(t + timedelta(minutes=1), spot=down)
        later = (await sim.fills())[n:]
        assert "continuous_50pct_trigger" not in {f["reason"] for f in later}, "frozen: only the stop can close it"
    pool_run(go)


def test_an_entry_on_one_strike_is_a_straddle_from_the_start(pool_run, monkeypatch):
    async def go(pool):
        sim = await Sim.create(pool, monkeypatch, S, cfg(S, convergence_mode="trailing_stop"), mode="positional")
        await sim.tick("09:30", spot=25000)
        s = sim.runner.strategy
        real = s.resolver.get_leg_by_premium
        picked = {}

        async def same_strike(instrument, expiry, side, target, **kw):
            if side == "PE" and "CE" in picked:
                leg = await s.resolver.get_leg(instrument, expiry, picked["CE"], "PE")
                return dataclasses.replace(leg, last_price=await s.resolver.get_ltp(leg))
            leg = await real(instrument, expiry, side, target, **kw)
            picked[side] = leg.strike
            return leg
        monkeypatch.setattr(s.resolver, "get_leg_by_premium", same_strike)
        await sim.tick("10:00", spot=25000)
        legs = await shorts(sim)
        assert len(legs) == 2 and len({float(p["metadata"]["strike"]) for p in legs}) == 1
        assert s.converged and abs(s.convergence_premium - sum(float(p["avg_entry_price"]) for p in legs)) < 0.01
    pool_run(go)

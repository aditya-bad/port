"""strangle_monthly_v2 on a simulated market: contract rotation and
sizing, the continuous 50% roll, the 15:13 EOD check, checkpoint
re-entry, the expiry backstop, the stale-price guard, hedging, restart."""

import dataclasses

import pytest
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
        assert [r["summary"] for r in rows] == ["Priced from a live quote: not traded yet today",
                                                "Every leg priced again: management resumes"], "when the hold ended, for the day check"
        assert set(rows[0]["data"]["tokens"]) == {str(tok)}
        quoted = await pool.fetch("SELECT instrument_token, price FROM strategy_quotes WHERE deployment_id = $1", sim.dep_id)
        assert {int(r["instrument_token"]) for r in quoted} == {tok}, "each quoted price kept for the day check"
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


# ── SPIKES: a one-off quote or a wide book isn't a price ──────────────
# DTT Sensex Strangle, 3 Nov: 77600CE (no trade in minutes, priced from
# its quote) read ₹722.38 at 13:34:31, ₹1,066.10 at 13:34:46 and ₹724.05
# at 13:35:01. The 15 s in between set the 50% roll off; the re-check's
# own quote was as wild and confirmed it; the put went from 75000 to
# 76500. Here: the call untraded the next day, priced from its quote, and
# that quote set as each test needs.

def _book(mid, half):
    return {"buy": [{"price": round(mid - half, 2), "quantity": 1500, "orders": 5}],
            "sell": [{"price": round(mid + half, 2), "quantity": 1500, "orders": 5}]}


async def quoted_call(sim, monkeypatch):
    """The next day, the CE untraded; `set(mid, half=0.05)` makes its
    quote's book that (None: the market's own). Patched on the resolver's
    class, so it survives a restart."""
    from app.options import OptionsResolver
    d2, tok = await next_day_untraded_call(sim)
    strat = sim.runner.strategy
    key = f"{strat.exchange}:{strat.legs['CE'][0]['symbol']}"
    book = {"mid": None, "half": 0.05}
    real = OptionsResolver.get_quotes

    async def quotes(self, keys):
        out = await real(self, keys)
        if book["mid"] is not None and key in out:
            out[key] = {**out[key], "depth": _book(book["mid"], book["half"])}
        return out
    monkeypatch.setattr(OptionsResolver, "get_quotes", quotes)

    def set_(mid, half=0.05):
        book["mid"], book["half"] = mid, half
    return d2, tok, set_


def _spike(sim):
    """A call price that puts the put under 40% of it."""
    pe = next(l for l in sim.runner.strategy.legs["PE"])
    return round(sim.dispatcher.last_prices[pe["token"]] / 0.4, 2)


def test_a_one_off_quote_spike_doesnt_roll(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch)
        d2, tok, quote = await quoted_call(sim, monkeypatch)
        t0 = ist_at(sim, d2, "09:30")
        await sim.tick(t0, spot=25000)
        normal = sim.runner.strategy._stale_quotes[tok][0]
        spike = _spike(sim)
        assert spike > 1.25 * normal
        quote(spike)
        await sim.tick(t0 + timedelta(seconds=15), spot=25000)
        quote(None)
        await sim.tick(t0 + timedelta(seconds=30), spot=25000)
        assert closes(await sim.fills()) == [], "a quote that stood for 15 s rolls nothing"
        held = [r for r in await decisions(sim) if r["rule"] == "quote_guard"]
        assert len(held) == 1 and held[0]["data"]["kept"] == round(normal, 2)
        rows = await pool.fetch("SELECT at, price FROM strategy_quotes WHERE deployment_id = $1 AND instrument_token = $2 "
                                "ORDER BY at", sim.dep_id, tok)
        assert [round(float(r["price"]), 2) for r in rows][:2] == [round(normal, 2)] * 2, \
            "what's recorded is the price taken, so the day check's replay takes it too"
    pool_run(go)


def test_a_jump_a_second_quote_agrees_with_is_taken(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch)
        d2, tok, quote = await quoted_call(sim, monkeypatch)
        t0 = ist_at(sim, d2, "09:30")
        await sim.tick(t0, spot=25000)
        quote(_spike(sim))
        await sim.tick(t0 + timedelta(seconds=15), spot=25000)
        assert closes(await sim.fills()) == []
        await sim.tick(t0 + timedelta(seconds=30), spot=25000)
        rolled = closes(await sim.fills())
        assert [f["reason"] for f in rolled] == ["continuous_50pct_trigger"], "15 s later the quote still says so: the move is real"
    pool_run(go)


def test_a_wide_book_is_no_price_for_an_untraded_leg(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch)
        d2, tok, quote = await quoted_call(sim, monkeypatch)
        sim.market.vol = 0.13          # the checkpoint would be met on the quote's mid
        t0 = ist_at(sim, d2, "09:25")
        normal = sim.market.price_of(sim.market.by_token[tok])
        quote(normal, half=0.20 * normal)   # a spread of 40% of the mid
        await sim.tick(t0, spot=25000)
        assert closes(await sim.fills()) == [], "it holds, as with no quote at all"
        summaries = [r["summary"] for r in await decisions(sim)]
        held = [x for x in summaries if "close enough together" in x]
        assert held and f"bid {normal * 0.8:,.2f} / ask {normal * 1.2:,.2f}" in held[0], "the hold says what the book was"
        assert "Priced from a live quote: not traded yet today" not in summaries
    pool_run(go)


def test_from_09_30_a_held_leg_with_no_usable_mid_is_priced_at_its_ask(pool_run, monkeypatch):
    """6 Nov 2026, DTT Bankex Strangle: 64000CE hadn't traded and its book
    had no usable mid; every rule held 09:20-11:10, its trailing stop due.
    A short leg is bought back at the ask, so from 09:30 that's its price
    (a book wider than 30%, or an ask only)."""
    async def go(pool):
        sim = await start(pool, monkeypatch)
        d2, tok, quote = await quoted_call(sim, monkeypatch)
        normal = sim.market.price_of(sim.market.by_token[tok])
        quote(normal, half=0.20 * normal)   # a spread of 40% of the mid
        await sim.tick(ist_at(sim, d2, "09:31"), spot=25000)
        strat = sim.runner.strategy
        assert round(strat._stale_quotes[tok][0], 2) == round(normal * 1.2, 2), "priced at the ask"
        assert "Priced from a live quote: not traded yet today" in [r["summary"] for r in await decisions(sim)]
    pool_run(go)


def test_a_held_leg_with_a_bid_only_still_holds_and_says_so(pool_run, monkeypatch):
    async def go(pool):
        from app.options import OptionsResolver
        sim = await start(pool, monkeypatch)
        d2, tok, quote = await quoted_call(sim, monkeypatch)
        strat = sim.runner.strategy
        key = f"{strat.exchange}:{strat.legs['CE'][0]['symbol']}"
        real = OptionsResolver.get_quotes

        async def bid_only(self, keys):
            out = await real(self, keys)
            if key in out:
                out[key] = {**out[key], "depth": {"buy": [{"price": 150.0, "quantity": 20}], "sell": []}}
            return out
        monkeypatch.setattr(OptionsResolver, "get_quotes", bid_only)
        await sim.tick(ist_at(sim, d2, "09:31"), spot=25000)
        held = [r["summary"] for r in await decisions(sim) if "close enough together" in r["summary"]]
        assert held and "bid only, 150.00" in held[0]
    pool_run(go)


def test_the_re_check_refuses_when_a_quote_is_far_from_the_price_it_checks(pool_run, monkeypatch):
    """Ticks set the roll off; the put's quote a moment later is 30% under
    its tick. Before, the quote's price was acted on; now the two
    disagree, so it waits, noted as the usual refusal."""
    async def go(pool):
        from app.options import OptionsResolver
        sim = await start(pool, monkeypatch)
        up = await find_spot(sim, lambda p: side_sum(p, "PE") <= 0.45 * side_sum(p, "CE"), step=25)
        pe = sim.runner.strategy.legs["PE"][0]
        key = f"{sim.runner.strategy.exchange}:{pe['symbol']}"
        real = OptionsResolver.get_quotes

        async def quotes(self, keys):
            out = await real(self, keys)
            if key in out:
                out[key] = {**out[key], "depth": _book(out[key]["last_price"] * 0.7, 0.05)}
            return out
        monkeypatch.setattr(OptionsResolver, "get_quotes", quotes)
        await sim.tick("11:00", spot=up)
        assert closes(await sim.fills()) == []
        rows = [r for r in await decisions(sim) if r["rule"] == "continuous_50pct_trigger"]
        assert len(rows) == 1 and rows[0]["outcome"] == "blocked"
        assert rows[0]["summary"] == "Not acted on: live quotes don't confirm the tick prices", \
            "the day check's replay holds off on this exact wording"
        assert rows[0]["data"]["jumped"] == [pe["symbol"]]
    pool_run(go)


def test_a_spike_just_after_a_restart_is_held_back_too(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch)
        d2, tok, quote = await quoted_call(sim, monkeypatch)
        t0 = ist_at(sim, d2, "09:30")
        await sim.tick(t0, spot=25000)
        normal = sim.runner.strategy._stale_quotes[tok][0]
        await sim.restart()
        assert sim.runner.strategy._quote_ref[tok][0] == normal, "its last quoted price, read back from what was recorded"
        quote(_spike(sim))
        await sim.tick(t0 + timedelta(seconds=15), spot=25000)
        assert closes(await sim.fills()) == []
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


# ── Live prices: what a trade could be done at, not a last trade from days ago
# DTT Sensex Strangle, 30 Oct: the 10:00 entry sold 70800PE at ₹125.90, its
# last trade, on 21 Oct: picked because that stale price matched the target,
# filled at it, while its bid/ask stood around twice that. 29 Oct 15:13: the
# end-of-day check bought 71400PE back at its bid/ask mid (248.22) and sold
# the same contract again at its last trade from 11:55 (342.30).

def test_an_entry_never_picks_or_fills_a_strike_at_a_last_trade_from_days_ago(pool_run, monkeypatch):
    from datetime import datetime
    async def go(pool):
        sim = await Sim.create(pool, monkeypatch, S, cfg(S), mode="positional")
        thin = "NIFTY26NOV2424300PE"                 # its bid/ask is about twice the target
        sim.market.thin[thin] = (166.70, datetime(2026, 10, 5, 9, 34))   # but its last trade, 9 days old, matches it
        sim.market.no_book.add("NIFTY26NOV2424400PE")
        sim.market.thin["NIFTY26NOV2424400PE"] = (166.65, datetime(2026, 10, 5, 9, 34))   # and nobody quotes this one at all
        assert sim.market.price_of(sim.market.by_key[f"NFO:{thin}"]) > 300
        await sim.tick("10:00", spot=25000)
        legs = await shorts(sim)
        pe = next(p for p in legs if p["symbol"].endswith("PE"))
        assert pe["symbol"] not in (thin, "NIFTY26NOV2424400PE")
        row = sim.market.by_key[f"NFO:{pe['symbol']}"]
        assert abs(float(pe["avg_entry_price"]) - sim.market.price_of(row)) < 0.06, "filled where it trades now"
        assert abs(float(pe["avg_entry_price"]) - 166.7) / 166.7 < 0.15
    pool_run(go)


def test_a_replacement_never_resells_the_strike_it_closes(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch)
        strat = sim.runner.strategy
        pe = strat.legs["PE"][0]
        own = sim.market.price_of(sim.market.by_key[f"NFO:{pe['symbol']}"])
        leg, price = await strat._pick_leg(sim.runner, "PE", own, without=pe)   # its own premium: its own strike is closest
        assert leg.strike != pe["strike"]
        assert abs(price - sim.market.price_of(sim.market.by_key[f"NFO:{leg.tradingsymbol}"])) < 0.06
    pool_run(go)


def test_a_leg_with_no_recent_trade_is_managed_and_closed_at_its_bid_ask(pool_run, monkeypatch):
    """A put that traded at 10:05 and not since: its last trade says the
    sides are even, its bid/ask that it has fallen to under half the
    call. The roll goes by the bid/ask, and closes the put there."""
    from datetime import datetime
    async def go(pool):
        sim = await start(pool, monkeypatch)
        strat = sim.runner.strategy
        pe = next(p for p in await shorts(sim) if p["symbol"].endswith("PE"))
        up = await find_spot(sim, lambda p: side_sum(p, "PE") <= 0.45 * side_sum(p, "CE"), step=25)
        ce_then = side_sum(await leg_prices(sim, up), "CE")
        sim.market.thin[pe["symbol"]] = (round(ce_then, 2), datetime(2026, 10, 14, 10, 5))   # even with the call, on its last trade
        await sim.tick("11:00", spot=up)
        fills = [f for f in await sim.fills() if f["reason"] == "continuous_50pct_trigger"]
        assert fills, "rolled on the bid/ask, which its last trade hid"
        closed = next(f for f in fills if f["position_id"] == pe["id"])
        live = sim.market.price_of(sim.market.by_key[f"NFO:{pe['symbol']}"])
        assert abs(float(closed["price"]) - live) < 0.06, "bought back at its bid/ask, not its last trade"
        rows = await pool.fetch("SELECT instrument_token FROM strategy_quotes WHERE deployment_id = $1", sim.dep_id)
        assert int(pe["instrument_token"]) in {int(r["instrument_token"]) for r in rows}
        assert any(r["summary"] == "Priced from a live quote: no recent trade" for r in await decisions(sim))
    pool_run(go)


def test_on_sensex_it_leans_to_round_strikes_when_the_premium_allows(pool_run, monkeypatch):
    async def go(pool):
        sim = await Sim.create(pool, monkeypatch, S, cfg(S, instrument="SENSEX", instrument_tokens=[265], adjustment_strike_window=60),
                               mode="positional", underlying="SENSEX", spot=82230)
        await sim.tick("10:00", spot=82230)
        legs = await shorts(sim)
        assert len(legs) == 2
        target = 500000 * 0.05 / 20 / 2
        for p in legs:
            assert float(p["metadata"]["strike"]) % 500 == 0, p["symbol"]
            assert abs(float(p["avg_entry_price"]) - target) / target <= 0.15
        strat = sim.runner.strategy
        plain = [await strat.resolver.get_leg_by_premium("SENSEX", strat.contract_expiry, side, target, strike_window=60, live=True)
                 for side in ("CE", "PE")]
        assert any(l.strike % 500 for l in plain), "the closest premiums weren't round: the lean chose"
    pool_run(go)


# ── FAR STRIKES: on NIFTY, more than 27 days out, never a strike ending in 50 ──

async def plain_pick(strat, side, target, **kw):
    """The pick with no FAR STRIKES rule: what it would take without it."""
    return await strat.resolver.get_leg_by_premium("NIFTY", strat.contract_expiry, side, target,
                                                   strike_window=strat.adjustment_strike_window, live=True, **kw)


def test_on_nifty_far_from_expiry_it_never_takes_a_strike_ending_in_50(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch)   # 14 Oct, the 24 Nov contract: 41 days out
        legs = await shorts(sim)
        assert len(legs) == 2
        for p in legs:
            assert float(p["metadata"]["strike"]) % 100 == 0, p["symbol"]
            assert p["metadata"]["trigger_values"]["strike_multiple"] == 100
        strat = sim.runner.strategy
        target = legs[0]["metadata"]["trigger_values"]["target_premium"]
        plain = [(await plain_pick(strat, side, target)).strike for side in ("CE", "PE")]
        assert all(s % 100 == 50 for s in plain), f"the closest premiums were 50 strikes ({plain}): the rule chose"
        await assert_book_consistent(pool, sim.dep_id)
    pool_run(go)


def test_within_27_days_a_strike_ending_in_50_can_be_taken(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch, far_expiry_days=60)   # 41 days out is now "near"
        legs = await shorts(sim)
        assert sorted(float(p["metadata"]["strike"]) for p in legs) == [23250.0, 26750.0]
        assert all(p["metadata"]["trigger_values"]["strike_multiple"] is None for p in legs)
        strat = sim.runner.strategy
        assert strat._pick_rules() == {}
        strat.far_expiry_days = 27
        assert strat._pick_rules() == {"strike_multiple": 100.0}, "active management's searches keep the rule"
        strat.today = strat.contract_expiry - timedelta(days=27)   # the day it turns 27 days out
        assert strat._pick_rules() == {} and strat._far_multiple() is None
    pool_run(go)


def test_rolls_and_replacements_far_from_expiry_take_only_100_strikes(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch)
        strat = sim.runner.strategy
        targets = (40, 75, 120, 166.7, 240, 320)
        for side in ("CE", "PE"):
            for t in targets:
                leg, _ = await strat._pick_leg(sim.runner, side, t)
                assert leg.strike % 100 == 0, (side, t, leg.strike)
        strat.far_strike_multiple = 0   # the rule off: some of the same searches land on a 50 strike
        off = [(await strat._pick_leg(sim.runner, side, t))[0].strike for side in ("CE", "PE") for t in targets]
        assert any(s % 100 == 50 for s in off), off
    pool_run(go)


def test_the_strike_cross_cap_stops_short_of_an_old_50_strike(pool_run, monkeypatch):
    """A call sold before the rule (or a contract that came within 27 days
    and went out again in a replay) at 25,050: a put chasing premium past
    it caps at 25,000, not onto 25,050."""
    async def go(pool):
        sim = await start(pool, monkeypatch)
        strat = sim.runner.strategy
        strat.legs["CE"][0]["strike"] = 25050.0
        leg, _ = await strat._pick_leg(sim.runner, "PE", 2000)
        assert leg.strike == 25000.0
        strat.legs["CE"][0]["strike"] = 26700.0
        strat.legs["PE"][0]["strike"] = 24950.0
        leg, _ = await strat._pick_leg(sim.runner, "CE", 2000)
        assert leg.strike == 25000.0
        strat.far_strike_multiple = 0
        leg, _ = await strat._pick_leg(sim.runner, "CE", 2000)
        assert leg.strike == 24950.0, "without the rule it caps onto the other side's strike"
    pool_run(go)


def test_hedges_far_from_expiry_take_only_100_strikes(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch, enable_hedging=True)
        longs = [p for p in await sim.open_positions() if p["side"] == "long"]
        assert len(longs) == 2
        assert all(float(p["metadata"]["strike"]) % 100 == 0 for p in longs), [p["symbol"] for p in longs]
        strat = sim.runner.strategy
        # a hedge kept a fixed distance from its short (a roll): 150 points
        # from 26,700 is 26,850; the nearest it may take is a 100 strike
        short = next(p for p in await shorts(sim) if p["symbol"].endswith("CE"))
        stub = dataclasses.make_dataclass("Stub", ["instrument_token", "strike"])(999, 26700.0)
        await strat._resolve_and_open_hedge(sim.runner, sim.market.now, "CE", stub, float(short["avg_entry_price"]), "test", 150)
        assert strat.hedges[999]["strike"] in (26800.0, 26900.0)
    pool_run(go)


def test_the_resolver_rule_leaves_no_other_strike_and_says_so_when_none_is_priced(pool_run, monkeypatch):
    async def go(pool):
        import pytest
        sim = await start(pool, monkeypatch)
        strat = sim.runner.strategy
        assert (await plain_pick(strat, "CE", 166.7)).strike % 100 == 50
        assert (await plain_pick(strat, "CE", 166.7, strike_multiple=100)).strike % 100 == 0
        with pytest.raises(ValueError, match="only multiples of 100000"):
            await plain_pick(strat, "CE", 166.7, strike_multiple=100000)
    pool_run(go)


def test_active_managements_extra_legs_far_from_expiry_take_only_100_strikes(pool_run, monkeypatch):
    """After converging under active_management the borrowed DTT
    `_adjust` sells the extra legs: its strike search keeps the rule."""
    async def go(pool):
        sim = await start(pool, monkeypatch, convergence_mode="active_management")
        strat = sim.runner.strategy
        strat.adjustment_size_pct = 0.25   # as _active_management_tick sets it
        for bigger in (300, 420, 560):
            await strat._adjust(sim.runner, sim.market.now, "PE", bigger)
        pes = [float(p["metadata"]["strike"]) for p in await shorts(sim) if p["symbol"].endswith("PE")]
        assert len(pes) == 4 and all(s % 100 == 0 for s in pes), pes   # without it: 23200, 23250, 23300, 23350
    pool_run(go)


def test_a_held_legs_book_somewhat_wide_still_prices_it(pool_run, monkeypatch):
    """5 Nov 2026, DTT Bankex Strangle: 64000CE hadn't traded and its book
    was wider than the 10% a strike search takes, so every rule held from
    09:20 to 12:46. A leg already held is priced from a book up to 30%
    wide (THIN BOOKS)."""
    async def go(pool):
        sim = await start(pool, monkeypatch)
        d2, tok, quote = await quoted_call(sim, monkeypatch)
        sim.market.vol = 0.13          # the checkpoint is met on the quote's mid
        normal = sim.market.price_of(sim.market.by_token[tok])
        quote(normal, half=0.10 * normal)   # a spread of 20% of the mid
        await sim.tick(ist_at(sim, d2, "09:30"), spot=25000)
        assert "checkpoint_target" in {f["reason"] for f in closes(await sim.fills())}, "managed, not held"
    pool_run(go)


def _capped(sim, monkeypatch, depth, last=None):
    """The CE at 25,000, so a put chasing premium caps onto 25,000PE, whose
    quote is `depth` with its last trade `last` (None: none today)."""
    from app.options import OptionsResolver
    strat = sim.runner.strategy
    strat.legs["CE"][0]["strike"] = 25000.0
    real = OptionsResolver.get_quotes

    async def quotes(self, keys):
        out = await real(self, keys)
        for k in keys:
            if k.endswith("25000PE"):
                out[k] = {**(out.get(k) or {}), "depth": depth, "last_price": last or 0,
                          "last_trade_time": None if last is None else sim.market.now}
        return out
    monkeypatch.setattr(OptionsResolver, "get_quotes", quotes)
    return strat


def test_the_strike_a_roll_is_forced_onto_sells_at_its_bid_when_its_book_is_wide(pool_run, monkeypatch):
    """5 Nov 2026, DTT Sensex Strangle: the put roll capped onto 77600PE was
    refused 70 times (09:23-15:09), "no live price": no trade that day and
    a book wider than the strike search's 10%. The forced strike takes its
    mid if its book is within 30%, else its bid, which a sell fills at."""
    async def go(pool):
        sim = await start(pool, monkeypatch)
        strat = _capped(sim, monkeypatch, {"buy": [{"price": 400.0, "quantity": 20}], "sell": [{"price": 700.0, "quantity": 20}]})
        leg, price = await strat._pick_leg(sim.runner, "PE", 2000)
        assert leg.strike == 25000.0 and price == 400.0, "a book 55% wide: sold at its bid"
        strat = _capped(sim, monkeypatch, {"buy": [{"price": 500.0, "quantity": 20}], "sell": [{"price": 600.0, "quantity": 20}]})
        _, price = await strat._pick_leg(sim.runner, "PE", 2000)
        assert price == 550.0, "a book 18% wide: its mid"
        strat = _capped(sim, monkeypatch, {"buy": [{"price": 450.0, "quantity": 20}], "sell": []})
        _, price = await strat._pick_leg(sim.runner, "PE", 2000)
        assert price == 450.0, "only a bid: the bid"
        # UNTRADED CAP: no trade and no bid on the capped strike: the nearest
        # strike short of it that has a price, instead of no roll at all
        strat = _capped(sim, monkeypatch, {"buy": [], "sell": []})
        leg, price = await strat._pick_leg(sim.runner, "PE", 2000)
        assert leg.strike < 25000.0 and price > 0, "a put below the call's strike, at its own price"
        assert strat._picked_capped, "the band check doesn't apply to it"
        # the nearest priced one is the strike being closed: nothing to change
        old = {"strike": leg.strike}
        try:
            await strat._pick_leg(sim.runner, "PE", 2000, without=old)
            raise AssertionError("re-selling the strike it closes changes nothing")
        except Exception as e:
            assert type(e).__name__ == "SameStrike"
    pool_run(go)


def test_a_leg_that_never_ticks_is_priced_from_its_quote_after_2_minutes(pool_run, monkeypatch):
    """5 Nov 2026: the feed was wrongly marked down at 11:43, so the puts
    sold after it (Nifty 24350PE at 12:22, ...) were never subscribed and
    never had a tick; every rule held to the close, the trailing stop too.
    After 2 minutes with no tick, the leg is priced from its quote."""
    async def go(pool):
        sim = await start(pool, monkeypatch)
        tok = int(next(p for p in await shorts(sim) if p["symbol"].endswith("CE"))["instrument_token"])
        orig = sim.dispatcher.publish

        def publish():
            orig()
            sim.dispatcher.last_prices.pop(tok, None)
            sim.dispatcher.last_price_times.pop(tok, None)
        sim.dispatcher.publish = publish
        sim.market.vol = 0.13          # the checkpoint is met
        t0 = ist_at(sim, sim.market.today + timedelta(days=1), "10:00")
        sim.market.now = sim.market.now.replace(day=t0.day)
        sim.runner.strategy._no_tick_since.clear()   # counted from this session's first tick without one
        await sim.tick(t0, spot=25000)
        await sim.tick(t0 + timedelta(seconds=60), spot=25000)
        assert closes(await sim.fills()) == [], "a minute without a tick still waits for one"
        await sim.tick(t0 + timedelta(seconds=130), spot=25000)
        assert "checkpoint_target" in {f["reason"] for f in closes(await sim.fills())}, "priced from its quote"
    pool_run(go)


def test_a_roll_isnt_made_onto_a_strike_far_outside_its_band(pool_run, monkeypatch):
    """6 Nov 2026 09:15, DTT Sensex Strangle: almost no BSE strike had
    traded yet, so the call roll's nearest priced strike was 83000CE at
    100.00 for a target of about 337; it sold that, then rolled straight
    back. A replacement more than 25% outside its band isn't made."""
    async def go(pool):
        import dataclasses
        sim = await start(pool, monkeypatch)
        strat = sim.runner.strategy
        old = strat.legs["CE"][0]
        real = strat.resolver.get_leg_by_premium

        async def far(*a, **k):   # the only strike with a price, a third of the target
            leg = await real(*a, **k)
            return dataclasses.replace(leg, strike=leg.strike + 1000, last_price=100.0)
        monkeypatch.setattr(strat.resolver, "get_leg_by_premium", far)
        prices = {l["token"]: 200.0 for s_ in strat.legs.values() for l in s_}
        done = await strat._replace_leg(sim.runner, sim.market.now, "CE", old, 337.0, "continuous_50pct_trigger", {}, prices,
                                        prefer_range=(308.0, 366.0))
        assert done is False and strat.legs["CE"][0] is old and closes(await sim.fills()) == []
        assert any("far outside the band" in r["summary"] for r in await decisions(sim))
        monkeypatch.setattr(strat.resolver, "get_leg_by_premium", real)
    pool_run(go)


def test_a_rule_held_by_a_fault_is_raised_after_3_minutes_not_10(pool_run, monkeypatch):
    """5 Nov 2026: the Sensex put roll was refused from 09:23; the first
    alert came at 09:37. A strangle's blocked rule is raised (pushed to the
    phone through the health monitor) after 3 minutes."""
    async def go(pool):
        sim = await start(pool, monkeypatch)
        assert sim.runner.blocked.AFTER == timedelta(minutes=3)
        d2, tok = await next_day_untraded_call(sim)
        await sim.tick(ist_at(sim, d2, "09:15"), spot=25000)
        await sim.tick(ist_at(sim, d2, "09:17"), spot=25000)
        n = await pool.fetchval("SELECT count(*) FROM strategy_issues WHERE deployment_id = $1 AND kind = 'blocked'", sim.dep_id)
        assert n == 0, "2 minutes: not yet"
        await sim.tick(ist_at(sim, d2, "09:18"), spot=25000)
        n = await pool.fetchval("SELECT count(*) FROM strategy_issues WHERE deployment_id = $1 AND kind = 'blocked'", sim.dep_id)
        assert n == 1, "3 minutes held: raised"
    pool_run(go)


def test_a_straddles_trailing_low_moves_on_quoted_prices_too(pool_run, monkeypatch):
    """5 Nov 2026, DTT BankNifty Strangle: converged at 13:20 at 1,406.80;
    the new put never had a tick, so the trailing stop's low stayed at
    1,406.80 while the straddle traded as low as 1,391.90. The low follows
    whatever price the strangle manages on: a leg priced from its quote
    (no ticks for 2 minutes) moves it like a ticked one."""
    async def go(pool):
        sim = await start(pool, monkeypatch, convergence_mode="trailing_stop")
        strat = sim.runner.strategy
        strat.converged, strat.convergence_premium, strat._trailing_floor = True, 10_000.0, 10_000.0
        tok = int(strat.legs["PE"][0]["token"])
        orig = sim.dispatcher.publish

        def publish():   # the put never ticks
            orig()
            sim.dispatcher.last_prices.pop(tok, None)
            sim.dispatcher.last_price_times.pop(tok, None)
        sim.dispatcher.publish = publish
        t0 = ist_at(sim, sim.market.today + timedelta(days=1), "10:00")
        sim.market.now = sim.market.now.replace(day=t0.day)
        strat._no_tick_since.clear()
        await sim.tick(t0, spot=25000)
        assert strat._trailing_floor == 10_000.0, "no price for the put yet: no decision, the low stays"
        await sim.tick(t0 + timedelta(seconds=130), spot=25000)
        quoted = strat._stale_quotes[tok][0]
        ce = sim.dispatcher.last_prices[int(strat.legs["CE"][0]["token"])]
        assert strat._trailing_floor == pytest.approx(ce + quoted, abs=0.05), "the low is the straddle at the put's quote"
    pool_run(go)

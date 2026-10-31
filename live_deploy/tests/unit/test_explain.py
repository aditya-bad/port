"""Explain (app/geek/explain.py): a replay told in words. Every reason and
number in it is what the strategy recorded; a rule without wording of its
own is said with its name and numbers."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.geek import explain as X

IST = ZoneInfo("Asia/Kolkata")


def t(h, m, day=21):
    return datetime(2026, 10, day, h, m, tzinfo=IST).isoformat()


def replay(events, frames=None, legs=None):
    frames = frames or [
        {"t": t(10, 0), "idx": 24900.0, "vix": 13.2, "booked": 0.0, "pnl": 0.0, "legs": [], "split": None},
        {"t": t(11, 40), "idx": 25100.0, "vix": 13.0, "booked": 0.0, "pnl": 2100.0, "legs": [], "split": None},
        {"t": t(15, 25), "idx": 25150.0, "vix": 12.6, "booked": 1800.0, "pnl": 4200.0, "legs": [], "split": [3100.0, -400.0, 1500.0]},
    ]
    return {"frames": frames, "events": events, "legs": legs or [], "start": t(10, 0), "end": t(15, 25), "index_name": "NIFTY 50"}


ENTRY = {"t": t(10, 0), "frame": 0, "kind": "entry", "label": "Entry", "trigger": "initial_entry",
         "values": {"target_premium": 120.5, "days_to_expiry": 34},
         "fills": [{"action": "sell", "symbol": "NIFTY26NOV25300CE", "qty": 75, "price": 121.0, "opening": True, "pnl": None},
                   {"action": "sell", "symbol": "NIFTY26NOV24500PE", "qty": 75, "price": 119.5, "opening": True, "pnl": None}],
         "booked": None, "idx": 24900.0, "pnl": 0.0}
ROLL = {"t": t(11, 42), "frame": 1, "kind": "roll", "label": "Roll", "trigger": "continuous_50pct_trigger",
        "values": {"bigger_side": "CE", "smaller_side": "PE", "bigger_sum": 127.0, "smaller_sum": 61.0, "ratio": 0.5},
        "fills": [{"action": "buy", "symbol": "NIFTY26NOV24500PE", "qty": 75, "price": 61.0, "opening": False, "pnl": 4387.5},
                  {"action": "sell", "symbol": "NIFTY26NOV24800PE", "qty": 75, "price": 118.0, "opening": True, "pnl": None}],
        "booked": 4387.5, "idx": 25100.0, "pnl": 2100.0}


def test_each_trade_said_with_its_reason_and_numbers():
    s = X.script(replay([ENTRY, ROLL]), name="Nifty Strangle A", strategy="strangle_monthly_v2", strategy_label="Monthly strangle")
    kinds = [b["kind"] for b in s["beats"]]
    assert kinds == ["intro", "trade", "trade", "summary"]
    intro, entry, roll, summary = (b["text"] for b in s["beats"])
    assert intro.startswith("Nifty Strangle A, running Monthly strangle. This replay runs from 21 Oct at 10:00 to 21 Oct at 15:25.")
    assert "NIFTY 50 was at 24,900, and India VIX at 13.2." in intro
    assert "It entered: it looked for a call and a put each worth about ₹120, on the contract 34 days from expiry." in entry
    assert "It sold the 25,300 call at ₹121 and sold the 24,500 put at ₹120." in entry
    assert ("The put side had fallen to ₹61, 48% of the call side's ₹127. The rule acts below 50%, "
            "so it rolled the cheaper put closer to the market") in roll
    assert "It bought back the 24,500 put at ₹61 and sold the 24,800 put at ₹118. That booked plus ₹4,388." in roll
    assert "So far it stands at plus ₹2,100." in roll
    assert summary.startswith("In all, plus ₹4,200: plus ₹1,800 booked and plus ₹2,400 still open.")
    assert "NIFTY 50 moved up 250 points, to 25,150, and VIX went from 13.2 to 12.6." in summary
    assert "The best single move was the roll at 11:42, booking ₹4,388." in summary
    assert "Roughly, plus ₹3,100 of it came from time passing, minus ₹400 from NIFTY 50's move, and plus ₹1,500 from changes in volatility." in summary
    assert s["words"] == sum(len(b["text"].split()) for b in s["beats"])


@pytest.mark.parametrize("trigger,values,want", [
    ("checkpoint_target", {"total_cycle_profit": 6100, "cycle_realized_pnl": 4400, "unrealized": 1700, "checkpoint_target": 5000},
     "The cycle had made ₹6,100: ₹4,400 booked and ₹1,700 still open. That passed its checkpoint of ₹5,000, so it closed everything"),
    ("eod_gap_check", {"bigger_side": "PE", "smaller_side": "CE", "bigger_sum": 200, "smaller_sum": 150, "eod_gap_floor": 0.8},
     "At the end-of-day check the call side was ₹150, 75% of the put side. It doesn't hold a gap that wide overnight (the floor is 80%)"),
    ("convergence_stop", {"convergence_premium": 210, "combined_now": 232, "stop_level": 231},
     "Its legs had met at one strike, a straddle, at ₹210 combined. They rose to ₹232, past the stop at ₹231"),
    ("contract_expiry_backstop", {"contract_expiry": "2026-10-27"}, "Its contract expired that day"),
])
def test_the_strangles_rules_in_words(trigger, values, want):
    assert want in X.why("strangle_monthly_v2", trigger, values)


def test_a_rule_without_wording_says_its_name_and_numbers():
    assert X.why("intraday_dtt_simple", "leg_stop", {"leg_price": 162.5, "stop": 160}) == "Its leg stop rule acted, seeing leg price 162.5 · stop 160."
    assert X.why("intraday_dtt_simple", "force_exit", None) == "Its exit time came, so it closed everything."
    assert X.why("calendar_btst", "odd_rule", None) == "Its odd rule rule acted."


def test_checks_that_didnt_trade_once_each_in_time_order():
    held = {"t": datetime(2026, 10, 21, 9, 15, tzinfo=IST), "rule": "stale_price_hold", "outcome": "blocked",
            "summary": "24500PE hasn't traded yet today: management held until it does"}
    again = {**held, "t": datetime(2026, 10, 21, 9, 16, tzinfo=IST)}
    nothing = {"t": datetime(2026, 10, 21, 15, 13, tzinfo=IST), "rule": "eod_gap_check", "outcome": "no_action",
               "summary": "PE/CE 0.92 ≥ floor 0.80: gap small enough to hold overnight."}
    s = X.script(replay([ENTRY, ROLL]), name="N", strategy="strangle_monthly_v2", strategy_label="S", decisions=[nothing, held, again])
    later = {**nothing, "t": datetime(2026, 10, 22, 15, 13, tzinfo=IST), "summary": "another day"}
    s2 = X.script(replay([ENTRY]), name="N", strategy="strangle_monthly_v2", strategy_label="S", decisions=[later])
    assert [b["text"] for b in s2["beats"] if b["kind"] == "decision"] == ["At 15:13 on 22 Oct it checked and did nothing: another day."]
    texts = [b["text"] for b in s["beats"] if b["kind"] == "decision"]
    assert texts == ["At 09:15 it held off: 24500PE hasn't traded yet today: management held until it does.",
                     "At 15:13 it checked and did nothing: PE/CE 0.92 ≥ floor 0.80: gap small enough to hold overnight."]
    order = [b["kind"] for b in s["beats"]]
    assert order == ["intro", "decision", "trade", "trade", "decision", "summary"], "in time order"
    assert [b["frame"] for b in s["beats"]] == [0, 0, 0, 1, 1, 2], "each at its frame"


def test_a_checkpoint_exit_and_re_entry_said_as_one():
    ev = {**ENTRY, "kind": "new", "label": "New cycle", "trigger": "checkpoint_target",
          "values": {"total_cycle_profit": 6100, "cycle_realized_pnl": 4400, "unrealized": 1700, "checkpoint_target": 5000},
          "closed_before": {"fills": [{"action": "buy", "symbol": "NIFTY26NOV25300CE", "qty": 75, "price": 40.0, "opening": False, "pnl": 6075.0}],
                            "booked": 6075.0, "cycle": 1}}
    text = X.script(replay([ev]), name="N", strategy="strangle_monthly_v2", strategy_label="S")["beats"][1]["text"]
    assert "passed its checkpoint of ₹5,000" in text and "It bought back the 25,300 call at ₹40. Closing those legs booked plus ₹6,075." in text
    assert "Then it entered again. It sold the 25,300 call at ₹121" in text


def test_a_value_not_recorded_is_left_out_and_the_start_says_what_was_held_before():
    assert X.why("strangle_monthly_v2", "checkpoint_target", {"total_cycle_profit": 3060, "cycle_realized_pnl": 4206, "unrealized": None, "checkpoint_target": 3000}) == \
        "The cycle had made ₹3,060: ₹4,206 booked. That passed its checkpoint of ₹3,000, so it closed everything and took the profit."
    legs = [{"symbol": "NIFTY26NOV24400PE", "t0": t(9, 30, 20)}, {"symbol": "NIFTY26NOV25650CE", "t0": t(10, 0)}]
    intro = X.script(replay([ENTRY], legs=legs), name="N", strategy="strangle_monthly_v2", strategy_label="S")["beats"][0]["text"]
    assert intro.endswith("It started holding 1 leg: the 24,400 put."), intro


def test_nothing_to_say_without_frames():
    assert X.script({"frames": [], "events": []}, name="N", strategy="s", strategy_label="S") == {"beats": [], "words": 0}


@pytest.mark.parametrize("v,sign,s", [(1234567, False, "₹12,34,567"), (-950.4, True, "minus ₹950"), (0.2, True, "₹0"), (None, False, "an unknown amount")])
def test_rupees_as_said(v, sign, s):
    assert X.rs(v, sign) == s


def test_contracts_as_said():
    assert X._contract("NIFTY26NOV24500PE") == "the 24,500 put"
    assert X._contract("NIFTY2450922400CE") == "the 22,400 call", "a weekly: its date isn't the strike"
    assert X._contract("NIFTY26NOVFUT") == "NIFTY26NOVFUT"

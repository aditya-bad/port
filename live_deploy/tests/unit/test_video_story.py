"""Videos (app/video/story.py, speech.py, voice.py): a replay told as
scenes and spoken steps. Every reason and number said is the strategy's
own record; a running cycle is told up to now; captions are said as
written, with ₹, times and decimals read the way a person would."""

import json
import os
from datetime import datetime

import pytest

from app.video import speech, story as V, voice
from tests.unit.video_sample import DECISIONS, DEP, IST, NOW, sample


def build(live=True, **kw):
    return V.build(sample(live=live), dep=kw.pop("dep", DEP), strategy_label="Monthly strangle",
                   decisions=kw.pop("decisions", DECISIONS), now=NOW, **kw)


def texts(s, kind=None, title=None):
    return [st["text"] for sc in s["scenes"] if (kind is None or sc["kind"] == kind) and (title is None or sc["title"] == title)
            for st in sc["steps"]]


def test_the_scenes_in_order_with_a_scene_for_each_trade():
    s = build()
    assert [sc["kind"] for sc in s["scenes"]] == ["open", "market", "trade", "trade", "trade", "trade",
                                                 "checks", "journey", "split", "now", "wrap"]
    assert [sc["title"] for sc in s["scenes"] if sc["kind"] == "trade"] == [
        "10:00 · New cycle", "13:40 · Roll", "15:13 · End-of-day check", "11:20 · Checkpoint hit"]
    assert s["title"] == "Nifty Strangle A · Cycle 7 to Cycle 8" and s["live"] is True
    for sc in s["scenes"]:
        for st in sc["steps"]:
            assert st["text"] and st["say"] and st["cue"] and 1.4 <= st["span"] <= 9


def test_each_trade_says_why_with_the_numbers_the_strategy_saw():
    s = build()
    entry = " ".join(texts(s, title="10:00 · New cycle"))
    assert "It looks for a call and a put each worth about ₹120, on the contract 30 days from expiry." in entry
    assert "It sells the 25,300 call at ₹121, 360 points above the market." in entry
    assert "With 75 units a side, it collects about ₹18,038 up front, the most it can make." in entry
    assert "While Nifty stays between 24,500 and 25,300, time is on its side." in entry
    roll = " ".join(texts(s, title="13:40 · Roll"))
    assert "The call side is now worth ₹127, and the put side has shrunk to ₹61." in roll
    assert "₹61 is just 48% of ₹127, so the rule fires." in roll
    assert "First it buys back the 24,500 put at ₹61. It sold that for ₹119.50 earlier, so that books ₹4,388." in roll
    assert "Now the sides are ₹127 and about ₹118, back in balance." in roll
    gap = " ".join(texts(s, title="15:13 · End-of-day check"))
    assert "It won't carry a gap wider than 80% overnight, and ₹105 is only 70% of ₹150, so it acts." in gap
    cp = " ".join(texts(s, title="11:20 · Checkpoint hit"))
    assert "the cycle's profit has reached ₹9,412: ₹5,588 booked and ₹3,825 still open." in cp
    assert "Its checkpoint is ₹5,000, 0.5% of the ₹10,00,000 capital." in cp
    assert "Closing those legs books plus ₹3,825." in cp
    assert "And it starts the next cycle straight away. It sells the 25,400 call at ₹118" in cp


def test_the_strangles_rules_from_its_own_settings():
    assert "rules" not in [sc["kind"] for sc in build()["scenes"]], "only when asked for"
    s = build(explain_rules=True)
    assert [sc["kind"] for sc in s["scenes"]][:3] == ["open", "rules", "market"]
    cards = texts(s, "rules")
    assert "if one falls below 50% of the other, it rolls the cheaper one closer to the market." in cards[1]
    assert cards[2] == "Every day at 15:13 it checks again, and it won't carry a gap wider than 80% overnight."
    other = V.build(sample(), dep={**DEP, "config": {**DEP["config"], "adjustment_trigger_ratio": 0.4, "eod_check_time": "15:05"}},
                    strategy_label="Monthly strangle", now=NOW, explain_rules=True)
    said = texts(other, "rules")
    assert "below 40% of the other" in said[1] and said[2].startswith("Every day at 15:05")


def test_a_running_cycle_is_told_up_to_now_with_where_it_stands():
    s = build()
    assert "It started Tuesday the 27th at 10:00 and it's still running: this is where it stands at 15:25 today." in texts(s, "open")[1]
    now = texts(s, "now")
    assert now[0].startswith("So where does it stand right now, at 15:25? The 25,400 call is at")
    assert "The roll comes below 50%" in now[1]
    assert texts(s, "wrap")[0].endswith("It's still running, so make this video again later to see how the rest of it goes.")


def test_a_finished_one_has_no_now_and_says_when_it_ran():
    s = build(live=False)
    assert "now" not in [sc["kind"] for sc in s["scenes"]]
    assert "from Tuesday the 27th at 10:00 to Thursday the 29th at 15:25." in texts(s, "open")[1]
    assert texts(s, "wrap")[0].endswith("Thanks for watching.")
    assert texts(s, "open")[0] == "Four trades, while Nifty swung 197 points, and it finished up ₹11,438. Here's how.", "no deep dip: the plain hook"


def test_the_checks_that_held_it_back_once_each_and_in_words():
    s = build(decisions=DECISIONS + [{**DECISIONS[0], "t": DECISIONS[0]["t"].replace(minute=31)}])
    said = texts(s, "checks")
    assert said[1] == "On Tuesday the 27th, at 09:30, it held off: the 24,500 put hasn't traded yet today: management held until it does."
    assert said[2] == "At 15:13, it checked and did nothing: put to call 0.92 at or above floor 0.80: gap small enough to hold overnight."
    assert len(said) == 3


def test_where_the_money_came_from_adds_up_to_the_total():
    s = build()
    d = next(sc["data"] for sc in s["scenes"] if sc["kind"] == "split")
    assert round(d["time"] + d["market"] + d["vol"], 2) == round(d["total"], 2)
    assert texts(s, "split")[-1].startswith(f"Together, plus ₹{round(d['total']):,}.")


def test_another_strategy_is_told_from_its_recorded_rule():
    D = sample(live=False)
    D["events"] = [D["events"][0], {**D["events"][1], "trigger": "leg_stop", "values": {"leg_price": 162.5, "stop": 160},
                                    "kind": "exit", "label": "Exit", "fills": D["events"][1]["fills"][:1]}]
    s = V.build(D, dep={**DEP, "strategy_name": "intraday_dtt_simple", "config": {"entry_time": "09:20", "per_leg_stop_loss_pct": 0.4}},
                strategy_label="DTT straddle", now=NOW, explain_rules=True)
    rules = texts(s, "rules")
    assert rules[0].startswith("Here's how this strategy plays. It sells a call and a put at the same strike")
    assert rules[-1] == "The settings it runs with: entry time 09:20; per-leg stop 40%."
    exit_ = " ".join(texts(s, "trade")[-3:])
    assert "Its leg stop rule acted, seeing leg price 162.5 · stop 160." in exit_


def test_nothing_to_tell_without_prices():
    with pytest.raises(V.NoStory):
        V.build({"frames": [], "events": []}, dep=DEP, strategy_label="S")


def test_the_story_is_plain_json():
    s = build()
    assert json.loads(json.dumps(s)) == s
    assert len(s["series"]["t"]) <= V.MAX_POINTS and len(s["series"]["idx"]) == len(s["series"]["t"])


@pytest.mark.parametrize("text,said", [
    ("That booked ₹4,388.", "That booked 4,388 rupees."),
    ("It sold it for ₹119.50 earlier.", "It sold it for 119 rupees 50 paise earlier."),
    ("its ₹10,00,000 capital", "its 10 lakh rupees capital"),
    ("₹1,23,456 in all", "1 lakh 23 thousand 456 rupees in all"),
    ("At 11:42 India VIX was 13.2.", "At 11 42 India Vix was 13 point 2."),
    ("At 10:00 and 09:05.", "At 10 o'clock and 9 oh 5."),
    ("minus ₹950, 48% of it", "minus 950 rupees, 48 percent of it"),
    ("NIFTY and BANKNIFTY", "Nifty and Bank Nifty"),
])
def test_captions_said_the_way_a_person_would(text, said):
    assert speech.spoken(text) == said


def test_long_sentences_go_to_the_voice_in_pieces_at_its_pauses():
    s = "Here's the rule. If one side falls below 50% of the other, the strangle is lopsided: the market is leaning on the call, and the put is too far away to earn much."
    p = voice.parts(s)
    assert " ".join(p) == s and len(p) == 3 and all(len(x.split()) <= voice.MAX_WORDS for x in p)
    assert voice.parts("It sells the 25,300 call at ₹121.") == ["It sells the 25,300 call at ₹121."]


def test_the_voice_writes_a_clip_a_step_with_its_captions_timed(tmp_path, monkeypatch):
    monkeypatch.setenv("LINCK_VIDEO_FAKE_VOICE", "1")
    s = build()
    (tmp_path / "story.json").write_text(json.dumps(s))
    narr = voice.speak(tmp_path)
    assert [len(r) for r in narr] == [len(sc["steps"]) for sc in s["scenes"]]
    st = narr[0][0]
    assert [c["text"] for c in st["caps"]] == voice.sentences(s["scenes"][0]["steps"][0]["text"])
    assert st["caps"][0]["t"] == 0 and st["caps"][1]["t"] > 1
    clip = voice.read_wav(tmp_path / "narr" / "01-01.wav")
    assert abs(len(clip) / voice.SR - st["dur"]) < 0.01
    assert json.loads((tmp_path / "narr.json").read_text()) == narr


def test_it_opens_on_the_most_telling_fact():
    s = build()
    assert texts(s, "open")[0] == ("At one point this cycle was down ₹916, on Tuesday the 27th at 13:00. "
                                   "And it's now up ₹6,877. Here's how it climbed back.")
    assert [st["cue"] for st in s["scenes"][0]["steps"]] == ["hook", "title", "count"]
    assert texts(s, "open")[2] == "₹9,412 is booked, and the positions still open are down ₹2,536, over four trades. Let's go through them."
    from app.video.story import _hook, When
    S = {"pnl": [0.0, 500.0, 900.0], "idx": [100.0, 120.0, 110.0], "t": [NOW.isoformat()] * 3}
    w = When(NOW, False)
    best = [{"label": "Roll", "t": NOW.isoformat(), "booked": 600.0}]
    assert _hook(S, best, 900.0, False, "Nifty", w, {}, 0, True).startswith(
        "One roll, on Thursday the 29th at 15:25, booked ₹600: 67% of everything this cycle has made.")
    lost = {**S, "pnl": [0.0, -200.0, -900.0]}
    assert _hook(lost, [], -900.0, False, "Nifty", w, {}, 0, True) == "This one lost ₹900. Let's find exactly where it went wrong."


def test_each_trade_picks_up_from_the_one_before():
    s = build()
    assert texts(s, title="15:13 · End-of-day check")[0].startswith("The next afternoon, at 15:13, Nifty had dropped 124 points, to 24,897.")
    assert texts(s, title="13:40 · Roll")[0].startswith("Four hours later")
    cp = texts(s, title="11:20 · Checkpoint hit")
    assert cp[0].startswith("The next morning, at 11:20, Nifty") and cp[1].startswith("By now the cycle's profit has reached ₹9,412")


@pytest.mark.parametrize("a,b,said", [
    ((10, 0, 27), (10, 40, 27), "40 minutes later"),
    ((10, 0, 27), (11, 10, 27), "An hour later"),
    ((10, 0, 27), (13, 40, 27), "Four hours later"),
    ((15, 13, 27), (9, 20, 28), "The next morning, at 09:20"),
    ((15, 13, 23), (11, 0, 26), "On Monday morning, at 11:00"),
    ((10, 0, 21), (14, 0, 26), "Five days later, on Monday afternoon, at 14:00"),
])
def test_how_long_between_trades_as_said(a, b, said):
    at = lambda h, m, d: datetime(2026, 10, d, h, m, tzinfo=IST).isoformat()  # noqa: E731
    assert V.later(at(*a), at(*b)) == said


def test_a_trade_scene_holds_at_its_moment_with_the_numbers_said():
    s = build()
    for sc in (x for x in s["scenes"] if x["kind"] == "trade"):
        d = sc["data"]
        assert d["w"][1] - d["at"] <= max(2, (d["at"] - d["w"][0]) // 8), "no hours after the moment on the chart"
        assert s["series"]["pnl"][d["at"]] == d["pnl"], "the P&L shown at the moment is the one said"
        said = " ".join(st["text"] for st in sc["steps"])
        if d["booked"] is not None:
            assert f"₹{round(abs(d['booked'])):,}" in said, sc["title"]
    gap = next(sc for sc in s["scenes"] if sc["title"] == "15:13 · End-of-day check")
    assert gap["data"]["booked"] == 1200.0
    assert s["series"]["pnl"][gap["data"]["at"]] == 893.0 or round(s["series"]["pnl"][gap["data"]["at"]]) == 893


def test_what_is_booked_and_what_is_open_is_said_and_carried():
    """The P&L is never just one number: where a trade's P&L is said, so
    is how much of it is booked and how the open positions stand, and the
    box's booked figure moves by what the trade books (before → after)."""
    s = build()
    gap = next(sc for sc in s["scenes"] if sc["title"] == "15:13 · End-of-day check")
    # the 24,500 put's roll booked ₹4,387.50 earlier; the 25,300 call's close books ₹1,200 here
    assert gap["data"]["booked_before"] == 4387.5 and gap["data"]["booked_after"] == 5587.5
    assert gap["steps"][-1]["text"] == "After the roll, the cycle stands at plus ₹893, with ₹5,588 booked and the open positions down ₹4,694."
    for sc in (x for x in s["scenes"] if x["kind"] == "trade" and x["data"].get("booked_after") is not None):
        d = sc["data"]
        mine = sum(f["pnl"] or 0 for f in d["fills"] if not f["opening"] and f["leg"] is not None)
        assert round(d["booked_after"] - d["booked_before"], 2) == round(mine, 2), sc["title"]
    w = next(sc for sc in s["scenes"] if sc["kind"] == "wrap")["data"]
    assert round(w["booked"] + w["open"], 2) == w["total"] and [x["title"] for x in w["trades"]] == [
        sc["title"] for sc in s["scenes"] if sc["kind"] == "trade"]
    assert ", with ₹" in texts(s, "wrap")[0] and "booked and the open positions" in texts(s, "wrap")[0]


def test_every_open_legs_price_at_every_step():
    """The option prices drawn under the index, and the positions table,
    come from the same steps as the index: each open leg's own price."""
    s, D = build(), sample()
    S = s["series"]
    assert len(S["held"]) == len(S["t"])
    frames = {f["t"]: f for f in D["frames"]}
    for i, t in enumerate(S["t"]):
        assert S["held"][i] == [[h[0], round(h[1], 2)] for h in frames[t]["legs"]]
    roll = next(sc for sc in s["scenes"] if sc["title"] == "13:40 · Roll")["data"]
    closed = [f for f in roll["fills"] if not f["opening"]]
    opened = [f for f in roll["fills"] if f["opening"]]
    assert [s["legs"][f["leg"]]["what"] for f in closed] == ["24,500 put"] and closed[0]["entry"] == 119.5
    assert [s["legs"][f["leg"]]["what"] for f in opened] == ["24,800 put"] and opened[0]["sold"]


def test_it_ends_on_what_made_the_result():
    assert "What made it? Mostly time passing: ₹5,089 of it." in texts(build(), "wrap")[0]

"""The weekly review's arithmetic (app/v2/review.py), on trades we choose."""

from datetime import date

from app.v2 import review as R

WEEK = date(2026, 10, 12)   # a Monday


def trade(dep, closed, pnl, net=None, opened=None):
    net = pnl if net is None else net
    return {"deployment_id": dep, "opened_at": opened or closed, "closed_at": closed,
            "pnl": pnl, "pnl_net": net, "pnl_net_slip": net - 10}


def daily_of(trades, extra=()):
    """What book.list_daily would say: each trade booked on its closing day."""
    out = [{"deployment_id": t["deployment_id"], "day": t["closed_at"][:10], "pnl": t["pnl"], "pnl_net": t["pnl_net"],
            "pnl_net_slip": t["pnl_net_slip"], "closes": 1} for t in trades]
    return out + list(extra)


def test_monday_and_the_default_week():
    assert R.monday(date(2026, 10, 18)) == WEEK and R.monday(WEEK) == WEEK
    assert R.default_week(date(2026, 10, 14), True) == WEEK, "this week once something closed in it"
    assert R.default_week(date(2026, 10, 12), False) == date(2026, 10, 5), "a Monday morning: last week"


def test_the_week_against_its_usual():
    t = [
        trade("a", "2026-10-13T11:00:00+05:30", 3000, 2900), trade("a", "2026-10-14T11:00:00+05:30", -1000, -1100),
        trade("b", "2026-10-16T14:00:00+05:30", 500),
        # the weeks before: a made 1,000 and 2,000; b only started last week
        trade("a", "2026-09-29T11:00:00+05:30", 1000, opened="2026-09-29T10:00:00+05:30"),
        trade("a", "2026-10-06T11:00:00+05:30", 2000), trade("b", "2026-10-07T11:00:00+05:30", -400, opened="2026-10-07T10:00:00+05:30"),
        trade("a", "2026-10-20T11:00:00+05:30", 9999),    # next week: not this one
    ]
    s = R.summarize(t, daily_of(t), WEEK)
    assert s["totals"] == {"pnl": 2500, "pnl_net": 2300, "pnl_net_slip": 2270, "trades": 3, "wins": 2}
    a = s["deployments"]["a"]
    assert (a["pnl"], a["trades"], a["wins"], a["weeks_known"]) == (2000, 2, 1, 2)
    assert a["usual"]["pnl"] == 1500.0 and a["usual"]["trades"] == 1.0, "the average of the weeks it existed"
    assert a["best"]["pnl"] == 3000 and a["worst"]["pnl"] == -1000
    b = s["deployments"]["b"]
    assert b["weeks_known"] == 1 and b["usual"]["pnl"] == -400.0
    assert [h["week"] for h in s["history"]][-2:] == ["2026-09-28", "2026-10-05"] and len(s["history"]) == 8
    assert s["usual"]["pnl"] == round((1000 + 1600) / 2, 2), "weeks with trades only"
    assert [x["pnl"] for x in s["best"]] == [3000, 500] and [x["pnl"] for x in s["worst"]] == [-1000]


def test_a_quiet_week():
    t = [trade("a", "2026-10-06T11:00:00+05:30", 2000)]
    s = R.summarize(t, daily_of(t), WEEK)
    assert s["totals"]["trades"] == 0 and s["best"] == [] and s["worst"] == []
    assert s["deployments"]["a"]["trades"] == 0 and s["deployments"]["a"]["usual"]["pnl"] == 2000.0


def test_a_leg_booked_inside_a_trade_still_open_counts_in_the_weeks_pnl():
    # a strangle rolled a leg on Monday for +5,489: booked, though the trade hasn't finished
    t = [trade("s", "2026-10-13T11:00:00+05:30", -17660)]
    roll = {"deployment_id": "x", "day": "2026-10-12", "pnl": 5489, "pnl_net": 5400, "pnl_net_slip": 5390, "closes": 1}
    s = R.summarize(t, daily_of(t, [roll]), WEEK)
    assert s["totals"]["pnl"] == -12171 and s["totals"]["trades"] == 1, "what the day bars add up to"
    assert s["deployments"]["x"]["pnl"] == 5489 and s["deployments"]["x"]["trades"] == 0


def test_rules_that_fired_more_than_usual():
    prev = [date(2026, 10, 5), date(2026, 9, 28), date(2026, 9, 21), date(2026, 9, 14)]
    counts = [{"deployment_id": "a", "trigger": "leg_stop", "week": w, "n": 1} for w in prev]
    counts += [{"deployment_id": "a", "trigger": "entry", "week": w, "n": 5} for w in prev]
    counts += [
        {"deployment_id": "a", "trigger": "leg_stop", "week": WEEK, "n": 4},       # usual 1/week: 4×
        {"deployment_id": "a", "trigger": "entry", "week": WEEK, "n": 6},          # usual 5: normal
        {"deployment_id": "a", "trigger": "kill_switch", "week": WEEK, "n": 3},    # never before
        {"deployment_id": "a", "trigger": "roll", "week": WEEK, "n": 2},           # too few to say
        {"deployment_id": "new", "trigger": "entry", "week": WEEK, "n": 9},        # no history: nothing to compare
    ]
    got = R.unusual_rules(counts, WEEK)
    assert [(x["trigger"], x["n"], x["usual"]) for x in got] == [("kill_switch", 3, 0), ("leg_stop", 4, 1.0)]


def test_the_saturday_push():
    r = {"week": "2026-10-12", "week_end": "2026-10-16", "totals": {"pnl": 2500, "pnl_net": 2300, "pnl_net_slip": 2270, "trades": 3, "wins": 2},
         "deployments": [{"name": "BankNifty DTT", "pnl_net": 1900}, {"name": "Strangle", "pnl_net": 400}], "health": [{}]}
    assert R.push_text(r) == ("Your week, 12–16 Oct", "+₹2,300 after charges · 3 trades · 67% won. Best: BankNifty DTT +₹1,900. 1 health alert to look at.")
    r2 = {**r, "week": "2026-09-28", "week_end": "2026-10-02", "totals": {**r["totals"], "trades": 0}, "health": []}
    assert R.push_text(r2) == ("Your week, 28 Sep–2 Oct", "No trades closed this week.")

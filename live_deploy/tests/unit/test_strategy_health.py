"""Strategy health (app/strategy_health.assess): each rule fires on a
deployment drifting from how it normally trades, and stays quiet on a
young deployment, on noise, and on a steady one."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app import strategy_health as H

IST = ZoneInfo("Asia/Kolkata")
TODAY = date(2026, 10, 21)   # a Wednesday


def series(pnls, slip=0.0, end=TODAY, every=1):
    """Trades one market day apart, the last closing on `end`."""
    out, d = [], end
    for p in reversed(pnls):
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        t = datetime(d.year, d.month, d.day, 10, 0, tzinfo=IST)
        out.append({"opened": t, "closed": t + timedelta(hours=2), "pnl": float(p), "slippage": slip})
        d -= timedelta(days=every)
    return out[::-1]


def rules(found):
    return sorted(f["rule"] for f in found)


STEADY = [1000, -500, 1000, 1000, -500] * 6   # 60% winners, losses of 500


def run(trades, **kw):
    return H.assess(trades, capital=kw.pop("capital", 500000), active=kw.pop("active", True), today=kw.pop("today", TODAY))


def test_a_steady_deployment_and_a_young_one_are_left_alone():
    assert run(series(STEADY)) == []
    assert run(series([-900] * 29)) == [], "under 30 trades: too few to judge"


def test_winning_less_often():
    found = run(series(STEADY[:20] + [1000, -500, -500, -500, -500, -500, -500, -500, 1000, -500]))
    f = next(x for x in found if x["rule"] == "win_rate")
    assert f["data"] == {"recent": 20.0, "usual": 60.0}
    assert f["body"] == "Won 20% of the last 10 trades against 60% before."
    # a dip of a couple of trades isn't drift
    assert "win_rate" not in rules(run(series(STEADY[:20] + [1000, -500, -500, 1000, -500, 1000, 1000, -500, 1000, -500])))


def test_losing_more_per_loss():
    found = run(series(STEADY[:20] + [1000, -900, 1000, -800, 1000, 1000, -800, 1000, 1000, 1000]))
    f = next(x for x in found if x["rule"] == "loss_size")
    assert f["data"] == {"recent": -833.33, "usual": -500.0}
    assert "1.7×" in f["body"]
    assert "loss_size" not in rules(run(series(STEADY[:20] + [1000, -600, 1000, -600, 1000, 1000, -600, 1000, 1000, 1000])))


def test_the_worst_trade_yet_is_flagged_for_three_days():
    t = series(STEADY + [-2000])
    f = next(x for x in run(t) if x["rule"] == "worst_loss")
    assert f["data"] == {"loss": -2000.0, "previous": -500.0}
    assert "worst_loss" not in rules(run(t, today=TODAY + timedelta(days=4)))


def test_paying_more_in_slippage():
    t = series(STEADY + STEADY[:5])
    for i, x in enumerate(t):
        x["slippage"] = 40.0 if i < len(t) - 10 else 120.0
    f = next(x for x in run(t) if x["rule"] == "slippage")
    assert f["data"] == {"recent": 120.0, "usual": 40.0}
    for x in t[-10:]:
        x["slippage"] = 70.0       # under twice, and up by only ₹30
    assert "slippage" not in rules(run(t))


def test_deepest_drawdown_yet():
    found = run(series(STEADY[:20] + [-3000, -2000, -1500, 500, -2000, 400, -1000, 300, -800, 200]), capital=500000)
    f = next(x for x in found if x["rule"] == "drawdown")
    assert f["data"]["worst_before"] == 500.0 and f["data"]["now"] >= 8000
    # the same drawdown on a big account is under 1% of capital: not flagged
    assert "drawdown" not in rules(run(series(STEADY[:20] + [-3000, -2000, -1500, 500, -2000, 400, -1000, 300, -800, 200]), capital=5_000_000))


def test_trading_less_than_usual():
    # a trade every market day for 30 days, then nothing for the last two weeks
    t = series(STEADY, end=TODAY - timedelta(days=15))
    f = next(x for x in run(t) if x["rule"] == "fewer_trades")
    assert f["data"]["recent"] == 0 and f["data"]["usual"] >= 9
    assert "fewer_trades" not in rules(run(t, active=False)), "a paused deployment isn't expected to trade"
    assert "fewer_trades" not in rules(run(series(STEADY))), "a trade a day, still trading"
    # a weekly strategy: ~2 trades in 10 days is its normal, never flagged
    assert "fewer_trades" not in rules(run(series(STEADY, every=5, end=TODAY - timedelta(days=15))))


def test_a_finding_keeps_its_key_until_a_new_trade_closes():
    t = series(STEADY[:20] + [1000, -500, -500, -500, -500, -500, -500, -500, 1000, -500])
    a, b = run(t), run(t, today=TODAY + timedelta(days=1))
    assert [x["key"] for x in a if x["rule"] == "win_rate"] == [x["key"] for x in b if x["rule"] == "win_rate"]
    more = t + series([-500], end=TODAY + timedelta(days=1))
    assert [x["key"] for x in run(more, today=TODAY + timedelta(days=1)) if x["rule"] == "win_rate"] != [x["key"] for x in a if x["rule"] == "win_rate"]

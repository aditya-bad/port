"""Every registered strategy has Geek mode's shared views; the UI and the
API agree on which strategies have views of their own
(static/linck/js/data/geek.js vs app/geek/__init__.py)."""

import re
from pathlib import Path

from app.geek import GEEK, common, module_for, supported
from app.strategies.registry import is_registered, list_strategies


def test_ui_and_api_agree_on_which_strategies_have_geek_mode():
    js = (Path(__file__).resolve().parents[2] / "static/linck/js/data/geek.js").read_text()
    ui = set(re.findall(r"'([a-z0-9_]+)'", re.search(r"new Set\(\[(.*?)\]\)", js).group(1)))
    assert ui == set(GEEK)
    assert all(is_registered(name) for name in GEEK)


# The API views each strategy's own pages call (static/linck/js/pages/geek/views.js).
CALLS = {
    "strangle_monthly_v2": {"cycles", "cycle", "adjustments", "entries", "premium"},
    "pivot_supertrend_options": {"signals", "trade"},
    "intraday_dtt_simple": {"straddles", "straddle"},
    "intraday_dtt_adjusted": {"sessions", "session", "moves"},
    "intraday_dtt_advanced": {"sessions", "session", "moves"},
}


def test_every_geek_module_has_the_views_the_ui_calls():
    assert set(CALLS) == set(GEEK)
    for name, mod in GEEK.items():
        assert {"engine", "decisions", "settings"} | CALLS[name] <= set(mod.VIEWS), name


def test_every_strategy_has_the_shared_views():
    for s in list_strategies():
        mod = module_for(s["name"])
        assert mod is not None and supported(s["name"]), s["name"]
        assert {"engine", "decisions", "settings"} <= set(mod.VIEWS), s["name"]
        if s["name"] not in GEEK:
            assert mod is common
    assert module_for("no_such_strategy") is None and not supported("no_such_strategy")


def test_shared_actions_read_every_strategys_open_and_close():
    from datetime import datetime, timedelta

    from app.geek.common import actions
    t = datetime(2026, 10, 19, 9, 20)

    def lot(i, action, side, meta_action, trigger, mins=0):
        return {"executed_at": t + timedelta(minutes=mins), "trigger": trigger, "action": action, "position_side": side,
                "symbol": f"S{i}", "qty": 75, "price": 100, "pnl": None, "metadata": {"action": meta_action},
                "opening": None}

    from app.geek.common import opening
    rows = [lot(1, "sell", "short", "sell_open_PE", "pivot_break_long"),
            lot(1, "buy", "short", "buy_close_PE", "st_flip", 40),
            lot(2, "sell", "short", "sell_open:CE", "entry", 50), lot(3, "sell", "short", "open", "entry", 50),
            lot(2, "buy", "short", "close", "roll", 90), lot(4, "sell", "short", "open", "roll", 91)]
    for r in rows:
        r["opening"] = opening(r["metadata"]["action"], r["position_side"], r["action"])
    acts = actions(rows)
    assert [(a["trigger"], a["kind"], len(a["fills"])) for a in acts] == [
        ("pivot_break_long", "entry", 1), ("st_flip", "exit", 1), ("entry", "entry", 2), ("roll", "switch", 2)]


def test_opening_falls_back_to_the_side_when_the_metadata_doesnt_say():
    from app.geek.common import opening
    assert opening(None, "short", "sell") and not opening(None, "short", "buy")
    assert opening("", "long", "buy") and not opening("weird", "long", "sell")
    assert opening("sell_open_PE", "short", "sell") and not opening("buy_close_CE", "short", "buy")
    assert not opening("close", "long", "sell") and opening("open", "long", "buy")

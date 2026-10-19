"""The UI offers Geek mode for exactly the strategies the API has a Geek
module for (static/linck/js/data/geek.js vs app/geek/__init__.py)."""

import re
from pathlib import Path

from app.geek import GEEK
from app.strategies.registry import is_registered


def test_ui_and_api_agree_on_which_strategies_have_geek_mode():
    js = (Path(__file__).resolve().parents[2] / "static/linck/js/data/geek.js").read_text()
    ui = set(re.findall(r"'([a-z0-9_]+)'", re.search(r"new Set\(\[(.*?)\]\)", js).group(1)))
    assert ui == set(GEEK)
    assert all(is_registered(name) for name in GEEK)


def test_every_geek_module_has_the_views_the_ui_calls():
    for mod in GEEK.values():
        assert {"engine", "decisions", "cycles", "adjustments", "entries", "premium", "settings"} <= set(mod.VIEWS)

"""
live_deploy — Geek mode: strategy-specific analytics for one deployment.

Each supported strategy has a module here exposing async view builders
(`VIEWS`: name -> builder(ctx) -> dict). The API (app/routers/geek.py)
looks the strategy up in GEEK and returns 404 for anything else, so the
UI only offers Geek mode where a module exists.
"""

from . import strangle_v2

GEEK = {"strangle_monthly_v2": strangle_v2}


def supported(strategy_name: str) -> bool:
    return strategy_name in GEEK

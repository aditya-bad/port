"""
live_deploy — Geek mode: analytics for one deployment.

Every strategy gets the shared views in `common` (engine, decisions,
settings, on top of the normal page's own tabs). A strategy with views
of its own has a module here exposing async view builders (`VIEWS`:
name -> builder(ctx) -> dict); the API (app/routers/geek.py) uses that
module for it and `common` for the rest.
"""

from . import common, dtt_straddle, strangle_v2, supertrend_options

# Strategies with views of their own. static/linck/js/data/geek.js lists
# the same names, to offer their extra views.
GEEK = {"strangle_monthly_v2": strangle_v2, "pivot_supertrend_options": supertrend_options,
        "intraday_dtt_simple": dtt_straddle}


def module_for(strategy_name: str):
    from ..strategies.registry import is_registered
    if strategy_name in GEEK:
        return GEEK[strategy_name]
    return common if is_registered(strategy_name) else None


def supported(strategy_name: str) -> bool:
    return module_for(strategy_name) is not None

"""
Live strategy modules.

Each strategy implements `StrategyBase` (app.deployments.strategy_base)
and registers itself via `@register_strategy(...)` from
`app.strategies.registry` — see that module's docstring for the exact
pattern.

IMPORTANT: a strategy file being registered requires it to actually be
imported somewhere, or the decorator never runs — that's what the import
list below does.

DeploymentManager looks up strategy_name in the registry when starting a
deployment's runner. An unregistered name is ALLOWED (not rejected) at
POST /deployments time — you can set up a deployment's name/capital/
tokens/config before the matching strategy code exists — but every API
response flags it via `strategy_registered: false` so this is never
silently misleading.
"""

from . import pivot_supertrend  # noqa: F401  (registers on import)
from . import pivot_supertrend_options  # noqa: F401  (registers on import)
from . import intraday_dtt_simple  # noqa: F401  (registers on import)
from . import pivot_supertrend_options_inverse  # noqa: F401  (registers on import)
from . import intraday_dtt_adjusted  # noqa: F401  (registers on import)

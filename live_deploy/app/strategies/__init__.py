"""
Live strategy modules — not implemented yet.

When a strategy is added here, it implements `StrategyBase`
(app.deployments.strategy_base) and registers itself via
`@register_strategy(...)` from `app.strategies.registry` — see that
module's docstring for the exact pattern.

IMPORTANT: a strategy file being registered requires it to actually be
imported somewhere, or the decorator never runs. Add an import line
below for each strategy module as it's added, e.g.:

    from . import pivot_supertrend  # noqa: F401  (registers on import)

DeploymentManager looks up strategy_name in the registry when starting
a deployment's runner — an unregistered name is rejected at
POST /deployments time (400), not silently accepted as a no-op.

Nothing is registered yet — "once infra is ready, I'll tell you the
strategies."
"""

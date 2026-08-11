"""
live_deploy — strategy registry.

Lets a strategy module announce itself so it shows up in the UI's
"available strategies" list and can be deployed by name, without
DeploymentManager needing to know about any specific strategy class.

Usage, once a real strategy exists:

    from ..deployments.strategy_base import StrategyBase
    from .registry import register_strategy

    @register_strategy(
        "pivot_supertrend",
        description="Pivot points + SuperTrend(7,3) intraday",
        default_config={"instrument_tokens": [256265], "pivot_type": "classic"},
    )
    class PivotSupertrendStrategy(StrategyBase):
        async def on_start(self, runner): ...
        async def on_tick(self, runner, tick): ...
        async def on_stop(self, runner): ...

...and the module doing this needs to actually be imported somewhere
(see the import list at the bottom of app/strategies/__init__.py) for
the decorator to run and the registration to take effect — a strategy
file that's never imported never registers itself, same as any Python
decorator-registration pattern.

Empty until the first real strategy is added.
"""

from typing import Optional, Type

_REGISTRY: dict[str, dict] = {}


def register_strategy(
    name: str, description: str = "", default_config: Optional[dict] = None,
):
    """Class decorator — see module docstring for usage."""
    def _decorator(cls: Type):
        if name in _REGISTRY:
            raise ValueError(f"Strategy {name!r} is already registered")
        _REGISTRY[name] = {
            "name": name,
            "description": description,
            "default_config": default_config or {},
            "cls": cls,
        }
        return cls
    return _decorator


def list_strategies() -> list[dict]:
    """JSON-safe listing for the UI/API — no class objects in here."""
    return [
        {
            "name": v["name"],
            "description": v["description"],
            "default_config": v["default_config"],
        }
        for v in _REGISTRY.values()
    ]


def get_strategy_class(name: str) -> Optional[Type]:
    entry = _REGISTRY.get(name)
    return entry["cls"] if entry else None


def is_registered(name: str) -> bool:
    return name in _REGISTRY

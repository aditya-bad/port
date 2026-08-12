"""
live_deploy — the contract future strategies implement.

Nothing implements this yet — "once infra is ready, I'll tell you the
strategies." This exists now so the rest of the infra (DeploymentRunner,
persistence, lifecycle) has a stable, concrete interface to build
against, rather than everything downstream being written against an
assumption of what a strategy will look like.
"""

from abc import ABC, abstractmethod
from typing import Any


class StrategyBase(ABC):
    """
    One instance of a StrategyBase subclass is attached to exactly one
    DeploymentRunner (one deployment = one strategy instance + one config
    + its own isolated positions/cash — never shared across deployments,
    even for two deployments of the "same" strategy).

    Lifecycle:
      on_start(runner)         — called once, after the runner has loaded
                                  this deployment's existing open
                                  positions from the DB (on fresh create,
                                  there are none; on resume/restart,
                                  there may be).
      on_tick(runner, tick)    — called for every tick whose
                                  instrument_token is in this deployment's
                                  config["instrument_tokens"].
      on_stop(runner)          — called once, before the runner
                                  unsubscribes from the tick broadcaster
                                  (deployment paused or stopped).

    Strategies never touch the DB directly — they call back into the
    runner (`await runner.buy(...)`, `await runner.sell(...)`,
    `runner.open_positions`, `runner.cash`,
    `await runner.list_closed_positions()`, ...), and the runner is what
    actually persists everything.
    """

    @abstractmethod
    async def on_start(self, runner: "Any") -> None: ...

    @abstractmethod
    async def on_tick(self, runner: "Any", tick: dict) -> None: ...

    @abstractmethod
    async def on_stop(self, runner: "Any") -> None: ...

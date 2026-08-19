"""
live_deploy — the contract future strategies implement.

Nothing implements this yet — "once infra is ready, I'll tell you the
strategies." This exists now so the rest of the infra (DeploymentRunner,
persistence, lifecycle) has a stable, concrete interface to build
against, rather than everything downstream being written against an
assumption of what a strategy will look like.
"""

from abc import ABC, abstractmethod
from typing import Any, Optional


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
    `runner.open_positions`, `runner.cash`, `runner.initial_capital`,
    `await runner.list_closed_positions()`, ...), and the runner is what
    actually persists everything.
    """

    @abstractmethod
    async def on_start(self, runner: "Any") -> None: ...

    @abstractmethod
    async def on_tick(self, runner: "Any", tick: dict) -> None: ...

    @abstractmethod
    async def on_stop(self, runner: "Any") -> None: ...

    def get_persistable_state(self) -> Optional[dict]:
        """
        Override to return a JSON-serializable dict of whatever
        live-learned internal state this strategy wants to survive a
        restart — e.g. an indicator's internals (SuperTrend trend/ATR/
        bands, computed pivots) that live only in this Python instance's
        memory and nothing else already captures. Most strategies have
        no state beyond their open positions, which are already
        resume-safe via the DB (see runner.open_positions on_start
        reconstruction) — those strategies simply never override this,
        and get the default: None, meaning "nothing to persist."

        Called opportunistically by DeploymentRunner.stop() — i.e. on
        pause, on stop, AND on a graceful full-server shutdown (which
        stops every runner the same way, see DeploymentManager.
        shutdown_all) — never on a tight per-tick loop, so this only
        needs to be correct when called, not cheap on every tick. ALSO
        called once a day, without stopping anything, by
        DeploymentManager.post_market_dump_loop() — a standing checkpoint
        shortly after market close so a same-day state loss (a redeploy
        that skips the graceful-shutdown window, an ungraceful kill)
        never costs more than "one step stale from this afternoon"
        instead of a full cold-start reseed. NOT called on an ungraceful
        kill (SIGKILL, OOM, crash) itself — those skip the shutdown path
        entirely, so a strategy using this should still tolerate its
        state being one step stale in that case, exactly as if it had
        just cold-started.

        Read back via `await runner.load_state()` — conventionally at
        the top of on_start(), before applying any config-provided seed,
        so a real restart resumes from where it left off instead of
        reverting to a static seed value that may be long stale by then.
        """
        return None

    async def on_post_market_checkpoint(self, runner: "Any") -> None:
        """
        Optional: called once a day by DeploymentManager.
        post_market_dump_loop(), BEFORE it calls dump_state() to persist
        whatever get_persistable_state() returns — a chance to actively
        REFRESH live in-memory state from an authoritative outside source
        (typically a REST fetch) rather than just persisting whatever
        happens to already be sitting in memory. Default no-op: most
        strategies have nothing that needs this (their positions/cash
        are already resume-safe via the DB, with no separately-computed
        indicator state that could have drifted).

        The strategies in this codebase that DO override this
        (pivot_supertrend / pivot_supertrend_options[_inverse]) use it to
        re-fetch gap-free candles from Kite's REST API and recompute
        SuperTrend fresh — correcting for a WebSocket tick gap's effect
        on a RECURSIVE indicator (each candle's state depends on the
        previous candle's, so one silently-missed candle, e.g. from a
        reconnect, permanently drifts every value after it). Mutating
        `self` here fixes the LIVE, currently-trading strategy instance
        immediately, not just what gets persisted — a deployment that
        stays running straight through this checkpoint self-heals
        without needing a restart at all.

        Exceptions raised here are caught and logged by the caller
        (DeploymentRunner.post_market_checkpoint) — a failure here still
        falls through to persisting whatever's already in memory, same
        as before this hook existed, rather than skipping the checkpoint
        entirely.
        """
        return None

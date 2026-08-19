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

    # Optional (Step 87) class attribute: "day" or "cycle_id" — set this
    # to opt into GET /deployments/{id}/adjustment-histogram, a bucket-
    # by-adjustment-count breakdown across every trading unit this
    # strategy has ever run ("N days/cycles had 0 adjustments, N had 1,
    # ..."). Requires positions.metadata to carry a "leg_role" of
    # "original" (the first/entry leg on a side) or "adjustment_<n>"
    # (every later rebalancing leg) — see intraday_dtt_adjusted.py's
    # `_enter`/`_adjust` for the reference shape.
    #   "day"       — group by the IST calendar day each leg opened on
    #                 (opened_at) — the right unit for an intraday
    #                 strategy where one full cycle IS one trading day
    #                 (e.g. intraday_dtt_adjusted).
    #   "cycle_id"  — group by positions.metadata->>'cycle_id' instead —
    #                 for a strategy whose own trading cycle can span
    #                 MANY days (e.g. strangle_monthly_v2's monthly
    #                 checkpoint-to-checkpoint cycles), where "day" would
    #                 be meaningless (a single cycle touches dozens of
    #                 calendar days, most with zero adjustments simply
    #                 because nothing happened that day, not because the
    #                 cycle itself was low-adjustment).
    # None (default) — not supported; the histogram section is omitted
    # entirely on the Detail page rather than shown empty/misleading.
    ADJUSTMENT_GROUP_BY: Optional[str] = None

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

    def get_status_fields(self) -> Optional[list]:
        """
        Optional (Step 87): a small list of live indicator values worth
        surfacing on the Detail page's Stats tab, specific to THIS
        strategy — e.g. SuperTrend's own current trend/value and pivot
        levels, something a straddle strategy has no equivalent of and
        most strategies have nothing at all to add here. Each entry is
        `{"label": str, "value": <JSON-safe>}`; return None (the
        default) if there's nothing beyond the generic P&L/position
        stats every deployment already shows.

        Called directly against the LIVE, currently-running strategy
        instance by GET /deployments/{id}/strategy-status, so read
        whatever attributes this instance already tracks (self.st,
        self.pivots, ...) — no separate bookkeeping needed just for
        display. Keep it cheap and read-only: called on-demand from an
        HTTP request, not gated behind any cache.

        A deployment that ISN'T currently running (paused/stopped) has
        no live instance to call this against — see
        `status_fields_from_state` below for that case instead.
        """
        return None

    @staticmethod
    def status_fields_from_state(state: dict) -> Optional[list]:
        """
        Optional (Step 87): the paused/stopped counterpart to
        get_status_fields() above — same return shape, but computed
        from a PERSISTED `deployment_state` blob (the exact dict
        get_persistable_state() last returned, reloaded via
        queries.load_deployment_state) instead of a live instance,
        since a non-running deployment has no live instance to ask.
        Reasonably fresh in practice: that blob is written at the
        moment a deployment pauses/stops (DeploymentRunner.stop()) and
        at least once daily regardless (DeploymentManager.
        post_market_dump_loop()) — see get_persistable_state's own
        docstring for the exact staleness guarantee.

        A `@staticmethod` (not an instance method) deliberately —
        GET /deployments/{id}/strategy-status calls this against the
        STRATEGY CLASS itself (looked up by name from the registry),
        never having constructed an instance at all for a deployment
        that isn't running. Default: None, same as get_status_fields.
        Must tolerate a malformed/incompatible/missing `state` (a
        future strategy version, a never-warmed-up deployment) by
        returning None rather than raising — the caller has no other
        fallback if this throws.
        """
        return None

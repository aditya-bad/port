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

    # ── when a fill happened ────────────────────────────────────────────
    #
    # A strategy that acts when a candle closes acts at the tick that
    # closed it, not at the candle's start. Stamping a fill with the
    # candle's start put it up to one candle early (an hour, on hourly
    # candles): before the price it was filled at even existed, and on the
    # wrong side of any time a repair or the day check cuts the day at.
    # A strategy sets `_tick_at` to each tick's time in on_tick and stamps
    # its fills with fill_time().
    _tick_at: Optional[Any] = None

    def fill_time(self, fallback: Optional[Any] = None) -> Optional[Any]:
        """The time of the tick being handled (else `fallback`)."""
        return self._tick_at or fallback

    @abstractmethod
    async def on_start(self, runner: "Any") -> None: ...

    @abstractmethod
    async def on_tick(self, runner: "Any", tick: dict) -> None: ...

    @abstractmethod
    async def on_stop(self, runner: "Any") -> None: ...

    # ── an entry that couldn't be made right now ────────────────────────
    #
    # A once-a-day entry (the straddles, the calendar) used to give up the
    # whole day on its first failure — Kite down for a minute at the entry
    # time cost the day, silently. Now: tried again every minute for
    # `entry_retry_minutes` (config, default 30) after the entry time,
    # reported as delayed meanwhile, as skipped if the window passes, and
    # cleared once it goes through.

    ENTRY_RETRY_MINUTES = 30

    def entry_should_wait(self, ts: "Any") -> bool:
        nxt = self.__dict__.get("_entry_next_try")
        return nxt is not None and ts < nxt

    async def entry_failed(self, runner: "Any", ts: "Any", entry_time: "Any", why: str) -> bool:
        """Note a failed entry attempt; True when it's time to give up for
        today (the retry window is over)."""
        from datetime import datetime as _dt, timedelta as _td
        mins = int((getattr(runner, "config", {}) or {}).get("entry_retry_minutes", self.ENTRY_RETRY_MINUTES))
        deadline = _dt.combine(ts.date(), entry_time) + _td(minutes=mins)
        self.__dict__["_entry_next_try"] = ts + _td(minutes=1)
        report = getattr(runner, "report", None)
        key, day = f"entry:{ts.date()}", ts.date()
        if ts < deadline:
            if report is not None:
                await report("entry_delayed", f"The {entry_time:%H:%M} entry hasn't gone through yet",
                             f"{why}. Trying again every minute until {deadline:%H:%M}.", key=key, day=day)
            return False
        self.__dict__["_entry_next_try"] = None
        if report is not None:
            await report("entry_skipped", "Today's entry was skipped",
                         f"{why}. Tried from {entry_time:%H:%M} until {deadline:%H:%M}; next at the next session's entry.",
                         key=key, day=day)
        return True

    async def entry_done(self, runner: "Any", ts: "Any") -> None:
        """The entry went through: a delay reported for it is cleared."""
        self.__dict__["_entry_next_try"] = None
        resolve = getattr(runner, "resolve", None)
        if resolve is not None:
            await resolve(f"entry:{ts.date()}")

    async def opened_today(self, runner: "Any", day: "Any") -> bool:
        """Whether this deployment opened anything on `day`, from the book:
        what "already entered today" rests on when saved state is lost or
        corrupt, so a restart never enters a second time."""
        fn = getattr(runner, "opened_on", None)
        if fn is None:
            return False
        try:
            return bool(await fn(day))
        except Exception:
            return False

    # ── saved state it can't use ────────────────────────────────────────
    #
    # Saved state that's garbage (a bad write, a half-finished migration)
    # used to be dropped silently, and whatever it held — today's trade
    # count, a re-entry gate — reset to its fresh-deploy default: a
    # strategy past its daily cap could trade again. Now it's reported,
    # and what can be rebuilt from the book is.

    async def state_unusable(self, runner: "Any", why: str, fallback: str) -> None:
        report = getattr(runner, "report", None)
        if report is None:
            return
        from datetime import timedelta as _td
        await report("state_lost", "Saved state couldn't be used at start", f"{why}. {fallback}",
                     key="state_lost", ttl=_td(hours=24))

    async def entries_on(self, runner: "Any", day: "Any", side: "Optional[str]" = None) -> int:
        """Positions this deployment opened on `day`, from the book (0 if
        it can't say)."""
        fn = getattr(runner, "positions_opened_on", None)
        if fn is None:
            return 0
        try:
            return int(await fn(day, side))
        except Exception:
            return 0

    async def exit_price(self, runner: "Any", token: int, symbol: str, entry: float) -> tuple:
        """The price to close a leg at when it must close now and its
        price isn't to hand: DeploymentRunner.exit_price's ladder (live,
        quote, today's stored candle, then its entry — reported). (price,
        source)."""
        fn = getattr(runner, "exit_price", None)
        if fn is not None:
            return await fn(token, symbol, entry)
        p = runner.dispatcher.last_prices.get(token)
        return (float(p), "live") if p else (float(entry), "entry")

    def tracked_tokens(self) -> "Optional[set | dict]":
        """The instrument tokens of every leg this strategy believes it
        holds right now (a dict {token: symbol} names them in a report). The runner checks them against the book's open
        positions (DeploymentRunner.check_legs) after a failure and every
        5 minutes, and reports a leg the strategy has lost track of (never
        managed, and positional: never closed) or one it thinks it holds
        that isn't there. None (the default) means it can't say."""
        return None

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

    # ── Geek mode's decision log ────────────────────────────────────────
    #
    # A check that didn't trade, for Geek mode's Decisions view (trades
    # explain themselves through their fills' metadata). Outcomes:
    # "no_action" (the rule looked and nothing was due) or "blocked" (a
    # trade was due but something stopped it: the daily cap, a stale
    # candle, no Kite session, a price that didn't confirm). Logging
    # only: never changes what or when a strategy trades, and a failure
    # to record never reaches the strategy (runner.record_decision
    # swallows it). `once` dedupes a check that would otherwise repeat
    # every candle: the same (rule, outcome, once) is noted at most once
    # per IST day.

    @staticmethod
    def signal_context(candle: dict, st=None) -> dict:
        """The signal candle and, if there is one, the SuperTrend's ATR and
        band width at the moment of an entry: added to the entry's
        trigger_values for Geek mode (did a bigger candle or a calmer
        market pay?). Logging only; never raises."""
        out: dict = {}
        try:
            out["candle"] = {k: round(float(candle[k]), 2) for k in ("open", "high", "low", "close") if candle.get(k) is not None}
            if st is not None:
                if getattr(st, "atr", None) is not None:
                    out["atr"] = round(float(st.atr), 2)
                if getattr(st, "final_upper", None) is not None and getattr(st, "final_lower", None) is not None:
                    out["band_width"] = round(abs(float(st.final_upper) - float(st.final_lower)), 2)
        except Exception:
            pass
        return out

    async def decide(self, runner: "Any", ts: "Any", rule: str, outcome: str, summary: str,
                     data: Optional[dict] = None, once: "Any" = None, fault: bool = False, missed: bool = False) -> None:
        """`fault`: blocked because something is wrong (no Kite, no price,
        a late candle) rather than by design (a cap, a cut-off) — blocked
        for 10 minutes running, it's raised as an issue. `missed`: a trade
        that was due is lost for good (a signal acted on now or never) —
        raised at once, with the day, for the Repair page."""
        record = getattr(runner, "record_decision", None)
        if record is None or ts is None:
            return
        # every check, before the once-a-day dedupe: a rule that stays
        # blocked is raised as an issue (DeploymentRunner.note_decision)
        note = getattr(runner, "note_decision", None)
        if note is not None:
            try:
                await note(ts, rule, outcome, summary, fault=fault, missed=missed)
            except Exception:
                pass
        if once is not None:
            seen = self.__dict__.setdefault("_decided_once", set())
            key = (rule, outcome, once, ts.date())
            if key in seen:
                return
            if len(seen) > 2000:   # a long-running process: forget earlier days
                seen.clear()
            seen.add(key)
        try:
            await record(ts, rule, outcome, summary, data or {})
        except Exception:   # display-only
            pass

"""live_deploy — Pydantic request/response models for the deployments API."""

from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class DeploymentCreate(BaseModel):
    deployment_name: str
    strategy_name: str
    mode: Literal["intraday", "positional"]
    initial_capital: float = Field(gt=0)
    config: dict = Field(default_factory=dict)
    # Optional, set at deploy time -- "why this one" is easiest to write
    # down while you're already looking at the config, not something to
    # reconstruct later from memory via the separate Edit modal (still
    # there, still the only way to change it afterward -- see
    # DeploymentUpdate).
    notes: Optional[str] = None


class DeploymentUpdate(BaseModel):
    """
    PATCH /deployments/{id} — partial update, every field optional so a
    caller can touch just one of them without disturbing the others.

    deployment_name (identity) and notes (free-text metadata, e.g. "why
    this was deployed") are editable regardless of status — a rename or
    a note is never a risky action.

    config is editable too, but ONLY while the deployment is `paused`
    (enforced in the router, not here) — never while `active`, never
    while `stopped`. The reasoning that used to keep config off this
    schema entirely still holds for a RUNNING deployment: the live
    strategy instance holds its own config-derived state in plain
    Python attributes, set once in on_start() — overwriting the DB row
    underneath it would be invisible to that instance until something
    re-reads config from scratch. What makes editing config SAFE while
    paused is that "something" already exists: pause() fully tears the
    runner down (DeploymentManager.pause -> runner.stop(), popped from
    the manager's runners dict) and resume() fully reconstructs it
    (DeploymentManager.resume -> _start_runner -> a brand-new strategy
    instance whose on_start() re-derives everything from the DB row,
    config included) — the EXACT SAME reconstruction path a real
    process restart already relies on for resume-safety. So editing
    config while paused and letting the next resume pick it up isn't a
    new mechanism bolted on top; it's the existing one, entered through
    a second door. A stopped deployment is deliberately excluded even
    though it also has no live runner — resume() itself refuses to ever
    resume a stopped one, so an edit there would never actually take
    effect; allowing it would just be confusing.

    strategy_name/mode/initial_capital are still NOT here, and still
    won't be: initial_capital is the fixed reference value several
    strategies size against and current_cash has already diverged from
    it through real P&L, so there's no clean "reload" semantics the way
    config gets from a pause/resume cycle; mode determines the whole
    runner setup, not something on_start() can pick back up mid-life.
    Stop and redeploy fresh for a genuine strategy/mode/capital change.

    include_in_reports is editable regardless of status too, same as
    deployment_name/notes — it's pure bookkeeping (whether this
    deployment's numbers count toward cross-deployment reports/totals),
    not something a live strategy instance holds any state derived
    from, so there's no equivalent of config's "only safe while paused"
    reasoning here. Bool, not Optional-with-different-default: None
    still means "field omitted, don't touch" (see
    queries.update_deployment_fields's own COALESCE docstring), an
    explicit False is a real, distinct "turn it off."

    tags is the same "editable regardless of status" story, but list-
    valued: a caller sends the FULL desired list (replace semantics,
    not add/remove-one), omitted (None) means "don't touch," an
    explicit [] means "clear every tag." Every name must already exist
    in tag_catalog (Settings -> Tags manages that list) -- the router
    validates this before ever calling update_deployment_fields, since
    a predefined catalog is the whole point (see the 0010 migration's
    own comment on why this isn't freeform text). Does NOT carry
    "Excluded from reports" -- that's include_in_reports above, not a
    member of this list.
    """
    deployment_name: Optional[str] = None
    notes: Optional[str] = None
    config: Optional[dict] = None
    include_in_reports: Optional[bool] = None
    tags: Optional[list[str]] = None
    # Same "editable regardless of status, None means don't touch"
    # story as include_in_reports above -- gates DeploymentRunner.
    # notify_execution's BOTH channels (in-app toast and mobile push)
    # for entry/exit executions on this deployment (see migration
    # 0011's own comment). Read fresh from the DB per execution, not
    # cached on a live runner, so this takes effect immediately.
    notifications_enabled: Optional[bool] = None


class DeploymentOut(BaseModel):
    id: UUID
    deployment_name: str
    strategy_name: str
    mode: str
    status: str
    initial_capital: float
    current_cash: float
    config: dict
    notes: Optional[str] = None
    # Opt-out of cross-deployment reports/totals -- see the 0009
    # migration's own comment and DeploymentUpdate's docstring above.
    # Defaults True so a row read before the column existed on some
    # ancient in-memory object (there isn't one — the DB always has it
    # once migrated — but this mirrors strategy_registered's own
    # defensive default just below) never surprises an old caller.
    include_in_reports: bool = True
    # Gates DeploymentRunner.notify_execution's entry/exit notifications
    # (both in-app toast and mobile push) for this deployment -- see
    # migration 0011's own comment. Same defensive-default reasoning as
    # include_in_reports just above.
    notifications_enabled: bool = True
    # Custom labels applied from the predefined catalog (Settings ->
    # Tags) -- see the 0010 migration's own comment. Never includes the
    # synthetic "Excluded from reports" chip; the frontend derives that
    # one from include_in_reports above, not from this list.
    tags: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    strategy_registered: bool = True   # False = created, but no code will ever run for it (yet)
    # Populated by the deployments router (list/get), NOT by
    # queries.create_deployment itself — a freshly created deployment is
    # correctly 0/0 by these defaults with no extra query needed. See
    # routers/deployments.py's _enrich_pnl / per-deployment pnl lookup.
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    # Entry-price cost basis of every currently OPEN position, netted:
    # +qty*avg_entry_price for a short (a sold option's premium, sitting
    # in cash as a credit until it's bought back), -qty*avg_entry_price
    # for a long (cash already spent to buy it). This is exactly why
    # current_cash isn't simply initial_capital + realized_pnl while
    # anything is still open — it always is, though:
    #   current_cash == initial_capital + realized_pnl + open_cost_basis
    # See routers/deployments.py's _open_cost_basis for the computation.
    open_cost_basis: float = 0.0

    class Config:
        from_attributes = True


class PositionOut(BaseModel):
    id: UUID
    deployment_id: UUID
    symbol: str
    instrument_token: int
    side: str
    status: str
    qty: float                              # currently OPEN qty -- 0 once closed. Rendered as "Open Qty".
    avg_entry_price: float                  # rendered as "Entry Price"
    # Both derived from position_lots (see queries._POSITION_LOT_AGG_JOIN),
    # not columns on `positions` itself -- that table only ever tracks
    # the CURRENT open qty (zeroed on close), not what was originally
    # entered or exited at. Every fill in this codebase is a full close
    # or a same-direction add (no partial exits -- record_fill requires
    # a closing qty to exactly match the open qty), so each position has
    # at most one exit-direction lot: total_qty is the sum of its
    # entry-direction lots' qty (== qty at the moment just before close,
    # for a closed position), exit_price is that single closing lot's
    # price (None while still open).
    total_qty: float = 0.0                  # qty ever entered -- open + already-closed portion, both.
    exit_price: Optional[float] = None      # None while status == "open"
    realized_pnl: float
    opened_at: datetime
    closed_at: Optional[datetime] = None
    current_price: Optional[float] = None
    unrealized_pnl: Optional[float] = None


class PositionOutWithEpisode(PositionOut):
    """PositionOut plus which EPISODE (Step 103) this position belongs
    to — see queries.list_positions_with_episode/_group_into_episodes:
    every position that overlapped in time with another (a straddle's
    CE+PE legs, a leg before and after a roll) shares the same
    `episode_opened_at`/`episode_closed_at` pair, the earliest opened_at
    and latest closed_at (None if any leg in the episode is still open)
    across the whole group. Used ONLY by GET /deployments/{id}/positions
    -- the Detail page's Positions tab ignores the two extra fields, and
    its Stats tab groups by them for the "per position" toggle instead
    of treating each row as its own trade."""
    episode_opened_at: datetime
    episode_closed_at: Optional[datetime] = None


class AggregatePositionOut(PositionOut):
    """PositionOut plus which deployment/strategy it belongs to — used
    ONLY by the cross-deployment /positions endpoint (the Dashboard's
    consolidated table); the per-deployment
    /deployments/{id}/positions endpoint keeps returning plain
    PositionOut, since the deployment is already implied by the URL."""
    deployment_name: str
    strategy_name: str


class LotOut(BaseModel):
    id: UUID
    position_id: UUID
    symbol: str
    action: str
    qty: float
    price: float
    executed_at: datetime
    reason: Optional[str] = None
    # The whole point of the trade-reason logging strategies write
    # (trigger/trigger_values/target_basis/resulting_state, where a
    # given strategy populates them — see strangle_monthly_v2's own
    # Section 12) — previously omitted here, which meant it was written
    # but never actually readable through this API. Older/simpler
    # strategies just put a handful of ad-hoc keys in here (or nothing);
    # the frontend renders whatever's actually present, never assumes
    # this specific strategy's schema.
    metadata: dict = {}


class LotsPage(BaseModel):
    total: int
    offset: int
    lots: list[LotOut]


class RecentTradeOut(BaseModel):
    """Cross-deployment trade row — LotOut plus which deployment/
    strategy it belongs to, for the Dashboard's recent-activity feed."""
    id: UUID
    deployment_id: UUID
    deployment_name: str
    strategy_name: str
    position_id: UUID
    symbol: str
    action: str
    qty: float
    price: float
    executed_at: datetime
    reason: Optional[str] = None
    metadata: dict = {}


class EventOut(BaseModel):
    id: UUID
    event_type: str
    message: Optional[str] = None
    metadata: dict
    created_at: datetime


class ReportOut(BaseModel):
    deployment_id: str
    deployment_name: str
    strategy_name: str
    mode: str
    status: str
    initial_capital: float
    current_cash: float
    closed_positions: int
    open_positions: int
    total_realized_pnl: float
    win_rate_pct: float
    avg_win: float
    avg_loss: float


class SnapshotOut(BaseModel):
    """One point on a deployment's equity curve — see
    queries.record_snapshot/list_snapshots and DeploymentManager's
    periodic snapshot loop for how these rows actually get written. One
    row per IST calendar day (Step 96) — that day's LAST snapshot (its
    post-market value), a single number, deliberately not a range (Step
    99 REMOVED the intraday day_high/day_low/max_profit/max_loss Step
    97 had briefly added — see DeploymentManager._snapshot_one's own
    comment for why: those were computed off `total_value`, which used
    to double-count a still-open short leg's entry premium, making
    "max profit that day" measure premium COLLECTED rather than
    premium actually EARNED; removed rather than re-derived from the
    fixed number, by explicit request for one clean value per day).

    `open_positions_value` is the mark-to-market UNREALIZED P&L sum of
    every position open at snapshot time — but Step 99: this (and its
    contribution to `total_value`) is now ALWAYS 0 for an "intraday"
    deployment, never computed at all, regardless of whether a leg
    happens to be open at snapshot time — an option leg's live premium
    swinging around while still open mid-session isn't realized profit
    yet, just noise, and intentionally never moves this number. Only a
    genuinely "positional" deployment (one designed to carry a position
    past market close) gets a real, non-zero value here. `total_value =
    initial_capital + realized_pnl_cumulative + open_positions_value`
    (NOT `cash + open_positions_value`, which is where the pre-Step-99
    double-count came from — see _snapshot_one's own comment for the
    full derivation) — "what this deployment's account is worth right
    now," the equity-curve Y axis, correctly excluding any not-yet-real
    intraday premium.
    """
    id: UUID
    deployment_id: UUID
    snapshot_at: datetime
    cash: float
    open_positions_value: float
    total_value: float
    realized_pnl_cumulative: float


class PortfolioSnapshotOut(BaseModel):
    """One point on the Portfolio view's combined equity curve — see
    queries.list_portfolio_equity_curve for the bucketing/summing.
    Deliberately its own schema, not SnapshotOut reused: there's no
    single `deployment_id` or `cash`/`open_positions_value` split once
    multiple deployments are summed into one bucket, and
    `deployments_count` (how many deployments actually contributed to
    this bucket) has no per-deployment equivalent at all."""
    bucket_at: datetime
    total_value: float
    realized_pnl_cumulative: float
    deployments_count: int
    metadata: dict = {}


class PnlDigestRow(BaseModel):
    """One day's (or week's) portfolio-wide realized-P&L summary — see
    queries.list_pnl_digest. Deliberately REALIZED only (no
    unrealized/mark-to-market field at all, not even as an optional
    one) — see that function's own docstring for why mixing a live
    number into a digest of settled history would misrepresent it."""
    period_start: datetime
    realized_pnl: float
    positions_closed: int
    wins: int
    losses: int
    fills: int


class PnlDigestRowWithRange(PnlDigestRow):
    """PnlDigestRow plus `max_profit`/`max_loss` (Step 100) — the
    deployment's OWN best/worst mark-to-market standing reached within
    that row's period. Deliberately its own subclass, NOT added to the
    base PnlDigestRow: that model is shared with the PORTFOLIO-wide
    digest (queries.list_pnl_digest), whose own docstring explicitly
    rejects mixing any mark-to-market number into a digest of settled
    history — a rule that's still correct for a live, right-now
    unrealized number, but doesn't actually apply to what's computed
    here (see queries.get_intraday_mtm_range/
    get_positional_episode_mtm_rows' own docstrings): once a period is
    over, ITS OWN best/worst point during that period is a fixed
    historical fact, not something that keeps changing the way "today's
    live unrealized P&L" does. Scoped to this subclass, used ONLY by
    GET /deployments/{id}/pnl-digest (a single deployment's own trend
    table), never by the portfolio-wide one.

    `is_position_row`/`period_start`/`period_end` (Step 101, redefined
    in Step 102) mean different things depending on the deployment's
    `mode` — see queries.list_pnl_digest_for_deployment's own docstring:
    for "intraday" mode (`is_position_row=False`) `period_start` is a
    calendar bucket boundary (day/week/month) and `period_end` is
    always None (unused); for "positional" mode (`is_position_row=True`)
    each row is one EPISODE — every position/leg/adjustment/roll that
    was ever open at the same time as another, combined into one — not
    a calendar bucket and not a single `positions` table row either
    (Step 101's first, since-corrected attempt). `period_start` is the
    episode's earliest constituent leg's `opened_at`, and `period_end`
    is its latest leg's `closed_at` (None if any leg in the episode is
    still open — which is ALSO what an intraday row's `period_end`
    looks like, hence `is_position_row` existing at all: `period_end`
    alone can't tell the two apart, since None means something
    different in each — always ignored/unused vs. genuinely still
    open).

    max_profit/max_loss optional, defaulting to None (not 0.0) — None
    means "not computed for this row" (e.g. an episode that opened
    moments ago, before the next snapshot_loop tick), rendered as "—"
    rather than a misleading zero.
    """
    is_position_row: bool = False
    period_end: Optional[datetime] = None
    max_profit: Optional[float] = None
    max_loss: Optional[float] = None


class PnlStrategyBreakdown(BaseModel):
    """One strategy's realized P&L within a single Reports-page period
    — see queries.pnl_by_strategy_for_range."""
    strategy_name: str
    realized_pnl: float
    positions_closed: int


class StatusField(BaseModel):
    """One live indicator value on GET /deployments/{id}/strategy-status
    — see StrategyBase.get_status_fields's own docstring (Step 87)."""
    label: str
    value: Any


class StrategyStatusOut(BaseModel):
    """GET /deployments/{id}/strategy-status — `source` tells the UI
    which of get_status_fields() (this deployment is currently running,
    freshest possible) or status_fields_from_state() (paused/stopped,
    from the last persisted checkpoint) produced `fields`, or
    "unavailable" if this strategy doesn't override either (most
    strategies) or hasn't warmed up far enough yet to have anything."""
    fields: list[StatusField]
    source: Literal["live", "persisted", "unavailable"]


class AdjustmentHistogramBucket(BaseModel):
    """One bucket of GET /deployments/{id}/adjustment-histogram — see
    queries.get_adjustment_histogram's own docstring (Step 87)."""
    adjustments: int
    label: str
    units: int


class AdjustmentHistogramOut(BaseModel):
    """`supported=False` (with an empty `buckets`) means this
    deployment's strategy doesn't set StrategyBase.ADJUSTMENT_GROUP_BY
    at all — the UI omits the whole section rather than showing an
    empty chart for a strategy with no adjustment concept."""
    supported: bool
    group_by: Optional[Literal["day", "cycle_id"]] = None
    buckets: list[AdjustmentHistogramBucket] = []


class PnlDeploymentBreakdown(BaseModel):
    """One deployment's realized P&L within a single Reports-page
    period — see queries.pnl_by_deployment_for_range."""
    deployment_id: UUID
    deployment_name: str
    strategy_name: str
    realized_pnl: float
    positions_closed: int


class StrategyLeaderboardRow(BaseModel):
    """One strategy's ALL-TIME realized P&L, across every deployment
    that ever ran it — see queries.list_strategy_leaderboard. Ships
    gross_win/gross_loss rather than a pre-computed profit factor; see
    that query's own docstring for why."""
    strategy_name: str
    realized_pnl: float
    positions_closed: int
    wins: int
    losses: int
    gross_win: float
    gross_loss: float
    deployments_count: int


class PnlReportOut(BaseModel):
    """The Reports page's full payload for ONE selected period
    (period type + offset from now — see app/routers/aggregate.py's
    period_bounds()): the period's own realized-P&L summary, the SAME
    summary for the immediately preceding period (so the frontend can
    show a "vs previous period" delta without a second round trip),
    and the by-strategy / by-deployment breakdowns for the selected
    period. All realized-P&L only, same reasoning as PnlDigestRow."""
    period: str            # "day" | "week" | "month"
    offset: int
    period_start: datetime
    period_end: datetime
    label: str              # human-readable period name, e.g. "16 Aug 2026" / "Week of 11 Aug 2026" / "Aug 2026"
    realized_pnl: float
    positions_closed: int
    wins: int
    losses: int
    fills: int
    prev_realized_pnl: float
    by_strategy: list[PnlStrategyBreakdown]
    by_deployment: list[PnlDeploymentBreakdown]


class TagOut(BaseModel):
    """One row of the predefined tag catalog -- Settings -> Tags manages
    this list; a deployment's own `tags` (DeploymentOut) is a list of
    NAMES drawn from here, not a list of these objects. See the 0010
    migration's own comment for why this catalog exists at all instead
    of freeform per-deployment text."""
    id: UUID
    name: str
    created_at: datetime

    class Config:
        from_attributes = True


class TagCreate(BaseModel):
    name: str

"""live_deploy — Pydantic request/response models for the deployments API."""

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class DeploymentCreate(BaseModel):
    deployment_name: str
    strategy_name: str
    mode: Literal["intraday", "positional"]
    initial_capital: float = Field(gt=0)
    config: dict = Field(default_factory=dict)


class DeploymentUpdate(BaseModel):
    """
    PATCH /deployments/{id} — partial update, both fields optional so a
    caller can rename without touching notes or vice versa. Deliberately
    narrow: deployment_name (identity) and notes (free-text metadata,
    e.g. "why this was deployed") are the only fields editable after
    creation. strategy_name/mode/initial_capital/config are NOT here on
    purpose — the running strategy instance and every P&L calculation
    already assume those are fixed for the deployment's lifetime (e.g.
    initial_capital is the fixed reference value several strategies size
    against); changing them post-creation without a real reset/restart
    semantics would silently corrupt state rather than do anything
    useful. Stop and redeploy fresh for a genuine strategy/capital/config
    change.
    """
    deployment_name: Optional[str] = None
    notes: Optional[str] = None


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
    created_at: datetime
    updated_at: datetime
    strategy_registered: bool = True   # False = created, but no code will ever run for it (yet)
    # Populated by the deployments router (list/get), NOT by
    # queries.create_deployment itself — a freshly created deployment is
    # correctly 0/0 by these defaults with no extra query needed. See
    # routers/deployments.py's _enrich_pnl / per-deployment pnl lookup.
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0

    class Config:
        from_attributes = True


class PositionOut(BaseModel):
    id: UUID
    deployment_id: UUID
    symbol: str
    instrument_token: int
    side: str
    status: str
    qty: float
    avg_entry_price: float
    realized_pnl: float
    opened_at: datetime
    closed_at: Optional[datetime] = None
    current_price: Optional[float] = None
    unrealized_pnl: Optional[float] = None


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
    periodic snapshot loop for how these rows actually get written.

    `open_positions_value` is the mark-to-market UNREALIZED P&L sum of
    every position open at snapshot time (not the notional value of the
    positions themselves — for short option legs "notional value"
    isn't a meaningful equity contribution the way it is for a long
    position, whereas unrealized P&L always is) — so `total_value =
    cash + open_positions_value` is genuinely "what this deployment's
    account is worth right now," the natural equity-curve Y axis.
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


class PnlStrategyBreakdown(BaseModel):
    """One strategy's realized P&L within a single Reports-page period
    — see queries.pnl_by_strategy_for_range."""
    strategy_name: str
    realized_pnl: float
    positions_closed: int


class PnlDeploymentBreakdown(BaseModel):
    """One deployment's realized P&L within a single Reports-page
    period — see queries.pnl_by_deployment_for_range."""
    deployment_id: UUID
    deployment_name: str
    strategy_name: str
    realized_pnl: float
    positions_closed: int


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

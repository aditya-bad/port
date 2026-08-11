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


class DeploymentOut(BaseModel):
    id: UUID
    deployment_name: str
    strategy_name: str
    mode: str
    status: str
    initial_capital: float
    current_cash: float
    config: dict
    created_at: datetime
    updated_at: datetime
    strategy_registered: bool = True   # False = created, but no code will ever run for it (yet)

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


class LotOut(BaseModel):
    id: UUID
    position_id: UUID
    action: str
    qty: float
    price: float
    executed_at: datetime
    reason: Optional[str] = None


class LotsPage(BaseModel):
    total: int
    offset: int
    lots: list[LotOut]


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

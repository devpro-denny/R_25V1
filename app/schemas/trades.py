"""
Pydantic schemas for trade-related responses.
"""

from datetime import datetime
from typing import Optional

from pydantic import AliasChoices, BaseModel, Field


class TradeResponse(BaseModel):
    """Schema for individual trade response."""

    contract_id: str
    symbol: str
    direction: str = Field(validation_alias=AliasChoices("direction", "signal"))
    strategy_type: Optional[str] = None
    stake: Optional[float] = None
    entry_price: Optional[float] = None
    exit_price: Optional[float] = None
    take_profit: Optional[float] = None
    stop_loss: Optional[float] = None
    status: str
    pnl: Optional[float] = Field(None, validation_alias=AliasChoices("profit", "pnl"))
    timestamp: Optional[datetime] = None
    duration: Optional[int] = None
    trailing_enabled: Optional[bool] = None
    stagnation_enabled: Optional[bool] = None

    class Config:
        from_attributes = True
        populate_by_name = True


class TradeStatsResponse(BaseModel):
    """Schema for trade statistics response."""

    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl: float
    daily_pnl: float

    # Extended stats for dashboard
    avg_win: Optional[float] = 0.0
    avg_loss: Optional[float] = 0.0
    largest_win: Optional[float] = 0.0
    largest_loss: Optional[float] = 0.0
    profit_factor: Optional[float] = 0.0

    class Config:
        from_attributes = True


class TradeExitControlsUpdate(BaseModel):
    """Schema for updating per-trade runtime exit controls."""

    trailing_enabled: Optional[bool] = None
    stagnation_enabled: Optional[bool] = None


class TradeExitControlsResponse(BaseModel):
    """Schema for per-trade runtime exit controls."""

    contract_id: str
    trailing_enabled: bool
    stagnation_enabled: bool


class ManualActiveTradeCreate(BaseModel):
    """Schema for registering a manually opened broker contract for monitoring."""

    open_contract_id: str = Field(
        validation_alias=AliasChoices("open_contract_id", "contract_id")
    )
    symbol: str
    direction: str = Field(default="UP")
    stake: Optional[float] = None
    entry_price: Optional[float] = None
    strategy_type: Optional[str] = None
    open_time: Optional[datetime] = None

    class Config:
        from_attributes = True
        populate_by_name = True

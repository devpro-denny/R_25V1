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

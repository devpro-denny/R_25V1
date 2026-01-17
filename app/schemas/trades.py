"""
Pydantic schemas for trade-related responses
"""

from pydantic import BaseModel, Field, AliasChoices
from typing import Optional

class TradeResponse(BaseModel):
    """Schema for individual trade response"""
    contract_id: str  # ✅ Already correct as string
    direction: str = Field(alias='signal')
    stake: Optional[float] = None
    entry_price: Optional[float] = None
    exit_price: Optional[float] = None
    take_profit: Optional[float] = None
    stop_loss: Optional[float] = None
    status: str
    pnl: Optional[float] = Field(None, validation_alias=AliasChoices('profit', 'pnl'))
    
    class Config:
        from_attributes = True  # For Pydantic v2 compatibility

class TradeStatsResponse(BaseModel):
    """Schema for trade statistics response"""
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
"""
Pydantic schemas for trade-related responses
"""

from pydantic import BaseModel
from typing import Optional

class TradeResponse(BaseModel):
    """Schema for individual trade response"""
    contract_id: str  # ✅ Already correct as string
    direction: str
    stake: float
    entry_price: float
    take_profit: Optional[float] = None
    stop_loss: Optional[float] = None
    status: str
    pnl: Optional[float] = None
    
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
    
    class Config:
        from_attributes = True
"""
Trades API Endpoints
View active trades, history, and statistics
"""

from fastapi import APIRouter, Query, Depends
from typing import List

from app.bot.state import bot_state
from app.schemas.trades import TradeResponse, TradeStatsResponse
from app.core.serializers import prepare_response  # ← ADD THIS LINE
from app.core.auth import get_current_active_user

router = APIRouter()

@router.get("/active", response_model=List[TradeResponse])
async def get_active_trades(
    current_user: dict = Depends(get_current_active_user)  # ← ADD AUTH
):
    """Get all active trades"""
    trades = bot_state.get_active_trades()
    return prepare_response(
        trades,
        id_fields=['contract_id']  # ← Convert contract_id to string
    )

@router.get("/history", response_model=List[TradeResponse])
async def get_trade_history(
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(get_current_active_user)  # ← ADD AUTH
):
    """Get trade history"""
    history = bot_state.get_trade_history(limit)
    return prepare_response(
        history,
        id_fields=['contract_id']  # ← Convert contract_id to string
    )

@router.get("/stats", response_model=TradeStatsResponse)
async def get_trade_stats(
    current_user: dict = Depends(get_current_active_user)  # ← ADD AUTH
):
    """Get trading statistics"""
    stats = bot_state.get_statistics()
    return prepare_response(stats)  # ← WRAP WITH prepare_response
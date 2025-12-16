"""
Monitoring API Endpoints
Get signals, performance metrics, and logs
"""

from fastapi import APIRouter, Query
from typing import List
import os

from app.bot.state import bot_state
from app.schemas.common import PerformanceResponse
from app.core.serializers import prepare_response  # ← ADD THIS LINE
from app.core.auth import get_current_active_user
from fastapi import Depends

router = APIRouter()

@router.get("/signals")
async def get_recent_signals(
    limit: int = Query(20, ge=1, le=50),
    current_user: dict = Depends(get_current_active_user)  # ← ADD AUTH
):
    """Get recent trade signals"""
    signals = bot_state.get_recent_signals(limit)
    return prepare_response(signals)  # ← WRAP WITH prepare_response

@router.get("/performance", response_model=PerformanceResponse)
async def get_performance(
    current_user: dict = Depends(get_current_active_user)  # ← ADD AUTH
):
    """Get bot performance metrics"""
    performance = {
        **bot_state.get_performance(),
        **bot_state.get_statistics()
    }
    return prepare_response(performance)  # ← WRAP WITH prepare_response

@router.get("/logs")
async def get_recent_logs(
    lines: int = Query(100, ge=1, le=1000),
    current_user: dict = Depends(get_current_active_user)  # ← ADD AUTH
):
    """Get recent log entries"""
    try:
        log_file = "trading_bot.log"
        if not os.path.exists(log_file):
            return {"logs": []}
        
        with open(log_file, 'r', encoding='utf-8') as f:
            all_lines = f.readlines()
            recent_lines = all_lines[-lines:]
            
        return prepare_response({  # ← WRAP WITH prepare_response
            "logs": [line.strip() for line in recent_lines],
            "total_lines": len(all_lines),
            "showing": len(recent_lines)
        })
    except Exception as e:
        return prepare_response({  # ← WRAP WITH prepare_response
            "logs": [],
            "error": str(e)
        })
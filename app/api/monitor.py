"""
Monitoring API Endpoints
Get signals, performance metrics, and logs
"""

from fastapi import APIRouter, Query
from typing import List
import os
import psutil

from app.bot.manager import bot_manager
from app.bot.events import event_manager
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
    signals = bot_manager.get_bot(current_user['id']).state.get_recent_signals(limit)
    return prepare_response(signals)  # ← WRAP WITH prepare_response

@router.get("/performance", response_model=PerformanceResponse)
async def get_performance(
    current_user: dict = Depends(get_current_active_user)
):
    """Get bot performance metrics"""
    bot = bot_manager.get_bot(current_user['id'])
    
    # Calculate error rate from multi-asset stats
    total_scans = bot.scan_count
    total_errors = sum(bot.errors_by_symbol.values())
    error_rate = (total_errors / total_scans * 100) if total_scans > 0 else 0.0
    
    # Get system metrics via psutil
    cpu_usage = psutil.cpu_percent(interval=None) # Non-blocking
    memory_usage = psutil.virtual_memory().percent
    
    performance = {
        **bot.state.get_performance(),
        **bot.state.get_statistics(),
        "cpu_usage": cpu_usage,
        "memory_usage": memory_usage,
        "error_rate": round(error_rate, 2),
        "active_connections": len(event_manager.active_connections)
    }
    return prepare_response(performance)

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
        
        # Read and filter logs
        filtered_logs = []
        user_id = current_user['id']
        
        with open(log_file, 'r', encoding='utf-8') as f:
            # Read from end efficiently (simplification: read all for now as logs aren't huge yet)
            # For production, utilize seek/tell or unix 'tail' equivalent
            all_lines = f.readlines()
            
            for line in reversed(all_lines):
                # Stop if we have enough
                if len(filtered_logs) >= lines:
                    break
                    
                # Filter logic:
                # 1. System logs: "[None]"
                # 2. User logs: "[{user_id}]"
                # 3. Allow legacy logs (no bracketed ID) if strict_mode is False (optional)
                
                if f"[{user_id}]" in line or "[None]" in line:
                    filtered_logs.append(line.strip())
                # If neither, it belongs to another user -> Skip
        
        # Reverse back to chronological order
        filtered_logs.reverse()
            
        return prepare_response({
            "logs": filtered_logs,
            "total_lines": len(all_lines),
            "showing": len(filtered_logs)
        })
    except Exception as e:
        return prepare_response({  # ← WRAP WITH prepare_response
            "logs": [],
            "error": str(e)
        })
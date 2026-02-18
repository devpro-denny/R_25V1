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
    current_user: dict = Depends(get_current_active_user)
):
    """Get recent log entries from both multiplier and Rise/Fall logs"""
    try:
        user_id = current_user['id']
        filtered_logs = []
        total_lines = 0

        # --- 1. Read multiplier bot logs ---
        log_file = "trading_bot.log"
        if os.path.exists(log_file):
            with open(log_file, 'r', encoding='utf-8') as f:
                all_lines = f.readlines()
                total_lines += len(all_lines)
                for line in all_lines:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    if f"[{user_id}]" in stripped or "[None]" in stripped:
                        filtered_logs.append(stripped)

        # --- 2. Read Rise/Fall bot logs ---
        rf_log_file = "risefall_bot.log"
        if os.path.exists(rf_log_file):
            with open(rf_log_file, 'r', encoding='utf-8') as f:
                rf_lines = f.readlines()
                total_lines += len(rf_lines)
                for line in rf_lines:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    # Normalize 4-field RF format to 3-field:
                    # "ts | risefallbot | LEVEL | msg" → "ts | LEVEL | [RF] msg"
                    import re
                    rf_match = re.match(
                        r'^(.+?)\s*\|\s*risefallbot(?:\.\S+)?\s*\|\s*([A-Z]+)\s*\|\s*(.+)$',
                        stripped
                    )
                    if rf_match:
                        ts, level, msg = rf_match.groups()
                        # Filter by user_id if present in original message
                        if f"[{user_id}]" in msg or "[None]" in msg or "[" not in msg:
                            stripped = f"{ts} | {level} | [RF] {msg}"
                        else:
                            continue  # Skip logs for other users
                    filtered_logs.append(stripped)

        # --- 3. Sort merged logs by timestamp (best-effort) ---
        # Both log formats start with a timestamp: "YYYY-MM-DD HH:MM:SS"
        filtered_logs.sort()

        # Take only the last N lines (most recent)
        filtered_logs = filtered_logs[-lines:]

        return prepare_response({
            "logs": filtered_logs,
            "total_lines": total_lines,
            "showing": len(filtered_logs)
        })
    except Exception as e:
        return prepare_response({
            "logs": [],
            "error": str(e)
        })
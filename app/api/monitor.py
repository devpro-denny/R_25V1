"""
Monitoring API Endpoints
Get signals, performance metrics, and logs
"""

import os
import re

import psutil
from fastapi import APIRouter, Depends, Query

from app.bot.events import event_manager
from app.bot.manager import bot_manager
from app.core.auth import get_current_active_user
from app.core.serializers import prepare_response
from app.schemas.common import PerformanceResponse

router = APIRouter()

def _is_decorative_log_line(line: str) -> bool:
    """Return True for visual separators like ======= to keep UI logs clean."""
    if not line:
        return False
    cleaned = line.strip()
    # Keep only the message segment when line includes timestamp/metadata pipes.
    if "|" in cleaned:
        cleaned = cleaned.split("|")[-1].strip()
    cleaned = re.sub(r"^\[[^\]]+\]\s*", "", cleaned)
    return bool(re.fullmatch(r"[=\-_*~]{8,}", cleaned))


@router.get("/signals")
async def get_recent_signals(
    limit: int = Query(20, ge=1, le=50),
    current_user: dict = Depends(get_current_active_user),
):
    """Get recent trade signals."""
    signals = bot_manager.get_bot(current_user["id"]).state.get_recent_signals(limit)
    return prepare_response(signals)


@router.get("/performance", response_model=PerformanceResponse)
async def get_performance(current_user: dict = Depends(get_current_active_user)):
    """Get bot performance metrics."""
    bot = bot_manager.get_bot(current_user["id"])

    total_scans = bot.scan_count
    total_errors = sum(bot.errors_by_symbol.values())
    error_rate = (total_errors / total_scans * 100) if total_scans > 0 else 0.0

    cpu_usage = psutil.cpu_percent(interval=None)
    memory_usage = psutil.virtual_memory().percent

    performance = {
        **bot.state.get_performance(),
        **bot.state.get_statistics(),
        "cpu_usage": cpu_usage,
        "memory_usage": memory_usage,
        "error_rate": round(error_rate, 2),
        "active_connections": len(event_manager.active_connections),
    }
    return prepare_response(performance)


@router.get("/logs")
async def get_recent_logs(
    lines: int = Query(100, ge=1, le=1000),
    current_user: dict = Depends(get_current_active_user),
):
    """Get recent logs for the currently running bot only."""
    try:
        user_id = current_user["id"]
        filtered_logs = []
        total_lines = 0

        status = bot_manager.get_status(user_id)
        running_bot = None
        if status.get("is_running"):
            running_bot = "risefall" if status.get("active_strategy") == "RiseFall" else "multiplier"

        if not running_bot:
            return prepare_response(
                {
                    "logs": [],
                    "total_lines": 0,
                    "showing": 0,
                    "running_bot": None,
                    "message": "No bot is currently running",
                }
            )

        if running_bot == "multiplier":
            log_file = "trading_bot.log"
            if os.path.exists(log_file):
                with open(log_file, "r", encoding="utf-8") as f:
                    all_lines = f.readlines()
                    total_lines += len(all_lines)
                    for line in all_lines:
                        stripped = line.strip()
                        if not stripped:
                            continue
                        if _is_decorative_log_line(stripped):
                            continue
                        if f"[{user_id}]" in stripped or "[None]" in stripped or "[" not in stripped:
                            filtered_logs.append(stripped)

        if running_bot == "risefall":
            rf_log_file = "risefall_bot.log"
            if os.path.exists(rf_log_file):
                with open(rf_log_file, "r", encoding="utf-8") as f:
                    rf_lines = f.readlines()
                    total_lines += len(rf_lines)
                    for line in rf_lines:
                        stripped = line.strip()
                        if not stripped:
                            continue

                        rf_match = re.match(
                            r"^(.+?)\s*\|\s*risefallbot(?:\.\S+)?\s*\|\s*([A-Z]+)\s*\|\s*(.+)$",
                            stripped,
                        )
                        if rf_match:
                            ts, level, msg = rf_match.groups()
                            if f"[{user_id}]" in msg or "[None]" in msg or "[" not in msg:
                                stripped = f"{ts} | {level} | [RF] {msg}"
                            else:
                                continue
                        if _is_decorative_log_line(stripped):
                            continue
                        filtered_logs.append(stripped)

        filtered_logs.sort()
        filtered_logs = filtered_logs[-lines:]

        return prepare_response(
            {
                "logs": filtered_logs,
                "total_lines": total_lines,
                "showing": len(filtered_logs),
                "running_bot": running_bot,
            }
        )
    except Exception as e:
        return prepare_response({"logs": [], "error": str(e)})

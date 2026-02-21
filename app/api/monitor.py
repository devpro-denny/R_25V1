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


def _normalize_strategy_name(strategy_name: str | None) -> str | None:
    """Normalize strategy names from profile/status payloads."""
    if not strategy_name:
        return None

    key = re.sub(r"[^a-z0-9]+", "", str(strategy_name).strip().lower())
    aliases = {
        "conservative": "Conservative",
        "scalping": "Scalping",
        "scalp": "Scalping",
        "risefall": "RiseFall",
        "rf": "RiseFall",
    }
    return aliases.get(key, str(strategy_name).strip())


def _safe_user_component(user_id: str) -> str:
    text = str(user_id) if user_id is not None else "anonymous"
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", text).strip("._")
    return cleaned or "anonymous"


def _resolve_active_strategy(status: dict) -> str | None:
    """Resolve active strategy from bot status payload."""
    active = status.get("active_strategy")
    if active:
        return _normalize_strategy_name(active)
    cfg = status.get("config")
    if isinstance(cfg, dict):
        return _normalize_strategy_name(cfg.get("strategy"))
    return None


def _resolve_log_files(active_strategy: str, user_id: str) -> list[str]:
    """Return candidate log files for a strategy (new path first, legacy fallback last)."""
    active_strategy = _normalize_strategy_name(active_strategy)
    user_key = _safe_user_component(user_id)
    if active_strategy == "Conservative":
        return [
            f"logs/conservative/{user_key}.log",
            "logs/conservative/conservative_bot.log",
            "logs/system/multiplier_system.log",
            "trading_bot.log",
        ]
    if active_strategy == "Scalping":
        return [
            f"logs/scalping/{user_key}.log",
            "logs/scalping/scalping_bot.log",
            "logs/system/multiplier_system.log",
            "trading_bot.log",
        ]
    if active_strategy == "RiseFall":
        try:
            from risefallbot.rf_config import RF_LOG_FILE

            return [f"logs/risefall/{user_key}.log", RF_LOG_FILE, "risefall_bot.log"]
        except Exception:
            return [
                f"logs/risefall/{user_key}.log",
                "logs/risefall/risefall_bot.log",
                "risefall_bot.log",
            ]
    return []


def _select_log_file(active_strategy: str, user_id: str) -> str | None:
    """Select first existing log file for the active strategy."""
    for candidate in _resolve_log_files(active_strategy, user_id):
        if os.path.exists(candidate):
            return candidate
    return None


def _should_include_log_line(line: str, user_id: str, active_strategy: str) -> bool:
    """
    Determine whether a log line should be included for the requesting user.
    For legacy Rise/Fall logs without [user_id], include the line because file is bot-isolated.
    """
    if f"[{user_id}]" in line or "[None]" in line:
        return True
    if _normalize_strategy_name(active_strategy) == "RiseFall":
        # Legacy RF log format may omit user_id context in message prefix.
        return True
    # Backward compatibility for older multiplier lines with no metadata tags.
    if "[" not in line:
        return True
    return False

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
        active_strategy = _resolve_active_strategy(status)
        running_bot = None
        if status.get("is_running"):
            running_bot = "risefall" if active_strategy == "RiseFall" else "multiplier"

        if not running_bot:
            return prepare_response(
                {
                    "logs": [],
                    "total_lines": 0,
                    "showing": 0,
                    "running_bot": None,
                    "active_strategy": None,
                    "message": "No bot is currently running",
                }
            )

        # Prefer user-scoped file, but fall back to other strategy files if
        # the first candidate is empty/unmatched (prevents blank terminal UI).
        for log_file in _resolve_log_files(active_strategy, user_id):
            if not os.path.exists(log_file):
                continue
            with open(log_file, "r", encoding="utf-8") as f:
                log_lines = f.readlines()
                total_lines += len(log_lines)

            candidate_filtered = []
            for line in log_lines:
                stripped = line.strip()
                if not stripped:
                    continue
                if _is_decorative_log_line(stripped):
                    continue
                if not _should_include_log_line(stripped, user_id, active_strategy):
                    continue
                candidate_filtered.append(stripped)

            if candidate_filtered:
                filtered_logs = candidate_filtered
                break

        filtered_logs = filtered_logs[-lines:]

        return prepare_response(
            {
                "logs": filtered_logs,
                "total_lines": total_lines,
                "showing": len(filtered_logs),
                "running_bot": running_bot,
                "active_strategy": active_strategy,
            }
        )
    except Exception as e:
        return prepare_response({"logs": [], "error": str(e)})

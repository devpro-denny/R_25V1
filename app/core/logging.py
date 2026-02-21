import logging
import sys
import asyncio
import time
import re
from typing import Optional

from app.core.context import user_id_var
from app.bot.events import event_manager

class ContextInjectingFilter(logging.Filter):
    """
    Injects user_id from contextvars into the log record.
    """
    def filter(self, record):
        record.user_id = user_id_var.get()
        return True

class WebSocketLoggingHandler(logging.Handler):
    """
    Broadcasts logs to the specific user via WebSocket.
    """
    def __init__(self, status_cache_ttl_seconds: float = 1.0):
        super().__init__()
        self._status_cache_ttl = status_cache_ttl_seconds
        self._status_cache = {}

    @staticmethod
    def _classify_bot_from_logger(logger_name: str) -> str:
        """Map logger namespace to bot type."""
        if str(logger_name).startswith("risefallbot"):
            return "risefall"
        if str(logger_name).startswith("conservative"):
            return "conservative"
        if str(logger_name).startswith("scalping"):
            return "scalping"
        return "system"

    def _get_running_bot_type(self, user_id: str) -> Optional[str]:
        """
        Return the currently running bot type for a user:
        - 'risefall'
        - 'conservative'
        - 'scalping'
        - None (no bot running / unknown)
        """
        now = time.time()
        cached = self._status_cache.get(user_id)
        if cached and (now - cached["ts"] <= self._status_cache_ttl):
            return cached["bot_type"]

        bot_type = None
        try:
            from app.bot.manager import bot_manager

            status = bot_manager.get_status(user_id)
            if status.get("is_running"):
                active_strategy = status.get("active_strategy")
                if not active_strategy:
                    cfg = status.get("config") if isinstance(status.get("config"), dict) else {}
                    active_strategy = cfg.get("strategy")
                if active_strategy == "RiseFall":
                    bot_type = "risefall"
                elif active_strategy == "Scalping":
                    bot_type = "scalping"
                elif active_strategy == "Conservative":
                    bot_type = "conservative"
                else:
                    bot_type = "system"
        except Exception:
            bot_type = None

        self._status_cache[user_id] = {"bot_type": bot_type, "ts": now}
        return bot_type

    @staticmethod
    def _is_decorative_log_line(msg: str) -> bool:
        """
        Suppress decorative divider lines (e.g. =======) for frontend readability.
        """
        if not msg:
            return False
        cleaned = msg.strip()
        # Handle messages like "[RF] ======="
        cleaned = re.sub(r"^\[[^\]]+\]\s*", "", cleaned)
        return bool(re.fullmatch(r"[=\-_*~]{8,}", cleaned))

    def emit(self, record):
        try:
            user_id = getattr(record, 'user_id', None)
            if not user_id:
                return

            context_bot = getattr(record, "bot_type", None)
            if context_bot in {"conservative", "scalping", "risefall"}:
                record_bot = context_bot
            else:
                record_bot = self._classify_bot_from_logger(record.name)
            running_bot = self._get_running_bot_type(user_id)

            # Strict isolation: only stream logs that belong to the bot
            # currently running for this user.
            if not running_bot or record_bot != running_bot:
                return

            # Use raw message (without duplicated timestamp/name prefixes)
            # because frontend already renders its own timestamp/level columns.
            msg = record.getMessage()
            if self._is_decorative_log_line(msg):
                return
            
            # Broadcast directly using event manager
            # We use create_task because emit is synchronous
            try:
                loop = asyncio.get_running_loop()
                if loop.is_running():
                    payload = {
                        "type": "log",
                        "bot": record_bot,
                        "level": record.levelname,
                        "message": msg,
                        "timestamp": record.created,
                        "account_id": user_id
                    }
                    loop.create_task(event_manager.broadcast(payload))
            except RuntimeError:
                # No running loop (e.g. startup/shutdown or different thread)
                pass
                
        except Exception:
            self.handleError(record)

def setup_api_logger():
    """Setup logging for FastAPI application"""
    
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    # 1. Console Handler (Standard Output)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    
    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)s | %(name)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # 2. Context Filter (Injects user_id)
    context_filter = ContextInjectingFilter()
    logger.addFilter(context_filter)
    
    # 3. WebSocket Handler (Streams to Frontend)
    ws_handler = WebSocketLoggingHandler()
    ws_handler.setLevel(logging.INFO)
    ws_handler.setFormatter(formatter)
    logger.addHandler(ws_handler)
    
    return logger

import asyncio
import logging
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.core.logging import WebSocketLoggingHandler


def _record(name: str, user_id: str, msg: str = "hello", bot_type: str | None = None):
    rec = logging.LogRecord(
        name=name,
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )
    rec.user_id = user_id
    if bot_type is not None:
        rec.bot_type = bot_type
    return rec


@pytest.mark.asyncio
async def test_ws_logging_handler_emits_only_matching_running_bot():
    handler = WebSocketLoggingHandler(status_cache_ttl_seconds=0)
    handler.setFormatter(logging.Formatter("%(message)s"))

    with patch("app.bot.manager.bot_manager.get_status", return_value={"is_running": True, "active_strategy": "RiseFall"}), \
         patch("app.core.logging.event_manager.broadcast", new=AsyncMock()) as mock_broadcast:
        handler.emit(_record("risefallbot.engine", "u1", "rf-line"))
        await asyncio.sleep(0)

        assert mock_broadcast.await_count == 1
        payload = mock_broadcast.await_args.args[0]
        assert payload["type"] == "log"
        assert payload["bot"] == "risefall"
        assert payload["account_id"] == "u1"
        assert isinstance(payload["timestamp"], str)
        # Must be parseable ISO-8601 so frontend Date rendering is stable.
        datetime.fromisoformat(payload["timestamp"])


@pytest.mark.asyncio
async def test_ws_logging_handler_drops_mixed_or_stopped_bot_logs():
    handler = WebSocketLoggingHandler(status_cache_ttl_seconds=0)
    handler.setFormatter(logging.Formatter("%(message)s"))

    with patch("app.core.logging.event_manager.broadcast", new=AsyncMock()) as mock_broadcast:
        # Running RiseFall: multiplier log must be dropped.
        with patch("app.bot.manager.bot_manager.get_status", return_value={"is_running": True, "active_strategy": "RiseFall"}):
            handler.emit(_record("trade_engine", "u1", "mult-line"))
            await asyncio.sleep(0)
            assert mock_broadcast.await_count == 0

        # No running bot: any log must be dropped.
        with patch("app.bot.manager.bot_manager.get_status", return_value={"is_running": False}):
            handler.emit(_record("risefallbot.strategy", "u1", "rf-line"))
            await asyncio.sleep(0)
            assert mock_broadcast.await_count == 0


@pytest.mark.asyncio
async def test_ws_logging_handler_drops_decorative_divider_logs():
    handler = WebSocketLoggingHandler(status_cache_ttl_seconds=0)
    handler.setFormatter(logging.Formatter("%(message)s"))

    with patch("app.bot.manager.bot_manager.get_status", return_value={"is_running": True, "active_strategy": "RiseFall"}), \
         patch("app.core.logging.event_manager.broadcast", new=AsyncMock()) as mock_broadcast:
        handler.emit(_record("risefallbot", "u1", "============================================================"))
        await asyncio.sleep(0)
        assert mock_broadcast.await_count == 0


@pytest.mark.asyncio
async def test_ws_logging_handler_accepts_lowercase_scalping_status_strategy():
    handler = WebSocketLoggingHandler(status_cache_ttl_seconds=0)
    handler.setFormatter(logging.Formatter("%(message)s"))

    with patch("app.bot.manager.bot_manager.get_status", return_value={"is_running": True, "active_strategy": "scalping"}), \
         patch("app.core.logging.event_manager.broadcast", new=AsyncMock()) as mock_broadcast:
        handler.emit(_record("TradingBot", "u1", "[SCALPING] line", bot_type="scalping"))
        await asyncio.sleep(0)

        assert mock_broadcast.await_count == 1
        payload = mock_broadcast.await_args.args[0]
        assert payload["bot"] == "scalping"


@pytest.mark.asyncio
async def test_ws_logging_handler_repairs_mojibake_message():
    handler = WebSocketLoggingHandler(status_cache_ttl_seconds=0)
    handler.setFormatter(logging.Formatter("%(message)s"))

    with patch("app.bot.manager.bot_manager.get_status", return_value={"is_running": True, "active_strategy": "scalping"}), \
         patch("app.core.logging.event_manager.broadcast", new=AsyncMock()) as mock_broadcast:
        handler.emit(_record("TradingBot", "u1", "\u00e2\u0153\u2026 Trade Engine connected", bot_type="scalping"))
        await asyncio.sleep(0)

        assert mock_broadcast.await_count == 1
        payload = mock_broadcast.await_args.args[0]
        assert payload["message"] == "✅ Trade Engine connected"

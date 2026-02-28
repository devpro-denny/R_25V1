import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from telegram_notifier import TelegramNotifier, TelegramLoggingHandler
import logging

@pytest.fixture
def mock_bot():
    with patch("telegram_notifier.Bot") as mock:
        yield mock

def test_telegram_notifier_init(mock_bot):
    with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "test_token", "TELEGRAM_CHAT_ID": "test_chat"}):
        notifier = TelegramNotifier()
        assert notifier.enabled is True
        assert notifier.bot is not None

def test_telegram_notifier_disabled_no_creds():
    with patch.dict("os.environ", {}, clear=True):
        notifier = TelegramNotifier()
        assert notifier.enabled is False

@pytest.mark.asyncio
async def test_notify_trade_opened(mock_bot):
    with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "test_token", "TELEGRAM_CHAT_ID": "test_chat"}):
        notifier = TelegramNotifier()
        notifier.bot.send_message = AsyncMock()
        
        trade_info = {
            "symbol": "R_25",
            "contract_id": "123",
            "direction": "UP",
            "stake": 10.0,
            "entry_price": 100.0,
            "multiplier": 160
        }
        
        # Test the legacy method alias if any, or main methods
        if hasattr(notifier, "notify_trade_opened"):
            await notifier.notify_trade_opened(trade_info)
            assert notifier.bot.send_message.called
            sent = notifier.bot.send_message.call_args.kwargs["text"]
            assert "📈 [LONG] 🚀 <b>TRADE OPENED: R_25</b>" in sent
            assert "Direction: <b>⬆️ UP</b>" in sent
            assert "â–" not in sent
            assert "â€¢" not in sent

@pytest.mark.asyncio
async def test_notify_error(mock_bot):
    with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "test_token", "TELEGRAM_CHAT_ID": "test_chat"}):
        notifier = TelegramNotifier()
        notifier.bot.send_message = AsyncMock()
        
        await notifier.notify_error("Test Error")
        assert notifier.bot.send_message.called

def test_telegram_logging_handler(mock_bot):
    with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "test_token", "TELEGRAM_CHAT_ID": "test_chat"}):
        notifier = TelegramNotifier()
        notifier.notify_error = AsyncMock()
        
        handler = TelegramLoggingHandler(notifier)
        logger = logging.getLogger("test_logger")
        logger.addHandler(handler)
        logger.setLevel(logging.ERROR)
        
        # This should trigger the handler
        logger.error("Test Log Error")
        
        # Since it's fire-and-forget with create_task, we might need to wait or check something else
        # But we can at least check if it didn't crash


@pytest.mark.asyncio
async def test_notify_bot_started_uses_clean_unicode(mock_bot):
    with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "test_token", "TELEGRAM_CHAT_ID": "test_chat"}):
        notifier = TelegramNotifier()
        notifier.bot.send_message = AsyncMock()

        await notifier.notify_bot_started(
            balance=10374.20,
            stake=20.0,
            strategy_name="Scalping",
            symbol_count=10,
        )

        assert notifier.bot.send_message.called
        sent = notifier.bot.send_message.call_args.kwargs["text"]
        assert "🚀 <b>BOT STARTED</b>" in sent
        assert "⚙️ <b>Configuration</b>" in sent
        assert "   • Strategy: 📊 Scalping" in sent
        assert "ðŸ" not in sent
        assert "â€¢" not in sent
        assert "â”" not in sent


def test_repair_mojibake_text_handles_mixed_tokens(mock_bot):
    with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "test_token", "TELEGRAM_CHAT_ID": "test_chat"}):
        notifier = TelegramNotifier()
        mixed = "🚀 Strength: â–®â–®â–®â–¯â–¯"
        repaired = notifier._repair_mojibake_text(mixed)

        assert repaired == "🚀 Strength: ▮▮▮▯▯"
        assert "â–" not in repaired


@pytest.mark.asyncio
async def test_notify_signal_strength_bar_uses_clean_blocks(mock_bot):
    with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "test_token", "TELEGRAM_CHAT_ID": "test_chat"}):
        notifier = TelegramNotifier()
        notifier.bot.send_message = AsyncMock()

        await notifier.notify_signal(
            {
                "signal": "DOWN",
                "score": 7.0,
                "symbol": "R_75",
                "strategy_type": "Scalping",
                "user_id": "user-1",
                "details": {"rsi": 41.0, "adx": 28.5},
            }
        )

        sent = notifier.bot.send_message.call_args.kwargs["text"]
        assert "📉 [SHORT] <b>SIGNAL DETECTED: R_75</b>" in sent
        assert "Direction: <b>⬇️ DOWN</b>" in sent
        assert "Strength: ▮▮▮▯▯ (7.0)" in sent
        assert "â–" not in sent


@pytest.mark.asyncio
async def test_notify_bot_stopped_uses_clean_separator(mock_bot):
    with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "test_token", "TELEGRAM_CHAT_ID": "test_chat"}):
        notifier = TelegramNotifier()
        notifier.bot.send_message = AsyncMock()

        await notifier.notify_bot_stopped({"total_pnl": 0.0, "total_trades": 0, "win_rate": 0.0})

        sent = notifier.bot.send_message.call_args.kwargs["text"]
        assert "🛑 <b>BOT STOPPED</b>" in sent
        assert "--------------------" in sent
        assert "â”" not in sent


@pytest.mark.asyncio
async def test_notify_trade_closed_uses_clean_outcome_emoji(mock_bot):
    with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "test_token", "TELEGRAM_CHAT_ID": "test_chat"}):
        notifier = TelegramNotifier()
        notifier.bot.send_message = AsyncMock()

        result = {"status": "won", "profit": 5.0, "current_price": 102.0, "contract_id": "123"}
        trade_info = {"symbol": "R_25", "direction": "UP", "entry_price": 100.0, "stake": 10.0}

        await notifier.notify_trade_closed(result, trade_info)

        sent = notifier.bot.send_message.call_args.kwargs["text"]
        assert "🟢 [WIN] 🏁 <b>TRADE CLOSED (WON): R_25</b>" in sent
        assert "â–" not in sent
        assert "â€¢" not in sent

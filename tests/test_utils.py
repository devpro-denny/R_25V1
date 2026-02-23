import asyncio
import logging
import math
import time
from typing import Any, Dict

import pytest

from utils import (
    setup_logger,
    StrategyUserFileRouterHandler,
    format_price,
    format_currency,
    format_percentage,
    format_timestamp,
    calculate_pnl,
    validate_api_response,
    parse_candle_data,
    safe_float,
    safe_int,
    truncate_string,
    get_signal_emoji,
    get_status_emoji,
    TokenBucket,
)


# -----------------------------
# Formatting helpers
# -----------------------------

def test_format_helpers_basic():
    assert format_price(123.4567) == "123.46"
    assert format_price(123.4, decimals=1) == "123.4"
    assert format_currency(12.5) == "$12.50"
    assert format_percentage(33.3333) == "33.33%"


def test_format_timestamp_current_monotonicity():
    # Calling without args should not raise and should produce a formatted string
    ts1 = format_timestamp()
    time.sleep(0.01)
    ts2 = format_timestamp()
    assert isinstance(ts1, str) and isinstance(ts2, str)
    # Strings follow YYYY-MM-DD HH:MM:SS length 19
    assert len(ts1) == 19 and len(ts2) == 19


# -----------------------------
# PnL calculation
# -----------------------------

def test_calculate_pnl_up_and_down():
    # UP direction: positive price change -> positive pnl
    pnl_up = calculate_pnl(entry_price=100.0, current_price=110.0, stake=10.0, multiplier=100, direction="UP")
    assert pytest.approx(pnl_up, rel=1e-6) == (10.0 / 100.0) * 10.0 * 100

    # DOWN direction: negative of price change
    pnl_down = calculate_pnl(entry_price=100.0, current_price=110.0, stake=10.0, multiplier=100, direction="DOWN")
    assert pytest.approx(pnl_down, rel=1e-6) == -(10.0 / 100.0) * 10.0 * 100


# -----------------------------
# Response validation and parsing
# -----------------------------

def test_validate_api_response():
    ok = {"msg_type": "balance", "balance": {"amount": 100}}
    assert validate_api_response(ok, expected_msg_type="balance") is True

    wrong_type = {"msg_type": "error_type"}
    assert validate_api_response(wrong_type, expected_msg_type="balance") is False

    has_error = {"error": {"code": "X", "message": "fail"}}
    assert validate_api_response(has_error, expected_msg_type="balance") is False

    not_dict = [1, 2, 3]
    assert validate_api_response(not_dict, expected_msg_type="balance") is False


def test_parse_candle_data():
    response = {
        "candles": [
            {"epoch": 1, "open": "1.0", "high": "2.0", "low": "0.5", "close": "1.5"},
            {"epoch": 2, "open": "1.5", "high": "2.5", "low": "1.0", "close": "2.0"},
        ]
    }
    parsed = parse_candle_data(response)
    assert parsed == [
        {"timestamp": 1, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5},
        {"timestamp": 2, "open": 1.5, "high": 2.5, "low": 1.0, "close": 2.0},
    ]
    assert parse_candle_data({}) == []


# -----------------------------
# Safe converters and truncation
# -----------------------------

def test_safe_converters_and_truncate():
    assert safe_float("12.34") == 12.34
    assert safe_float("x", default=1.23) == 1.23
    assert safe_float(None, default=-1.0) == -1.0

    assert safe_int("42") == 42
    assert safe_int("x", default=7) == 7
    assert safe_int(None, default=-2) == -2

    assert truncate_string("hello", max_length=10) == "hello"
    assert truncate_string("abcdefghijK", max_length=10) == "abcdefg..."


# -----------------------------
# Emojis helpers
# -----------------------------

def test_signal_and_status_emojis():
    assert get_signal_emoji("BUY") == "🟢"
    assert get_signal_emoji("sell") == "🔴"
    assert get_signal_emoji(None) == "⚪"
    assert get_signal_emoji(123) == "⚪"  # not mapped -> default

    assert get_status_emoji("open") == "📊"
    assert get_status_emoji("WON") == "✅"
    assert get_status_emoji(0) == "❓"  # not mapped -> default
    assert get_status_emoji(None) == "❓"


# -----------------------------
# Logger: ensure handlers and filter don't raise
# -----------------------------

def test_setup_logger_reuse_and_filter(monkeypatch):
    # Inject a user_id via contextvar to ensure formatter has value
    from app.core.context import user_id_var
    token = user_id_var.set("user-1")
    try:
        logger = setup_logger(level="DEBUG")
        # Re-calling shouldn't add duplicate handlers
        logger2 = setup_logger(level="DEBUG")
        assert logger is logger2

        # Ensure logger can emit without raising and includes user_id in record
        logger.info("test message")
        # Verify the filter injects user_id by making a custom handler to capture records
        captured = {}

        class Capture(logging.Handler):
            def emit(self, record):
                captured["user_id"] = getattr(record, "user_id", None)
                captured["msg"] = record.getMessage()

        h = Capture()
        logger.addHandler(h)
        logger.info("hello")
        logger.info("âœ… repaired")
        logger.removeHandler(h)

        assert captured["user_id"] == "user-1"
        assert captured["msg"] == "✅ repaired"
    finally:
        user_id_var.reset(token)


def test_strategy_user_file_router_handler_resolves_per_user_paths():
    fmt = logging.Formatter("%(message)s")
    handler = StrategyUserFileRouterHandler(fmt)

    rec_conservative = logging.LogRecord("x", logging.INFO, __file__, 1, "m", args=(), exc_info=None)
    rec_conservative.user_id = "user-a"
    rec_conservative.bot_type = "conservative"

    rec_scalping = logging.LogRecord("x", logging.INFO, __file__, 1, "m", args=(), exc_info=None)
    rec_scalping.user_id = "user_b"
    rec_scalping.bot_type = "scalping"

    rec_risefall = logging.LogRecord("x", logging.INFO, __file__, 1, "m", args=(), exc_info=None)
    rec_risefall.user_id = "user.c"
    rec_risefall.bot_type = "risefall"

    rec_system = logging.LogRecord("x", logging.INFO, __file__, 1, "m", args=(), exc_info=None)
    rec_system.user_id = "user-z"
    rec_system.bot_type = "system"

    assert handler._resolve_log_path(rec_conservative).replace("\\", "/") == "logs/conservative/user-a.log"
    assert handler._resolve_log_path(rec_scalping).replace("\\", "/") == "logs/scalping/user_b.log"
    assert handler._resolve_log_path(rec_risefall).replace("\\", "/") == "logs/risefall/user.c.log"
    assert handler._resolve_log_path(rec_system).replace("\\", "/") == "logs/system/multiplier_system.log"


# -----------------------------
# TokenBucket rate-limiter
# -----------------------------

def test_token_bucket_allows_capacity_burst_then_refills():
    async def _run():
        bucket = TokenBucket(rate=5.0, capacity=5.0)

        # Initially should have full capacity available
        assert bucket.available_tokens() == pytest.approx(5.0, rel=1e-3, abs=1e-3)

        # Consume all 5 tokens without awaiting (simulate immediate calls) using acquire
        for _ in range(5):
            await bucket.acquire(1.0)

        # After consuming, available should be near 0 (non-negative)
        assert bucket.available_tokens() >= 0.0
        assert bucket.available_tokens() <= 0.2  # tiny accrual okay

        # Wait ~0.5s -> expect ~2.5 tokens to be available (within tolerance)
        await asyncio.sleep(0.5)
        avail = bucket.available_tokens()
        assert 2.0 <= avail <= 3.5

    asyncio.run(_run())


def test_token_bucket_blocks_until_tokens_available():
    async def _run():
        bucket = TokenBucket(rate=2.0, capacity=2.0)

        # Use up current capacity
        await bucket.acquire(2.0)

        start = time.perf_counter()
        # Request 1 token; with rate 2/s, should take about 0.5s
        await bucket.acquire(1.0)
        elapsed = time.perf_counter() - start

        assert elapsed >= 0.45  # allow some scheduler jitter
        assert elapsed <= 1.0

    asyncio.run(_run())


# -----------------------------
# Additional behaviors
# -----------------------------

def test_signal_emojis_additional_mappings():
    assert get_signal_emoji("UP") == "⬆️"
    assert get_signal_emoji("down") == "⬇️"
    assert get_signal_emoji("HOLD") == "⚪"
    assert get_signal_emoji("unknown") == "⚪"  # default fallback


def test_status_emojis_additional_mappings():
    assert get_status_emoji("closed") == "🔒"
    assert get_status_emoji("cancelled") == "⛔"
    assert get_status_emoji("unknown") == "❓"
    assert get_status_emoji("unexpected") == "❓"  # default fallback


def test_calculate_lot_size_basic_rounding():
    from utils import calculate_lot_size
    lot = calculate_lot_size(balance=1000.0, risk_percent=2.5, stop_loss_pips=50.0, pip_value=0.1)
    # risk_amount = 1000 * 0.025 = 25; denominator = 50 * 0.1 = 5; lot = 25/5 = 5.0 -> rounded to 5.0
    assert lot == 5.0


def test_is_market_open_always_true():
    from utils import is_market_open
    assert is_market_open() is True


def test_print_statistics_output_formatting(capsys):
    from utils import print_statistics
    stats = {
        'total_trades': 5,
        'winning_trades': 3,
        'losing_trades': 2,
        'total_pnl': 12.34,
        'max_drawdown': -5.67,
        'largest_win': 9.0,
        'largest_loss': -3.21
    }
    print_statistics(stats)
    out = capsys.readouterr().out
    assert "TRADING STATISTICS" in out
    assert "Total Trades: 5" in out
    assert "Wins: 3 | Losses: 2" in out
    assert "Win Rate: 60.00%" in out
    assert "Total P&L: $12.34" in out
    assert "Max Drawdown: $-5.67" in out
    assert "Largest Win: $9.00" in out
    assert "Largest Loss: $-3.21" in out

import pytest
import asyncio
import json
from unittest.mock import patch, AsyncMock, MagicMock
from datetime import datetime
import risefallbot.rf_bot as rf_bot
from risefallbot.rf_risk_manager import RiseFallRiskManager

# ===========================================================================
# RiseFall Bot Units
# ===========================================================================

@pytest.fixture(autouse=True)
def reset_globals():
    rf_bot._running = False
    rf_bot._bot_task = None
    rf_bot._locked_symbol = None
    yield
    rf_bot._running = False
    rf_bot._bot_task = None

@pytest.mark.asyncio
async def test_fetch_user_config_supabase():
    mock_supabase = MagicMock()
    mock_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
        {"deriv_api_key": "MOCK_KEY", "stake_amount": 5.0}
    ]
    with patch("app.core.supabase.supabase", mock_supabase), \
         patch("risefallbot.rf_bot.os.getenv", return_value=None):
        config_data = await rf_bot._fetch_user_config()
        assert config_data["api_token"] == "MOCK_KEY"
        assert config_data["stake"] == 5.0

@pytest.mark.asyncio
async def test_acquire_session_lock_success():
    mock_supabase = MagicMock()
    mock_supabase.table.return_value.insert.return_value.execute.return_value.data = [{"id": 1}]
    with patch("app.core.supabase.supabase", mock_supabase), \
         patch("risefallbot.rf_bot.rf_config.RF_ENFORCE_DB_LOCK", True):
        success = await rf_bot._acquire_session_lock("user123")
        assert success is True

@pytest.mark.asyncio
async def test_process_symbol_lifecycle_failure():
    rm = RiseFallRiskManager()
    mock_em = AsyncMock()
    mock_em.broadcast = AsyncMock()
    
    mock_df = AsyncMock()
    mock_df.fetch_tick_history.return_value = MagicMock(empty=False)
    
    mock_strategy = MagicMock()
    mock_strategy.analyze.return_value = {
        "direction": "CALL",
        "stake": 1.0,
        "duration": 3,
        "duration_unit": "t",
        "trade_label": "RISE",
        "sequence_direction": "down",
        "tick_sequence": [100.4, 100.3, 100.2, 100.1, 100.2, 100.2],
        "sequence_signature": "sig-combined",
    }
    
    calls = {"n": 0}

    async def _broadcast_side_effect(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise Exception("Lifecycle crash")
        return None

    mock_em.broadcast.side_effect = _broadcast_side_effect
    mock_te = AsyncMock()
    
    with patch("risefallbot.rf_bot.logger"):
        await rf_bot._process_symbol("stpRNG1", mock_strategy, rm, mock_df, mock_te, 1.0, "user123", mock_em, MagicMock())
    
    # In rf_bot.py, "lifecycle error" is in the transient list, so it clears the halt in finally
    assert not rm.is_halted()

# ===========================================================================
# DataFetcher Units
# ===========================================================================
from data_fetcher import DataFetcher

@pytest.fixture
def fetcher():
    return DataFetcher(api_token="TEST_TOKEN")

@pytest.mark.asyncio
async def test_fetch_tick_success(fetcher):
    mock_ws = AsyncMock()
    mock_ws.closed = False
    mock_ws.send = AsyncMock()
    tick_data = json.dumps({"tick": {"quote": 1234.56, "symbol": "R_25"}})
    mock_ws.recv = AsyncMock(return_value=tick_data)
    fetcher.ws = mock_ws
    fetcher.is_connected = True
    
    with patch.object(DataFetcher, "ensure_connected", return_value=True):
        tick = await fetcher.fetch_tick("R_25")
        assert tick == 1234.56

@pytest.mark.asyncio
async def test_get_balance_success(fetcher):
    mock_ws = AsyncMock()
    mock_ws.closed = False
    balance_data = json.dumps({"balance": {"balance": 10000.0}})
    mock_ws.recv = AsyncMock(return_value=balance_data)
    fetcher.ws = mock_ws
    fetcher.is_connected = True
    
    with patch.object(DataFetcher, "ensure_connected", return_value=True):
        balance = await fetcher.get_balance()
        assert balance == 10000.0

@pytest.mark.asyncio
async def test_authorize_success(fetcher):
    mock_ws = AsyncMock()
    mock_ws.closed = False
    auth_data = json.dumps({"authorize": {"balance": 100.0}})
    mock_ws.recv = AsyncMock(return_value=auth_data)
    fetcher.ws = mock_ws
    fetcher.is_connected = True
    
    success = await fetcher.authorize()
    assert success is True

@pytest.mark.asyncio
async def test_fetch_candles_success(fetcher):
    mock_ws = AsyncMock()
    mock_ws.closed = False
    mock_ws.send = AsyncMock()
    # Mocking Deriv response format for ticks_history with style: candles
    candle_data = json.dumps({
        "candles": [
            {"open": "1.1", "high": "1.2", "low": "1.0", "close": "1.15", "epoch": 1600000000}
        ]
    })
    mock_ws.recv = AsyncMock(return_value=candle_data)
    fetcher.ws = mock_ws
    fetcher.is_connected = True
    
    with patch.object(DataFetcher, "ensure_connected", return_value=True):
        df = await fetcher.fetch_candles("R_25", 60, 1)
        assert len(df) == 1
        assert df.iloc[0]["open"] == 1.1

@pytest.mark.asyncio
async def test_fetch_timeframe_success(fetcher):
    # Mocking fetch_candles to return a valid DataFrame
    import pandas as pd
    mock_df = pd.DataFrame({
        "timestamp": [1600000000],
        "open": [1.1],
        "high": [1.2],
        "low": [1.0],
        "close": [1.15]
    })
    mock_df["datetime"] = pd.to_datetime(mock_df["timestamp"], unit="s")
    
    with patch.object(DataFetcher, "fetch_candles", return_value=mock_df):
        df = await fetcher.fetch_timeframe("R_25", "1m", count=1)
        assert df is not None
        assert not df.empty
        assert "open" in df.columns

@pytest.mark.asyncio
async def test_write_trade_to_db_failure(fetcher):
    # Testing rf_bot function
    mock_service = MagicMock()
    mock_service.save_trade.return_value = False # Persistent failure
    
    with patch("risefallbot.rf_bot.rf_config.RF_DB_WRITE_MAX_RETRIES", 2), \
         patch("risefallbot.rf_bot.rf_config.RF_DB_WRITE_RETRY_DELAY", 0.001):
        success = await rf_bot._write_trade_to_db_with_retry(
            user_id="u1", contract_id="c1", symbol="stpRNG1", direction="CALL",
            stake_val=1.0, pnl=0.5, status="won", closure_reason="target",
            duration=3, duration_unit="t", result={}, settlement={},
            UserTradesService=mock_service
        )
        assert success is False

@pytest.mark.asyncio
async def test_process_symbol_win_lifecycle():
    rm = RiseFallRiskManager()
    mock_em = AsyncMock()
    mock_df = AsyncMock()
    
    import pandas as pd
    mock_frame = pd.DataFrame({"quote": [100.4, 100.3, 100.2, 100.1, 100.2, 100.2]})
    mock_df.fetch_tick_history.return_value = mock_frame
    
    mock_strategy = MagicMock()
    mock_strategy.analyze.return_value = {
        "direction": "CALL",
        "stake": 1.0,
        "duration": 3,
        "duration_unit": "t",
        "trade_label": "RISE",
        "sequence_direction": "down",
        "tick_sequence": [100.4, 100.3, 100.2, 100.1, 100.2, 100.2],
        "sequence_signature": "sig-win",
    }
    
    mock_te = AsyncMock()
    mock_te.buy_rise_fall.return_value = {"contract_id": "c123", "buy_price": 10.0}
    mock_te.wait_for_result.return_value = {
        "profit": 0.5, "status": "won", "closure_type": "target", "sell_price": 10.5,
        "contract_id": "c123"
    }
    
    mock_uts = MagicMock()
    # Mock successful DB write
    with patch("risefallbot.rf_bot._write_trade_to_db_with_retry", return_value=True), \
         patch("risefallbot.rf_bot.logger"):
        await rf_bot._process_symbol("stpRNG1", mock_strategy, rm, mock_df, mock_te, 1.0, "user1", mock_em, mock_uts)
    
    assert not rm.is_halted()
    # Check that trade was recorded and then removed from active_trades
    assert len(rm.active_trades) == 0

"""
Unit tests for app.services.trades_service
Tests trade persistence, history fetching, and statistics calculation with Supabase and Cache mocking.
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime
from app.services.trades_service import UserTradesService

@pytest.fixture
def mock_supabase():
    with patch("app.services.trades_service.supabase") as mock:
        yield mock

@pytest.fixture
def mock_cache():
    with patch("app.services.trades_service.cache") as mock:
        yield mock

def test_save_trade_success(mock_supabase, mock_cache):
    """Test successful trade save path."""
    user_id = "user123"
    trade_data = {
        "contract_id": 12345,
        "symbol": "R_10",
        "signal": "UP",
        "stake": 10.0,
        "entry_price": 100.0,
        "exit_price": 105.0,
        "profit": 5.0,
        "status": "won",
        "timestamp": datetime(2023, 1, 1, 10, 0, 0)
    }
    
    # Mock Supabase response
    mock_response = MagicMock()
    mock_response.data = [{"id": 1}]
    mock_supabase.table.return_value.insert.return_value.execute.return_value = mock_response
    
    result = UserTradesService.save_trade(user_id, trade_data)
    
    assert result == {"id": 1}
    # Verify Supabase call
    mock_supabase.table.assert_called_with("trades")
    
    # Verify cache invalidation
    mock_cache.delete_pattern.assert_called_with(f"trades:{user_id}:*")
    mock_cache.delete.assert_any_call(f"stats:{user_id}")

def test_save_trade_missing_fields(mock_supabase, mock_cache):
    """Test save_trade with missing required fields."""
    user_id = "user123"
    # Missing contract_id
    trade_data = {"symbol": "R_10", "signal": "UP"}
    
    result = UserTradesService.save_trade(user_id, trade_data)
    
    assert result is None
    mock_supabase.table.assert_not_called()

def test_save_trade_exception(mock_supabase, mock_cache):
    """Test save_trade when Supabase fails."""
    user_id = "user123"
    trade_data = {"contract_id": 1, "symbol": "R_10", "signal": "UP"}
    
    mock_supabase.table.side_effect = Exception("DB Error")
    
    result = UserTradesService.save_trade(user_id, trade_data)
    
    assert result is None

def test_save_trade_duplicate_contract_updates_existing(mock_supabase, mock_cache):
    """Duplicate contract insert should fall back to update."""
    user_id = "user123"
    trade_data = {
        "contract_id": "c-1",
        "symbol": "R_10",
        "direction": "CALL",
        "profit": 1.2,
        "status": "won",
    }

    insert_chain = mock_supabase.table.return_value.insert.return_value
    insert_chain.execute.side_effect = Exception("duplicate key value violates unique constraint trades_contract_id_key")

    update_response = MagicMock()
    update_response.data = [{"id": 99, "contract_id": "c-1", "status": "won"}]
    (
        mock_supabase.table.return_value.update.return_value.eq.return_value.eq.return_value.execute
        .return_value
    ) = update_response

    result = UserTradesService.save_trade(user_id, trade_data)

    assert result == {"id": 99, "contract_id": "c-1", "status": "won"}
    mock_cache.delete_pattern.assert_called_with(f"trades:{user_id}:*")
    mock_cache.delete.assert_any_call(f"stats:{user_id}")
    mock_cache.delete.assert_any_call(f"trades:{user_id}:active")

def test_save_trade_normalizes_realized_open_status(mock_supabase, mock_cache):
    """Realized trades must not be persisted as open."""
    user_id = "user123"
    trade_data = {
        "contract_id": "c-realized-1",
        "symbol": "R_10",
        "direction": "CALL",
        "profit": -2.5,
        "exit_price": 99.2,
        "status": "open",
    }

    mock_response = MagicMock()
    mock_response.data = [{"id": 77, "status": "loss"}]
    mock_supabase.table.return_value.insert.return_value.execute.return_value = mock_response

    result = UserTradesService.save_trade(user_id, trade_data)

    assert result == {"id": 77, "status": "loss"}
    payload = mock_supabase.table.return_value.insert.call_args.args[0]
    assert payload["status"] == "loss"

def test_save_trade_uses_sell_time_when_timestamp_missing(mock_supabase, mock_cache):
    """Realized trades should persist a timestamp from broker sell_time."""
    user_id = "user123"
    trade_data = {
        "contract_id": "c-sell-time-1",
        "symbol": "R_25",
        "direction": "DOWN",
        "profit": 1.25,
        "status": "won",
        "sell_time": 1700000000,
    }

    mock_response = MagicMock()
    mock_response.data = [{"id": 91, "status": "win"}]
    mock_supabase.table.return_value.insert.return_value.execute.return_value = mock_response

    result = UserTradesService.save_trade(user_id, trade_data)

    assert result == {"id": 91, "status": "win"}
    payload = mock_supabase.table.return_value.insert.call_args.args[0]
    assert payload["timestamp"] == datetime.fromtimestamp(1700000000).isoformat()

def test_save_trade_uses_open_time_when_timestamp_missing(mock_supabase, mock_cache):
    """Fallback timestamps should be derived from open_time/date_start style fields."""
    user_id = "user123"
    trade_data = {
        "contract_id": "c-open-time-1",
        "symbol": "R_50",
        "direction": "UP",
        "profit": -0.75,
        "status": "closed",
        "open_time": datetime(2026, 3, 6, 10, 15, 0),
    }

    mock_response = MagicMock()
    mock_response.data = [{"id": 92, "status": "loss"}]
    mock_supabase.table.return_value.insert.return_value.execute.return_value = mock_response

    result = UserTradesService.save_trade(user_id, trade_data)

    assert result == {"id": 92, "status": "loss"}
    payload = mock_supabase.table.return_value.insert.call_args.args[0]
    assert payload["timestamp"] == "2026-03-06T10:15:00"

def test_save_trade_defaults_entry_source_and_derives_multiplier(mock_supabase, mock_cache):
    """Completed trades should not persist null metadata when it can be inferred."""
    user_id = "user123"
    trade_data = {
        "contract_id": "c-meta-1",
        "symbol": "R_25",
        "direction": "UP",
        "profit": 2.5,
        "status": "won",
    }

    mock_response = MagicMock()
    mock_response.data = [{"id": 93, "status": "win"}]
    mock_supabase.table.return_value.insert.return_value.execute.return_value = mock_response

    result = UserTradesService.save_trade(user_id, trade_data)

    assert result == {"id": 93, "status": "win"}
    payload = mock_supabase.table.return_value.insert.call_args.args[0]
    assert payload["entry_source"] == "system"
    assert payload["multiplier"] == 160.0

def test_save_trade_retries_without_optional_columns_on_schema_cache_error(mock_supabase, mock_cache):
    """PGRST204 schema-cache column errors should retry without optional fields."""
    user_id = "user123"
    trade_data = {
        "contract_id": "c-compat-1",
        "symbol": "R_10",
        "direction": "CALL",
        "status": "open",
        "entry_source": "manual_imported",
        "multiplier": 50,
    }

    insert_chain = mock_supabase.table.return_value.insert.return_value
    insert_chain.execute.side_effect = [
        Exception(
            "{'code': 'PGRST204', 'message': \"Could not find the 'entry_source' column of 'trades' in the schema cache\"}"
        ),
        MagicMock(data=[{"id": 88, "contract_id": "c-compat-1", "status": "open"}]),
    ]

    result = UserTradesService.save_trade(user_id, trade_data)

    assert result == {"id": 88, "contract_id": "c-compat-1", "status": "open"}
    first_payload = mock_supabase.table.return_value.insert.call_args_list[0].args[0]
    second_payload = mock_supabase.table.return_value.insert.call_args_list[1].args[0]
    assert "entry_source" in first_payload
    assert "multiplier" in first_payload
    assert "entry_source" not in second_payload
    assert "multiplier" not in second_payload

def test_track_active_trade_upsert_success(mock_supabase, mock_cache):
    """Open trade tracking should upsert an 'open' record."""
    user_id = "user123"
    trade_data = {
        "contract_id": "c-open-1",
        "symbol": "R_25",
        "direction": "CALL",
        "stake": 10.0,
        "entry_price": 100.0,
    }

    mock_response = MagicMock()
    mock_response.data = [{"contract_id": "c-open-1", "status": "open"}]
    mock_supabase.table.return_value.upsert.return_value.execute.return_value = mock_response

    result = UserTradesService.track_active_trade(user_id, trade_data)

    assert result == {"contract_id": "c-open-1", "status": "open"}
    payload = mock_supabase.table.return_value.upsert.call_args.args[0]
    assert payload["status"] == "open"
    assert payload["signal"] == "UP"
    assert payload["entry_source"] == "system"
    assert payload["multiplier"] == 160.0
    mock_cache.delete_pattern.assert_called_with(f"trades:{user_id}:*")
    mock_cache.delete.assert_any_call(f"stats:{user_id}")
    mock_cache.delete.assert_any_call(f"trades:{user_id}:active")

def test_track_active_trade_does_not_reopen_settled_row(mock_supabase, mock_cache):
    """Active tracker should not overwrite already-settled trades back to open."""
    user_id = "user123"
    trade_data = {
        "contract_id": "c-closed-1",
        "symbol": "R_25",
        "direction": "CALL",
        "stake": 10.0,
        "entry_price": 100.0,
    }

    existing_response = MagicMock()
    existing_response.data = [{
        "contract_id": "c-closed-1",
        "status": "open",
        "profit": 1.2,
        "exit_price": 101.2,
    }]
    (
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value
        .limit.return_value.execute.return_value
    ) = existing_response

    result = UserTradesService.track_active_trade(user_id, trade_data)

    assert result is not None
    assert result["status"] == "win"
    assert str(result["contract_id"]) == "c-closed-1"
    mock_supabase.table.return_value.upsert.assert_not_called()


def test_update_active_trade_exit_controls_success(mock_supabase, mock_cache):
    """Exit-control updates should persist for open trades and invalidate caches."""
    user_id = "user123"
    contract_id = "c-open-1"
    mock_response = MagicMock()
    mock_response.data = [{"contract_id": contract_id, "trailing_enabled": True, "stagnation_enabled": False}]
    (
        mock_supabase.table.return_value.update.return_value.eq.return_value.eq.return_value
        .eq.return_value.execute.return_value
    ) = mock_response

    result = UserTradesService.update_active_trade_exit_controls(
        user_id=user_id,
        contract_id=contract_id,
        trailing_enabled=1,
        stagnation_enabled="off",
    )

    assert result == {"contract_id": contract_id, "trailing_enabled": True, "stagnation_enabled": False}
    payload = mock_supabase.table.return_value.update.call_args.args[0]
    assert payload == {"trailing_enabled": True, "stagnation_enabled": False}
    mock_cache.delete_pattern.assert_called_with(f"trades:{user_id}:*")
    mock_cache.delete.assert_any_call(f"stats:{user_id}")
    mock_cache.delete.assert_any_call(f"trades:{user_id}:active")


def test_update_active_trade_exit_controls_no_payload_returns_none(mock_supabase, mock_cache):
    """No-op payloads should not call Supabase."""
    result = UserTradesService.update_active_trade_exit_controls(
        user_id="user123",
        contract_id="c-open-1",
        trailing_enabled=None,
        stagnation_enabled=None,
    )
    assert result is None
    mock_supabase.table.assert_not_called()


def test_update_active_trade_exit_controls_missing_column_graceful(mock_supabase, mock_cache):
    """Schema-missing optional toggle columns should fail gracefully and return None."""
    chain = (
        mock_supabase.table.return_value.update.return_value.eq.return_value.eq.return_value
        .eq.return_value.execute
    )
    chain.side_effect = Exception("Could not find the 'trailing_enabled' column of 'trades' in the schema cache")

    result = UserTradesService.update_active_trade_exit_controls(
        user_id="user123",
        contract_id="c-open-1",
        trailing_enabled=False,
    )

    assert result is None

def test_get_user_active_trades_cache_hit(mock_supabase, mock_cache):
    """Active trades should be returned from cache when present."""
    user_id = "user123"
    cached = [{"contract_id": "x", "status": "open"}]
    mock_cache.get.return_value = cached

    result = UserTradesService.get_user_active_trades(user_id)

    assert result == cached
    mock_supabase.table.assert_not_called()

def test_get_user_active_trades_cache_miss(mock_supabase, mock_cache):
    """Active trades should be loaded from DB and cached on miss."""
    user_id = "user123"
    mock_cache.get.return_value = None
    db_rows = [{"contract_id": "x1", "status": "open"}]
    mock_response = MagicMock()
    mock_response.data = db_rows
    (
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value
        .order.return_value.limit.return_value.execute.return_value
    ) = mock_response

    result = UserTradesService.get_user_active_trades(user_id, limit=5)

    assert result == db_rows
    mock_cache.set.assert_called_once()

def test_get_user_active_trades_repairs_stale_open_rows(mock_supabase, mock_cache):
    """Rows marked open with realized P/L should be repaired and excluded from active list."""
    user_id = "user123"
    mock_cache.get.return_value = None
    db_rows = [
        {"contract_id": "c-open", "status": "open", "profit": None, "exit_price": None},
        {"contract_id": "c-stale", "status": "open", "profit": -1.0, "exit_price": 99.0},
    ]
    select_response = MagicMock()
    select_response.data = db_rows
    (
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value
        .order.return_value.limit.return_value.execute.return_value
    ) = select_response

    result = UserTradesService.get_user_active_trades(user_id, limit=10)

    assert result == [{"contract_id": "c-open", "status": "open", "profit": None, "exit_price": None}]
    update_payload = mock_supabase.table.return_value.update.call_args.args[0]
    assert update_payload["status"] == "loss"
    mock_cache.set.assert_called_once()

def test_get_user_trades_cache_hit(mock_supabase, mock_cache):
    """Test fetching trades from cache."""
    user_id = "user123"
    cached_trades = [{"id": 1, "symbol": "R_10"}]
    mock_cache.get.return_value = cached_trades
    
    result = UserTradesService.get_user_trades(user_id)
    
    assert result == cached_trades
    mock_supabase.table.assert_not_called()

def test_get_user_trades_cache_miss(mock_supabase, mock_cache):
    """Test fetching trades from DB on cache miss."""
    user_id = "user123"
    mock_cache.get.return_value = None
    
    db_trades = [{"id": 1, "symbol": "R_10"}]
    mock_response = MagicMock()
    mock_response.data = db_trades
    mock_supabase.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = mock_response
    
    result = UserTradesService.get_user_trades(user_id, limit=10)
    
    assert result == db_trades
    # Verify cache set
    mock_cache.set.assert_called()

def test_get_user_stats_cache_hit(mock_supabase, mock_cache):
    """Test fetching stats from cache."""
    user_id = "user123"
    cached_stats = {"total_trades": 10, "win_rate": 60.0}
    mock_cache.get.return_value = cached_stats
    
    result = UserTradesService.get_user_stats(user_id)
    
    assert result == cached_stats
    mock_supabase.table.assert_not_called()

def test_get_user_stats_calculation(mock_supabase, mock_cache):
    """Test stats calculation from trade history."""
    user_id = "user123"
    mock_cache.get.return_value = None
    
    trades = [
        {"profit": 10.0},  # Win
        {"profit": -5.0},  # Loss
        {"profit": 15.0},  # Win
        {"profit": None},   # Invalid (ignored)
    ]
    mock_response = MagicMock()
    mock_response.data = trades
    mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = mock_response
    
    stats = UserTradesService.get_user_stats(user_id)
    
    assert stats["total_trades"] == 4
    assert stats["winning_trades"] == 2
    assert stats["losing_trades"] == 1
    assert stats["win_rate"] == 50.0  # (2 / 4) * 100
    assert stats["total_pnl"] == 20.0 # 10 - 5 + 15
    assert stats["avg_win"] == 12.5  # (10 + 15) / 2
    assert stats["avg_loss"] == 5.0
    assert stats["largest_win"] == 15.0
    assert stats["largest_loss"] == 5.0
    assert stats["profit_factor"] == 5.0 # 25 / 5

def test_get_user_stats_no_trades(mock_supabase, mock_cache):
    """Test stats calculation with no trades."""
    user_id = "user123"
    mock_cache.get.return_value = None
    
    mock_response = MagicMock()
    mock_response.data = []
    mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = mock_response
    
    stats = UserTradesService.get_user_stats(user_id)
    
    assert stats["total_trades"] == 0
    assert stats["win_rate"] == 0.0
    assert stats["total_pnl"] == 0.0

def test_get_user_stats_profit_factor_zero_loss(mock_supabase, mock_cache):
    """Test profit factor when there are only wins (no losses)."""
    user_id = "user123"
    mock_cache.get.return_value = None
    
    trades = [{"profit": 10.0}, {"profit": 20.0}]
    mock_response = MagicMock()
    mock_response.data = trades
    mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = mock_response
    
    stats = UserTradesService.get_user_stats(user_id)
    
    assert stats["losing_trades"] == 0
    assert stats["profit_factor"] == 0.0 # Per implementation logic (safety)

def test_get_user_stats_exception(mock_supabase, mock_cache):
    """Test stats calculation error fallback."""
    user_id = "user123"
    mock_cache.get.return_value = None
    mock_supabase.table.side_effect = Exception("DB Fail")
    
    stats = UserTradesService.get_user_stats(user_id)
    
    assert stats["total_trades"] == 0
    assert stats["win_rate"] == 0.0

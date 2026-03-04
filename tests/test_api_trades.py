"""
Unit tests for app.api.trades
Tests API endpoints with mocked auth and service layers.
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch
from app.core.auth import get_current_active_user
from app.main import app  # Import your FastAPI app

client = TestClient(app)
API_PREFIX = "/api/v1/trades"

@pytest.fixture(autouse=True)
def override_auth():
    """Override the authentication dependency for all tests."""
    app.dependency_overrides[get_current_active_user] = lambda: {"id": "user123", "email": "test@example.com", "role": "user"}
    yield
    app.dependency_overrides.clear()

def test_get_active_trades():
    """Test /active endpoint."""
    with patch("app.api.trades.bot_manager") as mock_bm:
        mock_bot = MagicMock()
        mock_bot.state.get_active_trades.return_value = [{
            "contract_id": 123, 
            "symbol": "R_10",
            "direction": "CALL",
            "status": "open",
            "stake": 10.0
        }]
        mock_bm.get_bot.return_value = mock_bot
        
        response = client.get(f"{API_PREFIX}/active")
        
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["contract_id"] == "123" # Serialized to string

def test_get_active_trades_falls_back_to_db_when_bot_not_running():
    """When no bot instance exists, /active should return persisted open trades."""
    with patch("app.api.trades.bot_manager") as mock_bm, \
         patch("app.api.trades.UserTradesService.get_user_active_trades") as mock_active_db:
        mock_bm._bots = {}
        mock_active_db.return_value = [{
            "contract_id": "789",
            "symbol": "R_25",
            "direction": "UP",
            "status": "open",
            "stake": 12.0,
        }]

        response = client.get(f"{API_PREFIX}/active")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["contract_id"] == "789"
        mock_active_db.assert_called_once_with("user123")

def test_get_trade_history():
    """Test /history endpoint."""
    with patch("app.services.trades_service.UserTradesService.get_user_trades") as mock_get:
        mock_get.return_value = [{
            "contract_id": 456, 
            "symbol": "R_10",
            "direction": "PUT",
            "strategy_type": "Scalping",
            "status": "won",
            "profit": 5.0
        }]
        
        response = client.get(f"{API_PREFIX}/history?limit=10")
        
        assert response.status_code == 200
        data = response.json()
        assert data[0]["contract_id"] == "456"
        assert data[0]["strategy_type"] == "Scalping"

def test_get_trade_stats():
    """Test /stats endpoint."""
    with patch("app.services.trades_service.UserTradesService.get_user_stats") as mock_stats:
        mock_stats.return_value = {
            "total_trades": 5, 
            "winning_trades": 4,
            "losing_trades": 1,
            "win_rate": 80.0,
            "total_pnl": 15.0,
            "daily_pnl": 5.0,
            "avg_win": 4.0,
            "avg_loss": 1.0,
            "largest_win": 5.0,
            "largest_loss": 1.0,
            "profit_factor": 4.0
        }
        
        response = client.get(f"{API_PREFIX}/stats")
        
        assert response.status_code == 200
        data = response.json()
        assert data["total_trades"] == 5

def test_get_trade_stats_empty():
    """Test /stats endpoint when service returns None."""
    with patch("app.services.trades_service.UserTradesService.get_user_stats") as mock_stats:
        mock_stats.return_value = None
        
        response = client.get(f"{API_PREFIX}/stats")
        
        assert response.status_code == 200
        data = response.json()
        assert data["total_trades"] == 0

def test_get_trade_stats_error():
    """Test /stats endpoint on exception."""
    with patch("app.services.trades_service.UserTradesService.get_user_stats") as mock_stats:
        mock_stats.side_effect = Exception("Service error")
        
        response = client.get(f"{API_PREFIX}/stats")
        
        assert response.status_code == 500

def test_debug_trade_stats_with_data():
    """Test /stats/debug endpoint with actual trade data to cover calculations."""
    with patch("app.core.supabase.supabase") as mock_supa, \
         patch("app.core.cache.cache") as mock_cache:
        
        # Mock Supabase
        mock_supa.table.return_value.select.return_value.eq.return_value.execute.return_value.count = 2
        mock_supa.table.return_value.select.return_value.eq.return_value.order.return_value.execute.return_value.data = [
            {"contract_id": 1, "symbol": "R_10", "profit": 10.0, "status": "won", "timestamp": "2026-02-20T10:00:00Z"},
            {"contract_id": 2, "symbol": "R_10", "profit": -5.0, "status": "lost", "timestamp": "2026-02-20T11:00:00Z"}
        ]
        
        # Mock Cache
        mock_cache.get.return_value = {"cached": "data"}
        
        response = client.get(f"{API_PREFIX}/stats/debug")
        
        assert response.status_code == 200
        data = response.json()
        assert data["database_queries"]["fetched_count"] == 2
        assert data["calculations"]["win_count"] == 1
        assert data["calculations"]["loss_count"] == 1
        assert data["calculations"]["total_pnl"] == 5.0
        assert "date_analysis" in data
        assert data["date_analysis"]["oldest_trade"] == "2026-02-20T10:00:00Z"

def test_debug_trade_stats_error_injection():
    """Test /stats/debug endpoint with error injection in different stages."""
    with patch("app.core.supabase.supabase") as mock_supa, \
         patch("app.core.cache.cache") as mock_cache, \
         patch("app.services.trades_service.UserTradesService.get_user_stats") as mock_service:
        
        # Inject error in database query stage
        mock_supa.table.side_effect = Exception("DB error")
        mock_cache.get.side_effect = Exception("Cache error")
        mock_service.side_effect = Exception("Service error")
        
        response = client.get(f"{API_PREFIX}/stats/debug")
        
        assert response.status_code == 200 # Catch-all handler returns 200 with error list
        data = response.json()
        assert len(data.get("errors", [])) > 0
        stages = [e["stage"] for e in data["errors"]]
        assert "database_queries" in stages
        assert "cache_check" in stages
        assert "service_method" in stages

def test_debug_trade_stats_critical_failure():
    """Test /stats/debug endpoint with a critical failure leading to catch-all handler."""
    # Temporarily change the dependency override to trigger KeyError on 103
    app.dependency_overrides[get_current_active_user] = lambda: {} # Missing 'id'
    
    try:
        response = client.get(f"{API_PREFIX}/stats/debug")
        
        assert response.status_code == 200
        data = response.json()
        assert "critical_error" in data
        assert "'id'" in data["critical_error"]
    finally:
        # Restore auth override
        app.dependency_overrides[get_current_active_user] = lambda: {"id": "user123", "email": "test@example.com", "role": "user"}


def test_update_active_trade_exit_controls_success():
    """Test toggling active-trade exit controls."""
    with patch("app.api.trades.bot_manager") as mock_bm:
        mock_risk_manager = MagicMock()
        mock_risk_manager.set_trade_exit_controls.return_value = {
            "contract_id": 308022298068,
            "trailing_enabled": False,
            "stagnation_enabled": True,
        }
        mock_bot = MagicMock()
        mock_bot.is_running = True
        mock_bot.risk_manager = mock_risk_manager
        mock_bm._bots = {"user123": mock_bot}

        response = client.patch(
            f"{API_PREFIX}/active/308022298068/exit-controls",
            json={"trailing_enabled": False},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["contract_id"] == "308022298068"
        assert data["trailing_enabled"] is False
        assert data["stagnation_enabled"] is True
        mock_risk_manager.set_trade_exit_controls.assert_called_once_with(
            contract_id="308022298068",
            trailing_enabled=False,
            stagnation_enabled=None,
        )


def test_update_active_trade_exit_controls_not_found():
    """Test exit-controls update when no running bot is available."""
    with patch("app.api.trades.bot_manager") as mock_bm:
        mock_bm._bots = {}

        response = client.patch(
            f"{API_PREFIX}/active/308022298068/exit-controls",
            json={"trailing_enabled": False},
        )

        assert response.status_code == 404
        assert "No running bot" in response.json()["detail"]


def test_register_manual_active_trade_success():
    """Manual contract registration should persist and return normalized trade payload."""
    with patch("app.api.trades.bot_manager") as mock_bm, \
         patch("app.api.trades.UserTradesService.track_active_trade") as mock_track, \
         patch("app.api.trades.event_manager.broadcast", new_callable=AsyncMock) as mock_broadcast:
        mock_risk_manager = MagicMock()
        mock_risk_manager.get_active_trade_info.return_value = None
        mock_bot = MagicMock()
        mock_bot.is_running = True
        mock_bot.risk_manager = mock_risk_manager
        mock_bot.strategy.get_strategy_name.return_value = "Scalping"
        mock_bot.state.active_trades = []
        mock_bot.telegram_bridge.notify_trade_opened = AsyncMock()
        mock_bm._bots = {"user123": mock_bot}

        mock_track.return_value = {
            "contract_id": "308022298068",
            "symbol": "R_50",
            "signal": "UP",
            "status": "open",
            "strategy_type": "Scalping",
            "stake": 10.0,
            "entry_price": 100.0,
            "timestamp": "2026-03-04T08:00:00",
        }

        response = client.post(
            f"{API_PREFIX}/active/manual",
            json={
                "open_contract_id": "308022298068",
                "symbol": "R_50",
                "direction": "CALL",
                "stake": 10.0,
                "entry_price": 100.0,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["contract_id"] == "308022298068"
        assert data["direction"] == "UP"
        assert data["strategy_type"] == "Scalping"
        assert data["status"] == "open"
        mock_track.assert_called_once()
        mock_risk_manager.record_trade_open.assert_called_once()
        mock_bot.telegram_bridge.notify_trade_opened.assert_awaited_once()
        mock_broadcast.assert_awaited_once()


def test_register_manual_active_trade_rejects_when_another_trade_is_active():
    """Manual registration should reject when runtime already tracks a different active contract."""
    with patch("app.api.trades.bot_manager") as mock_bm:
        mock_risk_manager = MagicMock()
        mock_risk_manager.get_active_trade_info.return_value = {"contract_id": "existing-1"}
        mock_bot = MagicMock()
        mock_bot.is_running = True
        mock_bot.risk_manager = mock_risk_manager
        mock_bot.strategy.get_strategy_name.return_value = "Conservative"
        mock_bm._bots = {"user123": mock_bot}

        response = client.post(
            f"{API_PREFIX}/active/manual",
            json={
                "open_contract_id": "new-1",
                "symbol": "R_75",
                "direction": "UP",
            },
        )

        assert response.status_code == 409
        assert "already has an active contract" in response.json()["detail"]


def test_register_manual_active_trade_rejects_when_active_trade_list_is_occupied():
    """Manual registration should also reject when active_trades list already has another contract."""
    with patch("app.api.trades.bot_manager") as mock_bm:
        mock_risk_manager = MagicMock()
        mock_risk_manager.active_trades = [{"contract_id": "existing-list-1", "symbol": "R_100"}]
        mock_risk_manager.get_active_trade_info.return_value = None
        mock_bot = MagicMock()
        mock_bot.is_running = True
        mock_bot.risk_manager = mock_risk_manager
        mock_bot.strategy.get_strategy_name.return_value = "Conservative"
        mock_bm._bots = {"user123": mock_bot}

        response = client.post(
            f"{API_PREFIX}/active/manual",
            json={
                "open_contract_id": "new-2",
                "symbol": "R_100",
                "direction": "UP",
            },
        )

        assert response.status_code == 409
        assert "already has an active contract" in response.json()["detail"]


def test_register_manual_active_trade_uses_running_stake_when_missing():
    """Manual registration should inherit configured running stake when payload stake is omitted."""
    with patch("app.api.trades.bot_manager") as mock_bm, \
         patch("app.api.trades.UserTradesService.track_active_trade") as mock_track, \
         patch("app.api.trades.event_manager.broadcast", new_callable=AsyncMock):
        mock_risk_manager = MagicMock()
        mock_risk_manager.active_trades = []
        mock_risk_manager.get_active_trade_info.return_value = None
        mock_bot = MagicMock()
        mock_bot.is_running = True
        mock_bot.risk_manager = mock_risk_manager
        mock_bot.user_stake = 15.0
        mock_bot.strategy.get_strategy_name.return_value = "Conservative"
        mock_bm._bots = {"user123": mock_bot}
        mock_track.return_value = {
            "contract_id": "manual-3",
            "symbol": "R_75",
            "signal": "UP",
            "status": "open",
            "strategy_type": "Conservative",
        }

        response = client.post(
            f"{API_PREFIX}/active/manual",
            json={
                "open_contract_id": "manual-3",
                "symbol": "R_75",
                "direction": "UP",
            },
        )

        assert response.status_code == 200
        saved_payload = mock_track.call_args.args[1]
        assert saved_payload["stake"] == 15.0

"""
Unit tests for app.api.trades
Tests API endpoints with mocked auth and service layers.
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch
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

import os
import sys
import pytest
import pandas as pd
import numpy as np
from unittest.mock import MagicMock, AsyncMock, patch

# Ensure project root is on sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Bootstrap required env vars at import-time so modules imported during
# test collection (before fixtures execute) can initialize safely.
_TEST_ENV_DEFAULTS = {
    "API_TOKEN": "fake_token",
    "APP_ID": "1089",
    "SUPABASE_URL": "https://fake.supabase.co",
    "SUPABASE_SERVICE_ROLE_KEY": (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJyb2xlIjoic2VydmljZV9yb2xlIiwiaXNzIjoic3VwYWJhc2UiLCJleHAiOjQ3NjUxMzI4MDB9."
        "testsignature"
    ),
    "DERIV_API_KEY_ENCRYPTION_SECRET": "test_deriv_api_key_secret",
    "TELEGRAM_BOT_TOKEN": "fake_bot_token",
    "TELEGRAM_CHAT_ID": "12345678",
}
for _key, _value in _TEST_ENV_DEFAULTS.items():
    os.environ.setdefault(_key, _value)

@pytest.fixture(autouse=True)
def mock_env_vars(monkeypatch):
    """Set default environment variables for tests"""
    monkeypatch.setenv("API_TOKEN", "fake_token")
    monkeypatch.setenv("APP_ID", "1089")
    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setenv(
        "SUPABASE_SERVICE_ROLE_KEY",
        (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJyb2xlIjoic2VydmljZV9yb2xlIiwiaXNzIjoic3VwYWJhc2UiLCJleHAiOjQ3NjUxMzI4MDB9."
            "testsignature"
        ),
    )
    monkeypatch.setenv("DERIV_API_KEY_ENCRYPTION_SECRET", "test_deriv_api_key_secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake_bot_token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345678")

@pytest.fixture
def mock_supabase(monkeypatch):
    """Mock Supabase client"""
    mock_client = MagicMock()
    # Setup common supabase response patterns
    mock_response = MagicMock()
    mock_response.data = []
    mock_response.error = None
    
    # Mock table interface
    table_mock = MagicMock()
    table_mock.select.return_value = table_mock
    table_mock.insert.return_value = table_mock
    table_mock.update.return_value = table_mock
    table_mock.delete.return_value = table_mock
    table_mock.eq.return_value = table_mock
    table_mock.execute.return_value = mock_response
    
    mock_client.table.return_value = table_mock
    
    monkeypatch.setattr("app.core.supabase.supabase", mock_client)
    return mock_client

@pytest.fixture
def mock_notifier(monkeypatch):
    """Mock Telegram notifier"""
    mock = MagicMock()
    mock.send_message = AsyncMock(return_value=True)
    mock.notify_trade_opened = AsyncMock() # corrected name
    mock.notify_trade_closed = AsyncMock() # corrected name
    mock.notify_error = AsyncMock()
    mock.enabled = True
    
    monkeypatch.setattr("telegram_notifier.notifier", mock)
    return mock

@pytest.fixture
def mock_deriv_api():
    """Mock Deriv API (websockets) to prevent all network activity"""
    with patch("data_fetcher.websockets.connect", new_callable=AsyncMock) as mock_connect:
        mock_ws = AsyncMock()
        mock_ws.send = AsyncMock()
        mock_ws.recv = AsyncMock()
        mock_ws.close = AsyncMock()
        mock_ws.closed = False
        mock_connect.return_value = mock_ws
        yield mock_connect, mock_ws

@pytest.fixture
def sample_ohlc_data():
    """Generate sample OHLC data for strategy testing"""
    def _generate(n=200, trend="bullish"): # Increased n default
        np.random.seed(42)
        # Use 'min' instead of 'T' (deprecated in pandas)
        dates = pd.date_range(end=pd.Timestamp.now(), periods=n, freq='min')
        
        if trend == "bullish":
            close_prices = 100 + np.cumsum(np.random.randn(n) * 0.1 + 0.05)
        elif trend == "bearish":
            close_prices = 100 + np.cumsum(np.random.randn(n) * 0.1 - 0.05)
        else: # sideways
            close_prices = 100 + np.cumsum(np.random.randn(n) * 0.1)
            
        df = pd.DataFrame({
            'timestamp': dates.view(np.int64) // 10**9,
            'datetime': dates,
            'open': close_prices - np.random.randn(n) * 0.02,
            'high': close_prices + abs(np.random.randn(n) * 0.05),
            'low': close_prices - abs(np.random.randn(n) * 0.05),
            'close': close_prices
        })
        return df
    return _generate

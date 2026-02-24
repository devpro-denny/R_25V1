import pytest
import asyncio
import logging
from unittest.mock import patch, AsyncMock, MagicMock
from datetime import datetime
from types import SimpleNamespace
import risefallbot.rf_bot as rf_bot
from risefallbot.rf_risk_manager import RiseFallRiskManager

@pytest.fixture(autouse=True)
def reset_globals():
    rf_bot._running = False
    rf_bot._bot_task = None
    if hasattr(rf_bot, "_running_by_user"):
        rf_bot._running_by_user.clear()
    if hasattr(rf_bot, "_bot_task_by_user"):
        rf_bot._bot_task_by_user.clear()
    rf_bot._decision_emit_state = {}
    rf_bot._locked_symbol = None
    if hasattr(rf_bot, "_lock_active"):
        rf_bot._lock_active = False
    yield
    rf_bot._running = False
    rf_bot._bot_task = None
    if hasattr(rf_bot, "_running_by_user"):
        rf_bot._running_by_user.clear()
    if hasattr(rf_bot, "_bot_task_by_user"):
        rf_bot._bot_task_by_user.clear()
    rf_bot._decision_emit_state = {}

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
async def test_fetch_user_config_specific_user():
    mock_supabase = MagicMock()
    mock_supabase.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = {
        "deriv_api_key": "USER_KEY",
        "stake_amount": 7.5,
    }

    with patch("app.core.supabase.supabase", mock_supabase), \
         patch("risefallbot.rf_bot.os.getenv", return_value=None):
        config_data = await rf_bot._fetch_user_config(user_id="user-abc")
        assert config_data["api_token"] == "USER_KEY"
        assert config_data["stake"] == 7.5

@pytest.mark.asyncio
async def test_fetch_user_config_env():
    # Mocking to return empty list to trigger fallback
    mock_supabase = MagicMock()
    mock_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = []
    
    with patch("app.core.supabase.supabase", mock_supabase), \
         patch("risefallbot.rf_bot.os.getenv") as mock_getenv, \
         patch("risefallbot.rf_bot.rf_config") as mock_cfg:
        
        mock_getenv.side_effect = lambda k, d=None: "ENV_KEY" if k == "DERIV_API_TOKEN" else d
        mock_cfg.RF_DEFAULT_STAKE = 2.0
        
        config_data = await rf_bot._fetch_user_config()
        assert config_data["api_token"] == "ENV_KEY"
        assert config_data["stake"] == 2.0

@pytest.mark.asyncio
async def test_acquire_session_lock_success():
    mock_supabase = MagicMock()
    # Mock insert success
    mock_supabase.table.return_value.insert.return_value.execute.return_value.data = [{"id": 1}]
    
    with patch("app.core.supabase.supabase", mock_supabase), \
         patch("risefallbot.rf_bot.rf_config.RF_ENFORCE_DB_LOCK", True):
        success = await rf_bot._acquire_session_lock("user123")
        assert success is True

@pytest.mark.asyncio
async def test_acquire_session_lock_duplicate():
    mock_supabase = MagicMock()
    # Mock insert exception for duplicate
    mock_supabase.table.return_value.insert.return_value.execute.side_effect = Exception("duplicate key")
    
    with patch("app.core.supabase.supabase", mock_supabase), \
         patch("risefallbot.rf_bot.rf_config.RF_ENFORCE_DB_LOCK", True):
        success = await rf_bot._acquire_session_lock("user123")
        assert success is False

@pytest.mark.asyncio
async def test_acquire_session_lock_reclaims_stale_row():
    mock_supabase = MagicMock()
    stale_started_at = "2000-01-01T00:00:00+00:00"
    table = mock_supabase.table.return_value
    select_query = MagicMock()
    table.select.return_value = select_query
    select_query.eq.return_value.limit.return_value.execute.return_value = SimpleNamespace(
        data=[{"user_id": "user123", "started_at": stale_started_at, "process_id": 42}]
    )
    table.insert.return_value.execute.return_value.data = [{"id": 1}]

    with patch("app.core.supabase.supabase", mock_supabase), \
         patch("risefallbot.rf_bot.rf_config.RF_ENFORCE_DB_LOCK", True), \
         patch("risefallbot.rf_bot.rf_config.RF_DB_LOCK_TTL_SECONDS", 60):
        success = await rf_bot._acquire_session_lock("user123")
        assert success is True

    assert table.delete.return_value.eq.return_value.execute.called

@pytest.mark.asyncio
async def test_acquire_session_lock_keeps_fresh_row():
    mock_supabase = MagicMock()
    fresh_started_at = datetime.now().isoformat()
    table = mock_supabase.table.return_value
    select_query = MagicMock()
    table.select.return_value = select_query
    select_query.eq.return_value.limit.return_value.execute.return_value = SimpleNamespace(
        data=[{"user_id": "user123", "started_at": fresh_started_at, "process_id": 42}]
    )
    table.insert.return_value.execute.side_effect = Exception("duplicate key")

    with patch("app.core.supabase.supabase", mock_supabase), \
         patch("risefallbot.rf_bot.rf_config.RF_ENFORCE_DB_LOCK", True), \
         patch("risefallbot.rf_bot.rf_config.RF_DB_LOCK_TTL_SECONDS", 3600):
        success = await rf_bot._acquire_session_lock("user123")
        assert success is False

    assert not table.delete.return_value.eq.return_value.execute.called

@pytest.mark.asyncio
async def test_write_trade_to_db_retry_success():
    mock_service = MagicMock()
    mock_service.save_trade.side_effect = [False, True]
    
    with patch("risefallbot.rf_bot.rf_config.RF_DB_WRITE_RETRY_DELAY", 0.01):
        success = await rf_bot._write_trade_to_db_with_retry(
            user_id="user123", contract_id="c1", symbol="R_25", direction="CALL",
            stake_val=1.0, pnl=0.5, status="won", closure_reason="target",
            duration=1, duration_unit="m", result={}, settlement={},
            UserTradesService=mock_service
        )
        assert success is True

@pytest.mark.asyncio
async def test_process_symbol_lifecycle_failure():
    rm = RiseFallRiskManager()
    mock_em = AsyncMock()
    mock_em.broadcast = AsyncMock()
    mock_uts = MagicMock()
    mock_strategy = MagicMock()
    
    mock_df = AsyncMock()
    # Ensure it doesn't return empty df to move past line 641
    mock_df.fetch_timeframe.return_value = MagicMock(empty=False)
    
    mock_strategy.analyze.return_value = {
        "direction": "CALL", "stake": 1.0, "duration": 5, "duration_unit": "m"
    }
    
    # Trigger one lifecycle broadcast failure, allow all later broadcasts
    calls = {"n": 0}
    async def _broadcast_side_effect(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise Exception("Lifecycle crash")
        return None
    mock_em.broadcast.side_effect = _broadcast_side_effect
    mock_te = AsyncMock()
    
    with patch("risefallbot.rf_bot.logger") as mock_logger:
        await rf_bot._process_symbol("R_25", mock_strategy, rm, mock_df, mock_te, 1.0, "user123", mock_em, mock_uts)
    
    # It should not be halted because "Lifecycle crash" is caught as a transient "lifecycle error"
    # and auto-cleared in the finally block.
    assert not rm.is_halted()

@pytest.mark.asyncio
async def test_bot_run_already_running():
    rf_bot._bot_task = MagicMock()
    rf_bot._bot_task.done.return_value = False # Bot IS running
    
    with patch("risefallbot.rf_bot._fetch_user_config") as mock_cfg:
        await rf_bot.run()
        mock_cfg.assert_not_called()

@pytest.mark.asyncio
async def test_bot_stop():
    rf_bot._running = True
    rf_bot.stop()
    assert rf_bot._running is False


def test_bot_stop_specific_user_does_not_stop_other_users():
    rf_bot._running_by_user["u1"] = True
    rf_bot._running_by_user["u2"] = True

    rf_bot.stop("u1")

    assert not rf_bot._running_by_user.get("u1", False)
    assert rf_bot._running_by_user.get("u2") is True

@pytest.mark.asyncio
async def test_process_symbol_no_data():
    rm = RiseFallRiskManager()
    mock_df = AsyncMock()
    mock_df.fetch_timeframe.return_value = None
    
    await rf_bot._process_symbol("R_25", MagicMock(), rm, mock_df, AsyncMock(), 1.0, "user123", AsyncMock(), MagicMock())
    assert not rm.is_halted()

@pytest.mark.asyncio
async def test_process_symbol_no_signal():
    rm = RiseFallRiskManager()
    mock_df = AsyncMock()
    mock_df.fetch_timeframe.return_value = MagicMock(empty=False)
    mock_strategy = MagicMock()
    mock_strategy.analyze.return_value = None
    mock_em = AsyncMock()
    mock_em.broadcast = AsyncMock()
    
    await rf_bot._process_symbol("R_25", mock_strategy, rm, mock_df, AsyncMock(), 1.0, "user123", mock_em, MagicMock())
    payloads = [c.args[0] for c in mock_em.broadcast.await_args_list if c.args]
    assert any(
        p.get("type") == "bot_decision"
        and p.get("decision") == "no_trade"
        and p.get("phase") == "signal"
        for p in payloads
    )
    assert not rm.is_halted()


def test_rf_per_user_file_handler_injects_missing_user_id():
    formatter = logging.Formatter(
        "%(asctime)s | %(name)s | %(levelname)s | [%(user_id)s] %(message)s"
    )
    handler = rf_bot._RFPerUserFileHandler(formatter)
    logger = logging.getLogger("risefallbot.strategy")
    logger.setLevel(logging.INFO)

    record = logger.makeRecord(
        name="risefallbot.strategy",
        level=logging.INFO,
        fn=__file__,
        lno=1,
        msg="[RF][R_50] test log line",
        args=(),
        exc_info=None,
    )
    # Simulate problematic records from child logger paths with no user_id attribute.
    assert not hasattr(record, "user_id")

    # Should not raise formatting/key errors.
    handler.emit(record)
    handler.close()

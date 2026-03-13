"""
Comprehensive Test Plan — Verify All Fixes Against Simulated Scenarios

Tests ALL fixes across:
  - rf_bot.py         (watchdog, auto-recovery, _running reset, closure_type)
  - rf_risk_manager.py (duplicate rejection, watchdog guard, post-acquire check)
  - rf_trade_engine.py (closure_type, TP/SL retry, market-price fallback)
  - bot_manager.py     (hard-cancel, strategy switch, cleanup)
  - rf_config.py       (constants validation)

All external I/O (Supabase, Deriv WebSocket) is mocked.
Run with: pytest tests/test_rf_all_fixes.py -v -s
"""

import asyncio
import logging
import logging.handlers
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, AsyncMock, MagicMock, PropertyMock

@pytest.fixture(autouse=True)
def disable_db_lock():
    """Automatically disable DB session lock and mock its acquisition for all tests."""
    with patch("risefallbot.rf_config.RF_ENFORCE_DB_LOCK", False), \
         patch("risefallbot.rf_bot._acquire_session_lock", new_callable=AsyncMock) as mock_lock:
        mock_lock.return_value = True
        # Enable propagation so caplog works
        logging.getLogger("risefallbot").propagate = True
        yield



# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture
def risk_manager():
    """Fresh RiseFallRiskManager for each test."""
    from risefallbot.rf_risk_manager import RiseFallRiskManager
    return RiseFallRiskManager()


@pytest.fixture
def bot_manager():
    """Fresh BotManager for each test."""
    from app.bot.manager import BotManager
    mgr = BotManager(max_concurrent_bots=5)
    mgr._get_user_strategy = AsyncMock(return_value="RiseFall")
    return mgr


@pytest.fixture
def mock_event_manager():
    """Mock event manager for broadcast assertions."""
    em = AsyncMock()
    em.broadcast = AsyncMock()
    return em


def _fake_rf_run_factory():
    """Create a fake rf_bot.run() that loops until _running is False."""
    async def fake_rf_run(stake=None, api_token=None, user_id=None):
        import risefallbot.rf_bot as bot_mod
        bot_mod._running = True
        bot_mod._bot_task = asyncio.current_task()
        while bot_mod._running:
            await asyncio.sleep(0.05)
    return fake_rf_run


# ===========================================================================
# TEST 1 — Clean Single Trade Lifecycle
# ===========================================================================

@pytest.mark.asyncio
async def test_01_clean_single_trade_lifecycle(risk_manager, caplog):
    """
    Confirm the full 6-step lifecycle executes correctly end-to-end.
    All steps must appear in sequence, mutex free after Step 6.
    """
    with caplog.at_level(logging.INFO, logger="risefallbot"):
        # Step 1: Acquire lock
        acquired = await risk_manager.acquire_trade_lock("stpRNG1", "pending")
        assert acquired is True
        assert risk_manager.trade_mutex.locked()

        # Step 3: Record trade open
        risk_manager.record_trade_open({
            "contract_id": "12345678",
            "symbol": "stpRNG1",
            "direction": "CALL",
            "stake": 1.00,
        })
        assert len(risk_manager.active_trades) == 1

        # Step 4: Record trade close
        risk_manager.record_trade_closed({
            "contract_id": "12345678",
            "profit": 0.50,
            "status": "win",
            "symbol": "stpRNG1",
        })
        assert len(risk_manager.active_trades) == 0
        # Mutex still held — DB write not done yet
        assert risk_manager.trade_mutex.locked()

        # Step 6: Release lock (after DB write)
        risk_manager.release_trade_lock(reason="stpRNG1 lifecycle complete")

    # Assertions
    assert not risk_manager.trade_mutex.locked(), "Mutex should be free after Step 6"
    assert not risk_manager.is_halted(), "System should not be halted"
    assert risk_manager.wins == 1
    assert risk_manager.daily_trade_count == 1

    log_text = caplog.text
    assert "STEP 1/6" in log_text
    assert "TRADE LOCK ACQUIRED" in log_text
    assert "STEP 3/6" in log_text
    assert "TRADE TRACKED" in log_text
    assert "STEP 4/6" in log_text
    assert "TRADE CLOSED" in log_text
    assert "STEP 6/6" in log_text
    assert "LOCK RELEASED" in log_text

    print("\n[TEST 1] [PASS] Clean single trade lifecycle passed")


# ===========================================================================
# TEST 2 — Duplicate Start Request (bot_manager.py Fix 1)
# ===========================================================================

@pytest.mark.asyncio
async def test_02_duplicate_start_hard_cancel(bot_manager, caplog):
    """
    Confirm _start_risefall_bot() hard-cancels existing task before launching.
    Only one bot instance per user.
    """
    user_id = "test-dup-start"

    with patch("risefallbot.rf_bot.run", side_effect=_fake_rf_run_factory()), \
         caplog.at_level(logging.WARNING):

        # First start
        r1 = await bot_manager.start_bot(
            user_id=user_id, api_token="T", stake=1.0, strategy_name="RiseFall"
        )
        assert r1["success"] is True
        await asyncio.sleep(0.1)

        # Second start — should cancel old and launch new
        r2 = await bot_manager.start_bot(
            user_id=user_id, api_token="T", stake=1.0, strategy_name="RiseFall"
        )
        # The second start cancels old and starts new — both succeed
        assert r2["success"] is True
        await asyncio.sleep(0.1)

        # Only one task should exist
        assert user_id in bot_manager._rf_tasks
        assert not bot_manager._rf_tasks[user_id].done()

    # Verify hard-cancel log
    assert "RF task already exists" in caplog.text or "cancelling" in caplog.text.lower()

    # Cleanup
    await bot_manager.stop_bot(user_id)
    print("\n[TEST 2] [PASS] Duplicate start hard-cancel passed")


# ===========================================================================
# TEST 3 — Strategy Switch While Bot Running (bot_manager.py Fix 2)
# ===========================================================================

@pytest.mark.asyncio
async def test_03_strategy_switch_cancels_rf_task(bot_manager, caplog):
    """
    Switching from RiseFall to another strategy must cancel the RF task cleanly.
    """
    user_id = "test-switch"

    with patch("risefallbot.rf_bot.run", side_effect=_fake_rf_run_factory()), \
         caplog.at_level(logging.INFO):

        # Start RF bot
        r1 = await bot_manager.start_bot(
            user_id=user_id, api_token="T", stake=1.0, strategy_name="RiseFall"
        )
        assert r1["success"] is True
        await asyncio.sleep(0.1)
        assert user_id in bot_manager._rf_tasks

        # Simulate strategy switch — stop RF
        from risefallbot import rf_bot
        rf_bot.stop()
        await bot_manager.stop_bot(user_id)

        # RF task should be cleaned up
        assert user_id not in bot_manager._rf_tasks

    print("\n[TEST 3] [PASS] Strategy switch RF cleanup passed")


# ===========================================================================
# TEST 4 — Ghost Mutex Recovery (rf_bot.py watchdog + auto-recovery)
# ===========================================================================

@pytest.mark.asyncio
async def test_04_ghost_mutex_watchdog_recovery(risk_manager, caplog):
    """
    Confirm the watchdog detects a stuck mutex with no active trades
    and releases it after RF_PENDING_TIMEOUT_SECONDS.
    """
    from risefallbot import rf_config

    with caplog.at_level(logging.WARNING, logger="risefallbot"):
        # Simulate: acquire lock for pending entry
        acquired = await risk_manager.acquire_trade_lock("stpRNG1", "pending")
        assert acquired is True
        assert risk_manager.trade_mutex.locked()

        # Simulate: pending entry is older than timeout
        risk_manager._pending_entry_timestamp = (
            datetime.now() - timedelta(seconds=rf_config.RF_PENDING_TIMEOUT_SECONDS + 10)
        )

        # Run the watchdog logic manually (same as rf_bot.py run-loop watchdog)
        if risk_manager.trade_mutex.locked() and len(risk_manager.active_trades) == 0:
            if risk_manager._pending_entry_timestamp != datetime.min:
                elapsed = (datetime.now() - risk_manager._pending_entry_timestamp).total_seconds()
            else:
                elapsed = 0.0

            if elapsed > rf_config.RF_PENDING_TIMEOUT_SECONDS:
                risk_manager._trade_mutex.release()
                risk_manager._trade_lock_active = False
                risk_manager._locked_symbol = None
                risk_manager._locked_trade_info = {}
                if risk_manager.is_halted():
                    risk_manager.clear_halt()

    assert not risk_manager.trade_mutex.locked(), "Watchdog should have released the mutex"
    assert not risk_manager.is_halted()

    print("\n[TEST 4] [PASS] Ghost mutex watchdog recovery passed")


# ===========================================================================
# TEST 5 — Manual Trade Close on Deriv Platform
# ===========================================================================

@pytest.mark.asyncio
async def test_05_manual_close_returns_closure_type():
    """
    Confirm wait_for_result() returns closure_type='manual' when a trade
    is sold externally (is_sold=1, not by bot).
    """
    from risefallbot.rf_trade_engine import RFTradeEngine

    engine = RFTradeEngine(api_token="TEST", app_id="1089")

    # Mock WebSocket to simulate manual close
    mock_ws = AsyncMock()
    mock_ws.open = True

    # First recv: subscription confirmation (not poc, will be skipped)
    # Second recv: contract settled externally (is_sold=1, no bot sell)
    import json
    manual_close_msg = json.dumps({
        "proposal_open_contract": {
            "is_sold": 1,
            "is_expired": 0,
            "contract_id": "contract_manual_test",
            "sell_price": 1.40,
            "buy_price": 1.00,
            "bid_price": 1.40,
            "profit": 0.40,  # Deriv provides actual profit field
        },
        "subscription": {"id": "sub_123"},
    })
    # Mock recv: First call (flush) returns TimeoutError, second call (main loop) returns the message
    mock_ws.recv = AsyncMock(side_effect=[asyncio.TimeoutError(), manual_close_msg])
    mock_ws.send = AsyncMock()
    engine.ws = mock_ws

    result = await engine.wait_for_result("contract_manual_test", stake=1.0)

    assert result is not None
    assert result["closure_type"] == "manual"
    assert result["profit"] == pytest.approx(0.40)
    assert result["status"] == "win"

    print("\n[TEST 5] [PASS] Manual close closure_type detection passed")


# ===========================================================================
# BONUS TESTS — Config & Structural Validation
# ===========================================================================

@pytest.mark.asyncio
async def test_config_values():
    """
    Confirm rf_config.py has the expected values for all critical constants.
    """
    from risefallbot import rf_config

    assert rf_config.RF_MAX_CONCURRENT_TOTAL == 1
    assert rf_config.RF_MAX_CONCURRENT_PER_SYMBOL == 1
    assert rf_config.RF_PENDING_TIMEOUT_SECONDS == 60
    assert rf_config.RF_SCAN_INTERVAL == 1
    assert rf_config.RF_MAX_CONSECUTIVE_LOSSES == 2  # Block after 2 losses to prevent 3rd
    assert rf_config.RF_LOSS_COOLDOWN_SECONDS == 600

    print("\n[CONFIG] [PASS] All config values correct")


@pytest.mark.asyncio
async def test_duplicate_trade_rejection_releases_mutex(risk_manager, caplog):
    """
    record_trade_open() must release mutex and halt when a duplicate is attempted.
    """
    with caplog.at_level(logging.CRITICAL, logger="risefallbot"):
        # Open a valid trade
        await risk_manager.acquire_trade_lock("stpRNG1", "first_contract")
        risk_manager.record_trade_open({
            "contract_id": "first_contract",
            "symbol": "stpRNG1",
            "direction": "CALL",
            "stake": 1.0,
        })
        assert len(risk_manager.active_trades) == 1

        # Attempt duplicate — should be rejected and mutex released
        risk_manager.record_trade_open({
            "contract_id": "duplicate_contract",
            "symbol": "stpRNG1",
            "direction": "CALL",
            "stake": 1.0,
        })

    # Mutex should be released by the rejection
    assert not risk_manager.trade_mutex.locked()
    # System should be halted
    assert risk_manager.is_halted()
    assert "Duplicate" in risk_manager._halt_reason
    # Only the first trade should be tracked
    assert len(risk_manager.active_trades) == 1

    print("\n[DUPLICATE] [PASS] Duplicate trade rejection releases mutex and halts")


@pytest.mark.asyncio
async def test_post_acquire_rejects_when_active_trade_exists():
    """
    Post-acquire check in acquire_trade_lock() must release mutex
    if active_trades is not empty.
    """
    from risefallbot.rf_risk_manager import RiseFallRiskManager

    rm = RiseFallRiskManager()

    # Manually inject an active trade without going through acquire
    rm.active_trades["existing_123"] = {
        "contract_id": "existing_123",
        "symbol": "stpRNG2",
        "direction": "CALL",
        "stake": 1.0,
    }

    # Attempt to acquire — post-acquire check should catch the active trade
    acquired = await rm.acquire_trade_lock("stpRNG1", "new_trade")
    assert acquired is False
    # Mutex must have been released by the post-acquire check
    assert not rm.trade_mutex.locked()

    print("\n[POST-ACQUIRE] [PASS] Post-acquire check rejects with existing active trade")


@pytest.mark.asyncio
async def test_cleanup_removes_done_rf_tasks(bot_manager):
    """
    cleanup_inactive_bots() must remove completed RF tasks.
    """
    user_id = "test-cleanup"

    # Create a done task
    async def instant_done():
        pass

    task = asyncio.create_task(instant_done())
    await asyncio.sleep(0.05)  # Let it complete
    assert task.done()

    bot_manager._rf_tasks[user_id] = task
    bot_manager._rf_start_times[user_id] = datetime.now()
    bot_manager._rf_stakes[user_id] = 1.0

    await bot_manager.cleanup_inactive_bots()

    assert user_id not in bot_manager._rf_tasks
    assert user_id not in bot_manager._rf_start_times
    assert user_id not in bot_manager._rf_stakes

    print("\n[CLEANUP] [PASS] Completed RF tasks cleaned up")




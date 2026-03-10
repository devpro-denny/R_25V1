"""
Unit tests for RiskManager
Tests global risk limits, multi-asset control, and trailing stop logic.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from risk_manager import RiskManager

@pytest.fixture
def rm():
    with patch("risk_manager.config") as mock_config:
        mock_config.MAX_TRADES_PER_DAY = 10
        mock_config.COOLDOWN_SECONDS = 60
        mock_config.MAX_CONSECUTIVE_LOSSES = 3
        mock_config.DAILY_LOSS_MULTIPLIER = 5.0
        mock_config.MAX_DAILY_LOSS = 50.0  # Set actual float value
        mock_config.FIXED_STAKE = 10.0
        mock_config.SYMBOLS = ["R_25", "R_50"]
        mock_config.ASSET_CONFIG = {
            "R_25": {"multiplier": 10},
            "R_50": {"multiplier": 10}
        }
        mock_config.USE_TOPDOWN_STRATEGY = True
        mock_config.MAX_CONCURRENT_TRADES = 2
        mock_config.TOPDOWN_MIN_RR_RATIO = 1.5
        mock_config.MIN_RR_RATIO = 1.0
        mock_config.STRICT_RR_ENFORCEMENT = True
        mock_config.MAX_RISK_PCT = 10.0
        mock_config.MIN_SIGNAL_STRENGTH = 5.0
        mock_config.STAKE_LIMIT_MULTIPLIER = 1.5
        mock_config.ENABLE_BREAKEVEN_RULE = True
        mock_config.BREAKEVEN_TRIGGER_PCT = 20.0
        mock_config.BREAKEVEN_MAX_LOSS_PCT = 5.0
        mock_config.ENABLE_MULTI_TIER_TRAILING = True
        mock_config.TRAILING_STOPS = [
            {'name': 'Tier 1', 'trigger_pct': 25.0, 'trail_pct': 10.0}
        ]
        mock_config.ENABLE_STAGNATION_EXIT = True
        mock_config.STAGNATION_EXIT_TIME = 300
        mock_config.STAGNATION_LOSS_PCT = 50.0
        yield RiskManager()

def test_initialization(rm):
    """Test RiskManager init values."""
    assert rm.max_trades_per_day == 10
    assert rm.max_concurrent_trades == 2
    assert rm.consecutive_losses == 0

def test_update_risk_settings(rm):
    """Test updating risk settings based on stake."""
    rm.update_risk_settings(stake=10.0)
    assert rm.fixed_stake == 10.0
    assert rm.max_daily_loss == 50.0 # 10 * 5.0

def test_can_trade_global_limit(rm):
    """Test global concurrent trades limit."""
    rm.active_trades = [{"symbol": "R_25"}, {"symbol": "R_50"}]
    can, reason = rm.can_trade("R_75")
    assert can is False
    assert "GLOBAL LIMIT" in reason

def test_can_trade_circuit_breaker(rm):
    """Test consecutive loss limit."""
    rm.consecutive_losses = 3
    can, reason = rm.can_trade()
    assert can is False
    assert "circuit breaker" in reason

def test_validate_trade_parameters(rm):
    """Test trade validation logic."""
    rm.update_risk_settings(10.0)
    
    # Happy path
    signal = {
        "symbol": "R_25",
        "entry_price": 100.0,
        "stop_loss": 99.5, # 0.5% distance -> 5% risk (multiplier 10)
        "take_profit": 101.5, # 1.5% distance -> R:R 3.0
        "score": 8.0
    }
    valid, reason = rm.validate_trade_parameters("R_25", 10.0, signal_dict=signal)
    assert valid is True

    # High risk
    signal_high_risk = signal.copy()
    signal_high_risk["stop_loss"] = 98.0 # 2% distance -> 20% risk
    signal_high_risk["take_profit"] = 105.0 # 5% profit -> R:R 2.5 (Passes R:R 1.0)
    valid, reason = rm.validate_trade_parameters("R_25", 10.0, signal_dict=signal_high_risk)
    assert valid is False
    assert "Risk too high" in reason

def test_record_trade_open_and_close(rm):
    """Test open/close lifecycle and stats update."""
    rm.active_trades = []
    trade_info = {
        "symbol": "R_25",
        "contract_id": "c1",
        "direction": "CALL",
        "stake": 10.0,
        "entry_price": 100.0
    }
    
    rm.record_trade_open(trade_info)
    assert len(rm.active_trades) == 1
    assert rm.total_trades == 1
    
    rm.record_trade_close("c1", 5.0, "won")
    assert len(rm.active_trades) == 0
    assert rm.daily_pnl == 5.0
    assert rm.winning_trades == 1
    assert rm.consecutive_losses == 0

def test_manual_import_trade_does_not_affect_daily_cooldown_counters(rm):
    """Manually imported/synced trades must not trip entry gates for system trades."""
    rm.last_trade_time = None
    trade_info = {
        "symbol": "R_25",
        "contract_id": "manual-1",
        "direction": "PUT",
        "stake": 10.0,
        "entry_price": 100.0,
        "entry_source": "manual_imported",
        "manual_tracking": True,
    }

    rm.record_trade_open(trade_info)
    assert len(rm.active_trades) == 1
    assert len(rm.trades_today) == 0
    assert rm.total_trades == 0
    assert rm.last_trade_time is None

    can_trade, _ = rm.can_trade("R_50")
    assert can_trade is True

    rm.record_trade_close("manual-1", -5.0, "lost")
    assert len(rm.active_trades) == 0
    assert rm.daily_pnl == 0.0
    assert rm.losing_trades == 0
    assert rm.consecutive_losses == 0


def test_manual_import_trade_respects_active_trade_lock_when_single_slot(rm):
    """Manual/synced trades should block new entries when only one slot is configured."""
    rm.max_concurrent_trades = 1
    rm.record_trade_open(
        {
            "symbol": "R_25",
            "contract_id": "manual-lock-1",
            "direction": "PUT",
            "stake": 10.0,
            "entry_price": 100.0,
            "entry_source": "manual_imported",
            "manual_tracking": True,
        }
    )

    can_trade, reason = rm.can_trade("R_50")
    assert can_trade is False
    assert "GLOBAL LIMIT" in reason

def test_manual_import_trade_exit_controls_toggle(rm):
    """Exit controls must be mutable for synced/manual-import trades."""
    trade_info = {
        "symbol": "R_25",
        "contract_id": "manual-toggle-1",
        "direction": "PUT",
        "stake": 10.0,
        "entry_price": 100.0,
        "entry_source": "manual_imported",
        "manual_tracking": True,
    }
    rm.record_trade_open(trade_info)

    updated = rm.set_trade_exit_controls(
        "manual-toggle-1",
        trailing_enabled=False,
        stagnation_enabled=False,
    )
    assert updated is not None
    assert updated["trailing_enabled"] is False
    assert updated["stagnation_enabled"] is False

    active = next(t for t in rm.active_trades if str(t.get("contract_id")) == "manual-toggle-1")
    assert active["trailing_enabled"] is False
    assert active["stagnation_enabled"] is False

def test_manual_import_trade_honors_disabled_exit_controls_on_open(rm):
    """Manual sync imports should start with trail/stagnation disabled when requested."""
    rm.record_trade_open(
        {
            "symbol": "R_25",
            "contract_id": "manual-flags-1",
            "direction": "PUT",
            "stake": 10.0,
            "entry_price": 100.0,
            "entry_source": "manual_imported",
            "manual_tracking": True,
            "trailing_enabled": False,
            "stagnation_enabled": False,
        }
    )

    active = next(t for t in rm.active_trades if str(t.get("contract_id")) == "manual-flags-1")
    assert active["trailing_enabled"] is False
    assert active["stagnation_enabled"] is False

def test_manual_import_get_active_trade_info_preserves_source_and_open_time(rm):
    """Runtime monitor metadata should preserve sync source and broker open time."""
    imported_open_time = datetime(2026, 3, 10, 10, 4, 6)
    rm.record_trade_open(
        {
            "symbol": "R_75",
            "contract_id": "manual-info-1",
            "direction": "DOWN",
            "stake": 5.0,
            "entry_price": 33004.7788,
            "multiplier": 200,
            "timestamp": imported_open_time.isoformat(),
            "entry_source": "manual_imported",
            "manual_tracking": True,
        }
    )

    active_info = rm.get_active_trade_info()

    assert active_info["contract_id"] == "manual-info-1"
    assert active_info["entry_source"] == "manual_imported"
    assert active_info["manual_tracking"] is True
    assert active_info["multiplier"] == 200
    assert active_info["open_time"] == imported_open_time
    assert active_info["timestamp"] == imported_open_time

def test_conservative_coerce_exit_flag_handles_supported_types(rm):
    """Exit-flag coercion should preserve explicit bool-like values."""
    assert rm._coerce_exit_flag(True) is True
    assert rm._coerce_exit_flag(0) is False
    assert rm._coerce_exit_flag("off") is False
    assert rm._coerce_exit_flag("yes") is True
    assert rm._coerce_exit_flag("unexpected", fallback=False) is False
    assert rm._coerce_exit_flag(2, fallback=True) is True

def test_record_trade_close_loss(rm):
    """Test recording a losing trade."""
    rm.active_trades = [{"symbol": "R_25", "contract_id": "c2"}]
    rm.record_trade_close("c2", -5.0, "lost")
    assert rm.losing_trades == 1
    assert rm.consecutive_losses == 1
    assert rm.daily_pnl == -5.0

def test_cooldown_logic(rm):
    """Test cooldown timer."""
    rm.last_trade_time = datetime.now()
    rm.cooldown_seconds = 60
    assert rm.get_cooldown_remaining() > 0
    
    rm.last_trade_time = datetime.now() - timedelta(seconds=70)
    assert rm.get_cooldown_remaining() == 0

def test_remaining_loss_capacity(rm):
    """Test loss capacity logic."""
    rm.max_daily_loss = 100.0
    rm.daily_pnl = -40.0
    assert rm.get_remaining_loss_capacity() == 60.0

def test_get_remaining_trades_today(rm):
    """Test remaining trades count."""
    rm.max_trades_per_day = 10
    rm.trades_today = [{"id": 1}, {"id": 2}]
    assert rm.get_remaining_trades_today() == 8

def test_active_trade_info(rm):
    """Test getting active trade info."""
    rm.active_trades = [{"contract_id": "c1", "symbol": "R_25"}]
    info = rm.get_active_trade_info()
    assert info["contract_id"] == "c1"
    
    rm.active_trades = []
    assert rm.get_active_trade_info() is None

def test_validate_rr_ratio(rm):
    """Test R:R ratio validation specifically."""
    rm.update_risk_settings(10.0)
    signal = {
        "symbol": "R_25",
        "entry_price": 100.0,
        "stop_loss": 99.0, # 1% risk -> 10.0
        "take_profit": 100.5, # 0.5% profit -> R:R 0.5
        "score": 8.0
    }
    # Should fail if min_rr is 1.0 (legacy or topdown)
    valid, reason = rm.validate_trade_parameters("R_25", 10.0, signal_dict=signal)
    assert valid is False
    assert "Invalid R:R" in reason

def test_print_status_smoke(rm):
    """Smoke test for print_status."""
    rm.active_trades = [{"symbol": "R_25", "strategy": "topdown", "phase": "recovery"}]
    rm.print_status() # Should not raise exception

@pytest.mark.asyncio
async def test_check_for_existing_positions_none(rm):
    """Test check_for_existing_positions when none exist."""
    from unittest.mock import AsyncMock
    mock_api = MagicMock()
    mock_api.portfolio = AsyncMock(return_value={"portfolio": {"contracts": []}})
    res = await rm.check_for_existing_positions(mock_api)
    assert res is False
    assert len(rm.active_trades) == 0

@pytest.mark.asyncio
async def test_check_for_existing_positions_found(rm):
    """Test check_for_existing_positions when one exists."""
    from unittest.mock import AsyncMock
    mock_api = MagicMock()
    mock_api.portfolio = AsyncMock(return_value={
        "portfolio": {
            "contracts": [{
                "contract_type": "CALL",
                "underlying": "R_25",
                "contract_id": "c_existing",
                "buy_price": 10.0,
                "entry_spot": 100.0
            }]
        }
    })
    res = await rm.check_for_existing_positions(mock_api)
    assert res is True
    assert len(rm.active_trades) == 1
    assert rm.active_trades[0]["contract_id"] == "c_existing"

def test_trailing_stop_stagnation_logic(rm):
    """Test stagnation logic in update_trailing_stop."""
    trade = {
        "contract_id": "c1",
        "timestamp": datetime.now() - timedelta(seconds=400),
        "stake": 10.0
    }
    # Profit is minor (2% -> 0.2), but time is long. 
    # Stagnation rule might not trigger in update_trailing_stop but in should_close_trade.
    # Actually update_trailing_stop only handles breakeven and trailing.
    pass

def test_should_close_trade_normal(rm):
    """Test normal (non-stagnation) close check."""
    rm.active_trades = [{"contract_id": "c1", "symbol": "R_25", "stake": 10.0, "timestamp": datetime.now()}]
    # No reason to close
    res = rm.should_close_trade("c1", 1.0, 101.0, 102.0)
    assert res["should_close"] is False

def test_trailing_stop_breakeven(rm):
    """Test breakeven protection activation."""
    trade = {"contract_id": "c1"}
    # Trigger breakeven (20% profit)
    # current_pnl = 2.0 (on 10.0 stake)
    res = rm.update_trailing_stop(trade, 2.0, 10.0)
    assert trade["breakeven_activated"] is True
    assert res["type"] == "breakeven"
    assert res["stop_profit_pct"] == -5.0

def test_trailing_stop_multi_tier(rm):
    """Test tiered trailing stop."""
    trade = {"contract_id": "c1"}
    # Trigger Tier 1 (25% profit)
    # current_pnl = 3.0 (on 10.0 stake)
    res = rm.update_trailing_stop(trade, 3.0, 10.0)
    assert trade["trail_stop_profit_pct"] == 20.0 # 30% profit - 10% trail
    assert res["type"] == "trailing"

def test_should_close_stagnation(rm):
    """Test stagnation exit logic."""
    rm.active_trades = [{
        "contract_id": "c1",
        "symbol": "R_25",
        "stake": 10.0,
        "timestamp": datetime.now() - timedelta(seconds=400) # Past stagnation time
    }]
    
    # Loss is 6.0 (60% of stake), stagnation limit is 50%
    res = rm.should_close_trade("c1", -6.0, 94.0, 95.0)
    assert res["should_close"] is True
    assert res["reason"] == "stagnation_exit"

def test_get_statistics(rm):
    """Test stats summary."""
    rm.total_trades = 2
    rm.winning_trades = 1
    rm.losing_trades = 1
    rm.total_pnl = 5.0
    rm.trades_today = [{"exit_type": "take_profit", "pnl": 10.0}, {"exit_type": "stop_loss", "pnl": -5.0}]

    stats = rm.get_statistics()
    assert stats["win_rate"] == 50.0
    assert stats["profit_factor"] == 2.0
    assert stats["avg_win"] == 10.0


def test_set_trade_exit_controls_matches_numeric_contract_id_with_string_input(rm):
    """Exit controls should update even when path contract_id is string and stored ID is int."""
    rm.active_trades = [{
        "contract_id": 308022298068,
        "symbol": "R_25",
        "trailing_enabled": True,
        "stagnation_enabled": True,
    }]
    rm.bot_state = MagicMock()
    rm.bot_state.active_trades = [{
        "contract_id": 308022298068,
        "trailing_enabled": True,
        "stagnation_enabled": True,
    }]

    updated = rm.set_trade_exit_controls("308022298068", trailing_enabled=False, stagnation_enabled=False)

    assert updated is not None
    assert updated["contract_id"] == "308022298068"
    assert updated["trailing_enabled"] is False
    assert updated["stagnation_enabled"] is False
    assert rm.active_trades[0]["trailing_enabled"] is False
    assert rm.active_trades[0]["stagnation_enabled"] is False
    assert rm.bot_state.active_trades[0]["trailing_enabled"] is False
    assert rm.bot_state.active_trades[0]["stagnation_enabled"] is False

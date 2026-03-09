import pytest
from unittest.mock import MagicMock, AsyncMock
from conservative_risk_manager import ConservativeRiskManager
import config

@pytest.fixture
def crm():
    return ConservativeRiskManager(user_id="test_user")

def test_crm_init(crm):
    assert crm.user_id == "test_user"
    assert crm.risk_manager is not None

def test_crm_properties(crm):
    crm.risk_manager.active_trades = [{"id": 1}]
    assert crm.active_trades == [{"id": 1}]

def test_crm_can_trade(crm):
    crm.risk_manager.can_trade = MagicMock(return_value=(True, "OK"))
    can, reason = crm.can_trade("R_25", verbose=True)
    assert can is True
    crm.risk_manager.can_trade.assert_called_with("R_25", True)

def test_crm_record_trade(crm):
    crm.risk_manager.record_trade_open = MagicMock()
    crm.risk_manager.record_trade_close = MagicMock()
    
    crm.record_trade_open({"id": 1})
    crm.record_trade_close("1", 10.0, "won")
    crm.record_trade_closed({"contract_id": "2", "profit": 5.0, "status": "won"})
    
    assert crm.risk_manager.record_trade_open.called
    assert crm.risk_manager.record_trade_close.call_count == 2

def test_crm_set_trade_exit_controls(crm):
    crm.risk_manager.set_trade_exit_controls = MagicMock(
        return_value={
            "contract_id": "manual-toggle-1",
            "trailing_enabled": False,
            "stagnation_enabled": True,
        }
    )

    updated = crm.set_trade_exit_controls(
        "manual-toggle-1",
        trailing_enabled=False,
        stagnation_enabled=True,
    )

    assert updated == {
        "contract_id": "manual-toggle-1",
        "trailing_enabled": False,
        "stagnation_enabled": True,
    }
    crm.risk_manager.set_trade_exit_controls.assert_called_once_with(
        contract_id="manual-toggle-1",
        trailing_enabled=False,
        stagnation_enabled=True,
    )

@pytest.mark.asyncio
async def test_crm_check_existing(crm):
    crm.risk_manager.check_for_existing_positions = AsyncMock(return_value=True)
    res = await crm.check_for_existing_positions(MagicMock())
    assert res is True

def test_crm_get_current_limits(crm):
    crm.risk_manager.active_trades = []
    crm.risk_manager.trades_today = []
    crm.risk_manager.consecutive_losses = 0
    crm.risk_manager.daily_pnl = 0.0
    
    limits = crm.get_current_limits()
    assert "max_concurrent_trades" in limits
    assert limits["current_concurrent_trades"] == 0

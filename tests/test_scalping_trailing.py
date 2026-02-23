"""
Tests for ScalpingRiskManager.check_trailing_profit()
"""
import pytest
import sys
import os

# Ensure project root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Mock supabase before importing scalping_risk_manager
from unittest.mock import MagicMock, patch

# Patch supabase at module level before import
mock_supabase = MagicMock()
mock_supabase.table.return_value.select.return_value.eq.return_value.gte.return_value.execute.return_value = MagicMock(data=[])

with patch.dict('sys.modules', {'app': MagicMock(), 'app.core': MagicMock(), 'app.core.supabase': MagicMock(supabase=mock_supabase)}):
    from scalping_risk_manager import ScalpingRiskManager


@pytest.fixture
def rm():
    """Create a ScalpingRiskManager with mocked DB."""
    with patch.dict('sys.modules', {'app': MagicMock(), 'app.core': MagicMock(), 'app.core.supabase': MagicMock(supabase=mock_supabase)}):
        manager = ScalpingRiskManager(user_id='test-user')
    # Simulate a trade being opened
    manager.record_trade_open({
        'contract_id': 'C001',
        'stake': 50.0,
        'symbol': 'R_50',
    })
    return manager


def _info(contract_id='C001', stake=50.0, symbol='R_50'):
    return {
        'contract_id': contract_id,
        'stake': stake,
        'symbol': symbol,
    }


class TestTrailingProfitBelowActivation:
    """Trailing should not activate below 8% profit."""
    
    def test_no_profit(self, rm):
        should_close, reason, just_activated = rm.check_trailing_profit(_info(), current_pnl=0.0)
        assert should_close is False
        assert reason == ''
        assert just_activated is False
    
    def test_small_profit(self, rm):
        # 6% of $50 = $3
        should_close, reason, just_activated = rm.check_trailing_profit(_info(), current_pnl=3.0)
        assert should_close is False
        assert just_activated is False
    
    def test_negative_pnl(self, rm):
        should_close, reason, just_activated = rm.check_trailing_profit(_info(), current_pnl=-5.0)
        assert should_close is False
        assert just_activated is False


class TestTrailingProfitActivation:
    """Trailing activates at 8% but does not close immediately."""
    
    def test_activation_at_threshold(self, rm):
        # 12% of $50 = $6
        should_close, reason, just_activated = rm.check_trailing_profit(_info(), current_pnl=6.0)
        assert should_close is False
        assert just_activated is True  # First activation!
        # State should be initialized
        assert 'C001' in rm._trailing_state
        assert rm._trailing_state['C001']['trailing_active'] is True
        assert rm._trailing_state['C001']['highest_profit_pct'] == pytest.approx(12.0)
    
    def test_activation_above_threshold(self, rm):
        # 14% of $50 = $7
        should_close, reason, just_activated = rm.check_trailing_profit(_info(), current_pnl=7.0)
        assert should_close is False
        assert just_activated is True  # First activation!
        assert rm._trailing_state['C001']['highest_profit_pct'] == pytest.approx(14.0)
    
    def test_subsequent_call_not_activation(self, rm):
        # First call activates
        _, _, activated1 = rm.check_trailing_profit(_info(), current_pnl=6.0)
        assert activated1 is True
        # Second call is NOT activation
        _, _, activated2 = rm.check_trailing_profit(_info(), current_pnl=7.0)
        assert activated2 is False


class TestTrailingProfitRisingProfit:
    """Highest profit should ratchet upward."""
    
    def test_profit_ratchets_up(self, rm):
        # Activate at 8%
        rm.check_trailing_profit(_info(), current_pnl=4.0)  # 8%
        # Rise to 12%
        rm.check_trailing_profit(_info(), current_pnl=6.0)  # 12%
        assert rm._trailing_state['C001']['highest_profit_pct'] == pytest.approx(12.0)
        # Rise to 20%
        rm.check_trailing_profit(_info(), current_pnl=10.0)  # 20%
        assert rm._trailing_state['C001']['highest_profit_pct'] == pytest.approx(20.0)


class TestTrailingProfitPullback:
    """Pullback within distance should not close; beyond distance should close."""
    
    def test_pullback_within_distance(self, rm):
        # Activate at 10%, peak at 12%
        rm.check_trailing_profit(_info(), current_pnl=5.0)  # 10%
        rm.check_trailing_profit(_info(), current_pnl=6.0)  # 12%
        
        # Pull back to 10% — floor is 12-3=9%, 10 > 9 so no exit
        should_close, reason, _ = rm.check_trailing_profit(_info(), current_pnl=5.0)  # 10%
        assert should_close is False
    
    def test_pullback_triggers_exit(self, rm):
        # Activate at 10%, peak at 12%
        rm.check_trailing_profit(_info(), current_pnl=5.0)   # 10%
        rm.check_trailing_profit(_info(), current_pnl=6.0)   # 12%
        
        # Pull back to 8% — floor is 12-3=9%, 8 < 9 so EXIT
        should_close, reason, _ = rm.check_trailing_profit(_info(), current_pnl=4.0)  # 8%
        assert should_close is True
        assert reason == 'trailing_profit_exit'
    
    def test_exact_floor_no_exit(self, rm):
        # Activate at 10%, peak at 12%
        rm.check_trailing_profit(_info(), current_pnl=5.0)   # 10%
        rm.check_trailing_profit(_info(), current_pnl=6.0)   # 12%
        
        # At exactly 9% — floor is 12-3=9%, 9 is NOT < 9, so no exit
        should_close, reason, _ = rm.check_trailing_profit(_info(), current_pnl=4.5)  # 9%
        assert should_close is False


class TestTrailingProfitProgressive:
    """Progressive tiers with dynamic trailing distance."""
    
    def test_tier1_distance_3pct(self, rm):
        """8-15% profit → 3% trail distance."""
        # $50 stake, so 1% = $0.50
        rm.check_trailing_profit(_info(), current_pnl=4.0)   # 8% → activates
        # Peak at 12%, distance=3%, floor=9%
        rm.check_trailing_profit(_info(), current_pnl=6.0)   # 12%
        floor = rm._trailing_state['C001']['highest_profit_pct'] - 3.0
        assert floor == pytest.approx(9.0)
    
    def test_tier2_distance_5pct(self, rm):
        """15-25% profit → 5% trail distance."""
        rm.check_trailing_profit(_info(), current_pnl=4.0)   # 8% → activates
        rm.check_trailing_profit(_info(), current_pnl=10.0)  # 20%
        # Peak at 20%, distance=5%, floor=15%
        should_close, _, _ = rm.check_trailing_profit(_info(), current_pnl=8.0)  # 16%
        assert should_close is False  # 16% > 15% floor
    
    def test_tier3_distance_7pct(self, rm):
        """25%+ profit → 7% trail distance."""
        rm.check_trailing_profit(_info(), current_pnl=4.0)    # 8% → activates
        rm.check_trailing_profit(_info(), current_pnl=15.0)   # 30%
        # Peak at 30%, distance=7%, floor=23%
        should_close, _, _ = rm.check_trailing_profit(_info(), current_pnl=12.0)  # 24%
        assert should_close is False  # 24% > 23% floor
        
        # Drop below floor
        should_close, reason, _ = rm.check_trailing_profit(_info(), current_pnl=11.0)  # 22%
        assert should_close is True  # 22% < 23% floor
        assert reason == 'trailing_profit_exit'
    
    def test_wider_trail_gives_more_room(self, rm):
        """At high profit, wider trail prevents premature exit."""
        rm.check_trailing_profit(_info(), current_pnl=4.0)   # 8% → activates
        rm.check_trailing_profit(_info(), current_pnl=15.0)  # 30% peak (tier3: 7% trail)
        
        # At 24%, with flat 3% trail this would exit (floor 27%), but with 7% trail floor is 23%
        should_close, _, _ = rm.check_trailing_profit(_info(), current_pnl=12.0)  # 24%
        assert should_close is False  # 24% > 23% — survived thanks to wider trail


class TestTrailingProfitCleanup:
    """State should be cleaned up when trade is closed."""
    
    def test_cleanup_on_close(self, rm):
        # Activate trailing
        rm.check_trailing_profit(_info(), current_pnl=6.0)  # 12%
        assert 'C001' in rm._trailing_state
        assert 'C001' in rm._trade_metadata
        
        # Close the trade
        rm.record_trade_close('C001', pnl=5.0, status='win')
        
        # State should be gone
        assert 'C001' not in rm._trailing_state
        assert 'C001' not in rm._trade_metadata


class TestTrailingProfitEdgeCases:
    """Edge cases."""
    
    def test_missing_contract_id(self, rm):
        should_close, reason, just_activated = rm.check_trailing_profit({'stake': 50.0}, current_pnl=10.0)
        assert should_close is False
        assert just_activated is False
    
    def test_zero_stake(self, rm):
        should_close, reason, just_activated = rm.check_trailing_profit(
            {'contract_id': 'C001', 'stake': 0.0}, current_pnl=10.0
        )
        assert should_close is False
        assert just_activated is False
    
    def test_very_high_profit_no_cap(self, rm):
        # 100% profit = $50, should still trail without capping
        rm.check_trailing_profit(_info(), current_pnl=40.0)  # 80%
        rm.check_trailing_profit(_info(), current_pnl=50.0)  # 100%
        assert rm._trailing_state['C001']['highest_profit_pct'] == pytest.approx(100.0)
        
        # Still trailing, not closed
        should_close, _, _ = rm.check_trailing_profit(_info(), current_pnl=49.0)  # 98%
        assert should_close is False  # floor is 100-3=97%, 98 > 97

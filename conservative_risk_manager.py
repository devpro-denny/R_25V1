"""
Conservative Risk Manager Wrapper
Wraps the existing risk_manager.py logic to implement the BaseRiskManager interface
"""

from base_risk_manager import BaseRiskManager
from risk_manager import RiskManager
from typing import Dict, Tuple, Optional
import logging

logger = logging.getLogger(__name__)


class ConservativeRiskManager(BaseRiskManager):
    """
    Wrapper for the existing conservative risk manager.
    Delegates to the original RiskManager implementation.
    """
    
    def __init__(self, user_id: str = None, overrides: Dict = None):
        """
        Initialize the conservative risk manager wrapper.
        
        Args:
            user_id: User identifier (for multi-tenant setups)
            overrides: User-specific parameter overrides (not used by conservative)
        """
        self.risk_manager = RiskManager()
        self.user_id = user_id
        logger.info(f"✅ Conservative risk manager wrapper initialized for user {user_id}")
    
    # Expose active_trades property from wrapped RiskManager
    @property
    def active_trades(self):
        """Expose active_trades from the wrapped RiskManager"""
        return self.risk_manager.active_trades
    
    def can_trade(self, symbol: str = None, verbose: bool = False) -> Tuple[bool, str]:
        """
        Check if trading is allowed.
        
        Args:
            symbol: Optional symbol to check
            verbose: If True, log every check
        
        Returns:
            Tuple of (can_trade: bool, reason: str)
        """
        can_trade, reason = self.risk_manager.can_trade(symbol, verbose)
        return can_trade, reason
    
    def can_open_trade(self, symbol: str, stake: float, 
                      take_profit: float = None, stop_loss: float = None,
                      signal_dict: Dict = None) -> Tuple[bool, str]:
        """
        Complete validation before opening trade on specific symbol.
        
        Args:
            symbol: Symbol to trade
            stake: Trade stake amount
            take_profit: Take profit level
            stop_loss: Stop loss level
            signal_dict: Full signal data
            
        Returns:
            Tuple of (can_open: bool, reason: str)
        """
        return self.risk_manager.can_open_trade(symbol, stake, take_profit, stop_loss, signal_dict)
    
    def record_trade_opened(self, trade_info: Dict) -> None:
        """
        Record that a new trade has been opened.
        
        Args:
            trade_info: Dict containing trade details
        """
        self.risk_manager.record_trade_open(trade_info)
    
    def record_trade_close(self, contract_id: str, pnl: float, status: str) -> None:
        """
        Record trade closure and update statistics.
        
        Args:
            contract_id: Contract ID
            pnl: Profit/loss amount
            status: Trade status ('won', 'lost', etc.)
        """
        self.risk_manager.record_trade_close(contract_id, pnl, status)
    
    def record_trade_closed(self, result: Dict) -> None:
        """
        Record that a trade has been closed (alternative interface).
        
        Args:
            result: Dict containing trade result
        """
        contract_id = result.get('contract_id')
        pnl = result.get('profit', 0.0)
        status = result.get('status', 'unknown')
        
        self.risk_manager.record_trade_close(
            contract_id=contract_id,
            pnl=pnl,
            status=status
        )
    
    def get_cooldown_remaining(self) -> int:
        """
        Get remaining cooldown time in seconds.
        
        Returns:
            Cooldown seconds remaining
        """
        return self.risk_manager.get_cooldown_remaining()
    
    async def check_for_existing_positions(self, trade_engine):
        """
        Check for existing positions on startup.
        
        Args:
            trade_engine: TradeEngine instance
            
        Returns:
            bool: True if existing positions found
        """
        return await self.risk_manager.check_for_existing_positions(trade_engine)
    
    def get_active_trade_info(self) -> Optional[Dict]:
        """
        Get information about active trade.
        
        Returns:
            Dict with trade info or None
        """
        return self.risk_manager.get_active_trade_info()
    
    def set_bot_state(self, state) -> None:
        """
        Set BotState instance for real-time API updates.
        
        Args:
            state: BotState instance
        """
        self.risk_manager.set_bot_state(state)
    
    def update_risk_settings(self, stake: float) -> None:
        """
        Update risk limits based on user's stake.
        
        Args:
            stake: User's trade stake amount
        """
        self.risk_manager.update_risk_settings(stake)
    
    def get_current_limits(self) -> Dict:
        """
        Get current risk parameters and limits.
        
        Returns:
            Dict of active thresholds and current counts
        """
        import config
        
        return {
            'max_concurrent_trades': config.MAX_CONCURRENT_TRADES,
            'current_concurrent_trades': len(self.risk_manager.active_trades),
            'max_trades_per_day': config.MAX_TRADES_PER_DAY,
            'daily_trade_count': len(self.risk_manager.trades_today),  # Fixed: use trades_today length
            'max_consecutive_losses': config.MAX_CONSECUTIVE_LOSSES,
            'consecutive_losses': self.risk_manager.consecutive_losses,
            'daily_pnl': self.risk_manager.daily_pnl,
            'cooldown_seconds': config.COOLDOWN_SECONDS,
        }
    
    def reset_daily_stats(self) -> None:
        """
        Reset daily statistics.
        """
        self.risk_manager.reset_daily_stats()

"""
Bot State Manager
Tracks bot status, trades, signals, and statistics in memory
Thread-safe access for concurrent requests
"""

from datetime import datetime
from typing import List, Dict, Optional
from threading import Lock
import logging

logger = logging.getLogger(__name__)

class BotState:
    """
    Global bot state tracker
    Provides thread-safe access to bot metrics and history
    """
    
    def __init__(self):
        self._lock = Lock()
        
        # Bot status
        self.status: str = "stopped"
        self.error_message: Optional[str] = None
        self.last_updated: datetime = datetime.now()
        
        # Account
        self.balance: float = 0.0
        
        # Trades (limited history in memory)
        self.active_trades: List[Dict] = []
        self.trade_history: List[Dict] = []
        self.max_history = 100  # Keep last 100 trades
        
        # Signals
        self.recent_signals: List[Dict] = []
        self.max_signals = 50  # Keep last 50 signals
        
        # Statistics
        self.statistics: Dict = {
            'total_trades': 0,
            'winning_trades': 0,
            'losing_trades': 0,
            'win_rate': 0.0,
            'total_pnl': 0.0,
            'daily_pnl': 0.0
        }
        
        # Performance metrics
        self.performance: Dict = {
            'uptime_seconds': 0,
            'cycles_completed': 0,
            'last_cycle_time': None
        }
    
    def update_status(self, status: str, error: Optional[str] = None):
        """Update bot status"""
        with self._lock:
            self.status = status
            self.error_message = error
            self.last_updated = datetime.now()
            logger.debug(f"Bot status updated: {status}")
    
    def update_balance(self, balance: float):
        """Update account balance"""
        with self._lock:
            self.balance = balance
            logger.debug(f"Balance updated: ${balance:.2f}")
    
    def add_trade(self, trade: Dict):
        """Add new trade to active trades"""
        with self._lock:
            trade_copy = trade.copy()
            trade_copy['added_at'] = datetime.now().isoformat()
            self.active_trades.append(trade_copy)
            logger.debug(f"Trade added: {trade.get('contract_id')}")
    
    def update_trade(self, contract_id: str, final_status: Dict):
        """Move trade from active to history"""
        with self._lock:
            # Find and remove from active
            trade = None
            for i, t in enumerate(self.active_trades):
                if t.get('contract_id') == contract_id:
                    trade = self.active_trades.pop(i)
                    break
            
            if trade:
                # Add final status
                trade['final_status'] = final_status
                trade['closed_at'] = datetime.now().isoformat()
                trade['pnl'] = final_status.get('profit', 0.0)
                
                # Add to history
                self.trade_history.insert(0, trade)
                
                # Trim history
                if len(self.trade_history) > self.max_history:
                    self.trade_history = self.trade_history[:self.max_history]
                
                logger.debug(f"Trade moved to history: {contract_id}")
    
    def add_signal(self, signal: Dict):
        """Add signal to recent signals"""
        with self._lock:
            signal_copy = signal.copy()
            signal_copy['timestamp'] = datetime.now().isoformat()
            self.recent_signals.insert(0, signal_copy)
            
            # Trim signals
            if len(self.recent_signals) > self.max_signals:
                self.recent_signals = self.recent_signals[:self.max_signals]
            
            logger.debug(f"Signal added: {signal.get('signal')}")
    
    def update_statistics(self, stats: Dict):
        """Update trading statistics"""
        with self._lock:
            self.statistics = stats.copy()
            logger.debug("Statistics updated")
    
    def get_status(self) -> Dict:
        """Get current bot status"""
        with self._lock:
            return {
                'status': self.status,
                'error_message': self.error_message,
                'last_updated': self.last_updated.isoformat(),
                'balance': self.balance
            }
    
    def get_active_trades(self) -> List[Dict]:
        """Get active trades"""
        with self._lock:
            return self.active_trades.copy()
    
    def get_trade_history(self, limit: int = 50) -> List[Dict]:
        """Get trade history"""
        with self._lock:
            return self.trade_history[:limit]
    
    def get_recent_signals(self, limit: int = 20) -> List[Dict]:
        """Get recent signals"""
        with self._lock:
            return self.recent_signals[:limit]
    
    def get_statistics(self) -> Dict:
        """Get trading statistics"""
        with self._lock:
            return self.statistics.copy()
    
    def get_performance(self) -> Dict:
        """Get performance metrics"""
        with self._lock:
            return self.performance.copy()

# Global bot state instance
bot_state = BotState()
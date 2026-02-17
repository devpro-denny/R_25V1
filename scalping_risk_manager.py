"""
Scalping Risk Manager Implementation
Independent risk management for scalping strategy with tighter limits
"""

from base_risk_manager import BaseRiskManager
from typing import Dict, Tuple, List
from datetime import datetime, timedelta
import logging
import scalping_config

logger = logging.getLogger(__name__)


class ScalpingRiskManager(BaseRiskManager):
    """
    Risk manager for scalping strategy with independent limits.
    Features:
    - 4 concurrent trades max
    - 80 trades per day max
    - 30 second cooldown
    - Runaway trade guardrail (10 trades in 10 minutes)
    - Daily counter inheritance from database
    """
    
    def __init__(self, user_id: str = None, overrides: Dict = None):
        """
        Initialize scalping risk manager.
        
        Args:
            user_id: User identifier
            overrides: User-specific parameter overrides from strategy_configs table
        """
        self.user_id = user_id
        
        # Apply overrides if provided
        self.max_concurrent_trades = overrides.get('max_concurrent_trades') if overrides else None
        if self.max_concurrent_trades is None:
            self.max_concurrent_trades = scalping_config.SCALPING_MAX_CONCURRENT_TRADES
        
        self.cooldown_seconds = overrides.get('cooldown_seconds') if overrides else None
        if self.cooldown_seconds is None:
            self.cooldown_seconds = scalping_config.SCALPING_COOLDOWN_SECONDS
        
        self.max_trades_per_day = overrides.get('max_trades_per_day') if overrides else None
        if self.max_trades_per_day is None:
            self.max_trades_per_day = scalping_config.SCALPING_MAX_TRADES_PER_DAY
        
        self.max_consecutive_losses = overrides.get('max_consecutive_losses') if overrides else None
        if self.max_consecutive_losses is None:
            self.max_consecutive_losses = scalping_config.SCALPING_MAX_CONSECUTIVE_LOSSES
        
        self.daily_loss_multiplier = overrides.get('daily_loss_multiplier') if overrides else None
        if self.daily_loss_multiplier is None:
            self.daily_loss_multiplier = scalping_config.SCALPING_DAILY_LOSS_MULTIPLIER
        
        # State tracking
        self.active_trades: List[str] = []  # List of active contract IDs
        self.daily_trade_count = 0
        self.daily_pnl = 0.0
        self.consecutive_losses = 0
        self.last_trade_time = None
        self.stake = 50.0  # Default, will be updated
        
        # Runaway trade protection
        self.recent_trade_timestamps: List[datetime] = []  # Rolling window of last 10 trades
        
        # Load today's stats from database
        self._load_daily_stats_from_db()
        
        logger.info(f"✅ Scalping Risk Manager initialized for user {user_id}")
        logger.info(f"📊 Limits - Concurrent: {self.max_concurrent_trades}, Daily: {self.max_trades_per_day}, Cooldown: {self.cooldown_seconds}s")
    
    def _load_daily_stats_from_db(self) -> None:
        """
        Load today's trade count and P&L from database.
        This ensures daily counters persist across strategy switches.
        """
        try:
            from app.core.supabase import supabase
            from datetime import date
            
            today_start = datetime.combine(date.today(), datetime.min.time())
            
            # Query today's trades for this user
            result = supabase.table('trades') \
                .select('profit, status') \
                .eq('user_id', self.user_id) \
                .gte('created_at', today_start.isoformat()) \
                .execute()
            
            if result.data:
                self.daily_trade_count = len(result.data)
                self.daily_pnl = sum(float(t.get('profit', 0)) for t in result.data if t.get('profit'))
                
                # Calculate consecutive losses from most recent trades
                recent_trades = sorted(result.data, key=lambda x: x.get('created_at', ''), reverse=True)
                self.consecutive_losses = 0
                for trade in recent_trades:
                    if trade.get('status') == 'loss':
                        self.consecutive_losses += 1
                    else:
                        break  # Stop at first non-loss
                
                logger.info(f"📊 Loaded today's stats - Trades: {self.daily_trade_count}, P&L: ${self.daily_pnl:.2f}, Consecutive Losses: {self.consecutive_losses}")
            else:
                logger.info("📊 No trades today yet, starting fresh")
        
        except Exception as e:
            logger.warning(f"⚠️ Could not load daily stats from database: {e}")
            logger.info("   Starting with zero counters")
    
    def can_trade(self) -> Tuple[bool, str]:
        """
        Check if trading is allowed.
        
        Returns:
            Tuple of (can_trade: bool, reason: str)
        """
        # CHECK 1: Concurrent trades limit
        if len(self.active_trades) >= self.max_concurrent_trades:
            return False, f"Max concurrent trades reached ({self.max_concurrent_trades})"
        
        # CHECK 2: Daily trade limit
        if self.daily_trade_count >= self.max_trades_per_day:
            return False, f"Daily trade limit reached ({self.max_trades_per_day})"
        
        # CHECK 3: Cooldown period
        if self.last_trade_time:
            time_since_last = (datetime.now() - self.last_trade_time).total_seconds()
            if time_since_last < self.cooldown_seconds:
                return False, f"Cooldown active ({int(self.cooldown_seconds - time_since_last)}s remaining)"
        
        # CHECK 4: Consecutive losses (circuit breaker)
        if self.consecutive_losses >= self.max_consecutive_losses:
            return False, f"Circuit breaker triggered ({self.consecutive_losses} consecutive losses)"
        
        # CHECK 5: Daily loss limit
        max_daily_loss = self.daily_loss_multiplier * self.stake
        if self.daily_pnl < -max_daily_loss:
            return False, f"Daily loss limit reached (${self.daily_pnl:.2f} < ${-max_daily_loss:.2f})"
        
        # CHECK 6: Runaway trade guardrail
        if len(self.recent_trade_timestamps) >= scalping_config.SCALPING_RUNAWAY_TRADE_COUNT:
            oldest_trade = self.recent_trade_timestamps[0]
            time_window = (datetime.now() - oldest_trade).total_seconds() / 60  # minutes
            
            if time_window < scalping_config.SCALPING_RUNAWAY_WINDOW_MINUTES:
                logger.warning(f"⚠️ RUNAWAY TRADE GUARDRAIL TRIGGERED: {scalping_config.SCALPING_RUNAWAY_TRADE_COUNT} trades in {time_window:.1f} minutes")
                return False, "Runaway trade protection activated"
        
        return True, "All checks passed"
    
    def record_trade_opened(self, trade_info: Dict) -> None:
        """
        Record that a new trade has been opened.
        
        Args:
            trade_info: Dict containing trade details
        """
        contract_id = trade_info.get('contract_id')
        stake = trade_info.get('stake', self.stake)
        
        if contract_id:
            self.active_trades.append(contract_id)
        
        self.daily_trade_count += 1
        self.last_trade_time = datetime.now()
        self.stake = stake
        
        # Add to runaway protection window
        self.recent_trade_timestamps.append(datetime.now())
        
        # Keep only last N trades in window
        if len(self.recent_trade_timestamps) > scalping_config.SCALPING_RUNAWAY_TRADE_COUNT:
            self.recent_trade_timestamps.pop(0)
        
        logger.info(f"📈 Trade opened - Contract: {contract_id}, Daily count: {self.daily_trade_count}/{self.max_trades_per_day}")
        logger.info(f"📊 Active trades: {len(self.active_trades)}/{self.max_concurrent_trades}")
    
    def record_trade_closed(self, result: Dict) -> None:
        """
        Record that a trade has been closed.
        Updates win/loss counters and P&L.
        
        Args:
            result: Dict containing trade result
        """
        contract_id = result.get('contract_id')
        profit = result.get('profit', 0.0)
        status = result.get('status', 'unknown')
        
        # Remove from active trades
        if contract_id in self.active_trades:
            self.active_trades.remove(contract_id)
        
        # Update P&L
        self.daily_pnl += profit
        
        # Update consecutive losses
        if status == 'loss':
            self.consecutive_losses += 1
            logger.info(f"❌ Loss recorded - Consecutive losses: {self.consecutive_losses}/{self.max_consecutive_losses}")
        else:
            self.consecutive_losses = 0  # Reset on win
            if status == 'win':
                logger.info(f"✅ Win recorded - Consecutive losses reset")
        
        logger.info(f"📊 Trade closed - P&L: ${profit:.2f}, Daily P&L: ${self.daily_pnl:.2f}")
        logger.info(f"📊 Active trades: {len(self.active_trades)}/{self.max_concurrent_trades}")
    
    def get_current_limits(self) -> Dict:
        """
        Get current risk parameters and limits.
        
        Returns:
            Dict of active thresholds and current counts
        """
        return {
            'strategy': 'Scalping',
            'max_concurrent_trades': self.max_concurrent_trades,
            'current_concurrent_trades': len(self.active_trades),
            'max_trades_per_day': self.max_trades_per_day,
            'daily_trade_count': self.daily_trade_count,
            'max_consecutive_losses': self.max_consecutive_losses,
            'consecutive_losses': self.consecutive_losses,
            'daily_pnl': self.daily_pnl,
            'max_daily_loss': self.daily_loss_multiplier * self.stake,
            'cooldown_seconds': self.cooldown_seconds,
            'last_trade_time': self.last_trade_time.isoformat() if self.last_trade_time else None,
            'runaway_protection_window_minutes': scalping_config.SCALPING_RUNAWAY_WINDOW_MINUTES,
            'recent_trade_count': len(self.recent_trade_timestamps),
        }
    
    def check_stagnation_exit(self, trade_info: Dict, current_pnl: float) -> Tuple[bool, str]:
        """
        Check if a trade should be closed due to stagnation.
        Scalping trades are closed if open for >2 minutes and losing >15% of stake.
        
        Args:
            trade_info: Dict containing trade details including open_time
            current_pnl: Current profit/loss of the trade
            
        Returns:
            Tuple of (should_exit: bool, reason: str)
        """
        open_time = trade_info.get('open_time')
        stake = trade_info.get('stake', self.stake)
        symbol = trade_info.get('symbol', 'UNKNOWN')
        
        if not open_time:
            return False, ''
        
        # Calculate how long trade has been open
        time_open = (datetime.now() - open_time).total_seconds()
        
        # Check if trade exceeds stagnation time threshold
        if time_open < scalping_config.SCALPING_STAGNATION_EXIT_TIME:
            return False, ''
        
        # Calculate loss percentage
        loss_pct = abs((current_pnl / stake) * 100) if stake > 0 else 0
        
        # Check if trade is losing more than threshold
        if current_pnl >= 0:
            return False, ''  # Not losing
        
        if loss_pct > scalping_config.SCALPING_STAGNATION_LOSS_PCT:
            logger.warning(
                f'[SCALP] Stagnation exit: {symbol} open {int(time_open)}s, '
                f'losing {loss_pct:.1f}% of stake'
            )
            return True, 'stagnation_exit'
        
        return False, ''
    
    def reset_daily_stats(self) -> None:
        """
        Reset daily statistics at midnight.
        """
        logger.info("🔄 Resetting daily stats for scalping risk manager")
        self.daily_trade_count = 0
        self.daily_pnl = 0.0
        self.consecutive_losses = 0
        self.recent_trade_timestamps = []
        logger.info("✅ Daily stats reset complete")

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
        
        # Per-trade metadata (stake, symbol, open_time) keyed by contract_id
        self._trade_metadata: Dict[str, Dict] = {}
        
        # Per-trade trailing profit state keyed by contract_id
        self._trailing_state: Dict[str, Dict] = {}
        
        # Runaway trade protection
        self.recent_trade_timestamps: List[datetime] = []  # Rolling window of last 10 trades
        
        # Load today's stats from database
        self._load_daily_stats_from_db()
        
        pass # logger.info(f"📊 Limits - Concurrent: {self.max_concurrent_trades}, Daily: {self.max_trades_per_day}, Cooldown: {self.cooldown_seconds}s")
    
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
    
    def set_bot_state(self, state):
        """Set BotState instance for real-time API updates (No-op for scalping for now)"""
        pass
        
    def update_risk_settings(self, stake: float):
        """Update risk limits based on user's stake."""
        self.stake = stake
        # Scalping doesn't use dynamic daily loss multiplier based on stake in the same way,
        # but we update the stake reference.
        logger.info(f"🔄 Scalping Risk Stake Updated: ${stake}")

    async def check_for_existing_positions(self, trade_engine) -> bool:
        """
        Check if there are any existing positions on startup.
        For scalping, we start fresh usually, but we should sync if possible.
        """
        # Simple implementation: just warn that we can't fully sync yet
        # or implement actual check if trade_engine supports it.
        # For now, return False to assume no positions or let trade engine handle it.
        return False

    def can_trade(self, symbol: str = None, verbose: bool = False) -> Tuple[bool, str]:
        """
        Check if trading is allowed.
        Updated signature to match RiskManager interface (accepts symbol and verbose).
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
                remaining = int(self.cooldown_seconds - time_since_last)
                return False, f"Cooldown active ({remaining}s remaining)"
        
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
                if verbose:
                    logger.warning(f"⚠️ RUNAWAY TRADE GUARDRAIL: {scalping_config.SCALPING_RUNAWAY_TRADE_COUNT} trades in {time_window:.1f} mins")
                return False, "Runaway trade protection activated"
        
        return True, "All checks passed"

    def can_open_trade(self, symbol: str, stake: float, 
                      take_profit: float = None, stop_loss: float = None,
                      signal_dict: Dict = None) -> Tuple[bool, str]:
        """
        Validate trade opening - simplifies to can_trade check for scalping.
        """
        # check global limits first
        can, reason = self.can_trade(symbol, verbose=True)
        if not can:
            return False, reason
            
        if stake <= 0:
            return False, "Stake must be positive"
            
        return True, "OK"
        
    def get_active_trade_info(self):
        """Return info about first active trade for monitoring, including metadata."""
        if not self.active_trades:
            return None
        contract_id = self.active_trades[0]
        info = {
            'contract_id': contract_id,
            'symbol': 'MULTI',
            'strategy': 'Scalping'
        }
        # Merge stored metadata (stake, symbol, open_time) if available
        meta = self._trade_metadata.get(contract_id, {})
        info.update(meta)
        return info

    def get_cooldown_remaining(self) -> int:
        """Get remaining cooldown in seconds"""
        if not self.last_trade_time:
            return 0
        
        time_since_last = (datetime.now() - self.last_trade_time).total_seconds()
        remaining = self.cooldown_seconds - time_since_last
        return max(0, int(remaining))

    def get_statistics(self) -> Dict:
        """Get current statistics dictionary"""
        return {
            'total_trades': self.daily_trade_count,
            'total_pnl': self.daily_pnl,
            'daily_pnl': self.daily_pnl,
            'win_rate': 0.0, # Not tracking wins separate from pnl yet in this simple view
            'consecutive_losses': self.consecutive_losses
        }
    
    @property
    def has_active_trade(self) -> bool:
        """Property to check if there are active trades"""
        return len(self.active_trades) > 0

    def record_trade_open(self, trade_info: Dict) -> None:
        """
        Record that a new trade has been opened.
        
        Args:
            trade_info: Dict containing trade details
        """
        contract_id = trade_info.get('contract_id')
        stake = trade_info.get('stake', self.stake)
        
        if contract_id:
            self.active_trades.append(contract_id)
            # Store per-trade metadata for monitoring
            self._trade_metadata[contract_id] = {
                'stake': stake,
                'symbol': trade_info.get('symbol', 'UNKNOWN'),
                'open_time': datetime.now(),
                'direction': trade_info.get('direction'),
                'entry_price': trade_info.get('entry_price'),
                'multiplier': trade_info.get('multiplier'),
            }
        
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
    
    def record_trade_opened(self, trade_info: Dict) -> None:
        """
        Alias for record_trade_open to satisfy base class interface.
        This method exists for compatibility with the abstract base class.
        
        Args:
            trade_info: Dict containing trade details
        """
        self.record_trade_open(trade_info)
    
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
        elif isinstance(result, str): # Handle legacy case where just ID passed (rare)
             if result in self.active_trades:
                 self.active_trades.remove(result)

        # Clean up per-trade state
        self._trade_metadata.pop(contract_id, None)
        self._trailing_state.pop(contract_id, None)

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
    
    def record_trade_close(self, contract_id: str, pnl: float, status: str) -> None:
        """
        Wrapper method for compatibility with runner.py.
        Converts individual parameters to a dict and calls record_trade_closed().
        
        Args:
            contract_id: Contract ID
            pnl: Profit/loss amount
            status: Trade status ('win', 'loss', 'breakeven', etc.)
        """
        result = {
            'contract_id': contract_id,
            'profit': pnl,
            'status': status
        }
        self.record_trade_closed(result)
    
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
    
    def should_close_trade(self, contract_id: str, current_pnl: float, 
                          current_price: float = None, previous_price: float = None) -> Dict:
        """
        Check if a scalping trade should be closed manually.
        
        Args:
            contract_id: Contract ID to identify which trade to check
            current_pnl: Current profit/loss
            current_price: Current spot price (optional, for future use)
            previous_price: Previous spot price (optional, for future use)
        
        Returns:
            Dict with should_close flag and reason
        """
        # Find the specific trade by contract_id
        active_trade = None
        for i, contract in enumerate(self.active_trades):
            if contract == contract_id:
                # We're storing just contract IDs, need to get trade info differently
                # For now, we'll work with limited info
                active_trade = {'contract_id': contract_id, 'index': i}
                break
        
        if not active_trade:
            return {'should_close': False, 'reason': 'Trade not found in active trades'}
        
        # Check stagnation exit if enabled
        # Note: For full stagnation checking, we'd need to track trade open_time
        # For now, we'll use a simplified check based on configuration
        if hasattr(scalping_config, 'SCALPING_STAGNATION_EXIT_TIME'):
            # This would require us to track trade metadata, which we'll add later
            pass
        
        # Check emergency daily loss protection
        potential_daily_loss = self.daily_pnl + current_pnl
        max_daily_loss = self.daily_loss_multiplier * self.stake
        
        if potential_daily_loss <= -(max_daily_loss * 0.9):
            return {
                'should_close': True,
                'reason': 'emergency_daily_loss',
                'message': f'Emergency: Daily loss approaching limit (${potential_daily_loss:.2f})',
                'current_pnl': current_pnl
            }
        
        # No exit conditions met
        return {'should_close': False, 'reason': 'monitor_active'}
    
    
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

    def check_trailing_profit(self, trade_info: Dict, current_pnl: float) -> Tuple[bool, str, bool]:
        """
        Check if a scalping trade should be closed via trailing profit.
        
        Once profit reaches SCALPING_TRAIL_ACTIVATION_PCT of stake, trailing activates.
        The trail follows SCALPING_TRAIL_DISTANCE_PCT behind the highest recorded profit.
        If profit drops below (highest - distance), the trade is closed to lock in gains.
        
        Args:
            trade_info: Dict with at least 'contract_id' and 'stake'
            current_pnl: Current unrealized profit/loss
            
        Returns:
            Tuple of (should_close: bool, reason: str, just_activated: bool)
            just_activated is True only on the first call that crosses the activation
            threshold — the caller should remove the server-side TP at this point.
        """
        contract_id = trade_info.get('contract_id')
        stake = trade_info.get('stake', self.stake)
        symbol = trade_info.get('symbol', 'UNKNOWN')
        
        if not contract_id or stake <= 0:
            return False, '', False
        
        # Calculate current profit as percentage of stake
        profit_pct = (current_pnl / stake) * 100
        
        # Not yet at activation threshold
        if profit_pct < scalping_config.SCALPING_TRAIL_ACTIVATION_PCT:
            return False, '', False
        
        # Get or initialize trailing state for this contract
        state = self._trailing_state.get(contract_id)
        
        if state is None:
            # First time reaching activation — initialize trailing
            trail_distance = self._get_trail_distance(profit_pct)
            trail_floor = profit_pct - trail_distance
            self._trailing_state[contract_id] = {
                'highest_profit_pct': profit_pct,
                'trailing_active': True,
            }
            logger.info(
                f"[SCALP] 📈 Trailing profit activated at {profit_pct:.1f}%, "
                f"trail distance {trail_distance:.1f}%, floor at {trail_floor:.1f}%"
            )
            return False, '', True  # just_activated=True → caller should remove server-side TP
        
        # Trailing is active — update highest profit (ratchet up only)
        if profit_pct > state['highest_profit_pct']:
            state['highest_profit_pct'] = profit_pct
        
        # Calculate the trailing floor using tiered distance based on PEAK profit
        trail_distance = self._get_trail_distance(state['highest_profit_pct'])
        trail_floor = state['highest_profit_pct'] - trail_distance
        
        # Check if profit dropped below the trailing floor
        if profit_pct < trail_floor:
            logger.warning(
                f"[SCALP] 🔒 Trailing profit EXIT: {symbol} profit dropped to {profit_pct:.1f}% "
                f"(peak {state['highest_profit_pct']:.1f}%, distance {trail_distance:.1f}%, floor {trail_floor:.1f}%)"
            )
            return True, 'trailing_profit_exit', False
        
        # Still above floor — continue trailing
        logger.debug(
            f"[SCALP] 📈 Trailing: {symbol} profit {profit_pct:.1f}% "
            f"(peak {state['highest_profit_pct']:.1f}%, distance {trail_distance:.1f}%, floor {trail_floor:.1f}%)"
        )
        return False, '', False
    
    def _get_trail_distance(self, profit_pct: float) -> float:
        """
        Get the trailing distance for a given profit percentage using tiered config.
        Higher profit → wider trail to give big winners room to breathe.
        """
        for min_pct, distance in scalping_config.SCALPING_TRAIL_TIERS:
            if profit_pct >= min_pct:
                return distance
        # Fallback (shouldn't reach here since activation is already checked)
        return scalping_config.SCALPING_TRAIL_TIERS[-1][1]
    
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

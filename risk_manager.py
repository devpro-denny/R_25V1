"""
Risk Manager for Deriv R_25 Trading Bot
Manages trading limits, cooldowns, and risk parameters
risk_manager.py - WITH PERCENTAGE-BASED DYNAMIC EXIT & TRAILING STOP
"""

from datetime import datetime, timedelta
from typing import Optional, Dict, List
import config
from utils import setup_logger, format_currency

logger = setup_logger()

class RiskManager:
    """Manages all risk-related operations with percentage-based dynamic exit logic"""
    
    def __init__(self):
        """Initialize RiskManager with default settings"""
        self.max_trades_per_day = config.MAX_TRADES_PER_DAY
        self.max_daily_loss = config.MAX_DAILY_LOSS
        self.cooldown_seconds = config.COOLDOWN_SECONDS
        
        # Trade tracking
        self.trades_today: List[Dict] = []
        self.last_trade_time: Optional[datetime] = None
        self.daily_pnl: float = 0.0
        self.current_date = datetime.now().date()
        
        # ⭐ ACTIVE TRADE TRACKING (ONLY 1 CONCURRENT TRADE ALLOWED) ⭐
        self.active_trade: Optional[Dict] = None
        self.has_active_trade = False
        
        # ⭐ NEW: PERCENTAGE-BASED Dynamic exit settings ⭐
        self.early_exit_threshold = 0.80  # Exit at 80% of target profit if reversal
        self.trailing_stop_activation_pct = 0.75  # Activate trailing stop at 75% of target
        self.trailing_stop_distance_pct = 0.15  # Trail 15% below peak profit
        
        # ⭐ NEW: Trailing stop tracking ⭐
        self.peak_profit: float = 0.0
        self.trailing_stop_active: bool = False
        self.trailing_stop_level: float = 0.0
        
        # Statistics
        self.total_trades = 0
        self.winning_trades = 0
        self.losing_trades = 0
        self.total_pnl = 0.0
        self.largest_win = 0.0
        self.largest_loss = 0.0
        self.max_drawdown = 0.0
        self.peak_balance = 0.0
        
        logger.info("[OK] Risk Manager initialized (Percentage-Based Dynamic Exit)")
        logger.info(f"   Early exit: {self.early_exit_threshold*100:.0f}% of target")
        logger.info(f"   Trailing stop: Activates at {self.trailing_stop_activation_pct*100:.0f}% of target")
        logger.info(f"   Trailing distance: {self.trailing_stop_distance_pct*100:.0f}% below peak")
    
    def reset_daily_stats(self):
        """Reset daily statistics at start of new day"""
        current_date = datetime.now().date()
        
        if current_date != self.current_date:
            logger.info(f"📅 New trading day - Resetting daily stats")
            self.current_date = current_date
            self.trades_today = []
            self.daily_pnl = 0.0
            self.last_trade_time = None
            
            # Clear active trade tracking for new day
            self.active_trade = None
            self.has_active_trade = False
            self._reset_trailing_stop()
    
    def can_trade(self) -> tuple[bool, str]:
        """
        Check if trading is allowed based on risk rules
        
        Returns:
            Tuple of (can_trade: bool, reason: str)
        """
        # Reset stats if new day
        self.reset_daily_stats()
        
        # ⭐ CRITICAL: Check if there's an active trade (ONLY 1 CONCURRENT TRADE) ⭐
        if self.has_active_trade:
            reason = "Active trade in progress (only 1 concurrent trade allowed)"
            logger.debug(f"⏸️ {reason}")
            return False, reason
        
        # Check daily trade limit
        if len(self.trades_today) >= self.max_trades_per_day:
            reason = f"Daily trade limit reached ({self.max_trades_per_day} trades)"
            logger.warning(f"⚠️ {reason}")
            return False, reason
        
        # Check daily loss limit
        if self.daily_pnl <= -self.max_daily_loss:
            reason = f"Daily loss limit reached ({format_currency(self.daily_pnl)})"
            logger.warning(f"⚠️ {reason}")
            return False, reason
        
        # Check cooldown period
        if self.last_trade_time:
            time_since_last = (datetime.now() - self.last_trade_time).total_seconds()
            if time_since_last < self.cooldown_seconds:
                remaining = self.cooldown_seconds - time_since_last
                reason = f"Cooldown active ({remaining:.0f}s remaining)"
                return False, reason
        
        return True, "OK"
    
    def validate_trade_parameters(self, stake: float, take_profit: float, 
                                  stop_loss: float) -> tuple[bool, str]:
        """
        Validate trade parameters against risk rules
        
        Args:
            stake: Trade stake amount
            take_profit: Take profit amount
            stop_loss: Stop loss amount
        
        Returns:
            Tuple of (is_valid: bool, message: str)
        """
        # Check stake
        if stake <= 0:
            return False, "Stake must be positive"
        
        if stake > config.FIXED_STAKE * 2:
            return False, f"Stake exceeds maximum ({config.FIXED_STAKE * 2})"
        
        # Check take profit
        if take_profit <= 0:
            return False, "Take profit must be positive"
        
        # Check stop loss
        if stop_loss <= 0:
            return False, "Stop loss must be positive"
        
        if stop_loss > config.MAX_LOSS_PER_TRADE:
            return False, f"Stop loss exceeds maximum ({config.MAX_LOSS_PER_TRADE})"
        
        # Check risk/reward ratio (optional)
        risk_reward_ratio = take_profit / stop_loss
        if risk_reward_ratio < 0.5:
            logger.warning(f"⚠️ Low risk/reward ratio: {risk_reward_ratio:.2f}")
        
        return True, "Valid"
    
    def record_trade_open(self, trade_info: Dict):
        """
        Record a new trade opening
        
        Args:
            trade_info: Dictionary with trade information
        """
        trade_record = {
            'timestamp': datetime.now(),
            'contract_id': trade_info.get('contract_id'),
            'direction': trade_info.get('direction'),
            'stake': trade_info.get('stake', 0.0),
            'entry_price': trade_info.get('entry_price', 0.0),
            'take_profit': trade_info.get('take_profit', 0.0),
            'stop_loss': trade_info.get('stop_loss', 0.0),
            'status': 'open'
        }
        
        self.trades_today.append(trade_record)
        self.last_trade_time = datetime.now()
        self.total_trades += 1
        
        # ⭐ Mark that we have an active trade (ONLY 1 CONCURRENT) ⭐
        self.active_trade = trade_record
        self.has_active_trade = True
        
        # ⭐ NEW: Reset trailing stop for new trade ⭐
        self._reset_trailing_stop()
        
        logger.info(f"📝 Trade recorded: {trade_info.get('direction')} @ {trade_info.get('entry_price')}")
        logger.info(f"🔒 Active trade locked (1/1 concurrent trades)")
        
        # ⭐ NEW: Log dynamic exit thresholds ⭐
        target_profit = trade_record['take_profit']
        trailing_activation = target_profit * self.trailing_stop_activation_pct
        early_exit_level = target_profit * self.early_exit_threshold
        
        logger.info(f"📊 Dynamic Exit Levels:")
        logger.info(f"   Trailing Stop Activates: {format_currency(trailing_activation)} ({self.trailing_stop_activation_pct*100:.0f}% of target)")
        logger.info(f"   Early Exit Threshold: {format_currency(early_exit_level)} ({self.early_exit_threshold*100:.0f}% of target)")
    
    def _reset_trailing_stop(self):
        """Reset trailing stop tracking"""
        self.peak_profit = 0.0
        self.trailing_stop_active = False
        self.trailing_stop_level = 0.0
    
    def should_close_trade(self, current_pnl: float, current_price: float, 
                          previous_price: float) -> Dict:
        """
        ⭐ NEW: Check if trade should be closed based on PERCENTAGE-BASED dynamic exit rules ⭐
        
        Args:
            current_pnl: Current profit/loss
            current_price: Current market price
            previous_price: Previous candle's close price
        
        Returns:
            Dict with close decision and reason
        """
        if not self.active_trade:
            return {'should_close': False, 'reason': 'No active trade'}
        
        target_profit = self.active_trade.get('take_profit', 0.0)
        direction = self.active_trade.get('direction', '')
        
        # Update peak profit
        if current_pnl > self.peak_profit:
            self.peak_profit = current_pnl
        
        # ⭐ RULE 1: Activate trailing stop at 75% of target profit ⭐
        trailing_activation_level = target_profit * self.trailing_stop_activation_pct
        
        if current_pnl >= trailing_activation_level and not self.trailing_stop_active:
            self.trailing_stop_active = True
            # Trail distance is 15% below current profit
            self.trailing_stop_level = current_pnl * (1 - self.trailing_stop_distance_pct)
            
            logger.info(f"🎯 Trailing stop ACTIVATED at {format_currency(current_pnl)}")
            logger.info(f"   Initial trailing stop: {format_currency(self.trailing_stop_level)}")
            logger.info(f"   (Trailing {self.trailing_stop_distance_pct*100:.0f}% below peak)")
        
        # ⭐ RULE 2: Update trailing stop as profit increases ⭐
        if self.trailing_stop_active:
            # New stop level is always 15% below peak profit
            new_stop_level = self.peak_profit * (1 - self.trailing_stop_distance_pct)
            
            if new_stop_level > self.trailing_stop_level:
                old_level = self.trailing_stop_level
                self.trailing_stop_level = new_stop_level
                logger.debug(f"📊 Trailing stop updated: {format_currency(old_level)} → {format_currency(new_stop_level)}")
            
            # Check if trailing stop hit
            if current_pnl <= self.trailing_stop_level:
                return {
                    'should_close': True,
                    'reason': 'trailing_stop',
                    'message': f'Trailing stop hit at {format_currency(current_pnl)} (peak: {format_currency(self.peak_profit)}, secured {(current_pnl/self.peak_profit)*100:.0f}% of peak)',
                    'current_pnl': current_pnl,
                    'peak_profit': self.peak_profit
                }
        
        # ⭐ RULE 3: Early exit at 80% of target if reversal detected ⭐
        early_exit_target = target_profit * self.early_exit_threshold
        
        if current_pnl >= early_exit_target:
            # Detect reversal based on price movement
            reversal_detected = self._detect_reversal(
                current_price, 
                previous_price, 
                direction
            )
            
            if reversal_detected:
                return {
                    'should_close': True,
                    'reason': 'early_exit',
                    'message': f'Early exit at {format_currency(current_pnl)} ({current_pnl/target_profit*100:.0f}% of {format_currency(target_profit)} target) - Reversal detected',
                    'current_pnl': current_pnl,
                    'target_profit': target_profit,
                    'percentage': current_pnl/target_profit*100
                }
        
        return {'should_close': False, 'reason': 'Continue monitoring'}
    
    def _detect_reversal(self, current_price: float, previous_price: float, 
                        direction: str) -> bool:
        """
        ⭐ NEW: Detect if price is reversing against trade direction ⭐
        
        Args:
            current_price: Current market price
            previous_price: Previous candle's close price
            direction: Trade direction ('BUY' or 'SELL')
        
        Returns:
            True if reversal detected
        """
        if direction.upper() in ['BUY', 'UP']:
            # For BUY trades, reversal = price moving down
            if current_price < previous_price:
                price_drop = ((previous_price - current_price) / previous_price) * 100
                logger.debug(f"⚠️ Reversal hint: Price dropped {price_drop:.2f}% ({previous_price:.2f} → {current_price:.2f})")
                return True
        
        else:  # SELL/DOWN
            # For SELL trades, reversal = price moving up
            if current_price > previous_price:
                price_rise = ((current_price - previous_price) / previous_price) * 100
                logger.debug(f"⚠️ Reversal hint: Price rose {price_rise:.2f}% ({previous_price:.2f} → {current_price:.2f})")
                return True
        
        return False
    
    def get_exit_status(self, current_pnl: float) -> Dict:
        """
        ⭐ NEW: Get current exit strategy status ⭐
        
        Args:
            current_pnl: Current profit/loss
        
        Returns:
            Dict with exit strategy status
        """
        if not self.active_trade:
            return {'active': False}
        
        target_profit = self.active_trade.get('take_profit', 0.0)
        early_exit_target = target_profit * self.early_exit_threshold
        trailing_activation = target_profit * self.trailing_stop_activation_pct
        
        status = {
            'active': True,
            'current_pnl': current_pnl,
            'target_profit': target_profit,
            'early_exit_target': early_exit_target,
            'trailing_activation_level': trailing_activation,
            'percentage_to_target': (current_pnl / target_profit * 100) if target_profit > 0 else 0,
            'percentage_to_trailing': (current_pnl / trailing_activation * 100) if trailing_activation > 0 else 0,
            'trailing_stop_active': self.trailing_stop_active,
            'trailing_stop_level': self.trailing_stop_level if self.trailing_stop_active else None,
            'peak_profit': self.peak_profit,
            'distance_to_early_exit': early_exit_target - current_pnl,
            'distance_to_trailing': trailing_activation - current_pnl
        }
        
        return status
    
    def record_trade_close(self, contract_id: str, pnl: float, status: str):
        """
        Record trade closure and update statistics
        
        Args:
            contract_id: Contract ID
            pnl: Profit/loss amount
            status: Trade status ('won', 'lost', 'sold', 'trailing_stop', 'early_exit')
        """
        # Find trade in today's list
        trade = None
        for t in self.trades_today:
            if t.get('contract_id') == contract_id:
                trade = t
                break
        
        if trade:
            trade['status'] = status
            trade['pnl'] = pnl
            trade['close_time'] = datetime.now()
            
            # ⭐ NEW: Record exit strategy used ⭐
            if self.trailing_stop_active:
                trade['exit_type'] = 'trailing_stop'
                trade['peak_profit'] = self.peak_profit
                trade['secured_percentage'] = (pnl / self.peak_profit * 100) if self.peak_profit > 0 else 0
            elif status == 'early_exit':
                trade['exit_type'] = 'early_exit'
                trade['target_percentage'] = (pnl / trade.get('take_profit', 1)) * 100
            else:
                trade['exit_type'] = 'normal'
        
        # ⭐ Clear active trade (ALLOW NEW TRADES) ⭐
        if self.active_trade and self.active_trade.get('contract_id') == contract_id:
            self.active_trade = None
            self.has_active_trade = False
            self._reset_trailing_stop()
            logger.info(f"🔓 Trade slot unlocked (0/1 concurrent trades)")
        
        # Update P&L
        self.daily_pnl += pnl
        self.total_pnl += pnl
        
        # Update win/loss stats
        if pnl > 0:
            self.winning_trades += 1
            if pnl > self.largest_win:
                self.largest_win = pnl
        elif pnl < 0:
            self.losing_trades += 1
            if pnl < self.largest_loss:
                self.largest_loss = pnl
        
        # Update drawdown
        if self.total_pnl > self.peak_balance:
            self.peak_balance = self.total_pnl
        
        current_drawdown = self.peak_balance - self.total_pnl
        if current_drawdown > self.max_drawdown:
            self.max_drawdown = current_drawdown
        
        logger.info(f"💰 Trade closed: {status.upper()} | P&L: {format_currency(pnl)}")
        logger.info(f"📊 Daily P&L: {format_currency(self.daily_pnl)} | Total: {format_currency(self.total_pnl)}")
    
    def get_statistics(self) -> Dict:
        """
        Get trading statistics
        
        Returns:
            Dictionary with statistics
        """
        win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades > 0 else 0
        
        # ⭐ NEW: Count exit types ⭐
        trailing_stop_exits = sum(1 for t in self.trades_today if t.get('exit_type') == 'trailing_stop')
        early_exits = sum(1 for t in self.trades_today if t.get('exit_type') == 'early_exit')
        
        return {
            'total_trades': self.total_trades,
            'winning_trades': self.winning_trades,
            'losing_trades': self.losing_trades,
            'win_rate': win_rate,
            'total_pnl': self.total_pnl,
            'daily_pnl': self.daily_pnl,
            'trades_today': len(self.trades_today),
            'largest_win': self.largest_win,
            'largest_loss': self.largest_loss,
            'max_drawdown': self.max_drawdown,
            'peak_balance': self.peak_balance,
            'trailing_stop_exits': trailing_stop_exits,
            'early_exits': early_exits
        }
    
    def get_remaining_trades_today(self) -> int:
        """Get number of remaining trades allowed today"""
        return max(0, self.max_trades_per_day - len(self.trades_today))
    
    def get_remaining_loss_capacity(self) -> float:
        """Get remaining loss capacity for today"""
        return max(0, self.max_daily_loss + self.daily_pnl)
    
    def get_cooldown_remaining(self) -> float:
        """
        Get remaining cooldown time in seconds
        
        Returns:
            Seconds remaining (0 if no cooldown)
        """
        if not self.last_trade_time:
            return 0.0
        
        elapsed = (datetime.now() - self.last_trade_time).total_seconds()
        remaining = self.cooldown_seconds - elapsed
        
        return max(0.0, remaining)
    
    def print_status(self):
        """Print current risk management status"""
        can_trade, reason = self.can_trade()
        
        print("\n" + "="*60)
        print("RISK MANAGEMENT STATUS")
        print("="*60)
        print(f"Can Trade: {'✅ YES' if can_trade else '❌ NO'}")
        if not can_trade:
            print(f"Reason: {reason}")
        print(f"Active Trades: {1 if self.has_active_trade else 0}/1")
        if self.has_active_trade and self.active_trade:
            print(f"  └─ {self.active_trade.get('direction')} @ {self.active_trade.get('entry_price', 0):.2f}")
            if self.trailing_stop_active:
                print(f"  └─ Trailing Stop: {format_currency(self.trailing_stop_level)} (Peak: {format_currency(self.peak_profit)})")
        print(f"Trades Today: {len(self.trades_today)}/{self.max_trades_per_day}")
        print(f"Daily P&L: {format_currency(self.daily_pnl)}")
        print(f"Cooldown: {self.get_cooldown_remaining():.0f}s remaining")
        print("="*60 + "\n")
    
    def is_within_trading_hours(self) -> bool:
        """
        Check if within trading hours (synthetic indices trade 24/7)
        
        Returns:
            True (always for synthetic indices)
        """
        return True

# Testing
if __name__ == "__main__":
    print("="*60)
    print("TESTING PERCENTAGE-BASED RISK MANAGER")
    print("="*60)
    
    # Create risk manager
    rm = RiskManager()
    
    # Test with $10 stake, $3 target
    print("\n1. Testing with $10 stake, $3 target profit...")
    trade_info = {
        'contract_id': 'test_001',
        'direction': 'BUY',
        'stake': 10.0,
        'entry_price': 100.0,
        'take_profit': 3.0,
        'stop_loss': 3.0
    }
    rm.record_trade_open(trade_info)
    
    print(f"\nExpected levels:")
    print(f"  Trailing activation: $2.25 (75% of $3.00)")
    print(f"  Early exit: $2.40 (80% of $3.00)")
    
    # Test trailing stop
    print("\n2. Testing trailing stop activation at 75%...")
    test_scenarios = [
        (1.00, 100.5, 100.3, "Below activation"),
        (2.30, 101.0, 100.8, "Should activate trailing stop (75%+)"),
        (2.80, 102.0, 101.8, "Higher profit - trail should update"),
        (2.30, 101.4, 102.0, "Drop to trailing stop level"),
    ]
    
    for pnl, current_price, prev_price, description in test_scenarios:
        result = rm.should_close_trade(pnl, current_price, prev_price)
        status = rm.get_exit_status(pnl)
        
        print(f"\n   {description}")
        print(f"   P&L: {format_currency(pnl)} ({status['percentage_to_target']:.0f}% of target)")
        print(f"   Trailing Active: {status['trailing_stop_active']}")
        if status['trailing_stop_active']:
            print(f"   Trail Level: {format_currency(status['trailing_stop_level'])}")
        print(f"   Should Close: {result['should_close']}")
        
        if result['should_close']:
            print(f"   ⚠️ EXIT: {result.get('message', '')}")
            break
    
    print("\n" + "="*60)
    print("✅ TEST COMPLETE!")
    print("="*60)
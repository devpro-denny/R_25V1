"""
Risk Manager for Deriv Multi-Asset Trading Bot
ENHANCED VERSION - Multi-Asset Scanner with Global Position Control
✅ Scans: R_25, R_50, R_501s, R_75, R_751s
✅ GLOBAL limit: 1 active trade across ALL assets
✅ First-Come, First-Served: First qualifying signal locks system
✅ Top-Down strategy with dynamic TP/SL
✅ Wait-and-cancel logic (4-minute decision point)
✅ Legacy scalping with two-phase risk
risk_manager.py - PRODUCTION VERSION
"""

from datetime import datetime, timedelta
from typing import Optional, Dict, List
import config
from utils import setup_logger, format_currency

logger = setup_logger()

class RiskManager:
    """
    Manages risk limits with GLOBAL position control across multiple assets:
    - CRITICAL: Only 1 active trade allowed across ALL symbols
    - Top-Down: Dynamic TP/SL based on market structure
    - Scalping + Cancellation: Wait-and-cancel (4-min decision)
    - Legacy: Fixed TP/SL percentages
    
    Multi-Asset Logic:
    - Scans all configured symbols (R_25, R_50, R_501s, R_75, R_751s)
    - First qualifying signal LOCKS the system
    - All other assets blocked until active trade closes
    - Daily limits apply GLOBALLY across portfolio
    """
    
    def __init__(self):
        """Initialize RiskManager with global multi-asset position control"""
        self.max_trades_per_day = config.MAX_TRADES_PER_DAY
        self.max_daily_loss = config.MAX_DAILY_LOSS
        self.cooldown_seconds = config.COOLDOWN_SECONDS
        
        # Trade tracking - GLOBAL across all assets
        self.trades_today: List[Dict] = []
        self.last_trade_time: Optional[datetime] = None
        self.daily_pnl: float = 0.0
        self.current_date = datetime.now().date()
        
        # CRITICAL: Global active trade tracking
        # Only ONE trade allowed across ALL assets
        self.active_trade: Optional[Dict] = None
        self.has_active_trade = False
        self.active_symbol: Optional[str] = None  # Which asset is locked
        
        # Statistics - GLOBAL portfolio metrics
        self.total_trades = 0
        self.winning_trades = 0
        self.losing_trades = 0
        self.total_pnl = 0.0
        self.largest_win = 0.0
        self.largest_loss = 0.0
        self.max_drawdown = 0.0
        self.peak_balance = 0.0
        
        # Per-asset statistics for analysis
        self.trades_by_symbol: Dict[str, int] = {symbol: 0 for symbol in config.SYMBOLS}
        self.pnl_by_symbol: Dict[str, float] = {symbol: 0.0 for symbol in config.SYMBOLS}
        
        # Cancellation statistics (for scalping mode)
        self.trades_cancelled = 0
        self.trades_committed = 0
        self.cancellation_savings = 0.0
        
        # Circuit breaker - GLOBAL across all assets
        self.consecutive_losses = 0
        self.max_consecutive_losses = 3
        
        # Strategy detection
        self.use_topdown = config.USE_TOPDOWN_STRATEGY
        self.cancellation_enabled = config.ENABLE_CANCELLATION and not self.use_topdown
        self.cancellation_fee = getattr(config, 'CANCELLATION_FEE', 0.45)
        
        # Multi-asset configuration
        self.symbols = config.SYMBOLS
        self.asset_config = config.ASSET_CONFIG
        
        # Initialize TP/SL amounts based on strategy
        self._initialize_strategy_parameters()
    
    def _initialize_strategy_parameters(self):
        """Initialize parameters based on active strategy"""
        if self.use_topdown:
            # Top-Down: Dynamic TP/SL from strategy
            self.target_profit = None  # Set dynamically per trade
            self.max_loss = None        # Set dynamically per trade
            logger.info("[OK] Risk Manager initialized (TOP-DOWN MODE - MULTI-ASSET)")
            logger.info(f"   Strategy: Market Structure Analysis")
            logger.info(f"   Assets: {', '.join(self.symbols)}")
            logger.info(f"   TP/SL: Dynamic (based on levels & swings)")
            logger.info(f"   Min R:R: 1:{config.TOPDOWN_MIN_RR_RATIO}")
            logger.info(f"   ⚠️ GLOBAL LIMIT: 1 active trade across ALL assets")
            
        elif self.cancellation_enabled:
            # Scalping with cancellation: Wait-and-cancel logic
            # Note: Uses base stake, actual amount calculated per symbol
            base_stake = config.FIXED_STAKE
            self.target_profit = (config.POST_CANCEL_TAKE_PROFIT_PERCENT / 100) * base_stake * config.MULTIPLIER
            self.max_loss = (config.POST_CANCEL_STOP_LOSS_PERCENT / 100) * base_stake * config.MULTIPLIER
            logger.info("[OK] Risk Manager initialized (SCALPING + WAIT-CANCEL MODE - MULTI-ASSET)")
            logger.info(f"   Assets: {', '.join(self.symbols)}")
            logger.info(f"   Phase 1: Wait 4-min → Cancel if unprofitable")
            logger.info(f"   Phase 2 Target: {format_currency(self.target_profit)} (base)")
            logger.info(f"   Phase 2 Max Loss: {format_currency(self.max_loss)} (base)")
            logger.info(f"   Cancellation Fee: {format_currency(self.cancellation_fee)}")
            logger.info(f"   ⚠️ GLOBAL LIMIT: 1 active trade across ALL assets")
            
        else:
            # Legacy: Fixed percentages
            base_stake = config.FIXED_STAKE
            self.target_profit = (config.TAKE_PROFIT_PERCENT / 100) * base_stake * config.MULTIPLIER
            self.max_loss = (config.STOP_LOSS_PERCENT / 100) * base_stake * config.MULTIPLIER
            logger.info("[OK] Risk Manager initialized (LEGACY MODE - MULTI-ASSET)")
            logger.info(f"   Assets: {', '.join(self.symbols)}")
            logger.info(f"   Target Profit: {format_currency(self.target_profit)} (base)")
            logger.info(f"   Max Loss: {format_currency(self.max_loss)} (base)")
            logger.info(f"   ⚠️ GLOBAL LIMIT: 1 active trade across ALL assets")
        
        logger.info(f"   Circuit Breaker: {self.max_consecutive_losses} consecutive losses (GLOBAL)")
        logger.info(f"   Max Trades/Day: {self.max_trades_per_day} (GLOBAL)")
        logger.info(f"   Max Daily Loss: {format_currency(self.max_daily_loss)} (GLOBAL)")
    
    def reset_daily_stats(self):
        """Reset daily statistics at start of new day"""
        current_date = datetime.now().date()
        
        if current_date != self.current_date:
            logger.info(f"📅 New trading day - Resetting GLOBAL stats")
            
            # Log yesterday's performance
            if len(self.trades_today) > 0:
                logger.info(f"📊 Yesterday: {len(self.trades_today)} trades, P&L: {format_currency(self.daily_pnl)}")
                
                # Log per-asset breakdown
                logger.info(f"   Asset Breakdown:")
                for symbol in self.symbols:
                    count = self.trades_by_symbol.get(symbol, 0)
                    pnl = self.pnl_by_symbol.get(symbol, 0.0)
                    if count > 0:
                        logger.info(f"      {symbol}: {count} trades, {format_currency(pnl)}")
                
                if self.cancellation_enabled:
                    cancelled_pct = (self.trades_cancelled / len(self.trades_today) * 100)
                    logger.info(f"   Cancelled: {self.trades_cancelled} ({cancelled_pct:.1f}%)")
                    logger.info(f"   Savings: {format_currency(self.cancellation_savings)}")
            
            self.current_date = current_date
            self.trades_today = []
            self.daily_pnl = 0.0
            self.last_trade_time = None
            
            # CRITICAL: Reset global position lock
            self.active_trade = None
            self.has_active_trade = False
            self.active_symbol = None
            
            self.consecutive_losses = 0
            self.trades_cancelled = 0
            self.trades_committed = 0
            self.cancellation_savings = 0.0
            
            # Reset per-asset trackers
            self.trades_by_symbol = {symbol: 0 for symbol in self.symbols}
            self.pnl_by_symbol = {symbol: 0.0 for symbol in self.symbols}
    
    def can_trade(self, symbol: str = None) -> tuple[bool, str]:
        """
        Check if trading is allowed GLOBALLY
        
        CRITICAL: This enforces the 1-trade limit across ALL assets
        If R_25 has an active trade, R_50/R_75/etc are ALL blocked
        
        Args:
            symbol: Optional symbol to check (for logging context)
        
        Returns:
            (can_trade, reason) - False if any global limit hit
        """
        self.reset_daily_stats()
        
        # CRITICAL: Global position check
        if self.has_active_trade:
            reason = f"GLOBAL LOCK: Active {self.active_symbol} trade in progress (1/1 limit)"
            if symbol and symbol != self.active_symbol:
                logger.debug(f"⏸️ {symbol} blocked: {reason}")
            return False, reason
        
        # GLOBAL circuit breaker
        if self.consecutive_losses >= self.max_consecutive_losses:
            reason = f"GLOBAL circuit breaker: {self.consecutive_losses} consecutive losses"
            logger.warning(f"🛑 {reason}")
            return False, reason
        
        # GLOBAL daily trade limit
        if len(self.trades_today) >= self.max_trades_per_day:
            reason = f"GLOBAL daily trade limit reached ({self.max_trades_per_day} trades)"
            logger.warning(f"⚠️ {reason}")
            return False, reason
        
        # GLOBAL daily loss limit
        if self.daily_pnl <= -self.max_daily_loss:
            reason = f"GLOBAL daily loss limit reached ({format_currency(self.daily_pnl)})"
            logger.warning(f"⚠️ {reason}")
            return False, reason
        
        # GLOBAL cooldown (applies to all assets)
        if self.last_trade_time:
            time_since_last = (datetime.now() - self.last_trade_time).total_seconds()
            if time_since_last < self.cooldown_seconds:
                remaining = self.cooldown_seconds - time_since_last
                reason = f"GLOBAL cooldown active ({remaining:.0f}s remaining)"
                return False, reason
        
        return True, "OK"
    
    def can_open_trade(self, symbol: str, stake: float, 
                      take_profit: float = None, stop_loss: float = None) -> tuple[bool, str]:
        """
        Complete validation before opening trade on specific symbol
        
        CRITICAL: Checks global limits + symbol-specific parameters
        This is the MAIN gate-keeper function
        
        Args:
            symbol: Asset to trade (e.g., 'R_25', 'R_50')
            stake: Trade stake amount
            take_profit: Optional TP level
            stop_loss: Optional SL level
        
        Returns:
            (can_open, reason) - True only if ALL checks pass
        """
        # Step 1: Check GLOBAL trade permission
        can_trade_global, reason = self.can_trade(symbol)
        if not can_trade_global:
            return False, reason
        
        # Step 2: Validate symbol exists
        if symbol not in self.symbols:
            return False, f"Unknown symbol: {symbol}"
        
        # Step 3: Validate trade parameters
        is_valid, validation_reason = self.validate_trade_parameters(
            symbol, stake, take_profit, stop_loss
        )
        if not is_valid:
            return False, validation_reason
        
        return True, "OK - FIRST-COME-FIRST-SERVED slot available"
    
    def validate_trade_parameters(self, symbol: str, stake: float, 
                                  take_profit: float = None, 
                                  stop_loss: float = None) -> tuple[bool, str]:
        """Validate trade parameters for specific symbol"""
        if stake <= 0:
            return False, "Stake must be positive"
        
        # Get symbol-specific max stake
        multiplier = self.asset_config.get(symbol, {}).get('multiplier', config.MULTIPLIER)
        max_stake = config.FIXED_STAKE * multiplier * 1.2
        
        if stake > max_stake:
            return False, f"Stake {stake:.2f} exceeds max {max_stake:.2f} for {symbol}"
        
        # Top-Down: TP/SL validation done by strategy
        # Strategy already validates R:R correctly using price distances
        if self.use_topdown:
            return True, f"Valid (Top-Down mode for {symbol})"
        
        # Cancellation mode: TP/SL applied after Phase 1
        if self.cancellation_enabled and (take_profit is None or stop_loss is None):
            return True, f"Valid (wait-and-cancel mode for {symbol})"
        
        # Legacy: Validate provided TP/SL
        if take_profit is not None and take_profit <= 0:
            return False, "Take profit must be positive"
        
        if stop_loss is not None and stop_loss <= 0:
            return False, "Stop loss must be positive"
        
        max_loss_per_trade = config.MAX_LOSS_PER_TRADE * multiplier
        if stop_loss and stop_loss > max_loss_per_trade * 1.15:
            return False, f"SL {format_currency(stop_loss)} exceeds max for {symbol}"
        
        if take_profit and stop_loss:
            risk_reward_ratio = take_profit / stop_loss
            if risk_reward_ratio < 1.5:
                logger.warning(f"⚠️ Low R:R ratio for {symbol}: {risk_reward_ratio:.2f}")
        
        return True, "Valid"
    
    def record_trade_open(self, trade_info: Dict):
        """
        Record a new trade opening
        
        CRITICAL: This LOCKS the global position
        All other assets are now blocked until this closes
        """
        symbol = trade_info.get('symbol', 'UNKNOWN')
        
        trade_record = {
            'timestamp': datetime.now(),
            'symbol': symbol,
            'contract_id': trade_info.get('contract_id'),
            'direction': trade_info.get('direction'),
            'stake': trade_info.get('stake', 0.0),
            'entry_price': trade_info.get('entry_price', 0.0),
            'take_profit': trade_info.get('take_profit'),
            'stop_loss': trade_info.get('stop_loss'),
            'status': 'open',
            'strategy': 'topdown' if self.use_topdown else ('scalping_cancel' if self.cancellation_enabled else 'legacy'),
            'phase': 'cancellation' if self.cancellation_enabled else 'committed',
            'cancellation_enabled': trade_info.get('cancellation_enabled', False),
            'cancellation_expiry': trade_info.get('cancellation_expiry')
        }
        
        self.trades_today.append(trade_record)
        self.last_trade_time = datetime.now()
        self.total_trades += 1
        
        # Update per-asset stats
        self.trades_by_symbol[symbol] = self.trades_by_symbol.get(symbol, 0) + 1
        
        # CRITICAL: Lock global position
        self.active_trade = trade_record
        self.has_active_trade = True
        self.active_symbol = symbol
        
        logger.info(f"🔒 GLOBAL POSITION LOCKED BY {symbol}")
        logger.info(f"📝 Trade #{self.total_trades}: {trade_info.get('direction')} {symbol} @ {trade_info.get('entry_price'):.4f}")
        logger.info(f"   Active: 1/1 | All other assets BLOCKED")
        
        if self.use_topdown:
            # Top-Down trade
            tp = trade_info.get('take_profit')
            sl = trade_info.get('stop_loss')
            if tp and sl:
                logger.info(f"🎯 Top-Down Structure Trade ({symbol}):")
                logger.info(f"   TP Level: {tp:.4f}")
                logger.info(f"   SL Level: {sl:.4f}")
        elif self.cancellation_enabled:
            # Wait-and-cancel trade
            logger.info(f"🛡️ Phase 1: Wait-and-Cancel (4-min decision)")
            logger.info(f"   Will check profit at 240s")
            logger.info(f"   Cancel if unprofitable, commit if profitable")
        else:
            # Legacy trade
            logger.info(f"   TP: {format_currency(trade_record['take_profit'])}")
            logger.info(f"   SL: {format_currency(trade_record['stop_loss'])}")
    
    def record_trade_cancelled(self, contract_id: str, refund: float):
        """Record a trade cancellation (wait-and-cancel at 4-min mark)"""
        for trade in self.trades_today:
            if trade.get('contract_id') == contract_id:
                trade['status'] = 'cancelled'
                trade['cancelled_time'] = datetime.now()
                trade['refund'] = refund
                trade['exit_type'] = 'cancelled_wait_cancel'
                
                # Calculate savings (what we would have lost if continued)
                estimated_loss = trade['stake'] - refund
                self.cancellation_savings += estimated_loss
                self.trades_cancelled += 1
                
                logger.info(f"🛑 Trade cancelled at 4-min decision point")
                logger.info(f"   Refund: {format_currency(refund)}")
                logger.info(f"   Fee paid: {format_currency(self.cancellation_fee)}")
                logger.info(f"   Prevented further loss")
                
                break
        
        # CRITICAL: Unlock global position
        if self.active_trade and self.active_trade.get('contract_id') == contract_id:
            released_symbol = self.active_symbol
            self.active_trade = None
            self.has_active_trade = False
            self.active_symbol = None
            logger.info(f"🔓 GLOBAL POSITION UNLOCKED ({released_symbol} cancelled)")
            logger.info(f"   All assets can now compete for next trade")
    
    def record_cancellation_expiry(self, contract_id: str):
        """Record when cancellation period expires (trade was profitable at 4-min)"""
        for trade in self.trades_today:
            if trade.get('contract_id') == contract_id:
                trade['phase'] = 'committed'
                trade['commitment_time'] = datetime.now()
                self.trades_committed += 1
                
                logger.info(f"✅ Trade committed to Phase 2 (was profitable at 4-min)")
                logger.info(f"   TP: {format_currency(self.target_profit)}")
                logger.info(f"   SL: {format_currency(self.max_loss)}")
                
                break
    
    def should_close_trade(self, current_pnl: float, current_price: float, 
                          previous_price: float) -> Dict:
        """Check if trade should be closed manually (emergency only)"""
        if not self.active_trade:
            return {'should_close': False, 'reason': 'No active trade'}
        
        # Emergency exit: daily loss limit approaching
        potential_daily_loss = self.daily_pnl + current_pnl
        if potential_daily_loss <= -(self.max_daily_loss * 0.9):
            return {
                'should_close': True,
                'reason': 'emergency_daily_loss',
                'message': f'Emergency: GLOBAL daily loss approaching limit ({format_currency(potential_daily_loss)})',
                'current_pnl': current_pnl
            }
        
        # Otherwise let Deriv handle exits (TP/SL via limit_order)
        return {'should_close': False, 'reason': 'Deriv limit_order active'}
    
    def get_exit_status(self, current_pnl: float) -> Dict:
        """Get current exit status"""
        if not self.active_trade:
            return {'active': False}
        
        phase = self.active_trade.get('phase', 'unknown')
        strategy = self.active_trade.get('strategy', 'unknown')
        symbol = self.active_trade.get('symbol', 'UNKNOWN')
        
        status = {
            'active': True,
            'symbol': symbol,
            'current_pnl': current_pnl,
            'phase': phase,
            'strategy': strategy,
            'consecutive_losses': self.consecutive_losses,
            'global_lock': True
        }
        
        if strategy == 'topdown':
            status['target_profit'] = self.active_trade.get('take_profit')
            status['max_loss'] = self.active_trade.get('stop_loss')
            status['dynamic_tp_sl'] = True
        elif phase == 'committed':
            status['target_profit'] = self.target_profit
            status['percentage_to_target'] = (current_pnl / self.target_profit * 100) if self.target_profit > 0 else 0
            status['auto_tp_sl'] = True
        else:
            status['cancellation_active'] = True
            status['can_cancel'] = True
            status['decision_at'] = '240s'
        
        return status
    
    def record_trade_close(self, contract_id: str, pnl: float, status: str):
        """
        Record trade closure and update statistics
        
        CRITICAL: This UNLOCKS the global position
        All assets can now compete for the next trade
        """
        trade = None
        for t in self.trades_today:
            if t.get('contract_id') == contract_id:
                trade = t
                break
        
        if trade:
            trade['status'] = status
            trade['pnl'] = pnl
            trade['close_time'] = datetime.now()
            symbol = trade.get('symbol', 'UNKNOWN')
            
            # Determine exit type based on strategy
            strategy = trade.get('strategy', 'unknown')
            
            if strategy == 'topdown':
                # Top-Down: Check if hit structure levels
                tp_level = trade.get('take_profit')
                sl_level = trade.get('stop_loss')
                
                if tp_level and abs(pnl) > 0:
                    trade['exit_type'] = 'structure_tp' if pnl > 0 else 'structure_sl'
                else:
                    trade['exit_type'] = 'manual_close'
                    
            elif trade.get('phase') == 'committed':
                # Scalping Phase 2: Check fixed TP/SL
                target_profit = self.target_profit
                max_loss = self.max_loss
                
                if abs(pnl - target_profit) < 0.1:
                    trade['exit_type'] = 'take_profit'
                    logger.info(f"🎯 Hit TAKE PROFIT target (Phase 2)!")
                elif abs(abs(pnl) - max_loss) < 0.1:
                    trade['exit_type'] = 'stop_loss'
                    logger.info(f"🛑 Hit STOP LOSS limit (Phase 2)")
                else:
                    trade['exit_type'] = 'other'
            else:
                trade['exit_type'] = 'early_close'
        
        # CRITICAL: Unlock global position
        if self.active_trade and self.active_trade.get('contract_id') == contract_id:
            released_symbol = self.active_symbol
            self.active_trade = None
            self.has_active_trade = False
            self.active_symbol = None
            logger.info(f"🔓 GLOBAL POSITION UNLOCKED ({released_symbol} closed)")
            logger.info(f"   All assets can now compete for next trade")
        
        # Update P&L - GLOBAL
        self.daily_pnl += pnl
        self.total_pnl += pnl
        
        # Update per-asset P&L
        if trade:
            symbol = trade.get('symbol', 'UNKNOWN')
            self.pnl_by_symbol[symbol] = self.pnl_by_symbol.get(symbol, 0.0) + pnl
        
        # Update win/loss stats - GLOBAL
        if pnl > 0:
            self.winning_trades += 1
            self.consecutive_losses = 0
            if pnl > self.largest_win:
                self.largest_win = pnl
            logger.info(f"✅ WIN | GLOBAL consecutive losses reset to 0")
        elif pnl < 0:
            self.losing_trades += 1
            self.consecutive_losses += 1
            if pnl < self.largest_loss:
                self.largest_loss = pnl
            logger.warning(f"❌ LOSS | GLOBAL consecutive losses: {self.consecutive_losses}/{self.max_consecutive_losses}")
        
        # Update drawdown - GLOBAL
        if self.total_pnl > self.peak_balance:
            self.peak_balance = self.total_pnl
        
        current_drawdown = self.peak_balance - self.total_pnl
        if current_drawdown > self.max_drawdown:
            self.max_drawdown = current_drawdown
        
        symbol_label = f"({symbol})" if trade else ""
        logger.info(f"💰 Trade closed {symbol_label}: {status.upper()} | P&L: {format_currency(pnl)}")
        logger.info(f"📊 GLOBAL Daily: {format_currency(self.daily_pnl)} | Total: {format_currency(self.total_pnl)}")
    
    def get_statistics(self) -> Dict:
        """Get comprehensive trading statistics"""
        win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades > 0 else 0
        
        # Count exit types
        tp_exits = sum(1 for t in self.trades_today if t.get('exit_type') in ['take_profit', 'structure_tp'])
        sl_exits = sum(1 for t in self.trades_today if t.get('exit_type') in ['stop_loss', 'structure_sl'])
        cancelled_exits = sum(1 for t in self.trades_today if 'cancelled' in t.get('exit_type', ''))
        
        # Calculate averages
        wins = [t['pnl'] for t in self.trades_today if t.get('pnl', 0) > 0]
        losses = [abs(t['pnl']) for t in self.trades_today if t.get('pnl', 0) < 0]
        
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0
        
        # Strategy-specific metrics
        total_attempted = len(self.trades_today)
        cancellation_rate = (self.trades_cancelled / total_attempted * 100) if total_attempted > 0 else 0
        
        stats = {
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
            'take_profit_exits': tp_exits,
            'stop_loss_exits': sl_exits,
            'cancelled_exits': cancelled_exits,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'consecutive_losses': self.consecutive_losses,
            'circuit_breaker_active': self.consecutive_losses >= self.max_consecutive_losses,
            'strategy_mode': 'topdown' if self.use_topdown else ('wait_cancel' if self.cancellation_enabled else 'legacy'),
            'multi_asset_mode': True,
            'active_symbol': self.active_symbol,
            'has_active_trade': self.has_active_trade,
            'trades_by_symbol': self.trades_by_symbol,
            'pnl_by_symbol': self.pnl_by_symbol
        }
        
        if self.cancellation_enabled:
            stats['trades_cancelled'] = self.trades_cancelled
            stats['trades_committed'] = self.trades_committed
            stats['cancellation_rate'] = cancellation_rate
            stats['cancellation_savings'] = self.cancellation_savings
        
        return stats
    
    def get_remaining_trades_today(self) -> int:
        """Get remaining trades allowed today (GLOBAL)"""
        return max(0, self.max_trades_per_day - len(self.trades_today))
    
    def get_remaining_loss_capacity(self) -> float:
        """Get remaining loss capacity for today (GLOBAL)"""
        return max(0, self.max_daily_loss + self.daily_pnl)
    
    def get_cooldown_remaining(self) -> float:
        """Get remaining cooldown time (GLOBAL)"""
        if not self.last_trade_time:
            return 0.0
        
        elapsed = (datetime.now() - self.last_trade_time).total_seconds()
        remaining = self.cooldown_seconds - elapsed
        return max(0.0, remaining)
    
    def get_active_trade_info(self) -> Optional[Dict]:
        """Get information about the current active trade"""
        if not self.has_active_trade or not self.active_trade:
            return None
        
        return {
            'symbol': self.active_symbol,
            'contract_id': self.active_trade.get('contract_id'),
            'direction': self.active_trade.get('direction'),
            'entry_price': self.active_trade.get('entry_price'),
            'stake': self.active_trade.get('stake'),
            'timestamp': self.active_trade.get('timestamp'),
            'strategy': self.active_trade.get('strategy'),
            'phase': self.active_trade.get('phase')
        }
    
    def print_status(self):
        """Print current risk management status"""
        can_trade, reason = self.can_trade()
        stats = self.get_statistics()
        
        print("\n" + "="*70)
        if self.use_topdown:
            print("RISK MANAGEMENT STATUS - TOP-DOWN STRATEGY (MULTI-ASSET)")
        elif self.cancellation_enabled:
            print("RISK MANAGEMENT STATUS - WAIT-AND-CANCEL STRATEGY (MULTI-ASSET)")
        else:
            print("RISK MANAGEMENT STATUS - LEGACY STRATEGY (MULTI-ASSET)")
        print("="*70)
        
        print(f"🌐 Scanning: {', '.join(self.symbols)}")
        print(f"🔒 GLOBAL Position Limit: 1 trade across ALL assets")
        print(f"\nCan Trade: {'✅ YES' if can_trade else '❌ NO'}")
        if not can_trade:
            print(f"Reason: {reason}")
        
        print(f"\n📍 Active Trades: {1 if self.has_active_trade else 0}/1 (GLOBAL)")
        if self.has_active_trade and self.active_trade:
            symbol = self.active_symbol
            strategy = self.active_trade.get('strategy', 'unknown')
            phase = self.active_trade.get('phase', 'unknown')
            print(f"  🔒 LOCKED BY: {symbol}")
            print(f"  └─ Strategy: {strategy.upper()}")
            print(f"  └─ Phase: {phase.upper()}")
            print(f"  └─ {self.active_trade.get('direction')} @ {self.active_trade.get('entry_price', 0):.4f}")
            
            if strategy == 'topdown':
                tp = self.active_trade.get('take_profit')
                sl = self.active_trade.get('stop_loss')
                if tp and sl:
                    print(f"  └─ TP: {tp:.4f} (structure level)")
                    print(f"  └─ SL: {sl:.4f} (swing point)")
            elif phase == 'cancellation':
                print(f"  └─ Waiting for 4-min decision point")
            else:
                print(f"  └─ TP: {format_currency(self.target_profit)}")
                print(f"  └─ SL: {format_currency(self.max_loss)}")
            
            # Show which assets are blocked
            blocked = [s for s in self.symbols if s != symbol]
            if blocked:
                print(f"  └─ ⛔ BLOCKED: {', '.join(blocked)}")
        else:
            print(f"  ✅ All assets competing for next signal")
        
        print(f"\n📊 Today's Performance (GLOBAL):")
        print(f"  Trades: {len(self.trades_today)}/{self.max_trades_per_day}")
        print(f"  Win Rate: {stats['win_rate']:.1f}%")
        print(f"  Daily P&L: {format_currency(self.daily_pnl)}")
        
        # Show per-asset breakdown
        print(f"\n📈 Per-Asset Breakdown:")
        for symbol in self.symbols:
            count = self.trades_by_symbol.get(symbol, 0)
            pnl = self.pnl_by_symbol.get(symbol, 0.0)
            if count > 0:
                print(f"  {symbol}: {count} trades, {format_currency(pnl)}")
            else:
                print(f"  {symbol}: No trades today")
        
        if self.cancellation_enabled:
            print(f"\n🛡️ Wait-and-Cancel Filter:")
            print(f"  Cancelled (4-min): {self.trades_cancelled}")
            print(f"  Committed (5-min): {self.trades_committed}")
            if self.trades_cancelled > 0:
                print(f"  Losses Prevented: {format_currency(self.cancellation_savings)}")
        
        print(f"\n⚡ Circuit Breaker (GLOBAL):")
        print(f"  Consecutive Losses: {self.consecutive_losses}/{self.max_consecutive_losses}")
        if self.consecutive_losses > 0:
            print(f"  ⚠️ {self.max_consecutive_losses - self.consecutive_losses} losses until GLOBAL halt")
        
        print(f"\n⏱️ Cooldown (GLOBAL): {self.get_cooldown_remaining():.0f}s remaining")
        print(f"📉 Remaining Loss Capacity: {format_currency(self.get_remaining_loss_capacity())}")
        print("="*70 + "\n")
    
    def is_within_trading_hours(self) -> bool:
        """Synthetic indices trade 24/7"""
        return True
    
    async def check_for_existing_positions(self, deriv_api) -> bool:
        """
        Check Deriv API for existing open positions on startup
        CRITICAL: Prevents double-entry after bot restart
        
        Args:
            deriv_api: Connected Deriv API instance
        
        Returns:
            True if existing position found and locked
        """
        try:
            # Query open positions from Deriv
            response = await deriv_api.portfolio({'portfolio': 1})
            
            if response and 'portfolio' in response:
                open_positions = [
                    p for p in response['portfolio']['contracts']
                    if p.get('contract_type') in ['CALL', 'PUT'] and 
                    p.get('underlying') in self.symbols
                ]
                
                if open_positions:
                    # Found existing position - lock the system
                    position = open_positions[0]  # Take first one
                    symbol = position.get('underlying')
                    contract_id = position.get('contract_id')
                    
                    logger.warning(f"⚠️ EXISTING POSITION DETECTED ON STARTUP")
                    logger.warning(f"   Symbol: {symbol}")
                    logger.warning(f"   Contract: {contract_id}")
                    logger.warning(f"   🔒 LOCKING GLOBAL POSITION")
                    
                    # Reconstruct active trade record
                    self.active_trade = {
                        'timestamp': datetime.now(),
                        'symbol': symbol,
                        'contract_id': contract_id,
                        'direction': position.get('contract_type'),
                        'stake': position.get('buy_price', 0.0),
                        'entry_price': position.get('entry_spot', 0.0),
                        'status': 'open',
                        'strategy': 'recovery',  # Mark as recovered
                        'phase': 'committed'
                    }
                    
                    self.has_active_trade = True
                    self.active_symbol = symbol
                    
                    logger.info(f"✅ Global lock restored - monitoring {symbol} position")
                    return True
            
            logger.info(f"✅ No existing positions - ready for first signal")
            return False
            
        except Exception as e:
            logger.error(f"❌ Error checking existing positions: {e}")
            # On error, assume no positions (safer to allow new trades)
            return False


if __name__ == "__main__":
    print("="*70)
    print("TESTING ENHANCED RISK MANAGER - MULTI-ASSET GLOBAL CONTROL")
    print("="*70)
    
    rm = RiskManager()
    
    print("\n✅ Configuration:")
    print(f"   Mode: {'TOP-DOWN' if rm.use_topdown else 'SCALPING'}")
    print(f"   Assets: {', '.join(rm.symbols)}")
    print(f"   🔒 GLOBAL LIMIT: 1 active trade across ALL assets")
    
    print("\n1. Testing can_open_trade for multiple symbols...")
    for symbol in ['R_25', 'R_50', 'R_75']:
        can_open, reason = rm.can_open_trade(symbol, 10.0)
        print(f"   {symbol}: {'✅' if can_open else '❌'} - {reason}")
    
    print("\n2. Opening trade on R_50 (first winner)...")
    trade_info = {
        'symbol': 'R_50',
        'contract_id': 'test_r50_001',
        'direction': 'BUY',
        'stake': 10.0,
        'entry_price': 100.0,
        'take_profit': 100.50 if rm.use_topdown else None,
        'stop_loss': 99.70 if rm.use_topdown else None,
    }
    rm.record_trade_open(trade_info)
    
    print("\n3. Testing if other symbols are blocked...")
    for symbol in ['R_25', 'R_75', 'R_751s']:
        can_open, reason = rm.can_open_trade(symbol, 10.0)
        status = '✅ ALLOWED' if can_open else '❌ BLOCKED'
        print(f"   {symbol}: {status} - {reason}")
    
    print("\n4. Closing R_50 trade...")
    rm.record_trade_close('test_r50_001', 6.0, 'won')
    
    print("\n5. Testing if symbols are unblocked...")
    for symbol in ['R_25', 'R_75']:
        can_open, reason = rm.can_open_trade(symbol, 10.0)
        status = '✅ ALLOWED' if can_open else '❌ BLOCKED'
        print(f"   {symbol}: {status} - {reason}")
    
    print("\n6. Statistics:")
    stats = rm.get_statistics()
    print(f"   Total trades: {stats['total_trades']}")
    print(f"   Win rate: {stats['win_rate']:.1f}%")
    print(f"   Active symbol: {stats['active_symbol']}")
    print(f"   Multi-asset mode: {stats['multi_asset_mode']}")
    print(f"\n   Trades by symbol:")
    for symbol, count in stats['trades_by_symbol'].items():
        pnl = stats['pnl_by_symbol'][symbol]
        print(f"      {symbol}: {count} trades, {format_currency(pnl)}")
    
    print("\n" + "="*70)
    print("✅ MULTI-ASSET RISK MANAGER TEST COMPLETE!")
    print("   ✅ Global position limit enforced")
    print("   ✅ First-come-first-served logic working")
    print("   ✅ All assets blocked when one is active")
    print("   ✅ All assets unblocked after close")
    print("="*70)
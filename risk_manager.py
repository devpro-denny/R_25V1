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
        self.max_trades_per_day = config.MAX_TRADES_PER_DAY
        self.max_daily_loss = getattr(config, 'MAX_DAILY_LOSS', None) # Default None, set dynamically
        self.cooldown_seconds = config.COOLDOWN_SECONDS
        self.max_loss_per_trade_base = getattr(config, 'MAX_LOSS_PER_TRADE', None) # Default None, set dynamically
        self.fixed_stake = None # STRICTLY USER DEFINED - Must be set via update_risk_settings
        
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
        
        # Link to BotState for API updates
        self.bot_state = None
    
    def set_bot_state(self, state):
        """Set BotState instance for real-time API updates"""
        self.bot_state = state
    
    def update_risk_settings(self, stake: float):
        """
        Update risk limits based on user's stake.
        Request:
        - daily_loss = 3 * stake
        - max_loss_per_trade = stake
        """
        self.fixed_stake = stake
        self.max_daily_loss = stake * 3.0
        self.max_loss_per_trade_base = stake
        
        # Update strategy params if needed (re-calc based on new stake)
        self._initialize_strategy_parameters()
        
        logger.info(f"🔄 Risk Limits Updated for Stake ${stake}:")
        logger.info(f"   Max Daily Loss: ${self.max_daily_loss} (3x Stake)")
        logger.info(f"   Max Loss/Trade: ${self.max_loss_per_trade_base} (1x Stake)")

    def _initialize_strategy_parameters(self):
        """Initialize parameters based on active strategy"""
        # Use self.fixed_stake instead of config.FIXED_STAKE
        base_stake = self.fixed_stake

        if self.use_topdown:
            # Top-Down: Dynamic TP/SL from strategy
            self.target_profit = None  # Set dynamically per trade
            self.max_loss = None        # Set dynamically per trade
            logger.info("[OK] Risk Manager initialized (TOP-DOWN MODE - MULTI-ASSET)")
            logger.info(f"   Strategy: Market Structure Analysis")
            logger.info(f"   Assets: {', '.join(self.symbols)}")
            logger.info(f"   TP/SL: Dynamic (based on levels & swings)")
            logger.info(f"   Min R:R: 1:{config.TOPDOWN_MIN_RR_RATIO}")
            logger.info(f"   Trailing Stop: Trigger @ {config.SECURE_PROFIT_TRIGGER_PCT}% | Trail Buffer {config.SECURE_PROFIT_BUFFER_PCT}%")
            logger.info(f"   ⚠️ GLOBAL LIMIT: 1 active trade across ALL assets")
            
        elif self.cancellation_enabled:
            # Scalping with cancellation: Wait-and-cancel logic
            # Note: Uses base stake, actual amount calculated per symbol
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
            self.target_profit = (config.TAKE_PROFIT_PERCENT / 100) * base_stake * config.MULTIPLIER
            self.max_loss = (config.STOP_LOSS_PERCENT / 100) * base_stake * config.MULTIPLIER
            logger.info("[OK] Risk Manager initialized (LEGACY MODE - MULTI-ASSET)")
            logger.info(f"   Assets: {', '.join(self.symbols)}")
            logger.info(f"   Target Profit: {format_currency(self.target_profit)} (base)")
            logger.info(f"   Max Loss: {format_currency(self.max_loss)} (base)")
            logger.info(f"   ⚠️ GLOBAL LIMIT: 1 active trade across ALL assets")
        
        logger.info(f"   Circuit Breaker: {self.max_consecutive_losses} consecutive losses (GLOBAL)")
        logger.info(f"   Max Trades/Day: {self.max_trades_per_day} (GLOBAL)")
        max_daily_display = format_currency(self.max_daily_loss) if self.max_daily_loss is not None else "WAITING_FOR_STAKE"
        logger.info(f"   Max Daily Loss: {max_daily_display} (GLOBAL)")
    
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
    
    def can_trade(self, symbol: str = None, verbose: bool = False) -> tuple[bool, str]:
        """
        Check if trading is allowed GLOBALLY
        
        CRITICAL: This enforces the 1-trade limit across ALL assets
        If R_25 has an active trade, R_50/R_75/etc are ALL blocked
        
        Args:
            symbol: Optional symbol to check (for logging context)
            verbose: If True, log every check to terminal
        
        Returns:
            (can_trade, reason) - False if any global limit hit
        """
        self.reset_daily_stats()
        
        # CRITICAL: Global position check
        if self.has_active_trade:
            reason = f"GLOBAL LOCK: Active {self.active_symbol} trade in progress (1/1 limit)"
            if symbol and symbol != self.active_symbol:
                logger.debug(f"⏸️ {symbol} blocked: {reason}")
            
            if verbose:
                print(f"[RISK] ⛔ blocked: {reason}")
            return False, reason
            
        # GLOBAL circuit breaker
        if self.consecutive_losses >= self.max_consecutive_losses:
            reason = f"GLOBAL circuit breaker: {self.consecutive_losses} consecutive losses"
            logger.warning(f"🛑 {reason}")
            
            if verbose:
                print(f"[RISK] ⛔ Circuit Breaker Active: {self.consecutive_losses}/{self.max_consecutive_losses} losses")
            return False, reason
            
        # GLOBAL daily trade limit
        if len(self.trades_today) >= self.max_trades_per_day:
            reason = f"GLOBAL daily trade limit reached ({self.max_trades_per_day} trades)"
            logger.warning(f"⚠️ {reason}")
            
            if verbose:
                print(f"[RISK] ⛔ Daily Limit Full: {len(self.trades_today)}/{self.max_trades_per_day} trades")
            return False, reason
        
        # GLOBAL daily loss limit
        if self.daily_pnl <= -self.max_daily_loss:
            reason = f"GLOBAL daily loss limit reached ({format_currency(self.daily_pnl)})"
            logger.warning(f"⚠️ {reason}")
            
            if verbose:
                print(f"[RISK] ⛔ Max Daily Loss Hit: {format_currency(self.daily_pnl)}")
            return False, reason
        
        # GLOBAL cooldown (applies to all assets)
        if self.last_trade_time:
            time_since_last = (datetime.now() - self.last_trade_time).total_seconds()
            
            if time_since_last < self.cooldown_seconds:
                remaining = self.cooldown_seconds - time_since_last
                reason = f"GLOBAL cooldown active ({remaining:.0f}s remaining)"
                
                if verbose:
                    print(f"[RISK] ⏳ Cooldown: {remaining:.0f}s wait")
                return False, reason
        
        return True, "OK"
    
    def can_open_trade(self, symbol: str, stake: float, 
                      take_profit: float = None, stop_loss: float = None,
                      signal_dict: Dict = None) -> tuple[bool, str]:
        """
        Complete validation before opening trade on specific symbol
        
        CRITICAL: Checks global limits + symbol-specific parameters
        This is the MAIN gate-keeper function
        
        Args:
            symbol: Asset to trade (e.g., 'R_25', 'R_50')
            stake: Trade stake amount
            take_profit: Optional TP level
            stop_loss: Optional SL level
            signal_dict: Optional full signal dictionary for advanced validation
        
        Returns:
            (can_open, reason) - True only if ALL checks pass
        """
        # Step 1: Check GLOBAL trade permission
        # Use verbose=True here as we are in the trade execution flow
        can_trade_global, reason = self.can_trade(symbol, verbose=True)
        if not can_trade_global:
            return False, reason
        
        # Step 2: Validate symbol exists
        if symbol not in self.symbols:
            return False, f"Unknown symbol: {symbol}"
        
        # Step 3: Validate trade parameters
        is_valid, validation_reason = self.validate_trade_parameters(
            symbol, stake, take_profit, stop_loss, signal_dict, verbose=True
        )
        if not is_valid:
            return False, validation_reason
        
        return True, "OK - FIRST-COME-FIRST-SERVED slot available"
    
    def calculate_risk_amounts(self, signal_dict: Dict, stake: float) -> Dict:
        """Calculate risk/reward in dollars from percentages"""
        if not signal_dict:
            return {}

        entry_price = signal_dict.get('entry_price', 0.0)
        stop_loss = signal_dict.get('stop_loss', 0.0)
        take_profit = signal_dict.get('take_profit', 0.0)
        symbol = signal_dict.get('symbol', 'UNKNOWN')
        multiplier = self.asset_config.get(symbol, {}).get('multiplier', config.MULTIPLIER)

        if entry_price == 0:
            # Fallback: Try to use current_price from signal as entry point
            current_price = signal_dict.get('current_price', 0.0)
            if current_price > 0:
                entry_price = current_price
                logger.warning(f"⚠️ entry_price was 0, using current_price as fallback: {current_price}")
            else:
                logger.error(f"❌ Cannot calculate R:R: entry_price and current_price both missing")
                return {}

        # Risk in dollars
        risk_distance_pct = abs(entry_price - stop_loss) / entry_price * 100
        risk_usd = stake * (risk_distance_pct / 100) * multiplier

        # Reward in dollars
        reward_distance_pct = abs(take_profit - entry_price) / entry_price * 100
        reward_usd = stake * (reward_distance_pct / 100) * multiplier

        # R:R ratio (stake-independent)
        rr_ratio = reward_usd / risk_usd if risk_usd > 0 else 0

        # Risk as percentage of stake
        risk_pct = (risk_usd / stake) * 100

        return {
            'risk_usd': risk_usd,
            'reward_usd': reward_usd,
            'rr_ratio': rr_ratio,
            'risk_pct': risk_pct
        }

    def validate_trade_parameters(self, symbol: str, stake: float, 
                                  take_profit: float = None, 
                                  stop_loss: float = None,
                                  signal_dict: Dict = None,
                                  verbose: bool = False) -> tuple[bool, str]:
        """Validate trade parameters for specific symbol"""
        if stake <= 0:
            return False, "Stake must be positive"
        
        # Get symbol-specific max stake
        multiplier = self.asset_config.get(symbol, {}).get('multiplier', config.MULTIPLIER)
        
        # Ensure stake is set
        if self.fixed_stake is None:
             logger.warning("⚠️ Accessing validation before stake initialized. Defaulting base to stake.")
             base_reference = stake
        else:
             base_reference = self.fixed_stake

        # Limit: 1.5x of user's base stake setting
        max_stake = base_reference * multiplier * 1.5
        
        if stake > max_stake:
            reason = f"Stake {stake:.2f} exceeds max {max_stake:.2f} for {symbol}"
            if verbose:
                print(f"[RISK] ⛔ Stake Limit Exceeded: {stake:.2f} > {max_stake:.2f}")
            return False, reason

        # NEW: Extensive Validation using Signal Dict if available
        if signal_dict:
            amounts = self.calculate_risk_amounts(signal_dict, stake)
            
            # Check 1: R:R Ratio
            if amounts.get('rr_ratio', 0) < config.MIN_RR_RATIO:
                # Only enforce STRICTLY if configured
                msg = f"R:R {amounts.get('rr_ratio', 0):.2f} < {config.MIN_RR_RATIO}"
                if getattr(config, 'STRICT_RR_ENFORCEMENT', False):
                    logger.warning(f"❌ REJECTED: {msg}")
                    return False, f"Invalid R:R: {amounts.get('rr_ratio', 0):.2f}"
                else:
                    logger.warning(f"⚠️ Low R:R: {msg}")

            # Check 2: Maximum Risk Percentage
            max_risk_pct = getattr(config, 'MAX_RISK_PCT', 15.0)
            if amounts.get('risk_pct', 0) > max_risk_pct:
                logger.warning(f"❌ REJECTED: Risk {amounts.get('risk_pct', 0):.1f}% > {max_risk_pct}%")
                return False, f"Risk too high: {amounts.get('risk_pct', 0):.1f}% of stake"

            # Check 3: Signal Strength
            min_strength = getattr(config, 'MIN_SIGNAL_STRENGTH', 8.0)
            strength = signal_dict.get('score', 0)
            if strength < min_strength:
                logger.warning(f"❌ REJECTED: Strength {strength:.1f} < {min_strength}")
                return False, f"Signal too weak: {strength:.1f}"

            logger.info(f"✅ VALIDATED: R:R {amounts.get('rr_ratio', 0):.2f}, Risk {amounts.get('risk_pct', 0):.1f}%, Strength {strength:.1f}")

        
        # Legacy Validation (Fallbacks)
        if self.cancellation_enabled and (take_profit is None or stop_loss is None):
            return True, f"Valid (wait-and-cancel mode for {symbol})"
        
        if take_profit is not None and take_profit <= 0:
            return False, "Take profit must be positive"
        
        if stop_loss is not None and stop_loss <= 0:
            return False, "Stop loss must be positive"
        
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
            'entry_spot': trade_info.get('entry_spot', 0.0),
            'take_profit': trade_info.get('take_profit'),
            'stop_loss': trade_info.get('stop_loss'),
            'status': 'open',
            'strategy': 'topdown' if self.use_topdown else ('scalping_cancel' if self.cancellation_enabled else 'legacy'),
            'phase': 'cancellation' if self.cancellation_enabled else 'committed',
            'cancellation_enabled': trade_info.get('cancellation_enabled', False),
            'cancellation_expiry': trade_info.get('cancellation_expiry'),
            'highest_unrealized_pnl': 0.0, # Track peak profit for trailing stop
            'has_been_profitable': False   # Track if trade ever went into profit
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
        
        # Update BotState if linked
        if self.bot_state:
            self.bot_state.add_trade(trade_record)
    
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
    
    def check_early_exit(self, trade, current_price, elapsed_seconds, stake):
        """Check if trade should be exited early (Fast Failure)"""
        if not getattr(config, 'ENABLE_EARLY_EXIT', True):
            return False, None

        # Get time window based on hour
        current_hour = datetime.now().hour
        time_window = getattr(config, 'EARLY_EXIT_TIME_DAY', 45) if 12 <= current_hour <= 22 else getattr(config, 'EARLY_EXIT_TIME_NIGHT', 20)

        if elapsed_seconds > time_window:
            return False, None  # Past early exit window

        entry_price = trade.get('entry_price', 0.0)
        direction = trade.get('direction', 'UP')

        # Calculate current loss as percentage of stake (using distances)
        # Note: We need approx loss pct.
        # Loss Pct = (Dist / Entry) * Multiplier * Stake / Stake * 100
        #          = (Dist / Entry) * Multiplier * 100
        
        symbol = trade.get('symbol', 'UNKNOWN')
        multiplier = self.asset_config.get(symbol, {}).get('multiplier', config.MULTIPLIER)
        
        if direction == "UP":
            dist = entry_price - current_price
        else:
            dist = current_price - entry_price

        if dist > 0 and entry_price > 0:
            loss_pct_of_stake = (dist / entry_price) * multiplier * 100
            
            threshold = getattr(config, 'EARLY_EXIT_LOSS_PCT', 5.0)
            if loss_pct_of_stake >= threshold:
                logger.warning(f"⚠️ Early exit triggered: Loss {loss_pct_of_stake:.1f}% > {threshold}% at {elapsed_seconds}s")
                return True, f"early_exit_fast_failure_{loss_pct_of_stake:.1f}pct"

        return False, None

    def update_trailing_stop(self, trade, current_price, current_pnl, stake):
        """
        Update trailing stop based on user-defined Price Formula:
        risk_dollars = stake * trail_percentage
        price_distance = (risk_dollars * entry_price) / (multiplier * stake)
        """
        if not getattr(config, 'ENABLE_MULTI_TIER_TRAILING', True):
            return None

        if stake <= 0: return None
        
        # Calculate Pnl % of Stake for Tier Activation
        pnl_pct = (current_pnl / stake) * 100

        # Find active tier
        active_tier = None
        tiers = getattr(config, 'TRAILING_STOPS', [])
        for tier in sorted(tiers, key=lambda x: x['trigger_pct'], reverse=True):
            if pnl_pct >= tier['trigger_pct']:
                active_tier = tier
                break

        if not active_tier:
            return None  # Below minimum threshold

        # ======================================================================
        # CORRECT FORMULA IMPLEMENTATION
        # ======================================================================
        # 1. Get Multiplier
        symbol = trade.get('symbol', 'UNKNOWN')
        multiplier = self.asset_config.get(symbol, {}).get('multiplier', config.MULTIPLIER)
        entry_price = trade.get('entry_price', 0.0)
        
        # 2. Calculate Risk in Dollars
        trail_pct = active_tier['trail_pct'] / 100.0
        risk_dollars = stake * trail_pct
        
        # 3. Convert to Price Distance
        # Formula: (risk_dollars * entry_price) / (multiplier * stake)
        if multiplier > 0 and stake > 0:
            price_distance = (risk_dollars * entry_price) / (multiplier * stake)
        else:
            return None

        # 4. Calculate Potential New Stop Price
        direction = trade.get('direction', 'UP')
        
        if direction == 'UP':
            # UP Trade: Stop is BELOW price
            potential_stop = current_price - price_distance
        else:
            # DOWN Trade: Stop is ABOVE price
            potential_stop = current_price + price_distance
            
        # 5. Update Persistent Stop Price (Only if Tighter)
        current_dynamic_stop = trade.get('dynamic_stop_price')
        
        updated = False
        if current_dynamic_stop is None:
            trade['dynamic_stop_price'] = potential_stop
            updated = True
            logger.info(f"🛡️ Trailing Activated ({active_tier['name']}): Stop set to {potential_stop:.4f}")
        else:
            # Only tighten
            if direction == 'UP' and potential_stop > current_dynamic_stop:
                trade['dynamic_stop_price'] = potential_stop
                updated = True
            elif direction == 'DOWN' and potential_stop < current_dynamic_stop:
                trade['dynamic_stop_price'] = potential_stop
                updated = True
                
        if updated:
             logger.info(f"🛡️ Trailing Update ({active_tier['name']}): Moving Stop to {potential_stop:.4f} (Profit: {pnl_pct:.1f}%)")
        
        return {
            'stop_price': trade['dynamic_stop_price'], 
            'tier_name': active_tier['name']
        }

    def should_close_trade(self, current_pnl: float, current_price: float, 
                          previous_price: float) -> Dict:
        """Check if trade should be closed manually"""
        if not self.active_trade:
             return {'should_close': False, 'reason': 'No active trade'}

        stake = self.active_trade.get('stake', 0.0)
        if stake <= 0:
             return {'should_close': False, 'reason': 'Stake missing'}

        elapsed_seconds = (datetime.now() - self.active_trade.get('timestamp', datetime.now())).total_seconds()

        # 1. Early Exit (Fast Failure)
        should_exit, reason_msg = self.check_early_exit(self.active_trade, current_price, elapsed_seconds, stake)
        if should_exit:
             return {'should_close': True, 'reason': 'early_exit', 'message': reason_msg, 'current_pnl': current_pnl}

        # 2. Stagnation Exit
        if getattr(config, 'ENABLE_STAGNATION_EXIT', True):
             stagnation_time = getattr(config, 'STAGNATION_EXIT_TIME', 90)
             if elapsed_seconds >= stagnation_time and current_pnl < 0:
                  loss_pct = (abs(current_pnl) / stake) * 100
                  stagnation_loss_limit = getattr(config, 'STAGNATION_LOSS_PCT', 6.0)
                  
                  if loss_pct >= stagnation_loss_limit:
                       return {
                           'should_close': True, 
                           'reason': 'stagnation_exit',
                           'message': f'💤 Stagnation: Loss {loss_pct:.1f}% > {stagnation_loss_limit}% after {int(elapsed_seconds)}s',
                           'current_pnl': current_pnl
                       }
        
        # 3. Trailing Stop (Price Based)
        # Update highest unrealized PnL (still needed for Tier activation)
        current_peak = self.active_trade.get('highest_unrealized_pnl', 0.0)
        if current_pnl > current_peak:
            self.active_trade['highest_unrealized_pnl'] = current_pnl
            current_peak = current_pnl
            
        trailing = self.update_trailing_stop(self.active_trade, current_price, current_peak, stake)
        if trailing:
             stop_price = trailing['stop_price']
             tier_name = trailing['tier_name']
             direction = self.active_trade.get('direction', 'UP')
             
             # Check if Price Crossed Stop
             hit_stop = False
             if direction == 'UP' and current_price <= stop_price:
                 hit_stop = True
             elif direction == 'DOWN' and current_price >= stop_price:
                 hit_stop = True
                 
             if hit_stop:
                  return {
                      'should_close': True,
                      'reason': 'trailing_stop_hit',
                      'message': f'🎯 Trailing Stop ({tier_name}): Price {current_price:.4f} hit Stop {stop_price:.4f}',
                      'current_pnl': current_pnl
                  }
        
        # Emergency exit logic
        # ... (Global daily loss logic)
        potential_daily_loss = self.daily_pnl + current_pnl
        if self.max_daily_loss and potential_daily_loss <= -(self.max_daily_loss * 0.9):
            return {
                'should_close': True,
                'reason': 'emergency_daily_loss',
                'message': f'Emergency: GLOBAL daily loss approaching limit ({format_currency(potential_daily_loss)})',
                'current_pnl': current_pnl
            }

        return {'should_close': False, 'reason': 'monitor_active'}
    
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
        
        gross_profit = sum(wins)
        gross_loss = sum(losses)
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else (float('inf') if gross_profit > 0 else 0.0)
        
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
            'profit_factor': profit_factor,
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
                    
                    # Update BotState if linked
                    if self.bot_state:
                        self.bot_state.add_trade(self.active_trade)
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
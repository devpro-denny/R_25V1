"""
Bot Runner - Multi-Asset Sequential Scanner
Manages the lifecycle of the trading bot with multi-asset support
✅ Scans: R_25, R_50, R_501s, R_75, R_751s
✅ Sequential Top-Down analysis per symbol
✅ Global 1-trade limit enforcement
✅ First-Come-First-Served execution
✅ Continuous monitoring of active trades
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional, Dict, List
from enum import Enum

# Import existing bot modules
from data_fetcher import DataFetcher
from strategy import TradingStrategy
from trade_engine import TradeEngine
from risk_manager import RiskManager
import config

from app.bot.state import BotState
from app.bot.events import event_manager
from app.bot.telegram_bridge import telegram_bridge
from app.core.context import user_id_var
from app.services.trades_service import UserTradesService  # ← NEW IMPORT
from functools import wraps

def with_user_context(func):
    @wraps(func)
    async def wrapper(self, *args, **kwargs):
        token = None
        if self.account_id:
            token = user_id_var.set(self.account_id)
        try:
            return await func(self, *args, **kwargs)
        finally:
            if token:
                user_id_var.reset(token)
    return wrapper

from utils import setup_logger

logger = setup_logger()

class BotStatus(str, Enum):
    """Bot status enumeration"""
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"

class BotRunner:
    """
    Multi-Asset Trading Bot Runner
    - Scans multiple symbols sequentially
    - Enforces global 1-trade position limit
    - First qualifying signal locks the system
    - Monitors active trades across all assets
    """
    
    def __init__(self, api_token: Optional[str] = None, account_id: Optional[str] = None):
        self.is_running = False
        self.task: Optional[asyncio.Task] = None
        self.status = BotStatus.STOPPED
        self.start_time: Optional[datetime] = None
        self.error_message: Optional[str] = None
        
        # Identity
        self.account_id = account_id
        self.api_token = api_token or config.DERIV_API_TOKEN
        
        # Instance State
        self.state = BotState()
        
        # Bot components (initialized on start)
        self.data_fetcher: Optional[DataFetcher] = None
        self.trade_engine: Optional[TradeEngine] = None
        self.strategy: Optional[TradingStrategy] = None
        self.risk_manager: Optional[RiskManager] = None
        
        # Multi-asset configuration
        self.symbols: List[str] = config.SYMBOLS
        self.asset_config: Dict = config.ASSET_CONFIG
        
        # User Configurable Settings
        self.user_stake: Optional[float] = None
        self.active_strategy: str = "Conservative" # Default strategy
        
        # Scanning statistics
        self.scan_count = 0
        self.signals_by_symbol: Dict[str, int] = {symbol: 0 for symbol in self.symbols}
        self.errors_by_symbol: Dict[str, int] = {symbol: 0 for symbol in self.symbols}
        
        # Logging control
        self.last_status_log: Dict[str, Dict] = {} # {symbol: {'msg': str, 'time': datetime}}
        
        # Telegram bridge
        self.telegram_bridge = telegram_bridge
    
    @with_user_context
    async def start_bot(self, api_token: Optional[str] = None, stake: Optional[float] = None, strategy_name: Optional[str] = None) -> dict:
        """
        Start the trading bot
        Returns status dict
        """
        if self.is_running:
            return {
                "success": False,
                "message": "Bot is already running",
                "status": self.status.value
            }
        
        # Update token if provided
        if api_token:
            self.api_token = api_token

        # Update User Settings
        if stake:
            self.user_stake = stake
        # Ensure fallback if user_stake is still None (though main.py sends default)
        
        if strategy_name:
            self.active_strategy = strategy_name
        
        # STRICT ENFORCEMENT: User Stake Must Be Present
        if self.user_stake is None:
            return {
                "success": False,
                "message": "❌ Start Failed: Stake amount not configured. Please set your stake in Settings.",
                "status": self.status.value
            }
            
        current_stake = self.user_stake
        
        # Risk settings will be applied in _run_bot after components are initialized
        # (self.risk_manager is None here until _run_bot starts)
            
        try:
            logger.info(f"🚀 Starting bot for {self.account_id or 'default user'}...")
            logger.info(f"📊 Scanning symbols: {', '.join(self.symbols)}")
            logger.info(f"⚙️ Strategy: {self.active_strategy} | Stake: ${current_stake}")
            self.status = BotStatus.STARTING
            self.error_message = None
            self.state.update_status("starting")
            
            # Load historical trades from DB
            try:
                history = UserTradesService.get_user_trades(self.account_id, limit=100)
                if history:
                    # Update state with history 
                    # Note: We need to adapt the format slightly if needed, but BotState expects dicts
                    # We might want to populate stats based on this history too
                    self.state.trade_history = history
                    logger.info(f"📜 Loaded {len(history)} historical trades from DB")
            except Exception as e:
                logger.warning(f"⚠️ Failed to load trade history: {e}")

            # Create bot task
            self.task = asyncio.create_task(self._run_bot())
            
            # Wait for bot to fully initialize
            max_wait = 10
            for i in range(max_wait):
                await asyncio.sleep(1)
                
                if self.is_running:
                    logger.info("✅ Multi-asset bot started successfully")
                    await event_manager.broadcast({
                        "type": "bot_status",
                        "status": "running",
                        "message": f"Multi-asset bot started - scanning {len(self.symbols)} symbols",
                        "symbols": self.symbols,
                        "account_id": self.account_id
                    })
                    
                    return {
                        "success": True,
                        "message": f"Bot started - scanning {len(self.symbols)} symbols",
                        "status": self.status.value,
                        "symbols": self.symbols
                    }
                
                if self.status == BotStatus.ERROR:
                    error_msg = self.error_message or "Bot initialization failed"
                    raise Exception(error_msg)
            
            raise Exception("Bot startup timeout")
                
        except Exception as e:
            logger.error(f"❌ Failed to start bot: {e}")
            self.status = BotStatus.ERROR
            self.error_message = str(e)
            self.state.update_status("error", error=str(e))
            
            if self.task and not self.task.done():
                self.task.cancel()
            
            # Only notify telegram if this is the main/default bot or configured for it
            # For now, suppressing per-user telegram errors to avoid spam in admin channel
            # unless a bridge is configured per user.
            
            return {
                "success": False,
                "message": f"Failed to start bot: {e}",
                "status": self.status.value
            }
    
    @with_user_context
    async def stop_bot(self) -> dict:
        """
        Stop the trading bot gracefully
        Returns status dict
        """
        if not self.is_running:
            return {
                "success": False,
                "message": "Bot is not running",
                "status": self.status.value
            }
        
        try:
            logger.info("🛑 Stopping multi-asset trading bot...")
            self.status = BotStatus.STOPPING
            self.state.update_status("stopping")
            
            # Cancel the bot task
            if self.task:
                self.task.cancel()
                try:
                    await self.task
                except asyncio.CancelledError:
                    pass
            
            # Disconnect bot components
            if self.data_fetcher:
                await self.data_fetcher.disconnect()
            if self.trade_engine:
                await self.trade_engine.disconnect()
            
            self.is_running = False
            self.status = BotStatus.STOPPED
            self.task = None
            self.start_time = None
            
            self.task = None
            self.start_time = None
            
            self.state.update_status("stopped")
            logger.info("✅ Bot stopped successfully")
            
            # Notify Telegram with stats
            try:
                stats = self.state.get_statistics()
                stats['scan_summary'] = {
                    'total_scans': self.scan_count,
                    'signals_by_symbol': self.signals_by_symbol
                }
                await self.telegram_bridge.notify_bot_stopped(stats)
            except:
                pass
            
            await event_manager.broadcast({
                "type": "bot_status",
                "status": "stopped",
                "message": "Multi-asset bot stopped successfully",
                "account_id": self.account_id
            })
            
            return {
                "success": True,
                "message": "Bot stopped successfully",
                "status": self.status.value
            }
            
        except Exception as e:
            logger.error(f"❌ Error stopping bot: {e}")
            return {
                "success": False,
                "message": f"Error stopping bot: {e}",
                "status": self.status.value
            }
    
    async def restart_bot(self) -> dict:
        """
        Restart the trading bot
        Returns status dict
        """
        logger.info("🔄 Restarting multi-asset trading bot...")
        
        if self.is_running:
            stop_result = await self.stop_bot()
            if not stop_result["success"]:
                return stop_result
            
            await asyncio.sleep(3)
        
        return await self.start_bot()
    
    def get_status(self) -> dict:
        """Get current bot status with multi-asset info"""
        uptime = None
        if self.start_time:
            uptime = int((datetime.now() - self.start_time).total_seconds())
        
        # Get active trade info from risk manager
        active_trade_info = None
        if self.risk_manager and self.risk_manager.has_active_trade:
            active_trade_info = self.risk_manager.get_active_trade_info()
        
        return {
            "status": self.status.value,
            "is_running": self.is_running,
            "uptime_seconds": uptime,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "error_message": self.error_message,
            "error_message": self.error_message,
            "balance": self.state.balance,
            "active_trades": self.state.active_trades,
            "active_trades_count": len(self.state.active_trades),
            "active_trades": self.state.active_trades,
            "active_trades_count": len(self.state.active_trades),
            "statistics": self.state.get_statistics(),
            "config": {
                "stake": self.user_stake if self.user_stake else config.FIXED_STAKE,
                "strategy": self.active_strategy
            },
            "multi_asset": {
                "symbols": self.symbols,
                "scan_count": self.scan_count,
                "active_symbol": active_trade_info['symbol'] if active_trade_info else None,
                "signals_by_symbol": self.signals_by_symbol,
                "errors_by_symbol": self.errors_by_symbol
            }
        }
    
    @with_user_context
    async def _run_bot(self):
        """
        Main bot loop - Multi-asset sequential scanner
        Continuously scans all symbols looking for first qualifying signal
        """
        try:
            logger.info("🤖 Multi-asset bot main loop starting...")
            
            # Initialize components with dynamic token
            token_to_use = self.api_token
            
            if not token_to_use:
                 error_msg = f"❌ User {self.account_id} has no API Token! Cannot start bot."
                 logger.error(error_msg)
                 raise ValueError(error_msg)
            
            # Initialize bot components
            try:
                self.data_fetcher = DataFetcher(
                    token_to_use,
                    config.DERIV_APP_ID
                )
                
                self.trade_engine = TradeEngine(
                    token_to_use,
                    config.DERIV_APP_ID
                )
                
                self.strategy = TradingStrategy()
                self.risk_manager = RiskManager()
                self.risk_manager.set_bot_state(self.state)
                
                # CRITICAL: Apply user stake immediately after initialization
                if self.user_stake:
                    self.risk_manager.update_risk_settings(self.user_stake)
                    logger.info(f"✅ Risk limits updated for stake: ${self.user_stake}")
                
                logger.info("✅ Components initialized for multi-asset mode")
            except Exception as e:
                self.status = BotStatus.ERROR
                self.error_message = f"Component initialization failed: {e}"
                logger.error(f"❌ {self.error_message}")
                return
            
            # Connect to Deriv API
            try:
                logger.info("🔌 Connecting DataFetcher...")
                data_connected = await self.data_fetcher.connect()
                if not data_connected:
                    raise Exception("DataFetcher failed to connect (check logs for details)")
                
                logger.info("🔌 Connecting TradeEngine...")
                trade_connected = await self.trade_engine.connect()
                if not trade_connected:
                    raise Exception("TradeEngine failed to connect (check logs for details)")
                
                logger.info("✅ Connected to Deriv API")
            except Exception as e:
                self.status = BotStatus.ERROR
                self.error_message = f"Deriv API connection failed: {e}"
                logger.error(f"❌ {self.error_message}")
                return
            
            # Check for existing positions on startup
            try:
                has_existing = await self.risk_manager.check_for_existing_positions(self.trade_engine)
                if has_existing:
                    logger.warning("⚠️ Existing position detected - system locked on startup")
            except Exception as e:
                logger.warning(f"⚠️ Could not check existing positions: {e}")
            
            # Get initial balance
            try:
                balance = await self.data_fetcher.get_balance()
                if balance:
                    self.state.update_balance(balance)
                    logger.info(f"💰 Initial balance: ${balance:.2f}")
            except Exception as e:
                logger.warning(f"⚠️ Could not fetch balance: {e}")
                balance = 0.0
            
            # Mark as running
            self.is_running = True
            self.status = BotStatus.RUNNING
            self.start_time = datetime.now()
            self.error_message = None
            self.state.update_status("running")
            
            logger.info("✅ Multi-asset bot is now running")
            logger.info(f"🔍 Scanning {len(self.symbols)} symbols per cycle")
            
            # Notify Telegram
            try:
                await self.telegram_bridge.notify_bot_started(balance or 0.0, self.user_stake, self.active_strategy)
            except Exception as e:
                logger.warning(f"⚠️ Telegram notification failed: {e}")
            
            # Broadcast to WebSockets
            await event_manager.broadcast({
                "type": "bot_status",
                "status": "running",
                "account_id": self.account_id,
                "message": f"Multi-asset bot started - scanning {len(self.symbols)} symbols",
                "balance": balance,
                "symbols": self.symbols
            })
            
            # Main trading loop - MULTI-ASSET SEQUENTIAL SCANNER
            while self.is_running:
                try:
                    self.scan_count += 1
                    logger.info(f"🔄 Scan cycle #{self.scan_count} - Checking {len(self.symbols)} symbols")
                    
                    # Execute multi-asset scan cycle
                    await self._multi_asset_scan_cycle()
                    
                    # Determine wait time based on risk manager state
                    cooldown = self.risk_manager.get_cooldown_remaining()
                    
                    # If actively monitoring a trade, check more frequently
                    if self.risk_manager.has_active_trade:
                        wait_time = max(cooldown, 10)  # Check every 10s when trade active
                        logger.debug(f"⏱️ Active trade - next check in {wait_time}s")
                    else:
                        wait_time = max(cooldown, 30)  # Standard 30s cycle when scanning
                        logger.debug(f"⏱️ No active trade - next scan in {wait_time}s")
                    
                    # Sleep with cancellation check
                    for _ in range(int(wait_time)):
                        if not self.is_running:
                            break
                        await asyncio.sleep(1)
                    
                except asyncio.CancelledError:
                    logger.info("Bot loop cancelled")
                    break
                except Exception as e:
                    logger.error(f"Error in scan cycle: {e}")
                    
                    try:
                        await self.telegram_bridge.notify_error(str(e))
                    except:
                        pass
                    
                    await event_manager.broadcast({
                        "type": "error",
                        "message": str(e),
                        "timestamp": datetime.now().isoformat(),
                        "account_id": self.account_id
                    })
                    await asyncio.sleep(30)
            
        except asyncio.CancelledError:
            logger.info("Bot task cancelled")
        except Exception as e:
            logger.error(f"Fatal error in bot: {e}")
            self.status = BotStatus.ERROR
            self.error_message = str(e)
            bot_state.update_status("error", error=str(e))
            
            try:
                await self.telegram_bridge.notify_error(f"Fatal error: {e}")
            except:
                pass
            
            await event_manager.broadcast({
                "type": "error",
                "message": f"Fatal error: {e}",
                "timestamp": datetime.now().isoformat(),
                "account_id": self.account_id
            })
        finally:
            self.is_running = False
            logger.info("Multi-asset bot main loop exited")
    
    async def _multi_asset_scan_cycle(self):
        """
        CRITICAL: Multi-Asset Sequential Scanner
        
        Process:
        1. Check global trade permission (1-trade limit)
        2. If position active → Monitor only (skip scanning)
        3. If no position → Scan all symbols sequentially
        4. First qualifying signal → Execute and lock system
        5. All other symbols blocked until trade closes
        """
        
        # Step 1: Check global permission
        can_trade_global, reason = self.risk_manager.can_trade()
        
        # If we have an active trade, monitor it instead of scanning
        if self.risk_manager.has_active_trade:
            logger.debug(f"🔒 Monitoring active {self.risk_manager.active_symbol} trade")
            await self._monitor_active_trade()
            return
        
        if not can_trade_global:
            logger.debug(f"⏸️ Global trading paused: {reason}")
            return
        
        # Step 2: Sequential symbol scanning (First-Come-First-Served)
        logger.info(f"🔍 Scanning all {len(self.symbols)} symbols for entry signals...")
        
        for symbol in self.symbols:
            # Check if we can still trade (might have changed during loop)
            can_trade_now, _ = self.risk_manager.can_trade(symbol)
            if not can_trade_now:
                logger.debug(f"⏸️ {symbol} - Global state changed, stopping scan")
                break
            
            try:
                # Execute Top-Down analysis for this symbol
                signal_found = await self._analyze_symbol(symbol)
                
                if signal_found:
                    # CRITICAL: First qualifying signal locks the system
                    logger.info(f"🎯 {symbol} won the race - executing trade")
                    logger.info(f"🔒 All other symbols now BLOCKED")
                    break  # Exit loop - system is now locked
                
            except Exception as e:
                # Log error but continue to next symbol
                logger.error(f"❌ Error analyzing {symbol}: {e}")
                self.errors_by_symbol[symbol] = self.errors_by_symbol.get(symbol, 0) + 1
                
                # If too many errors for this symbol, notify
                if self.errors_by_symbol[symbol] >= 5:
                    try:
                        await self.telegram_bridge.notify_error(
                            f"Multiple errors for {symbol}: {e}"
                        )
                    except:
                        pass
                
                continue  # Move to next symbol
        
        logger.debug(f"✅ Scan cycle complete - checked {len(self.symbols)} symbols")
    
    async def _analyze_symbol(self, symbol: str) -> bool:
        """
        Analyze single symbol for entry signal
        
        Phase 1: Directional Bias (1w, 1d, 4h)
        Phase 2: Level Classification (1h, 5m)
        Phase 3: Entry Execution (1m Momentum + Retest)
        
        Returns:
            True if trade executed, False if no signal
        """
        logger.debug(f"🔎 Analyzing {symbol}...")
        
        # Fetch multi-timeframe data for this symbol
        # Fetch multi-timeframe data for this symbol
        try:
            market_data = await self.data_fetcher.fetch_all_timeframes(symbol)
            
            # Validate we have all required timeframes
            required_timeframes = ['1m', '5m', '1h', '4h', '1d', '1w']  # Full Top-Down requirement
            if not all(tf in market_data for tf in required_timeframes):
                logger.warning(f"⚠️ {symbol} - Missing required timeframes")
                return False
            
        except Exception as e:
            logger.error(f"❌ {symbol} - Data fetch failed: {e}")
            raise  # Re-raise to be caught by caller
        
        # Extract timeframe data
        data_1m = market_data.get('1m')
        data_5m = market_data.get('5m')
        data_1h = market_data.get('1h')
        data_4h = market_data.get('4h')
        data_1d = market_data.get('1d')
        data_1w = market_data.get('1w')
        
        # Execute strategy analysis based on Active Strategy
        try:
            if self.active_strategy == "Conservative":
                # Use standard Top-Down Strategy
                signal = self.strategy.analyze(data_1m, data_5m, data_1h, data_4h, data_1d, data_1w)
            
            elif self.active_strategy == "Scalping":
                # TODO: Implement distinct Scalping logic
                # For now, we either fallback or return no signal to indicate "Coming Soon" behavior
                # or strictly separate it.
                # Given user request: "next will be Scalping". It implies it's not ready or just placeholder.
                # But to avoid breaking if selected, let's log and return False for now, 
                # OR we could just map it to the same strategy with different params if that was the intent.
                # Assuming "Future" means not now:
                
                # However, to be safe if user accidentally selects it:
                logger.debug(f"⚠️ Scalping strategy selected but not fully implemented. Skipping analysis.")
                return False
                
            else:
                # Default to Conservative if unknown
                signal = self.strategy.analyze(data_1m, data_5m, data_1h, data_4h, data_1d, data_1w)

        except Exception as e:
            logger.error(f"❌ {symbol} - Strategy analysis failed: {e}")
            raise
        
        if not signal.get('can_trade'):
            details = signal.get('details', {})
            reason = details.get('reason', 'Unknown')
            passed_checks = details.get('passed_checks', [])
            
            # Format reason with checks
            if passed_checks:
                checks_str = ", ".join(passed_checks)
                full_reason = f"{reason} (Checks Passed: {checks_str})"
            else:
                full_reason = reason
            
            # Smart Logging: Only log if reason changed or > 60s passed to avoid spam
            now = datetime.now()
            last_log = self.last_status_log.get(symbol, {'msg': '', 'time': datetime.min})
            
            should_log = False
            if full_reason != last_log['msg']:
                should_log = True
            elif (now - last_log['time']).total_seconds() > 60:
                should_log = True
                
            if should_log:
                logger.info(f"⏳ {symbol} - Skipped: {full_reason}")
                self.last_status_log[symbol] = {'msg': full_reason, 'time': now}
            else:
                # Debug only for spammy updates
                logger.debug(f"⏭️ {symbol} - No signal: {full_reason}")
                
            return False
        
        # We have a signal! Log it
        logger.info(f"🎯 {symbol} - SIGNAL DETECTED!")
        logger.info(f"   Direction: {signal['signal']}")
        logger.info(f"   Score: {signal.get('score', 0):.2f}")
        checks_passed = ", ".join(signal.get('details', {}).get('passed_checks', []))
        logger.info(f"   Confidence: {signal.get('confidence', 0):.1f}%")
        logger.info(f"   Checks Passed: {checks_passed}")
        
        # Track signal
        self.signals_by_symbol[symbol] = self.signals_by_symbol.get(symbol, 0) + 1
        
        # Notify Telegram about signal
        try:
            signal_with_symbol = signal.copy()
            signal_with_symbol['symbol'] = symbol
            await self.telegram_bridge.notify_signal(signal_with_symbol)
        except:
            pass
        
        # Broadcast signal to WebSockets
        timestamp = datetime.now().isoformat()
        signal['timestamp'] = timestamp # CRITICAL: Track signal time for result linking
        
        await event_manager.broadcast({
            "type": "signal",
            "symbol": symbol,
            "signal": signal['signal'],
            "score": signal.get('score', 0),
            "confidence": signal.get('confidence', 0),
            "timestamp": timestamp,
            "account_id": self.account_id
        })
        
        # Record signal in state
        self.state.add_signal(signal)
        
        # Get symbol-specific configuration
        multiplier = self.asset_config.get(symbol, {}).get('multiplier', config.MULTIPLIER)
        
        # Determine Stake (User Preference)
        base_stake = self.user_stake
        if base_stake is None:
             # Should not happen due to start_bot check, but safety first
             logger.error(f"❌ {symbol} - Critical: User stake is None during analysis")
             return False
             
        # CRITICAL FIX: Do NOT multiply by multiplier. 
        # The stake passed to Deriv API (amount) is the user's risk amount (cost), 
        # not the total exposure.
        stake = base_stake
        
        # Validate with risk manager (including global checks)
        can_open, validation_msg = self.risk_manager.can_open_trade(
            symbol=symbol,
            stake=stake,
            take_profit=signal.get('take_profit'),
            stop_loss=signal.get('stop_loss')
        )
        
        if not can_open:
            logger.warning(f"❌ {symbol} - Trade blocked: {validation_msg}")
            return False
        
        # Execute trade!
        logger.info(f"🚀 {symbol} - Executing {signal['signal']} trade...")
        logger.info(f"   Stake: ${stake:.2f} (multiplier: {multiplier}x)")
        
        try:
            # Add symbol to signal data
            signal_with_symbol = signal.copy()
            signal_with_symbol['symbol'] = symbol
            signal_with_symbol['stake'] = stake
            
            # Execute trade using TradeEngine
            result = await self.trade_engine.execute_trade(
                signal_with_symbol, 
                self.risk_manager
            )
            
            if result:
                # Trade executed and completed
                pnl = result.get('profit', 0.0)
                status = result.get('status', 'unknown')
                contract_id = result.get('contract_id')
                
                logger.info(f"✅ {symbol} - Trade completed: {status}")
                logger.info(f"💰 P&L: ${pnl:.2f}")

                # CRITICAL FIX: Add signal to result for DB persistence
                if 'signal' not in result:
                    result['signal'] = signal_with_symbol['signal']
                
                # Record trade closure
                self.risk_manager.record_trade_close(contract_id, pnl, status)
                self.state.update_trade(contract_id, result)

                # Persist to Supabase
                UserTradesService.save_trade(self.account_id, result)
                
                # Notify Telegram
                try:
                    # MERGE complete trade details into result for notification
                    result_for_notify = result.copy()
                    result_for_notify.update(signal_with_symbol) # Contains direction, stake, symbol
                    
                    # Ensure symbol is set (sometimes signal uses 'symbol', result uses 'symbol')
                    if 'symbol' not in result_for_notify:
                         result_for_notify['symbol'] = symbol
                    
                    await self.telegram_bridge.notify_trade_closed(result_for_notify, pnl, status)
                except:
                    pass
                
                # Broadcast to WebSockets
                await event_manager.broadcast({
                    "type": "trade_closed",
                    "symbol": symbol,
                    "trade": result,
                    "pnl": pnl,
                    "status": status,
                    "timestamp": datetime.now().isoformat(),
                    "account_id": self.account_id
                })
                
                # Update statistics
                stats = self.risk_manager.get_statistics()
                self.state.update_statistics(stats)
                
                # CRITICAL: Update signal result and broadcast
                signal_timestamp = signal_with_symbol.get('timestamp')
                if signal_timestamp:
                    self.state.update_signal_result(signal_timestamp, status, pnl)
                    
                    await event_manager.broadcast({
                        "type": "signal_updated",
                        "timestamp": signal_timestamp,
                        "result": status,
                        "pnl": pnl,
                        "account_id": self.account_id
                    })

                    # Send UI Notification
                    notification_type = "success" if pnl > 0 else "error" if pnl < 0 else "info"
                    await event_manager.broadcast({
                        "type": "notification",
                        "level": notification_type,
                        "title": f"Trade {status.title()}",
                        "message": f"{symbol} trade closed. P&L: ${pnl:.2f}",
                        "timestamp": datetime.now().isoformat(),
                        "account_id": self.account_id
                    })
                
                await event_manager.broadcast({
                    "type": "statistics",
                    "stats": stats,
                    "timestamp": datetime.now().isoformat(),
                    "account_id": self.account_id
                })
                
                return True  # Trade executed
            else:
                logger.error(f"❌ {symbol} - Trade execution failed")
                return False
                
        except Exception as e:
            logger.error(f"❌ {symbol} - Trade execution error: {e}")
            
            try:
                await self.telegram_bridge.notify_error(f"{symbol} trade failed: {e}")
            except:
                pass
            
            return False
    
    async def _monitor_active_trade(self):
        """
        Monitor the currently active trade
        This runs when a trade is locked, checking its status
        """
        if not self.risk_manager.has_active_trade:
            return
        
        active_info = self.risk_manager.get_active_trade_info()
        if not active_info:
            return
        
        symbol = active_info['symbol']
        contract_id = active_info['contract_id']
        
        try:
            # Fetch current trade status from Deriv
            # This allows us to detect early closures or updates
            trade_status = await self.trade_engine.get_trade_status(contract_id)
            
            if trade_status and trade_status.get('is_sold'):
                # Trade closed externally or by TP/SL
                logger.info(f"🔔 {symbol} trade detected as closed")
                
                pnl = trade_status.get('profit', 0.0)
                status = trade_status.get('status', 'sold')
                
                # Record closure
                self.risk_manager.record_trade_close(contract_id, pnl, status)
                self.state.update_trade(contract_id, trade_status)
                
                logger.info(f"🔓 {symbol} trade closed - system unlocked")
                logger.info(f"💰 P&L: ${pnl:.2f}")
                
                # Notify Telegram
                try:
                    # MERGE complete trade details into result for notification
                    result_for_notify = trade_status.copy()
                    result_for_notify.update(active_info) # Contains direction, stake, symbol from RiskManager
                    
                    await self.telegram_bridge.notify_trade_closed(result_for_notify, pnl, status)
                except:
                    pass
            
        except Exception as e:
            logger.warning(f"⚠️ Could not monitor {symbol} trade: {e}")

# Global bot runner instance - DEPRECATED / DEFAULT
# We keep this for backward compatibility if needed, using env vars
bot_runner = BotRunner()
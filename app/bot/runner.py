"""
Bot Runner - Multi-Asset Sequential Scanner
Manages the lifecycle of the trading bot with multi-asset support
- Scans strategy symbol universe
- Sequential top-down analysis per symbol
- Global 1-trade limit enforcement
- First-come-first-served execution
- Continuous monitoring of active trades
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional, Dict, List
from enum import Enum

# Import existing bot modules
from data_fetcher import DataFetcher
from trade_engine import TradeEngine
import config

from app.bot.state import BotState
from app.bot.events import event_manager
from app.bot.telegram_bridge import telegram_bridge
from app.core.context import user_id_var, bot_type_var
from app.services.trades_service import UserTradesService
from functools import wraps


def _strategy_to_bot_type(strategy_name: Optional[str]) -> str:
    value = (strategy_name or "").strip().lower()
    if value == "scalping":
        return "scalping"
    if value == "conservative":
        return "conservative"
    if value == "risefall":
        return "risefall"
    return "system"


def with_user_context(func):
    @wraps(func)
    async def wrapper(self, *args, **kwargs):
        user_token = None
        bot_token = None
        if self.account_id:
            user_token = user_id_var.set(self.account_id)
        bot_token = bot_type_var.set(_strategy_to_bot_type(self._get_strategy_name()))
        try:
            return await func(self, *args, **kwargs)
        finally:
            if user_token:
                user_id_var.reset(user_token)
            if bot_token:
                bot_type_var.reset(bot_token)
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
    
    def __init__(self, api_token: Optional[str] = None, account_id: Optional[str] = None,
                 strategy = None, risk_manager = None):
        # Backward compatibility: allow positional form
        # BotRunner(account_id, strategy, risk_manager).
        if (
            isinstance(api_token, str)
            and account_id is not None
            and not isinstance(account_id, str)
            and strategy is not None
            and risk_manager is None
        ):
            account_id, strategy, risk_manager, api_token = api_token, account_id, strategy, None
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
        
        # Bot components (initialized on start or injected)
        self.data_fetcher: Optional[DataFetcher] = None
        self.trade_engine: Optional[TradeEngine] = None
        
        # Strategy and risk manager injection (NEW)
        if strategy is None:
            # Default to conservative strategy
            from conservative_strategy import ConservativeStrategy
            self.strategy = ConservativeStrategy()
        else:
            self.strategy = strategy
        
        if risk_manager is None:
            # Will be initialized in _run_bot for backward compatibility
            self.risk_manager = None
        else:
            self.risk_manager = risk_manager
        
        # Multi-asset configuration
        self.symbols: List[str] = config.SYMBOLS
        self.asset_config: Dict = config.ASSET_CONFIG
        
        # User Configurable Settings
        self.user_stake: Optional[float] = None
        if self.strategy and hasattr(self.strategy, "get_strategy_name"):
            try:
                self.active_strategy = self.strategy.get_strategy_name()
            except Exception:
                self.active_strategy = "Conservative"
        else:
            self.active_strategy = "Conservative"
        
        # Scanning statistics
        self.scan_count = 0
        self.signals_by_symbol: Dict[str, int] = {symbol: 0 for symbol in self.symbols}
        self.errors_by_symbol: Dict[str, int] = {symbol: 0 for symbol in self.symbols}
        
        # Logging control
        self.last_status_log: Dict[str, Dict] = {} # {symbol: {'msg': str, 'time': datetime}}
        # Structured decision event throttling cache
        self._decision_log_state: Dict[str, Dict] = {}
        
        # Telegram bridge
        self.telegram_bridge = telegram_bridge

        # Sync per-strategy market scope on init.
        self._sync_strategy_scope()

    def _get_strategy_name(self) -> str:
        """Resolve strategy name safely for structured decision events."""
        try:
            if self.strategy and hasattr(self.strategy, "get_strategy_name"):
                return self.strategy.get_strategy_name()
        except Exception:
            pass
        return getattr(self, "active_strategy", "Unknown") or "Unknown"

    def _sync_strategy_scope(self) -> None:
        """Bind runner symbol universe/config to currently injected strategy."""
        if self.strategy and hasattr(self.strategy, "get_symbols"):
            try:
                self.symbols = list(self.strategy.get_symbols())
            except Exception:
                self.symbols = list(config.SYMBOLS)
        else:
            self.symbols = list(config.SYMBOLS)

        if self.strategy and hasattr(self.strategy, "get_asset_config"):
            try:
                self.asset_config = dict(self.strategy.get_asset_config())
            except Exception:
                self.asset_config = dict(config.ASSET_CONFIG)
        else:
            self.asset_config = dict(config.ASSET_CONFIG)

        # Keep symbol counters aligned with active symbol universe.
        self.signals_by_symbol = {symbol: self.signals_by_symbol.get(symbol, 0) for symbol in self.symbols}
        self.errors_by_symbol = {symbol: self.errors_by_symbol.get(symbol, 0) for symbol in self.symbols}

    def _cycle_step(
        self,
        symbol: str,
        step: int,
        total_steps: int,
        message: str,
        emoji: str = "\u2139\ufe0f",
        level: str = "info",
    ) -> None:
        """Rise/Fall-style lifecycle log line for multiplier strategies."""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        strategy = self._get_strategy_name()
        line = f"[{strategy}][{symbol}] STEP {step}/{total_steps} | {ts} | {emoji} {message}"
        getattr(logger, level)(line)

    def _should_emit_decision(
        self, key: str, fingerprint: str, min_interval_seconds: int = 20
    ) -> bool:
        """Throttle repeated decision events for cleaner frontend timelines."""
        now = datetime.now()
        last = self._decision_log_state.get(key)
        if not last:
            self._decision_log_state[key] = {"fingerprint": fingerprint, "time": now}
            return True

        last_fingerprint = last.get("fingerprint")
        last_time = last.get("time", datetime.min)
        elapsed = (now - last_time).total_seconds()

        if fingerprint != last_fingerprint or elapsed >= min_interval_seconds:
            self._decision_log_state[key] = {"fingerprint": fingerprint, "time": now}
            return True

        return False

    async def _broadcast_decision(
        self,
        symbol: str,
        phase: str,
        decision: str,
        reason: Optional[str] = None,
        details: Optional[Dict] = None,
        severity: str = "info",
        throttle_key: Optional[str] = None,
        min_interval_seconds: int = 20,
    ) -> None:
        """
        Broadcast structured bot decision events for frontend consumption.
        """
        fingerprint = f"{phase}|{decision}|{reason or ''}"
        if throttle_key and not self._should_emit_decision(
            throttle_key, fingerprint, min_interval_seconds=min_interval_seconds
        ):
            return

        payload = {
            "type": "bot_decision",
            "bot": "multiplier",
            "strategy": self._get_strategy_name(),
            "symbol": symbol,
            "phase": phase,
            "decision": decision,
            "severity": severity,
            "message": reason or decision.replace("_", " "),
            "timestamp": datetime.now().isoformat(),
            "account_id": self.account_id,
        }
        if reason:
            payload["reason"] = reason
        if details:
            payload["details"] = details

        try:
            await event_manager.broadcast(payload)
        except Exception as e:
            logger.debug(f"Decision event broadcast skipped due to error: {e}")
    
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

        # Ensure runner scope and logging context reflect active strategy.
        self._sync_strategy_scope()
        
        # STRICT ENFORCEMENT: User Stake Must Be Present
        if self.user_stake is None:
            return {
                "success": False,
                "message": "Start failed: Stake amount not configured. Please set your stake in Settings.",
                "status": self.status.value
            }
            
        current_stake = self.user_stake
        
        # Risk settings will be applied in _run_bot after components are initialized
        # (self.risk_manager is None here until _run_bot starts)
            
        try:
            self._cycle_step(
                "SYSTEM",
                1,
                6,
                f"Startup requested for {self.account_id or 'default user'}",
                emoji="\U0001F680",
            )
            logger.info(f"[{self.active_strategy}][SYSTEM] \U0001F4DA Symbols: {', '.join(self.symbols)}")
            logger.info(f"[{self.active_strategy}][SYSTEM] \U0001F4B5 Stake: ${current_stake}")
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
                    logger.info(f"[{self.active_strategy}][SYSTEM] \U0001F5C2\ufe0f Loaded {len(history)} historical trades")
            except Exception as e:
                logger.warning(f"[{self.active_strategy}][SYSTEM] \u26A0\ufe0f Failed to load history: {e}")

            # Create bot task
            self.task = asyncio.create_task(self._run_bot())
            
            # Wait for bot to fully initialize
            max_wait = 10
            for i in range(max_wait):
                await asyncio.sleep(1)
                
                if self.is_running:
                    self._cycle_step("SYSTEM", 6, 6, "Bot started successfully", emoji="\u2705")
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
            self._cycle_step("SYSTEM", 6, 6, f"Startup failed: {e}", emoji="\u274C", level="error")
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
            self._cycle_step("SYSTEM", 1, 3, "Stop requested", emoji="\U0001F6D1")
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
            self._cycle_step("SYSTEM", 3, 3, "Bot stopped successfully", emoji="\u2705")
            
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
            self._cycle_step("SYSTEM", 3, 3, f"Stop failed: {e}", emoji="\u274C", level="error")
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
        logger.info(f"[{self._get_strategy_name()}][SYSTEM] \u267B\ufe0f Restart requested")
        
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
        if self.risk_manager and self.risk_manager.active_trades:
            # For status display, just show the first one or latest
            # Ideally we return all, but for backward compatibility, let's see.
            # RiskManager.get_active_trade_info() also needs fixing.
            # For now, let's call it and assume I fix it to use active_trades[0]
            active_trade_info = self.risk_manager.get_active_trade_info()
        
        return {
            "status": self.status.value,
            "is_running": self.is_running,
            "active_strategy": self.active_strategy,
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
            self._cycle_step("SYSTEM", 1, 6, "Main loop starting", emoji="\U0001F504")
            
            # Initialize components with dynamic token
            token_to_use = self.api_token
            
            if not token_to_use:
                 error_msg = f"User {self.account_id} has no API token configured"
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
                
                # Only initialize risk_manager if not already injected
                if self.risk_manager is None:
                    from conservative_risk_manager import ConservativeRiskManager
                    self.risk_manager = ConservativeRiskManager(user_id=self.account_id)
                
                # Set bot state for risk manager
                if hasattr(self.risk_manager, 'set_bot_state'):
                    self.risk_manager.set_bot_state(self.state)
                
                # Apply user stake if provided
                if self.user_stake:
                    if hasattr(self.risk_manager, 'update_risk_settings'):
                        self.risk_manager.update_risk_settings(self.user_stake)
                    if hasattr(self.risk_manager, 'stake'):
                        self.risk_manager.stake = self.user_stake
                    logger.info(f"[{self._get_strategy_name()}][SYSTEM] \U0001F6E1\ufe0f Risk limits updated for stake: ${self.user_stake}")
                
                self._cycle_step("SYSTEM", 2, 6, "Components initialized", emoji="\U0001F9E9")
            except Exception as e:
                self.status = BotStatus.ERROR
                self.error_message = f"Component initialization failed: {e}"
                self._cycle_step("SYSTEM", 2, 6, self.error_message, emoji="\u274C", level="error")
                return
            
            # Connect to Deriv API
            try:
                self._cycle_step("SYSTEM", 3, 6, "Connecting DataFetcher", emoji="\U0001F50C")
                data_connected = await self.data_fetcher.connect()
                if not data_connected:
                    reason = self.data_fetcher.last_error or "Unknown connection error"
                    raise Exception(f"DataFetcher failed to connect: {reason}")
                
                self._cycle_step("SYSTEM", 4, 6, "Connecting TradeEngine", emoji="\U0001F50C")
                trade_connected = await self.trade_engine.connect()
                if not trade_connected:
                    raise Exception("TradeEngine failed to connect (check logs for details)")
                
                self._cycle_step("SYSTEM", 4, 6, "Connected to Deriv API", emoji="\u2705")
            except Exception as e:
                self.status = BotStatus.ERROR
                self.error_message = f"Deriv API connection failed: {e}"
                self._cycle_step("SYSTEM", 4, 6, self.error_message, emoji="\u274C", level="error")
                return
            
            # Check for existing positions on startup
            try:
                has_existing = await self.risk_manager.check_for_existing_positions(self.trade_engine)
                if has_existing:
                    logger.warning(f"[{self._get_strategy_name()}][SYSTEM] \U0001F512 Existing position detected on startup")
            except Exception as e:
                logger.warning(f"[{self._get_strategy_name()}][SYSTEM] \u26A0\ufe0f Existing-position check failed: {e}")
            
            # Get initial balance
            try:
                balance = await self.data_fetcher.get_balance()
                if balance:
                    self.state.update_balance(balance)
                    logger.info(f"[{self._get_strategy_name()}][SYSTEM] \U0001F4B0 Initial balance: ${balance:.2f}")
            except Exception as e:
                logger.warning(f"[{self._get_strategy_name()}][SYSTEM] \u26A0\ufe0f Initial balance fetch failed: {e}")
                balance = 0.0
            
            # Mark as running
            self.is_running = True
            self.status = BotStatus.RUNNING
            self.start_time = datetime.now()
            self.error_message = None
            self.state.update_status("running")
            
            self._cycle_step("SYSTEM", 5, 6, "Bot is now running", emoji="\u2705")
            logger.info(f"[{self._get_strategy_name()}][SYSTEM] \U0001F50E Scanning {len(self.symbols)} symbols per cycle")
            
            # Notify Telegram
            try:
                await self.telegram_bridge.notify_bot_started(balance or 0.0, self.user_stake, self.active_strategy)
            except Exception as e:
                logger.warning(f"[{self._get_strategy_name()}][SYSTEM] \u26A0\ufe0f Telegram notification failed: {e}")
            
            # Broadcast to WebSockets
            await event_manager.broadcast({
                "type": "bot_status",
                "status": "running",
                "account_id": self.account_id,
                "message": f"Multi-asset bot started - scanning {len(self.symbols)} symbols",
                "balance": balance,
                "symbols": self.symbols
            })
            
            # Broadcast initial statistics
            initial_stats = self.state.get_statistics()
            await event_manager.broadcast({
                "type": "statistics",
                "stats": initial_stats,
                "strategy": self.active_strategy,
                "timestamp": datetime.now().isoformat(),
                "account_id": self.account_id
            })
            
            # Main trading loop - MULTI-ASSET SEQUENTIAL SCANNER
            while self.is_running:
                try:
                    self.scan_count += 1
                    logger.info(
                        f"[{self._get_strategy_name()}][SYSTEM] \U0001F50E CYCLE #{self.scan_count} | "
                        f"Checking {len(self.symbols)} symbols"
                    )
                    
                    # Execute multi-asset scan cycle
                    await self._multi_asset_scan_cycle()
                    
                    # Determine wait time based on risk manager state
                    cooldown = self.risk_manager.get_cooldown_remaining()
                    
                    # If actively monitoring a trade, check more frequently
                    if self.risk_manager.active_trades:
                        wait_time = max(cooldown, 10)  # Check every 10s when trade active
                        logger.debug(f"[{self._get_strategy_name()}][SYSTEM] \u23F1\ufe0f Active trade monitor in {wait_time}s")
                    else:
                        wait_time = max(cooldown, 30)  # Standard 30s cycle when scanning
                        logger.debug(f"[{self._get_strategy_name()}][SYSTEM] \u23F1\ufe0f Next scan in {wait_time}s")
                    
                    # Sleep with cancellation check
                    for _ in range(int(wait_time)):
                        if not self.is_running:
                            break
                        await asyncio.sleep(1)
                    
                except asyncio.CancelledError:
                    logger.info(f"[{self._get_strategy_name()}][SYSTEM] \U0001F6D1 Bot loop cancelled")
                    break
                except Exception as e:
                    logger.error(f"[{self._get_strategy_name()}][SYSTEM] \u274C Scan cycle error: {e}")
                    
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
            logger.info(f"[{self._get_strategy_name()}][SYSTEM] \U0001F6D1 Bot task cancelled")
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
            self._cycle_step("SYSTEM", 6, 6, "Main loop exited", emoji="\U0001F3C1")
    
    async def _multi_asset_scan_cycle(self):
        """
        CRITICAL: Multi-Asset Sequential Scanner
        
        Process:
        1. Check global trade permission (1-trade limit)
        2. If position active -> monitor only (skip scanning)
        3. If no position -> scan all symbols sequentially
        4. First qualifying signal -> execute and lock system
        5. All other symbols blocked until trade closes
        """
        
        # Step 1: Check global permission
        can_trade_global, reason = self.risk_manager.can_trade()
        
        # If we have an active trade, monitor it instead of scanning
        if self.risk_manager.active_trades:
            logger.debug(f"[{self._get_strategy_name()}][SYSTEM] \U0001F440 Monitoring {len(self.risk_manager.active_trades)} active trade(s)")
            await self._broadcast_decision(
                symbol="SYSTEM",
                phase="scan",
                decision="no_trade",
                reason="Active trade is being monitored",
                details={"active_trades": len(self.risk_manager.active_trades)},
                throttle_key="scan:active_trade",
            )
            await self._monitor_active_trade()
            return
        
        if not can_trade_global:
            logger.info(f"[{self._get_strategy_name()}][SYSTEM] \u23F8\ufe0f Global trading paused: {reason}")
            await self._broadcast_decision(
                symbol="SYSTEM",
                phase="risk",
                decision="no_trade",
                reason=reason,
                details={"scope": "global_gate"},
                severity="warning",
                throttle_key="scan:global_gate",
            )
            return
        
        # Step 2: Sequential symbol scanning (First-Come-First-Served)
        logger.info(f"[{self._get_strategy_name()}][SYSTEM] \U0001F50D Scanning symbols for entry signals")
        
        for symbol in self.symbols:
            # Check if we can still trade (might have changed during loop)
            can_trade_now, _ = self.risk_manager.can_trade(symbol)
            if not can_trade_now:
                logger.debug(f"[{self._get_strategy_name()}][{symbol}] \u26D4 Global state changed, stopping scan")
                break
            
            try:
                # Execute Top-Down analysis for this symbol
                signal_found = await self._analyze_symbol(symbol)
                
                if signal_found:
                    # CRITICAL: First qualifying signal locks the system
                    logger.info(f"[{self._get_strategy_name()}][{symbol}] \U0001F3C1 First qualifying signal won this cycle")
                    logger.info(f"[{self._get_strategy_name()}][SYSTEM] \U0001F512 Other symbols blocked until closure")
                    break  # Exit loop - system is now locked
                
            except Exception as e:
                # Log error but continue to next symbol
                logger.error(f"[{self._get_strategy_name()}][{symbol}] \u274C Symbol analysis failed: {type(e).__name__}: {e}", exc_info=True)
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
        
        logger.debug(f"[{self._get_strategy_name()}][SYSTEM] \u2705 Scan cycle complete")
    
    async def _analyze_symbol(self, symbol: str) -> bool:
        """
        Analyze single symbol for entry signal
        
        Phase 1: Directional Bias (1w, 1d, 4h)
        Phase 2: Level Classification (1h, 5m)
        Phase 3: Entry Execution (1m Momentum + Retest)
        
        Returns:
            True if trade executed, False if no signal
        """
        self._cycle_step(symbol, 1, 6, "Fetching multi-timeframe data", emoji="\U0001F4E5")
        
        # Fetch multi-timeframe data for this symbol
        # Fetch multi-timeframe data for this symbol
        try:
            market_data = await self.data_fetcher.fetch_all_timeframes(symbol)
            
            # Validate we have all required timeframes
            required_timeframes = ['1m', '5m', '1h', '4h', '1d', '1w']  # Full Top-Down requirement
            if not all(tf in market_data for tf in required_timeframes):
                missing_tfs = [tf for tf in required_timeframes if tf not in market_data]
                self._cycle_step(
                    symbol,
                    1,
                    6,
                    f"Missing required timeframes: {', '.join(missing_tfs)}",
                    emoji="\u26A0\ufe0f",
                    level="warning",
                )
                await self._broadcast_decision(
                    symbol=symbol,
                    phase="data",
                    decision="no_trade",
                    reason="Missing required timeframes",
                    details={"missing_timeframes": missing_tfs},
                    severity="warning",
                    throttle_key=f"{symbol}:missing_timeframes",
                )
                return False
            
        except Exception as e:
            self._cycle_step(symbol, 1, 6, f"Data fetch failed: {e}", emoji="\u274C", level="error")
            raise  # Re-raise to be caught by caller
        
        # Extract timeframe data
        data_1m = market_data.get('1m')
        data_5m = market_data.get('5m')
        data_1h = market_data.get('1h')
        data_4h = market_data.get('4h')
        data_1d = market_data.get('1d')
        data_1w = market_data.get('1w')
        
        # Execute strategy analysis using injected strategy
        try:
            self._cycle_step(symbol, 2, 6, "Running strategy analysis", emoji="\U0001F9E0")
            # Get required timeframes for this strategy
            required_tfs = self.strategy.get_required_timeframes()
            
            # Build kwargs for strategy analyze method
            strategy_kwargs = {}
            for tf in required_tfs:
                strategy_kwargs[f'data_{tf.replace("m", "m").replace("h", "h").replace("d", "d").replace("w", "w")}'] = market_data.get(tf)
            strategy_kwargs['symbol'] = symbol
            
            # Call strategy analyze method
            signal = self.strategy.analyze(**strategy_kwargs)

        except Exception as e:
            self._cycle_step(symbol, 2, 6, f"Strategy analysis failed: {e}", emoji="\u274C", level="error")
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
                self._cycle_step(symbol, 3, 6, f"No trade setup: {full_reason}", emoji="\u23ED\ufe0f")
                self.last_status_log[symbol] = {'msg': full_reason, 'time': now}
                await self._broadcast_decision(
                    symbol=symbol,
                    phase="signal",
                    decision="no_trade",
                    reason=full_reason,
                    details={"passed_checks": passed_checks},
                    throttle_key=f"{symbol}:signal_skip",
                )
            else:
                # Debug only for spammy updates
                logger.debug(f"[{self._get_strategy_name()}][{symbol}] \u23ED\ufe0f No signal: {full_reason}")
                
            return False
        
        # We have a signal! Log it
        checks_passed = ", ".join(signal.get('details', {}).get('passed_checks', []))
        direction_emoji = "\U0001F7E2" if str(signal.get("signal", "")).upper() in {"BUY", "UP"} else "\U0001F534"
        self._cycle_step(
            symbol,
            3,
            6,
            f"Signal {signal['signal']} {direction_emoji} | Score {signal.get('score', 0):.2f} | Conf {signal.get('confidence', 0):.0f}%",
            emoji="\U0001F3AF",
        )
        logger.debug(f"   Checks: {checks_passed}")
        await self._broadcast_decision(
            symbol=symbol,
            phase="signal",
            decision="opportunity_detected",
            reason="All strategy checks aligned",
            details={
                "direction": signal.get("signal"),
                "score": signal.get("score", 0),
                "confidence": signal.get("confidence", 0),
                "checks_passed": signal.get("details", {}).get("passed_checks", []),
            },
            min_interval_seconds=0,
        )
        
        # Track signal
        self.signals_by_symbol[symbol] = self.signals_by_symbol.get(symbol, 0) + 1
        

        
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
        multiplier = self.asset_config.get(symbol, {}).get('multiplier')
        
        if not multiplier:
            self._cycle_step(symbol, 4, 6, "Missing multiplier in asset config", emoji="\u274C", level="error")
            await self._broadcast_decision(
                symbol=symbol,
                phase="risk",
                decision="no_trade",
                reason="Missing multiplier configuration",
                severity="error",
                throttle_key=f"{symbol}:missing_multiplier",
            )
            return False

        # Determine Stake (User Preference)
        base_stake = self.user_stake
        if base_stake is None:
             # Should not happen due to start_bot check, but safety first
             self._cycle_step(symbol, 4, 6, "Stake not configured", emoji="\u274C", level="error")
             await self._broadcast_decision(
                 symbol=symbol,
                 phase="risk",
                 decision="no_trade",
                 reason="Stake not configured",
                 severity="error",
                 throttle_key=f"{symbol}:stake_missing",
             )
             return False
             
        # CRITICAL FIX: Do NOT multiply by multiplier. 
        # The stake passed to Deriv API (amount) is the user's risk amount (cost), 
        # not the total exposure.
        stake = base_stake
        
        # Debug: Log signal structure before validation
        logger.debug(f"Signal structure - Entry: {signal.get('entry_price')}, TP: {signal.get('take_profit')}, SL: {signal.get('stop_loss')}")
        
        # CRITICAL FIX: Add symbol to signal before validation
        signal_for_validation = signal.copy()
        signal_for_validation['symbol'] = symbol
        
        # Validate with risk manager (including global checks)
        can_open, validation_msg = self.risk_manager.can_open_trade(
            symbol=symbol,
            stake=stake,
            take_profit=signal.get('take_profit'),
            stop_loss=signal.get('stop_loss'),
            signal_dict=signal_for_validation
        )
        
        if not can_open:
            self._cycle_step(symbol, 4, 6, f"Risk gate blocked trade: {validation_msg}", emoji="\U0001F6D1", level="warning")
            await self._broadcast_decision(
                symbol=symbol,
                phase="risk",
                decision="no_trade",
                reason=validation_msg,
                details={"gate": "can_open_trade"},
                severity="warning",
                throttle_key=f"{symbol}:trade_blocked",
            )
            return False
            
        # Notify Telegram about signal (Moved here to ensure all checks passed)
        try:
            signal_with_symbol = signal.copy()
            signal_with_symbol['symbol'] = symbol
            await self.telegram_bridge.notify_signal(signal_with_symbol)
        except:
            pass
        
        # Execute trade!
        self._cycle_step(
            symbol,
            5,
            6,
            f"Executing {signal['signal']} | Stake ${stake:.2f} | Multiplier {multiplier}x",
            emoji="\U0001F680",
        )
        await self._broadcast_decision(
            symbol=symbol,
            phase="execution",
            decision="opportunity_taken",
            reason="Risk checks passed, executing trade",
            details={
                "direction": signal.get("signal"),
                "stake": stake,
                "multiplier": multiplier,
            },
            min_interval_seconds=0,
        )
        
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
                
                result_emoji = "\u2705" if pnl > 0 else ("\u274C" if pnl < 0 else "\u2696\ufe0f")
                self._cycle_step(
                    symbol,
                    6,
                    6,
                    f"Trade completed: {status} | P&L: ${pnl:.2f} | Contract: {contract_id}",
                    emoji=result_emoji,
                )

                # CRITICAL FIX: Add signal to result for DB persistence
                if 'signal' not in result:
                    result['signal'] = signal_with_symbol['signal']
                
                # NEW: Add strategy_type to result for database
                result['strategy_type'] = self.strategy.get_strategy_name()
                
                # Record trade closure
                self.risk_manager.record_trade_close(contract_id, pnl, status)
                self.state.update_trade(contract_id, result)


                # Persist to Supabase with error handling
                try:
                    saved = UserTradesService.save_trade(self.account_id, result)
                    if saved:
                        logger.info(f"[{self._get_strategy_name()}][{symbol}] \U0001F9FE Trade persisted to DB: {contract_id}")
                    else:
                        logger.error(
                            f"[{self._get_strategy_name()}][{symbol}] \u274C DB persistence failed for contract {contract_id} (no data returned)"
                        )
                        # Notify via Telegram
                        try:
                            await self.telegram_bridge.notify_error(
                                f"Trade executed but DB save failed: {symbol} {status}"
                            )
                        except:
                            pass
                except Exception as e:
                    logger.error(f"[{self._get_strategy_name()}][{symbol}] \u274C DB save exception for contract {contract_id}: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
                    # Notify via Telegram
                    try:
                        await self.telegram_bridge.notify_error(
                            f"Trade executed but DB error: {symbol} - {str(e)}"
                        )
                    except:
                        pass

                
                # Notify Telegram
                try:
                    # MERGE complete trade details into result for notification
                    result_for_notify = result.copy()
                    result_for_notify.update(signal_with_symbol) # Contains direction, stake, symbol
                    
                    # Ensure symbol is set (sometimes signal uses 'symbol', result uses 'symbol')
                    if 'symbol' not in result_for_notify:
                         result_for_notify['symbol'] = symbol
                    
                    await self.telegram_bridge.notify_trade_closed(result_for_notify, pnl, status, strategy_type=self.strategy.get_strategy_name())
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
                self._cycle_step(symbol, 6, 6, "Trade execution failed (no result)", emoji="\u274C", level="error")
                await self._broadcast_decision(
                    symbol=symbol,
                    phase="execution",
                    decision="opportunity_failed",
                    reason="Trade engine returned no result",
                    severity="error",
                    min_interval_seconds=0,
                )
                return False
                
        except Exception as e:
            self._cycle_step(symbol, 6, 6, f"Trade execution failed: {type(e).__name__}: {e}", emoji="\u274C", level="error")
            logger.error(
                f"[{self._get_strategy_name()}][{symbol}] TRADE_EXECUTION_FAILED traceback",
                exc_info=True,
            )
            await self._broadcast_decision(
                symbol=symbol,
                phase="execution",
                decision="opportunity_failed",
                reason=f"{type(e).__name__}: {e}",
                severity="error",
                min_interval_seconds=0,
            )
            
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
        if not self.risk_manager or not getattr(self.risk_manager, "has_active_trade", False):
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
            # Check for stagnation exit (scalping trades only)
            if trade_status and not trade_status.get('is_sold'):
                if (
                    hasattr(self.risk_manager, "check_trailing_profit")
                    and hasattr(self.risk_manager, "check_stagnation_exit")
                ):
                    current_pnl = trade_status.get('profit', 0.0)
                    trade_info = {
                        'open_time': active_info.get('open_time'),
                        'stake': active_info.get('stake'),
                        'symbol': symbol,
                        'contract_id': contract_id,
                        'direction': active_info.get('direction'),
                        'entry_price': active_info.get('entry_price'),
                        'multiplier': active_info.get('multiplier')
                    }
                    
                    # CHECK 1: Trailing profit exit (when trade is in profit)
                    should_trail_exit, trail_reason, just_activated = self.risk_manager.check_trailing_profit(trade_info, current_pnl)
                    
                    # On first activation, remove server-side TP so trailing controls exit
                    if just_activated:
                        try:
                            await self.trade_engine.remove_take_profit(contract_id)
                        except Exception as e:
                            self._cycle_step(
                                symbol,
                                4,
                                6,
                                f"Failed to remove server-side TP: {e}",
                                emoji="\u274C",
                                level="error",
                            )
                    
                    if should_trail_exit:
                        self._cycle_step(
                            symbol,
                            4,
                            6,
                            "Trailing profit exit triggered - locking gains",
                            emoji="\U0001F4C8",
                            level="warning",
                        )
                        
                        try:
                            sell_result = await self.trade_engine.close_trade(contract_id)
                            
                            if sell_result:
                                pnl = sell_result.get('profit', current_pnl)
                                status = 'won' if pnl > 0 else ('lost' if pnl < 0 else 'break_even')
                                
                                # Record closure
                                self.risk_manager.record_trade_close(contract_id, pnl, status)
                                self.state.update_trade(contract_id, sell_result)
                                
                                self._cycle_step(
                                    symbol,
                                    6,
                                    6,
                                    "Trade closed (trailing profit) - system unlocked",
                                    emoji="\U0001F512",
                                )
                                logger.info(f"[{self._get_strategy_name()}][{symbol}] \U0001F4B0 P&L: ${pnl:.2f}")
                                
                                # Persist to DB
                                try:
                                    result_for_db = sell_result.copy()
                                    result_for_db.update(active_info)
                                    result_for_db['strategy_type'] = self.strategy.get_strategy_name()
                                    result_for_db['exit_reason'] = trail_reason
                                    saved = UserTradesService.save_trade(self.account_id, result_for_db)
                                    if saved:
                                        logger.info(
                                            f"[{self._get_strategy_name()}][{symbol}] "
                                            f"\u2705 Trailing profit trade persisted to DB: {contract_id}"
                                        )
                                except Exception as e:
                                    logger.error(
                                        f"[{self._get_strategy_name()}][{symbol}] "
                                        f"\u274C DB save failed for trailing profit trade: {e}"
                                    )
                                
                                # Notify Telegram
                                try:
                                    result_for_notify = sell_result.copy()
                                    result_for_notify.update(active_info)
                                    result_for_notify['exit_reason'] = trail_reason
                                    
                                    await self.telegram_bridge.notify_trade_closed(
                                        result_for_notify, pnl, status,
                                        strategy_type=self.strategy.get_strategy_name()
                                    )
                                except:
                                    pass
                                
                                return  # Exit monitoring after closing
                        except Exception as e:
                            self._cycle_step(
                                symbol,
                                6,
                                6,
                                f"Failed to close trailing profit trade: {e}",
                                emoji="\u274C",
                                level="error",
                            )
                    
                    # CHECK 2: Stagnation exit (when trade is open too long and losing)
                    should_exit, exit_reason = self.risk_manager.check_stagnation_exit(trade_info, current_pnl)
                    
                    if should_exit:
                        self._cycle_step(
                            symbol,
                            4,
                            6,
                            "Stagnation exit triggered - closing trade",
                            emoji="\u23F0",
                            level="warning",
                        )
                        
                        # Close the trade immediately
                        try:
                            sell_result = await self.trade_engine.close_trade(contract_id)
                            
                            if sell_result:
                                pnl = sell_result.get('profit', current_pnl)
                                status = 'loss' if pnl < 0 else ('win' if pnl > 0 else 'break_even')
                                
                                # Record closure with stagnation exit reason
                                self.risk_manager.record_trade_close(contract_id, pnl, status)
                                self.state.update_trade(contract_id, sell_result)
                                
                                self._cycle_step(
                                    symbol,
                                    6,
                                    6,
                                    "Trade closed (stagnation) - system unlocked",
                                    emoji="\U0001F513",
                                )
                                logger.info(f"[{self._get_strategy_name()}][{symbol}] \U0001F4B0 P&L: ${pnl:.2f}")
                                
                                # Notify Telegram with stagnation exit reason
                                try:
                                    result_for_notify = sell_result.copy()
                                    result_for_notify.update(active_info)
                                    result_for_notify['exit_reason'] = exit_reason
                                    
                                    await self.telegram_bridge.notify_trade_closed(
                                        result_for_notify, pnl, status, 
                                        strategy_type=self.strategy.get_strategy_name()
                                    )
                                except:
                                    pass
                                
                                return  # Exit monitoring after closing
                        except Exception as e:
                            self._cycle_step(
                                symbol,
                                6,
                                6,
                                f"Failed to close stagnant trade: {e}",
                                emoji="\u274C",
                                level="error",
                            )
            
            if trade_status and trade_status.get('is_sold'):
                # Trade closed externally or by TP/SL
                logger.info(f"[{self._get_strategy_name()}][{symbol}] \U0001F4E1 Trade detected as closed")
                
                pnl = trade_status.get('profit', 0.0)
                status = trade_status.get('status', 'sold')
                
                # Record closure
                self.risk_manager.record_trade_close(contract_id, pnl, status)
                self.state.update_trade(contract_id, trade_status)
                
                logger.info(f"[{self._get_strategy_name()}][{symbol}] \U0001F513 Trade closed - system unlocked")
                logger.info(f"[{self._get_strategy_name()}][{symbol}] \U0001F4B0 P&L: ${pnl:.2f}")
                
                # Notify Telegram
                try:
                    # MERGE complete trade details into result for notification
                    result_for_notify = trade_status.copy()
                    result_for_notify.update(active_info) # Contains direction, stake, symbol from RiskManager
                    
                    await self.telegram_bridge.notify_trade_closed(result_for_notify, pnl, status, strategy_type=self.strategy.get_strategy_name())
                except:
                    pass
            
        except Exception as e:
            logger.warning(f"[{self._get_strategy_name()}][{symbol}] \u26A0\ufe0f Could not monitor trade: {e}")

# Global bot runner instance - DEPRECATED / DEFAULT
# We keep this for backward compatibility if needed, using env vars
bot_runner = BotRunner()

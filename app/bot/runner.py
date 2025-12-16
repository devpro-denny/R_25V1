"""
Bot Runner - Manages the lifecycle of the trading bot
Wraps existing bot logic without modifying it
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional
from enum import Enum

# Import existing bot modules (DO NOT MODIFY THESE)
from data_fetcher import DataFetcher
from strategy import TradingStrategy
from trade_engine import TradeEngine
from risk_manager import RiskManager
import config

from app.bot.state import bot_state
from app.bot.events import event_manager
from app.bot.telegram_bridge import telegram_bridge

logger = logging.getLogger(__name__)

class BotStatus(str, Enum):
    """Bot status enumeration"""
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"

class BotRunner:
    """
    Manages the trading bot lifecycle
    - Start/Stop/Restart
    - Status tracking
    - Event broadcasting
    - Telegram notifications
    """
    
    def __init__(self):
        self.is_running = False
        self.task: Optional[asyncio.Task] = None
        self.status = BotStatus.STOPPED
        self.start_time: Optional[datetime] = None
        self.error_message: Optional[str] = None
        
        # Bot components (initialized on start)
        self.data_fetcher: Optional[DataFetcher] = None
        self.trade_engine: Optional[TradeEngine] = None
        self.strategy: Optional[TradingStrategy] = None
        self.risk_manager: Optional[RiskManager] = None
        
        # Telegram bridge
        self.telegram_bridge = telegram_bridge
    
    async def start_bot(self) -> dict:
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
        
        try:
            logger.info("🚀 Starting trading bot...")
            self.status = BotStatus.STARTING
            self.error_message = None  # Clear previous errors
            bot_state.update_status("starting")
            
            # Create bot task
            self.task = asyncio.create_task(self._run_bot())
            
            # Wait longer for bot to fully initialize (up to 10 seconds)
            max_wait = 10
            for i in range(max_wait):
                await asyncio.sleep(1)
                
                # Check if bot started successfully
                if self.is_running:
                    logger.info("✅ Bot started successfully")
                    await event_manager.broadcast({
                        "type": "bot_status",
                        "status": "running",
                        "message": "Bot started successfully"
                    })
                    
                    return {
                        "success": True,
                        "message": "Bot started successfully",
                        "status": self.status.value
                    }
                
                # Check if bot failed during startup
                if self.status == BotStatus.ERROR:
                    error_msg = self.error_message or "Bot initialization failed"
                    raise Exception(error_msg)
            
            # Timeout - bot didn't start in time
            raise Exception("Bot startup timeout - took longer than expected")
                
        except Exception as e:
            logger.error(f"❌ Failed to start bot: {e}")
            self.status = BotStatus.ERROR
            self.error_message = str(e)
            bot_state.update_status("error", error=str(e))
            
            # Cancel the task if it's still running
            if self.task and not self.task.done():
                self.task.cancel()
            
            # Notify Telegram about startup failure
            try:
                await self.telegram_bridge.notify_error(f"Failed to start bot: {e}")
            except:
                pass
            
            return {
                "success": False,
                "message": f"Failed to start bot: {e}",
                "status": self.status.value
            }
    
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
            logger.info("🛑 Stopping trading bot...")
            self.status = BotStatus.STOPPING
            bot_state.update_status("stopping")
            
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
            
            bot_state.update_status("stopped")
            logger.info("✅ Bot stopped successfully")
            
            # Notify Telegram
            try:
                stats = bot_state.get_statistics()
                await self.telegram_bridge.notify_bot_stopped(stats)
            except:
                pass
            
            await event_manager.broadcast({
                "type": "bot_status",
                "status": "stopped",
                "message": "Bot stopped successfully"
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
        logger.info("🔄 Restarting trading bot...")
        
        # Stop if running
        if self.is_running:
            stop_result = await self.stop_bot()
            if not stop_result["success"]:
                return stop_result
            
            # Wait for clean shutdown
            await asyncio.sleep(3)
        
        # Start bot
        return await self.start_bot()
    
    def get_status(self) -> dict:
        """Get current bot status"""
        uptime = None
        if self.start_time:
            uptime = int((datetime.now() - self.start_time).total_seconds())
        
        return {
            "status": self.status.value,
            "is_running": self.is_running,
            "uptime_seconds": uptime,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "error_message": self.error_message,
            "statistics": bot_state.get_statistics()
        }
    
    async def _run_bot(self):
        """
        Main bot loop - Wraps existing bot logic
        This runs continuously in the background
        """
        try:
            logger.info("🤖 Bot main loop starting...")
            
            # Initialize bot components (existing modules)
            try:
                self.data_fetcher = DataFetcher(
                    config.DERIV_API_TOKEN,
                    config.DERIV_APP_ID
                )
                
                self.trade_engine = TradeEngine(
                    config.DERIV_API_TOKEN,
                    config.DERIV_APP_ID
                )
                
                self.strategy = TradingStrategy()
                self.risk_manager = RiskManager()
            except Exception as e:
                self.status = BotStatus.ERROR
                self.error_message = f"Component initialization failed: {e}"
                logger.error(f"❌ {self.error_message}")
                return
            
            # Connect to Deriv API
            try:
                data_connected = await self.data_fetcher.connect()
                trade_connected = await self.trade_engine.connect()
                
                if not data_connected or not trade_connected:
                    raise Exception("Failed to connect to Deriv API")
            except Exception as e:
                self.status = BotStatus.ERROR
                self.error_message = f"Deriv API connection failed: {e}"
                logger.error(f"❌ {self.error_message}")
                return
            
            # Get initial balance
            try:
                balance = await self.data_fetcher.get_balance()
                if balance:
                    bot_state.update_balance(balance)
            except Exception as e:
                logger.warning(f"⚠️ Could not fetch balance: {e}")
                balance = 0.0
            
            # Mark as running - THIS IS CRITICAL
            self.is_running = True
            self.status = BotStatus.RUNNING
            self.start_time = datetime.now()
            self.error_message = None  # Clear any previous errors
            bot_state.update_status("running")
            
            logger.info("✅ Bot is now running")
            
            # NOTIFY TELEGRAM: Bot started
            try:
                await self.telegram_bridge.notify_bot_started(balance or 0.0)
            except Exception as e:
                logger.warning(f"⚠️ Telegram notification failed: {e}")
            
            # Broadcast to WebSockets
            await event_manager.broadcast({
                "type": "bot_status",
                "status": "running",
                "message": "Bot started successfully",
                "balance": balance
            })
            
            cycle_count = 0
            
            # Main trading loop (continuous)
            while self.is_running:
                try:
                    cycle_count += 1
                    logger.debug(f"Trading cycle #{cycle_count}")
                    
                    # Execute one trading cycle (existing logic)
                    await self._trading_cycle()
                    
                    # Check cooldown
                    cooldown = self.risk_manager.get_cooldown_remaining()
                    wait_time = max(cooldown, 30)  # Minimum 30s between cycles
                    
                    # Sleep with cancellation check
                    for _ in range(int(wait_time)):
                        if not self.is_running:
                            break
                        await asyncio.sleep(1)
                    
                except asyncio.CancelledError:
                    logger.info("Bot loop cancelled")
                    break
                except Exception as e:
                    logger.error(f"Error in trading cycle: {e}")
                    
                    # NOTIFY TELEGRAM: Error
                    try:
                        await self.telegram_bridge.notify_error(str(e))
                    except:
                        pass
                    
                    # Broadcast to WebSockets
                    await event_manager.broadcast({
                        "type": "error",
                        "message": str(e),
                        "timestamp": datetime.now().isoformat()
                    })
                    await asyncio.sleep(30)
            
        except asyncio.CancelledError:
            logger.info("Bot task cancelled")
        except Exception as e:
            logger.error(f"Fatal error in bot: {e}")
            self.status = BotStatus.ERROR
            self.error_message = str(e)
            bot_state.update_status("error", error=str(e))
            
            # NOTIFY TELEGRAM: Fatal error
            try:
                await self.telegram_bridge.notify_error(f"Fatal error: {e}")
            except:
                pass
            
            # Broadcast to WebSockets
            await event_manager.broadcast({
                "type": "error",
                "message": f"Fatal error: {e}",
                "timestamp": datetime.now().isoformat()
            })
        finally:
            self.is_running = False
            logger.info("Bot main loop exited")
    
    async def _trading_cycle(self):
        """
        Execute one trading cycle
        This is the existing bot logic wrapped with Telegram notifications
        """
        # Check if can trade
        can_trade, reason = self.risk_manager.can_trade()
        if not can_trade:
            logger.debug(f"Cannot trade: {reason}")
            return
        
        # Fetch market data
        market_data = await self.data_fetcher.fetch_multi_timeframe_data(config.SYMBOL)
        
        if '1m' not in market_data or '5m' not in market_data:
            logger.warning("Failed to fetch market data")
            return
        
        data_1m = market_data['1m']
        data_5m = market_data['5m']
        
        # Analyze market
        signal = self.strategy.analyze(data_1m, data_5m)
        
        if not signal['can_trade']:
            logger.debug(f"No trade signal: {signal['details'].get('reason', 'Unknown')}")
            return
        
        # NOTIFY TELEGRAM: Signal detected
        try:
            await self.telegram_bridge.notify_signal(signal)
        except:
            pass
        
        # Broadcast signal event to WebSockets
        await event_manager.broadcast({
            "type": "signal",
            "signal": signal['signal'],
            "score": signal['score'],
            "confidence": signal.get('confidence', 0),
            "timestamp": datetime.now().isoformat()
        })
        
        # Record signal
        bot_state.add_signal(signal)
        
        # Validate trade parameters
        valid, msg = self.risk_manager.validate_trade_parameters(
            config.FIXED_STAKE,
            config.FIXED_TP,
            config.MAX_LOSS_PER_TRADE
        )
        
        if not valid:
            logger.warning(f"Invalid trade parameters: {msg}")
            return
        
        # Execute trade
        logger.info(f"Executing {signal['signal']} trade...")
        
        # Open trade
        trade_info = await self.trade_engine.open_trade(
            direction=signal['signal'],
            stake=config.FIXED_STAKE,
            take_profit=config.FIXED_TP,
            stop_loss=config.MAX_LOSS_PER_TRADE
        )
        
        if not trade_info:
            logger.error("Failed to open trade")
            return
        
        # Record trade opening
        self.risk_manager.record_trade_open(trade_info)
        bot_state.add_trade(trade_info)
        
        # NOTIFY TELEGRAM: Trade opened
        try:
            await self.telegram_bridge.notify_trade_opened(trade_info)
        except:
            pass
        
        # Broadcast trade opened event to WebSockets
        await event_manager.broadcast({
            "type": "trade_opened",
            "trade": trade_info,
            "timestamp": datetime.now().isoformat()
        })
        
        # Monitor trade
        final_status = await self.trade_engine.monitor_trade(
            trade_info['contract_id'],
            trade_info,
            max_duration=config.MAX_TRADE_DURATION,
            risk_manager=self.risk_manager
        )
        
        if final_status:
            # Record trade closure
            pnl = final_status.get('profit', 0.0)
            status = final_status.get('status', 'unknown')
            contract_id = final_status.get('contract_id')
            
            self.risk_manager.record_trade_close(contract_id, pnl, status)
            bot_state.update_trade(contract_id, final_status)
            
            # NOTIFY TELEGRAM: Trade closed
            try:
                await self.telegram_bridge.notify_trade_closed(final_status, pnl, status)
            except:
                pass
            
            # Broadcast trade closed event to WebSockets
            await event_manager.broadcast({
                "type": "trade_closed",
                "trade": final_status,
                "pnl": pnl,
                "status": status,
                "timestamp": datetime.now().isoformat()
            })
            
            # Update statistics
            stats = self.risk_manager.get_statistics()
            bot_state.update_statistics(stats)
            
            # Broadcast stats update to WebSockets
            await event_manager.broadcast({
                "type": "statistics",
                "stats": stats,
                "timestamp": datetime.now().isoformat()
            })

# Global bot runner instance
bot_runner = BotRunner()
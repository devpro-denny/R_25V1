"""
Main Controller for Deriv Multi-Asset Multipliers Trading Bot
Coordinates all components and runs the trading loop across multiple assets
main.py - MULTI-ASSET WITH TOP-DOWN STRATEGY SUPPORT
"""

import asyncio
import signal
import sys
from datetime import datetime
from typing import Dict, List, Optional
import config
from utils import setup_logger, print_statistics, format_currency
from data_fetcher import DataFetcher
from strategy import TradingStrategy
from trade_engine import TradeEngine
from risk_manager import RiskManager

# Setup logger
logger = setup_logger(config.LOG_FILE, config.LOG_LEVEL)

# Try to import telegram notifier
try:
    from telegram_notifier import notifier, TelegramLoggingHandler
    TELEGRAM_ENABLED = True
    
    # Attach Telegram logging handler to root logger
    if TELEGRAM_ENABLED:
        try:
            telegram_handler = TelegramLoggingHandler(notifier)
            logging.getLogger().addHandler(telegram_handler)
            logger.info("✅ Telegram error logging enabled")
        except Exception as e:
            logger.warning(f"⚠️ Failed to setup Telegram logging: {e}")
            
except ImportError:
    TELEGRAM_ENABLED = False
    logger.warning("⚠️ Telegram notifier not available")

class TradingBot:
    """Main trading bot controller with multi-asset support"""
    
    def __init__(self):
        """Initialize trading bot components"""
        self.running = False
        self.data_fetcher = None
        self.trade_engine = None
        self.strategy = None
        self.risk_manager = None
        
        # Multi-asset tracking
        self.symbols = config.get_all_symbols()
        self.asset_signals: Dict[str, Optional[Dict]] = {symbol: None for symbol in self.symbols}
        
        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        logger.warning("\n⚠️ Shutdown signal received")
        self.running = False
    
    async def initialize(self) -> bool:
        """
        Initialize all bot components
        
        Returns:
            True if initialization successful
        """
        try:
            logger.info("="*60)
            logger.info("🚀 Initializing Deriv Multi-Asset Multipliers Trading Bot")
            logger.info("="*60)
            
            # Validate configuration
            logger.info("📋 Validating configuration...")
            config.validate_config()
            logger.info("✅ Configuration valid")
            
            # Initialize components
            logger.info("🔧 Initializing components...")
            
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
            
            # Connect to API
            logger.info("🔌 Connecting to Deriv API...")
            
            data_connected = await self.data_fetcher.connect()
            trade_connected = await self.trade_engine.connect()
            
            if not data_connected or not trade_connected:
                logger.error("❌ Failed to connect to API")
                return False
            
            # Get and log account balance
            balance = await self.data_fetcher.get_balance()
            if balance:
                logger.info(f"💰 Account Balance: {format_currency(balance)}")
                if TELEGRAM_ENABLED:
                    try:
                        await notifier.notify_bot_started(balance)
                    except Exception as e:
                        logger.error(f"❌ Telegram notification failed: {e}")
            
            # Log trading parameters
            logger.info("="*60)
            
            strategy_mode = "TOP-DOWN MULTI-TIMEFRAME" if config.USE_TOPDOWN_STRATEGY else "TWO-PHASE SCALPING"
            logger.info(f"TRADING PARAMETERS - {strategy_mode}")
            logger.info("="*60)
            logger.info(f"📊 Assets Monitored: {len(self.symbols)}")
            for symbol in self.symbols:
                asset_info = config.get_asset_info(symbol)
                logger.info(f"   • {symbol}: {asset_info['multiplier']}x ({asset_info['description']})")
            
            stake_display = format_currency(config.FIXED_STAKE) if config.FIXED_STAKE else "USER_DEFINED"
            logger.info(f"💵 Stake: {stake_display}")
            logger.info(f"🎯 Max Concurrent Trades: {config.MAX_CONCURRENT_TRADES}")
            
            if config.USE_TOPDOWN_STRATEGY:
                logger.info(f"📈 Strategy: Top-Down Multi-Timeframe Analysis")
                logger.info(f"📊 Timeframes: 1w, 1d, 4h, 1h, 5m, 1m")
                logger.info(f"🎯 Min R:R Ratio: 1:{config.TOPDOWN_MIN_RR_RATIO}")
                logger.info(f"💰 Dynamic TP/SL: Based on market structure")
            else:
                logger.info(f"🎯 Take Profit: {config.TAKE_PROFIT_PERCENT}%")
                logger.info(f"🛑 Stop Loss: {config.STOP_LOSS_PERCENT}%")
            
            logger.info(f"⏰ Cooldown: {config.COOLDOWN_SECONDS}s")
            logger.info(f"🔢 Max Daily Trades: {config.MAX_TRADES_PER_DAY}")
            daily_loss_display = format_currency(config.MAX_DAILY_LOSS) if config.MAX_DAILY_LOSS else "DYNAMIC (3x Stake)"
            logger.info(f"💸 Max Daily Loss: {daily_loss_display}")
            logger.info("="*60)
            
            logger.info("✅ Bot initialized successfully!")
            return True
            
        except Exception as e:
            logger.error(f"❌ Initialization failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False
    
    async def shutdown(self):
        """Gracefully shutdown the bot"""
        logger.info("🛑 Shutting down bot...")
        
        try:
            # Disconnect from API
            if self.data_fetcher:
                await self.data_fetcher.disconnect()
            
            if self.trade_engine:
                await self.trade_engine.disconnect()
            
            # Print final statistics
            if self.risk_manager:
                logger.info("\n" + "="*60)
                logger.info("FINAL STATISTICS")
                logger.info("="*60)
                stats = self.risk_manager.get_statistics()
                print_statistics(stats)
                
                if TELEGRAM_ENABLED:
                    try:
                        await notifier.notify_bot_stopped(stats)
                    except Exception as e:
                        logger.error(f"❌ Telegram notification failed: {e}")
            
            logger.info("✅ Bot shutdown complete")
            
        except Exception as e:
            logger.error(f"❌ Error during shutdown: {e}")
    
    async def analyze_asset(self, symbol: str) -> Optional[Dict]:
        """
        Analyze a single asset and generate trading signal
        
        Args:
            symbol: Trading symbol (e.g., 'R_25')
        
        Returns:
            Signal dictionary or None if analysis failed
        """
        try:
            logger.info(f"📊 Analyzing {symbol}...")
            
            if config.USE_TOPDOWN_STRATEGY:
                # Fetch all timeframes for Top-Down analysis
                all_timeframes = await self.data_fetcher.fetch_all_timeframes(symbol)
                
                if not all_timeframes:
                    logger.warning(f"⚠️ Failed to fetch data for {symbol}")
                    return None
                
                fetched_tfs = list(all_timeframes.keys())
                logger.debug(f"   Fetched timeframes: {', '.join(fetched_tfs)}")
                
                # Analyze with all available timeframes
                signal = self.strategy.analyze(
                    data_1m=all_timeframes.get('1m'),
                    data_5m=all_timeframes.get('5m'),
                    data_1h=all_timeframes.get('1h'),
                    data_4h=all_timeframes.get('4h'),
                    data_1d=all_timeframes.get('1d'),
                    data_1w=all_timeframes.get('1w')
                )
            else:
                # Legacy: Use 1m+5m only
                market_data = await self.data_fetcher.fetch_multi_timeframe_data(symbol)
                
                if '1m' not in market_data or '5m' not in market_data:
                    logger.warning(f"⚠️ Failed to fetch complete data for {symbol}")
                    return None
                
                signal = self.strategy.analyze(market_data['1m'], market_data['5m'])
            
            # Add symbol to signal
            if signal:
                signal['symbol'] = symbol
                signal['asset_info'] = config.get_asset_info(symbol)
            
            return signal
            
        except Exception as e:
            logger.error(f"❌ Error analyzing {symbol}: {e}")
            return None
    
    async def scan_all_assets(self) -> List[Dict]:
        """
        Scan all configured assets and return valid trading signals
        
        Returns:
            List of valid signals sorted by strength (if prioritization enabled)
        """
        logger.info(f"🔍 Scanning {len(self.symbols)} assets for trading opportunities...")
        
        valid_signals = []
        
        # Analyze each asset sequentially to avoid rate limiting
        for symbol in self.symbols:
            signal = await self.analyze_asset(symbol)
            
            if signal and signal.get('can_trade'):
                valid_signals.append(signal)
                logger.info(f"✅ {symbol}: Valid {signal['signal']} signal (score: {signal.get('score', 0)})")
            else:
                reason = signal['details'].get('reason', 'Unknown') if signal else 'Analysis failed'
                logger.debug(f"⚪ {symbol}: {reason}")
            
            # Store signal for tracking
            self.asset_signals[symbol] = signal
            
            # Brief pause between assets to respect rate limits
            await asyncio.sleep(0.5)
        
        if not valid_signals:
            logger.info("📭 No valid signals found across all assets")
            return []
        
        # Prioritize by signal strength if enabled
        if config.PRIORITIZE_BY_SIGNAL_STRENGTH:
            valid_signals.sort(key=lambda s: s.get('score', 0), reverse=True)
            logger.info(f"📊 Prioritized {len(valid_signals)} signals by strength")
        
        return valid_signals
    
    async def trading_cycle(self):
        """Execute one trading cycle across all assets"""
        try:
            # Check if we can trade
            can_trade, reason = self.risk_manager.can_trade()
            
            if not can_trade:
                logger.debug(f"⏸️ Cannot trade: {reason}")
                return
            
            # Scan all assets for trading opportunities
            valid_signals = await self.scan_all_assets()
            
            if not valid_signals:
                return
            
            # Trade the first valid signal (respecting MAX_CONCURRENT_TRADES limit)
            signal = valid_signals[0]
            symbol = signal['symbol']
            
            logger.info(f"🎯 Selected {symbol} for trading (strongest signal)")
            
            # Notify signal detected
            if TELEGRAM_ENABLED:
                try:
                    await notifier.notify_signal(signal)
                except Exception as e:
                    logger.error(f"❌ Telegram notification failed: {e}")
            
            # Validate trade parameters
            if config.USE_TOPDOWN_STRATEGY:
                # Top-Down: TP/SL come from strategy
                tp_price = signal.get('take_profit')
                sl_price = signal.get('stop_loss')
                
                if not tp_price or not sl_price:
                    logger.warning(f"⚠️ {symbol}: Strategy did not provide TP/SL levels")
                    return
                
                # Validate risk/reward ratio
                entry_price = signal.get('entry_price', 0)
                if entry_price > 0:
                    rr_ratio = signal.get('risk_reward_ratio', 0)
                    if rr_ratio < config.TOPDOWN_MIN_RR_RATIO:
                        logger.warning(f"⚠️ {symbol}: R:R ratio {rr_ratio:.2f} below minimum {config.TOPDOWN_MIN_RR_RATIO}")
                        return
                
                valid = True
                msg = "Top-Down parameters validated"
            else:
                # Legacy: Validate only stake
                valid, msg = self.risk_manager.validate_trade_parameters(
                    stake=config.FIXED_STAKE or 50.0
                )
            
            if not valid:
                logger.warning(f"⚠️ {symbol}: Invalid trade parameters: {msg}")
                return
            
            # Execute trade
            logger.info(f"🚀 Executing {signal['signal']} trade on {symbol}...")
            
            # Log trade details if using Top-Down
            if config.USE_TOPDOWN_STRATEGY:
                logger.info(f"   📍 Entry: {signal.get('entry_price', 0):.4f}")
                logger.info(f"   🎯 TP: {signal.get('take_profit', 0):.4f}")
                logger.info(f"   🛡️ SL: {signal.get('stop_loss', 0):.4f}")
                logger.info(f"   📊 R:R: 1:{signal.get('risk_reward_ratio', 0):.2f}")
            
            # Execute trade with monitoring
            result = await self.trade_engine.execute_trade(signal, self.risk_manager)
            
            if result:
                # Trade completed successfully
                pnl = result.get('profit', 0.0)
                status = result.get('status', 'unknown')
                contract_id = result.get('contract_id')
                
                # Record trade closure
                self.risk_manager.record_trade_close(
                    contract_id,
                    pnl,
                    status
                )
                
                # Log statistics
                stats = self.risk_manager.get_statistics()
                logger.info(f"📈 Win Rate: {stats['win_rate']:.1f}%")
                logger.info(f"💰 Total P&L: {format_currency(stats['total_pnl'])}")
                logger.info(f"📊 Trades Today: {stats['trades_today']}/{config.MAX_TRADES_PER_DAY}")
                
                # Send Telegram notification
                if TELEGRAM_ENABLED:
                    trade_info = None
                    for t in self.risk_manager.trades_today:
                        if t.get('contract_id') == contract_id:
                            trade_info = t
                            break
                    
                    if trade_info:
                        try:
                            await notifier.notify_trade_closed(result, trade_info)
                        except Exception as e:
                            logger.error(f"❌ Telegram notification failed: {e}")
            else:
                logger.error(f"❌ {symbol}: Trade execution failed")
            
        except Exception as e:
            logger.error(f"❌ Error in trading cycle: {e}")
            import traceback
            logger.error(traceback.format_exc())
    
    async def run(self):
        """Main trading loop"""
        try:
            # Initialize
            if not await self.initialize():
                logger.error("❌ Failed to initialize bot")
                return
            
            self.running = True
            logger.info("\n🚀 Starting main trading loop")
            logger.info(f"📊 Monitoring {len(self.symbols)} assets: {', '.join(self.symbols)}")
            logger.info("Press Ctrl+C to stop\n")
            
            cycle_count = 0
            
            # Main loop
            while self.running:
                try:
                    cycle_count += 1
                    logger.info(f"\n{'='*60}")
                    logger.info(f"CYCLE #{cycle_count} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                    logger.info(f"{'='*60}")
                    
                    # Execute trading cycle (scans all assets)
                    await self.trading_cycle()
                    
                    # Check cooldown
                    cooldown = self.risk_manager.get_cooldown_remaining()
                    if cooldown > 0:
                        logger.info(f"⏰ Cooldown: {cooldown:.0f}s remaining")
                    
                    # Wait before next cycle
                    wait_time = max(cooldown, 30)  # At least 30 seconds between cycles
                    logger.info(f"⏳ Next cycle in {wait_time:.0f}s...")
                    
                    # Sleep with interrupt check
                    for _ in range(int(wait_time)):
                        if not self.running:
                            break
                        await asyncio.sleep(1)
                    
                except KeyboardInterrupt:
                    logger.warning("\n⚠️ Keyboard interrupt received")
                    self.running = False
                    break
                    
                except Exception as e:
                    logger.error(f"❌ Error in main loop: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
                    await asyncio.sleep(30)  # Wait before retry
            
        except Exception as e:
            logger.error(f"❌ Fatal error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            
        finally:
            await self.shutdown()

def main():
    """Entry point"""
    try:
        # Determine strategy mode
        strategy_name = "Top-Down Multi-Timeframe" if config.USE_TOPDOWN_STRATEGY else "Two-Phase Scalping"
        
        # Print welcome banner
        print("\n" + "="*60)
        print("   DERIV MULTI-ASSET MULTIPLIERS TRADING BOT")
        print(f"   {strategy_name.upper()}")
        print("="*60)
        print(f"   Version: 3.0 (Multi-Asset)")
        print(f"   Assets: {', '.join(config.get_all_symbols())}")
        print(f"   Strategy: {strategy_name}")
        print(f"   Max Concurrent: {config.MAX_CONCURRENT_TRADES}")
        if config.USE_TOPDOWN_STRATEGY:
            print(f"   Min R:R: 1:{config.TOPDOWN_MIN_RR_RATIO}")
        print("="*60 + "\n")
        
        # Create and run bot
        bot = TradingBot()
        asyncio.run(bot.run())
        
    except KeyboardInterrupt:
        print("\n\n✅ Bot stopped by user")
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
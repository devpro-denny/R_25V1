"""
Telegram Notifier for Deriv R_25 Trading Bot
FIXED VERSION - Handles None values and cancellation phases
Sends trade notifications via Telegram
"""

import os
import asyncio
from typing import Dict, Optional
from datetime import datetime
from telegram import Bot
from telegram.error import TelegramError
import logging
import config
from utils import setup_logger, format_currency

logger = setup_logger()

class TelegramLoggingHandler(logging.Handler):
    """
    Logging handler that sends error logs to Telegram
    """
    def __init__(self, notifier_instance):
        super().__init__()
        self.notifier = notifier_instance
        self.setLevel(logging.ERROR)
        
    def emit(self, record):
        try:
            msg = self.format(record)
            # Avoid infinite recursion if the notifier itself logs an error
            # We can run this in the background or use ensure_future if we are in an async loop context
            # However, logging emit is sync. Converting to async is tricky without loop reference.
            # Best effort: use event loop if available
            
            try:
                loop = asyncio.get_running_loop()
                if loop and loop.is_running():
                    loop.create_task(self.notifier.notify_error(f"LOG: {msg}"))
            except RuntimeError:
                # No running loop, or different thread. 
                # Ideally we shouldn't block, but for critical errors it might be worth it.
                # For now, let's just skip if no loop to avoid breaking sync code
                pass
                
        except Exception:
            self.handleError(record)


class TelegramNotifier:
    """Handles Telegram notifications for trading events"""
    
    def __init__(self):
        """Initialize Telegram notifier"""
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.bot = None
        self.enabled = False
        
        # Deduplication tracking
        self.processed_closed_trades = set() # Stores f"{contract_id}_{status}"
        
        if self.bot_token and self.chat_id:
            try:
                self.bot = Bot(token=self.bot_token)
                self.enabled = True
                logger.info("✅ Telegram notifications enabled")
            except Exception as e:
                logger.warning(f"⚠️ Failed to initialize Telegram bot: {e}")
                self.enabled = False
        else:
            logger.info("ℹ️ Telegram notifications disabled (no credentials)")
    
    def _safe_format(self, value, default: str = "N/A") -> str:
        """Safely format a value, handling None cases"""
        if value is None:
            return default
        try:
            if isinstance(value, (int, float)):
                return format_currency(value)
            return str(value)
        except Exception:
            return default
            
    def _create_strength_bar(self, score: float, max_score: int = 10) -> str:
        """Create a visual strength bar"""
        # score is typically 0-10 or similar
        normalized_score = max(0, min(score, max_score))
        filled = int((normalized_score / max_score) * 5) # 5 bars total
        empty = 5 - filled
        return "▮" * filled + "▯" * empty

    async def send_message(self, message: str, parse_mode: str = "HTML") -> bool:
        """
        Send a message via Telegram
        
        Args:
            message: Message text
            parse_mode: Parse mode (HTML or Markdown)
        
        Returns:
            True if sent successfully
        """
        if not self.enabled:
            return False
        
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode=parse_mode
            )
            return True
        except TelegramError as e:
            logger.error(f"❌ Failed to send Telegram message: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Telegram error: {e}")
            return False
    
    async def notify_bot_started(self, balance: float, stake: float = None, strategy_name: str = None):
        """Notify that bot has started"""
        # Use provided strategy name or fallback to config detection
        if strategy_name:
            strategy_mode = f"📊 {strategy_name}"
        else:
            strategy_mode = "🛡️ Top-Down Structure" if config.USE_TOPDOWN_STRATEGY else "⚡ Classic Scalping"
        
        if config.ENABLE_CANCELLATION and not config.USE_TOPDOWN_STRATEGY:
            risk_text = (
                f"🛡️ <b>Cancellation Protection</b>\n"
                f"   • Duration: {config.CANCELLATION_DURATION}s\n"
                f"   • Fee: {format_currency(config.CANCELLATION_FEE)}"
            )
        elif config.USE_TOPDOWN_STRATEGY:
            risk_text = (
                f"🛡️ <b>Risk Management</b>\n"
                f"   • TP/SL: Dynamic (Structure)\n"
                f"   • Min R:R: 1:{config.TOPDOWN_MIN_RR_RATIO}"
            )
        else:
            risk_text = (
                f"🛡️ <b>Risk Management</b>\n"
                f"   • TP: {config.TAKE_PROFIT_PERCENT}%\n"
                f"   • SL: {config.STOP_LOSS_PERCENT}%"
            )

        message = (
            "🚀 <b>BOT STARTED</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 Account: <b>{config.DERIV_APP_ID}</b>\n"
            f"💰 Balance: <b>{format_currency(balance)}</b>\n\n"
            f"⚙️ <b>Configuration</b>\n"
            f"   • Strategy: {strategy_mode}\n"
            f"   • Symbols: {len(config.SYMBOLS)} Active\n"
            f"   • Stake: {format_currency(stake) if stake else 'USER_DEFINED'}\n\n"
            f"{risk_text}\n\n"
            f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        await self.send_message(message)
    
    async def notify_signal(self, signal: Dict):
        """Notify about trading signal"""
        direction = signal.get('signal', 'UNKNOWN')
        score = signal.get('score', 0)
        details = signal.get('details', {})
        symbol = signal.get('symbol', config.SYMBOL)
        
        if direction == 'HOLD':
            return  # Don't notify for HOLD signals
            
        emoji = "🟢" if direction == "BUY" else "🔴"
        strength_bar = self._create_strength_bar(score, config.MINIMUM_SIGNAL_SCORE + 4) # Adjust scale
        
        # Safely get values with defaults
        rsi = details.get('rsi', 0)
        adx = details.get('adx', 0)
        
        message = (
            f"{emoji} <b>SIGNAL DETECTED: {symbol}</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"📍 Direction: <b>{direction}</b>\n"
            f"📊 Strength: {strength_bar} ({score:.1f})\n\n"
            f"📉 <b>Technical Indicators</b>\n"
            f"   • RSI: {rsi:.1f}\n"
            f"   • ADX: {adx:.1f}\n"
        )
        
        # Add pivot/level info if available
        if 'proximity' in details:
            message += f"   • Level Dist: {details['proximity']:.3f}%\n"
            
        message += f"\n⏰ {datetime.now().strftime('%H:%M:%S')}"
        
        await self.send_message(message)
    
    async def notify_trade_opened(self, trade_info: Dict):
        """Notify that a trade has been opened"""
        direction = trade_info.get('direction', 'UNKNOWN')
        emoji = "🟢" if direction == "BUY" else "🔴"
        symbol = trade_info.get('symbol', config.SYMBOL)
        stake = trade_info.get('stake', 0)
        
        # Calculate projected targets
        tp_amount = 0
        sl_risk = 0
        
        entry_spot = trade_info.get('entry_spot') or trade_info.get('entry_price', 0)
        multiplier = trade_info.get('multiplier', config.MULTIPLIER)
        
        # 1. Try to calculate from exact price levels (Dynamic/Top-Down)
        if entry_spot > 0 and trade_info.get('take_profit') and trade_info.get('stop_loss'):
            tp_price = trade_info['take_profit']
            sl_price = trade_info['stop_loss']
            
            # Profit = Stake * Multiplier * (% Change)
            # % Change = abs(Target - Entry) / Entry
            tp_amount = stake * multiplier * (abs(tp_price - entry_spot) / entry_spot)
            sl_risk = stake * multiplier * (abs(entry_spot - sl_price) / entry_spot)
            
        # 2. Fallback: Use amount estimates if provided (Legacy)
        elif 'take_profit_amount' in trade_info:
             tp_amount = trade_info['take_profit_amount']
             if 'stop_loss_amount' in trade_info:
                sl_risk = trade_info['stop_loss_amount']

        # 3. Fallback: Estimate based on config percentages (Fixed/Legacy)
        else:
             if trade_info.get('take_profit') or config.TAKE_PROFIT_PERCENT:
                 tp_amount = stake * multiplier * (config.TAKE_PROFIT_PERCENT / 100)
             if trade_info.get('stop_loss') or config.STOP_LOSS_PERCENT:
                 sl_risk = stake * multiplier * (config.STOP_LOSS_PERCENT / 100)
                
        rr_ratio = f"1:{tp_amount/sl_risk:.1f}" if sl_risk > 0 else "N/A"
        
        message = (
            f"{emoji} <b>TRADE OPENED: {symbol}</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"📍 Direction: <b>{direction}</b>\n"
            f"💵 Stake: {format_currency(stake)} (x{trade_info.get('multiplier', 0)})\n"
            f"📉 Entry: {trade_info.get('entry_price', 0):.2f}\n\n"
            f"🎯 <b>Targets & Risk</b>\n"
            f"   • Target: +{format_currency(tp_amount)}\n"
            f"   • Risk: -{format_currency(sl_risk)}\n"
            f"   • Ratio: {rr_ratio}\n"
        )
        
        # Add cancellation info if active
        if trade_info.get('cancellation_enabled', False):
             message += f"\n🛡️ <b>Cancellation Active</b> ({config.CANCELLATION_DURATION}s)\n"
        
        message += (
            f"\n🔑 ID: <code>{trade_info.get('contract_id', 'N/A')}</code>\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}"
        )
        
        await self.send_message(message)
    
    async def notify_trade_closed(self, result: Dict, trade_info: Dict):
        """Notify that a trade has been closed"""
        status = result.get('status', 'unknown')
        # Safely get profit, default to 0.0 if None
        profit = result.get('profit')
        if profit is None:
            profit = 0.0
        else:
            profit = float(profit)
            
        contract_id = result.get('contract_id') or trade_info.get('contract_id')
        
        # Deduplication check
        if contract_id:
            dedup_key = f"{contract_id}_{status}"
            if dedup_key in self.processed_closed_trades:
                logger.debug(f"🔁 Duplicate notification prevented for {dedup_key}")
                return
            
            # Add to processed set (limit size to 100)
            self.processed_closed_trades.add(dedup_key)
            if len(self.processed_closed_trades) > 100:
                self.processed_closed_trades.pop()
        
        symbol = trade_info.get('symbol', config.SYMBOL)
        
        # Safely get stake, default to 1.0 (to avoid division by zero) if None or 0
        stake = trade_info.get('stake')
        if stake is None:
            stake = 1.0
        else:
            stake = float(stake)
            if stake == 0:
                stake = 1.0
        
        # Determine emoji and outcome
        if profit > 0:
            emoji = "✅"
            header = "TRADE WON"
        elif profit < 0:
            emoji = "❌"
            header = "TRADE LOST"
        else:
            emoji = "⚪"
            header = "TRADE CLOSED"
            
        roi = (profit / stake) * 100
        
        # Duration calculation
        # assuming we don't have exact duration easily, we can skip or add if timestamp available
        # For now, just show result
        
        if result.get('exit_reason') == 'secure_profit_trailing_stop':
            status = 'TRAILING STOP 🎯'
        
        message = (
            f"{emoji} <b>{header}: {symbol}</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 <b>Net Result: {format_currency(profit)}</b>\n"
            f"📈 ROI: {roi:+.1f}%\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"📍 Direction: {trade_info.get('direction', 'UNKNOWN')}\n"
            f"📉 Exit Price: {result.get('current_price', 0):.2f}\n" 
            f"⏱️ Reason: {status.upper()}\n\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}"
        )
        await self.send_message(message)
    
    async def notify_daily_summary(self, stats: Dict):
        """Send daily trading summary"""
        win_rate = stats.get('win_rate', 0)
        total_pnl = stats.get('total_pnl', 0)
        
        # Performance Badge
        if win_rate >= 80 and stats.get('total_trades', 0) > 3:
            badge = "🔥 CRUSHING IT"
        elif total_pnl > 0:
             badge = "✅ PROFITABLE"
        else:
             badge = "📉 RECOVERY NEEDED"
        
        message = (
            f"📅 <b>DAILY REPORT: {datetime.now().strftime('%Y-%m-%d')}</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"💵 <b>Total P&L: {format_currency(total_pnl)}</b>\n"
            f"📊 Status: {badge}\n\n"
            f"📈 <b>Statistics</b>\n"
            f"   • Trades: {stats.get('total_trades', 0)}\n"
            f"   • Win Rate: {win_rate:.1f}%\n"
            f"   • Wins: {stats.get('winning_trades', 0)}\n"
            f"   • Losses: {stats.get('losing_trades', 0)}\n\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}"
        )
        
        await self.send_message(message)
    
    async def notify_error(self, error_msg: str):
        """Notify about errors"""
        message = (
            f"⚠️ <b>SYSTEM ALERT</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"❌ <b>Error Detected</b>\n{error_msg}\n\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}"
        )
        await self.send_message(message)
    
    async def notify_connection_lost(self):
        """Notify that connection was lost"""
        message = (
            "🔌 <b>CONNECTION LOST</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "⚠️ The bot has lost connection to the server.\n"
            "🔄 Reconnecting...\n\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}"
        )
        await self.send_message(message)
    
    async def notify_connection_restored(self):
        """Notify that connection was restored"""
        message = (
            "⚡ <b>ONLINE</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "✅ Connection has been restored.\n"
            "🤖 Resuming trading operations.\n\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}"
        )
        await self.send_message(message)
    
    async def notify_bot_stopped(self, stats: Dict):
        """Notify that bot has stopped"""
        total_pnl = stats.get('total_pnl', 0)
        
        message = (
            f"🛑 <b>BOT STOPPED</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"💵 Final P&L: <b>{format_currency(total_pnl)}</b>\n"
            f"📊 Total Trades: {stats.get('total_trades', 0)}\n"
            f"🎯 Win Rate: {stats.get('win_rate', 0):.1f}%\n\n"
            f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        
        await self.send_message(message)

    async def notify_approval_request(self, user_info: Dict):
        """Notify admin about a new user approval request"""
        email = user_info.get('email', 'Unknown')
        user_id = user_info.get('id', 'Unknown')
        
        message = (
            "👤 <b>NEW USER REQUEST</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"📧 Email: <code>{email}</code>\n"
            f"🆔 ID: <code>{user_id}</code>\n\n"
            "⚠️ <b>Action Required</b>\n"
            "This user has requested access to the dashboard.\n"
            "Please review and approve via Supabase or Admin API.\n\n"
            f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        
        await self.send_message(message)

# Create global instance
notifier = TelegramNotifier()
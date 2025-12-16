"""
Telegram Notifier for Deriv R_25 Trading Bot
Sends trade notifications via Telegram
"""

import os
import asyncio
from typing import Dict, Optional
from datetime import datetime
from telegram import Bot
from telegram.error import TelegramError
import config
from utils import setup_logger, format_currency

logger = setup_logger()

class TelegramNotifier:
    """Handles Telegram notifications for trading events"""
    
    def __init__(self):
        """Initialize Telegram notifier"""
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.bot = None
        self.enabled = False
        
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
    
    async def notify_bot_started(self, balance: float):
        """Notify that bot has started"""
        message = (
            "🤖 <b>Trading Bot Started</b>\n\n"
            f"💰 Balance: {format_currency(balance)}\n"
            f"📊 Symbol: {config.SYMBOL}\n"
            f"📈 Multiplier: {config.MULTIPLIER}x\n"
            f"💵 Stake: {format_currency(config.FIXED_STAKE)}\n"
            f"🎯 Take Profit: {format_currency(config.FIXED_TP)}\n"
            f"🛑 Stop Loss: {format_currency(config.MAX_LOSS_PER_TRADE)}\n"
            f"🔢 Max Daily Trades: {config.MAX_TRADES_PER_DAY}\n\n"
            f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        await self.send_message(message)
    
    async def notify_signal(self, signal: Dict):
        """Notify about trading signal"""
        direction = signal['signal']
        score = signal.get('score', 0)
        details = signal.get('details', {})
        
        if direction == 'HOLD':
            return  # Don't notify for HOLD signals
        
        emoji = "🟢" if direction == "BUY" else "🔴"
        
        message = (
            f"{emoji} <b>{direction} SIGNAL DETECTED</b>\n\n"
            f"📊 Score: {score}/{config.MINIMUM_SIGNAL_SCORE}\n"
            f"📈 RSI: {details.get('rsi', 0):.2f}\n"
            f"💪 ADX: {details.get('adx', 0):.2f}\n"
            f"📉 ATR 1m: {details.get('atr_1m', 0):.4f}\n"
            f"📉 ATR 5m: {details.get('atr_5m', 0):.4f}\n\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}"
        )
        await self.send_message(message)
    
    async def notify_trade_opened(self, trade_info: Dict):
        """Notify that a trade has been opened"""
        direction = trade_info.get('direction', 'UNKNOWN')
        emoji = "🟢" if direction == "BUY" else "🔴"
        
        message = (
            f"{emoji} <b>TRADE OPENED</b>\n\n"
            f"📍 Direction: <b>{direction}</b>\n"
            f"💰 Stake: {format_currency(trade_info.get('stake', 0))}\n"
            f"📈 Entry Price: {trade_info.get('entry_price', 0):.2f}\n"
            f"🎯 Take Profit: {format_currency(trade_info.get('take_profit', 0))}\n"
            f"🛑 Stop Loss: {format_currency(trade_info.get('stop_loss', 0))}\n"
            f"📊 Multiplier: {trade_info.get('multiplier', 0)}x\n"
            f"🔑 Contract ID: <code>{trade_info.get('contract_id', 'N/A')}</code>\n\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}"
        )
        await self.send_message(message)
    
    async def notify_trade_closed(self, result: Dict, trade_info: Dict):
        """Notify that a trade has been closed"""
        status = result.get('status', 'unknown')
        profit = result.get('profit', 0)
        
        # Determine emoji based on outcome
        if profit > 0:
            emoji = "✅"
            outcome = "WON"
        elif profit < 0:
            emoji = "❌"
            outcome = "LOST"
        else:
            emoji = "⚪"
            outcome = "CLOSED"
        
        direction = trade_info.get('direction', 'UNKNOWN')
        entry_price = trade_info.get('entry_price', 0)
        current_price = result.get('current_price', 0)
        price_change = current_price - entry_price
        price_change_pct = (price_change / entry_price * 100) if entry_price > 0 else 0
        
        message = (
            f"{emoji} <b>TRADE {outcome}</b>\n\n"
            f"📍 Direction: <b>{direction}</b>\n"
            f"💰 P&L: <b>{format_currency(profit)}</b>\n"
            f"📈 Entry: {entry_price:.2f}\n"
            f"📉 Exit: {current_price:.2f}\n"
            f"📊 Change: {price_change:+.2f} ({price_change_pct:+.2f}%)\n"
            f"⏱️ Status: {status.upper()}\n"
            f"🔑 Contract: <code>{result.get('contract_id', 'N/A')}</code>\n\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}"
        )
        await self.send_message(message)
    
    async def notify_daily_summary(self, stats: Dict):
        """Send daily trading summary"""
        win_rate = stats.get('win_rate', 0)
        total_pnl = stats.get('total_pnl', 0)
        
        emoji = "📊"
        if total_pnl > 0:
            emoji = "💰"
        elif total_pnl < 0:
            emoji = "📉"
        
        message = (
            f"{emoji} <b>DAILY SUMMARY</b>\n\n"
            f"📈 Total Trades: {stats.get('total_trades', 0)}\n"
            f"✅ Wins: {stats.get('winning_trades', 0)}\n"
            f"❌ Losses: {stats.get('losing_trades', 0)}\n"
            f"🎯 Win Rate: {win_rate:.1f}%\n"
            f"💰 Total P&L: <b>{format_currency(total_pnl)}</b>\n"
            f"📊 Today's Trades: {stats.get('trades_today', 0)}/{config.MAX_TRADES_PER_DAY}\n"
            f"💵 Daily P&L: {format_currency(stats.get('daily_pnl', 0))}\n\n"
            f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        await self.send_message(message)
    
    async def notify_error(self, error_msg: str):
        """Notify about errors"""
        message = (
            f"⚠️ <b>ERROR ALERT</b>\n\n"
            f"❌ {error_msg}\n\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}"
        )
        await self.send_message(message)
    
    async def notify_connection_lost(self):
        """Notify that connection was lost"""
        message = (
            "⚠️ <b>CONNECTION LOST</b>\n\n"
            "Attempting to reconnect...\n\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}"
        )
        await self.send_message(message)
    
    async def notify_connection_restored(self):
        """Notify that connection was restored"""
        message = (
            "✅ <b>CONNECTION RESTORED</b>\n\n"
            "Bot is back online!\n\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}"
        )
        await self.send_message(message)
    
    async def notify_bot_stopped(self, stats: Dict):
        """Notify that bot has stopped"""
        total_pnl = stats.get('total_pnl', 0)
        emoji = "💰" if total_pnl > 0 else "📉" if total_pnl < 0 else "📊"
        
        message = (
            f"🛑 <b>Trading Bot Stopped</b>\n\n"
            f"{emoji} Final P&L: <b>{format_currency(total_pnl)}</b>\n"
            f"📈 Total Trades: {stats.get('total_trades', 0)}\n"
            f"✅ Wins: {stats.get('winning_trades', 0)}\n"
            f"❌ Losses: {stats.get('losing_trades', 0)}\n"
            f"🎯 Win Rate: {stats.get('win_rate', 0):.1f}%\n\n"
            f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        await self.send_message(message)

# Create global instance
notifier = TelegramNotifier()
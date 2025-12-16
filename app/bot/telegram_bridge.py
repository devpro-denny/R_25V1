"""
Telegram Bridge - Simplified version without event registration
Just import and use directly in runner.py
"""

import logging
from typing import Dict

# Import the global telegram notifier from your existing module
try:
    from telegram_notifier import notifier
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    notifier = None

logger = logging.getLogger(__name__)

class TelegramBridge:
    """
    Simple Telegram bridge - call methods directly from runner.py
    No event registration needed
    """
    
    def __init__(self):
        self.active = TELEGRAM_AVAILABLE and notifier and notifier.enabled
        if self.active:
            logger.info("âœ… Telegram bridge active")
        else:
            logger.warning("âš ï¸ Telegram bridge inactive")
    
    async def notify_bot_started(self, balance: float):
        """Notify bot started"""
        if not self.active:
            return
        try:
            await notifier.notify_bot_started(balance)
        except Exception as e:
            logger.error(f"Telegram error (bot_started): {e}")
    
    async def notify_bot_stopped(self, stats: Dict):
        """Notify bot stopped"""
        if not self.active:
            return
        try:
            await notifier.notify_bot_stopped(stats)
        except Exception as e:
            logger.error(f"Telegram error (bot_stopped): {e}")
    
    async def notify_signal(self, signal: Dict):
        """Notify trading signal"""
        if not self.active or signal.get('signal') == 'HOLD':
            return
        try:
            await notifier.notify_signal(signal)
        except Exception as e:
            logger.error(f"Telegram error (signal): {e}")
    
    async def notify_trade_opened(self, trade: Dict):
        """Notify trade opened"""
        if not self.active:
            return
        try:
            await notifier.notify_trade_opened(trade)
        except Exception as e:
            logger.error(f"Telegram error (trade_opened): {e}")
    
    async def notify_trade_closed(self, trade: Dict, pnl: float, status: str):
        """Notify trade closed"""
        if not self.active:
            return
        try:
            result = {
                "status": status,
                "profit": pnl,
                "current_price": trade.get("exit_price", 0),
                "contract_id": trade.get("contract_id")
            }
            
            trade_info = {
                "direction": trade.get("direction"),
                "entry_price": trade.get("entry_price"),
                "stake": trade.get("stake"),
                "multiplier": trade.get("multiplier")
            }
            
            await notifier.notify_trade_closed(result, trade_info)
        except Exception as e:
            logger.error(f"Telegram error (trade_closed): {e}")
    
    async def notify_error(self, error_msg: str):
        """Notify error"""
        if not self.active:
            return
        try:
            await notifier.notify_error(error_msg)
        except Exception as e:
            logger.error(f"Telegram error (error): {e}")
    
    async def notify_connection_lost(self):
        """Notify connection lost"""
        if not self.active:
            return
        try:
            await notifier.notify_connection_lost()
        except Exception as e:
            logger.error(f"Telegram error (connection_lost): {e}")
    
    async def notify_connection_restored(self):
        """Notify connection restored"""
        if not self.active:
            return
        try:
            await notifier.notify_connection_restored()
        except Exception as e:
            logger.error(f"Telegram error (connection_restored): {e}")
    
    async def send_daily_summary(self, stats: Dict):
        """Send daily summary"""
        if not self.active:
            return
        try:
            await notifier.notify_daily_summary(stats)
        except Exception as e:
            logger.error(f"Telegram error (daily_summary): {e}")

# Global instance
telegram_bridge = TelegramBridge()

"""
Telegram Bridge - direct call adapter used by runner.py
"""

import logging
from importlib import import_module
from typing import Dict

logger = logging.getLogger(__name__)

# Module-level state kept for test patching compatibility.
notifier = None
TELEGRAM_AVAILABLE = False


def _resolve_notifier(log_errors: bool = True):
    """
    Resolve and cache telegram_notifier.notifier.

    Behavior:
    - If a notifier object is already injected, TELEGRAM_AVAILABLE acts as a gate.
    - If notifier is missing, attempt lazy import to recover from startup races.
    """
    global notifier, TELEGRAM_AVAILABLE

    if notifier is not None:
        if not TELEGRAM_AVAILABLE:
            return None
        return notifier if getattr(notifier, "enabled", False) else None

    try:
        module = import_module("telegram_notifier")
        candidate = getattr(module, "notifier", None)
        if candidate is None:
            TELEGRAM_AVAILABLE = False
            return None

        notifier = candidate
        TELEGRAM_AVAILABLE = True
        return notifier if getattr(notifier, "enabled", False) else None
    except Exception as e:
        TELEGRAM_AVAILABLE = False
        notifier = None
        if log_errors:
            logger.warning(f"Telegram bridge could not bind notifier: {e}")
        return None


class TelegramBridge:
    """
    Simple Telegram bridge - call methods directly from runner.py.
    """

    def __init__(self):
        self.active = False
        self._refresh_active()
        if self.active:
            logger.info("Telegram bridge active")
        else:
            logger.warning("Telegram bridge inactive")

    def _refresh_active(self) -> None:
        bound = _resolve_notifier(log_errors=False)
        self.active = bool(bound)

    def _get_notifier(self):
        self._refresh_active()
        if not self.active:
            return None
        return notifier

    async def notify_bot_started(self, balance: float, stake: float = None, strategy_name: str = None):
        """Notify bot started"""
        bound = self._get_notifier()
        if not bound:
            return
        try:
            await bound.notify_bot_started(balance, stake, strategy_name)
        except Exception as e:
            logger.error(f"Telegram error (bot_started): {e}")

    async def notify_bot_stopped(self, stats: Dict):
        """Notify bot stopped"""
        bound = self._get_notifier()
        if not bound:
            return
        try:
            await bound.notify_bot_stopped(stats)
        except Exception as e:
            logger.error(f"Telegram error (bot_stopped): {e}")

    async def notify_signal(self, signal: Dict):
        """Notify trading signal"""
        if signal.get("signal") == "HOLD":
            return
        bound = self._get_notifier()
        if not bound:
            return
        try:
            await bound.notify_signal(signal)
        except Exception as e:
            logger.error(f"Telegram error (signal): {e}")

    async def notify_trade_opened(self, trade: Dict, strategy_type: str = "Conservative"):
        """Notify trade opened"""
        bound = self._get_notifier()
        if not bound:
            return
        try:
            await bound.notify_trade_opened(trade, strategy_type=strategy_type)
        except Exception as e:
            logger.error(f"Telegram error (trade_opened): {e}")

    async def notify_trade_closed(self, trade: Dict, pnl: float, status: str, strategy_type: str = "Conservative"):
        """Notify trade closed"""
        bound = self._get_notifier()
        if not bound:
            return
        try:
            result = {
                "status": status,
                "profit": pnl,
                "current_price": trade.get("exit_price", 0),
                "contract_id": trade.get("contract_id"),
                "exit_reason": trade.get("exit_reason"),
            }

            trade_info = {
                "direction": trade.get("direction"),
                "entry_price": trade.get("entry_price"),
                "stake": trade.get("stake"),
                "multiplier": trade.get("multiplier"),
                "symbol": trade.get("symbol"),
            }

            await bound.notify_trade_closed(result, trade_info, strategy_type=strategy_type)
        except Exception as e:
            logger.error(f"Telegram error (trade_closed): {e}")

    async def notify_error(self, error_msg: str):
        """Notify error"""
        bound = self._get_notifier()
        if not bound:
            return
        try:
            await bound.notify_error(error_msg)
        except Exception as e:
            logger.error(f"Telegram error (error): {e}")

    async def notify_connection_lost(self):
        """Notify connection lost"""
        bound = self._get_notifier()
        if not bound:
            return
        try:
            await bound.notify_connection_lost()
        except Exception as e:
            logger.error(f"Telegram error (connection_lost): {e}")

    async def notify_connection_restored(self):
        """Notify connection restored"""
        bound = self._get_notifier()
        if not bound:
            return
        try:
            await bound.notify_connection_restored()
        except Exception as e:
            logger.error(f"Telegram error (connection_restored): {e}")

    async def send_daily_summary(self, stats: Dict):
        """Send daily summary"""
        bound = self._get_notifier()
        if not bound:
            return
        try:
            await bound.notify_daily_summary(stats)
        except Exception as e:
            logger.error(f"Telegram error (daily_summary): {e}")


# Global instance
telegram_bridge = TelegramBridge()

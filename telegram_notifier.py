"""
Telegram Notifier for Deriv R_25 Trading Bot
FIXED VERSION - Handles None values and cancellation phases
Sends trade notifications via Telegram
"""

import os
import asyncio
import re
from typing import Dict, Optional
from datetime import datetime
from telegram import Bot
from telegram.error import TelegramError
import logging
import config
from utils import format_currency

logger = logging.getLogger(__name__)

class TelegramLoggingHandler(logging.Handler):
    """
    Logging handler that sends error logs to Telegram with rate limiting
    """
    def __init__(self, notifier_instance):
        super().__init__()
        self.notifier = notifier_instance
        self.setLevel(logging.ERROR)
        
        # Rate limiting: track last send time
        self.last_send_time = 0
        self.min_interval = 5  # Minimum 5 seconds between messages
        
    def emit(self, record):
        try:
            # Prevent infinite loops - don't log Telegram errors via Telegram
            if 'telegram' in record.name.lower() or 'Failed to send Telegram' in record.getMessage():
                return
                
            # Rate limiting
            import time
            current_time = time.time()
            if current_time - self.last_send_time < self.min_interval:
                return  # Skip this message to avoid flooding
            
            msg = self.format(record)
            
            # Best effort: use event loop if available
            try:
                loop = asyncio.get_running_loop()
                if loop and loop.is_running():
                    # Don't wait for completion, fire and forget
                    loop.create_task(self._send_safe(msg))
                    self.last_send_time = current_time
            except RuntimeError:
                # No running loop, skip to avoid blocking
                pass
                
        except Exception:
            self.handleError(record)
    
    async def _send_safe(self, msg: str):
        """Safely send message without retriggering errors"""
        try:
            await self.notifier.notify_error(f"LOG: {msg}")
        except Exception:
            # Silently ignore - we don't want to create a loop
            pass



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
                logger.info("âœ… Telegram notifications enabled")
            except Exception as e:
                logger.warning(f"âš ï¸ Failed to initialize Telegram bot: {e}")
                self.enabled = False
        else:
            logger.info("â„¹ï¸ Telegram notifications disabled (no credentials)")
    
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
        normalized_score = max(0, min(score, max_score))
        filled = int((normalized_score / max_score) * 5) # 5 bars total
        empty = 5 - filled
        return "▮" * filled + "▯" * empty

    @staticmethod
    def _repair_mojibake_text(text: str) -> str:
        """
        Repair common UTF-8 -> cp1252/latin1 mojibake sequences in message text.
        Handles mixed strings (valid emoji + corrupted tokens) by repairing
        either the whole message or individual tokens.
        """
        if not isinstance(text, str) or not text:
            return text

        markers = ("Ã", "Â", "â", "ð", "ï", "Å")

        def marker_count(value: str) -> int:
            return sum(value.count(m) for m in markers)

        def repair_chunk(chunk: str) -> str:
            repaired = chunk
            for _ in range(3):
                base_score = marker_count(repaired)
                if base_score == 0:
                    break

                best_value = repaired
                best_score = base_score
                for enc in ("cp1252", "latin1"):
                    try:
                        candidate = repaired.encode(enc).decode("utf-8")
                    except UnicodeError:
                        continue

                    candidate_score = marker_count(candidate)
                    if candidate != repaired and candidate_score < best_score:
                        best_value = candidate
                        best_score = candidate_score

                if best_value == repaired:
                    break
                repaired = best_value
            return repaired

        repaired_full = repair_chunk(text)
        if repaired_full != text:
            return repaired_full

        # Whole-string repair can fail when a message mixes valid emoji with
        # corrupted tokens. Repair non-whitespace chunks independently.
        parts = re.split(r"(\s+)", text)
        repaired_parts = []
        changed = False
        for part in parts:
            if not part or part.isspace():
                repaired_parts.append(part)
                continue
            repaired_part = repair_chunk(part)
            if repaired_part != part:
                changed = True
            repaired_parts.append(repaired_part)

        return "".join(repaired_parts) if changed else text

    @staticmethod
    def _to_float(value, default: float = 0.0) -> float:
        """Best-effort float coercion."""
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _normalize_strategy_name(
        self,
        strategy_value: Optional[str] = None,
        payload: Optional[Dict] = None,
    ) -> str:
        """
        Normalize strategy labels to one of:
        - Conservative
        - Scalping
        - RiseFall
        """
        candidates = []
        if strategy_value:
            candidates.append(strategy_value)
        if isinstance(payload, dict):
            for key in ("strategy_type", "strategy", "risk_mode", "active_strategy"):
                value = payload.get(key)
                if value:
                    candidates.append(value)

        for raw in candidates:
            normalized = str(raw).strip().lower().replace("_", "").replace("/", "")
            if normalized in {"scalping", "scalp"}:
                return "Scalping"
            if normalized in {"risefall", "rf"}:
                return "RiseFall"
            if normalized in {"conservative", "topdown", "top-down"}:
                return "Conservative"

        return "Conservative"

    @staticmethod
    def _extract_user_id(*payloads: Optional[Dict]) -> str:
        """Extract user id/account id from one or more payload dictionaries."""
        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            for key in ("user_id", "account_id", "user"):
                value = payload.get(key)
                if value:
                    return str(value)
        return "N/A"

    def _extract_execution_reason(self, payload: Optional[Dict], default_reason: str) -> str:
        """Extract a human-readable execution reason from payload fields."""
        if isinstance(payload, dict):
            for key in ("execution_reason", "reason", "trade_reason", "entry_reason"):
                value = payload.get(key)
                if value:
                    return str(value)

            details = payload.get("details")
            if isinstance(details, dict):
                reason = details.get("reason")
                if reason:
                    return str(reason)

                passed_checks = details.get("passed_checks")
                if isinstance(passed_checks, list) and passed_checks:
                    checks = ", ".join(str(item) for item in passed_checks)
                    return f"Checks passed: {checks}"

        return default_reason

    def _format_risk_summary(self, payload: Optional[Dict], strategy_type: str) -> str:
        """Build concise risk summary for signal/open/close notifications."""
        if not isinstance(payload, dict):
            return "N/A"

        stake = self._to_float(payload.get("stake"), 0.0)
        if strategy_type == "RiseFall":
            duration = payload.get("duration")
            duration_unit = payload.get("duration_unit")
            duration_text = ""
            if duration is not None:
                duration_text = f", duration={duration}{duration_unit or ''}"
            if stake > 0:
                return f"Max loss {format_currency(stake)}{duration_text}"
            return "Max loss equals stake per contract"

        multiplier = self._to_float(payload.get("multiplier"), 0.0)
        entry = self._to_float(payload.get("entry_spot", payload.get("entry_price")), 0.0)
        sl = self._to_float(payload.get("stop_loss"), 0.0)
        rr = payload.get("risk_reward_ratio")
        min_rr = payload.get("min_rr_required")

        if stake > 0 and multiplier > 0 and entry > 0 and sl > 0:
            sl_risk = stake * multiplier * (abs(entry - sl) / entry)
            summary = f"Projected SL risk {format_currency(sl_risk)}"
            if rr is not None:
                summary += f", R:R {self._to_float(rr):.2f}"
            if min_rr is not None:
                summary += f", min R:R {self._to_float(min_rr):.2f}"
            return summary

        if stake > 0 and multiplier > 0:
            mult_display = int(multiplier) if multiplier.is_integer() else multiplier
            summary = f"Stake {format_currency(stake)} @ x{mult_display}"
            if rr is not None:
                summary += f", R:R {self._to_float(rr):.2f}"
            if min_rr is not None:
                summary += f", min R:R {self._to_float(min_rr):.2f}"
            return summary

        if stake > 0:
            return f"Stake {format_currency(stake)}"

        return "Configured by strategy risk rules"

    async def send_message(self, message: str, parse_mode: str = "HTML", retries: int = 3) -> bool:
        """
        Send a message via Telegram with timeout and retry logic
        
        Args:
            message: Message text
            parse_mode: Parse mode (HTML or Markdown)
            retries: Number of retry attempts (default: 3)
        
        Returns:
            True if sent successfully
        """
        if not self.enabled:
            return False

        # Normalize corrupted emoji/glyph sequences before sending to Telegram.
        message = self._repair_mojibake_text(message)
        
        for attempt in range(retries):
            try:
                await asyncio.wait_for(
                    self.bot.send_message(
                        chat_id=self.chat_id,
                        text=message,
                        parse_mode=parse_mode
                    ),
                    timeout=10.0
                )
                return True
                
            except asyncio.TimeoutError:
                if attempt < retries - 1:
                    wait_time = 2 ** attempt
                    logger.warning(f"âš ï¸ Telegram timeout (attempt {attempt + 1}/{retries}), retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"âŒ Failed to send Telegram message: Timed out after {retries} attempts")
                    return False
                    
            except TelegramError as e:
                if attempt < retries - 1 and "timeout" in str(e).lower():
                    wait_time = 2 ** attempt
                    logger.warning(f"âš ï¸ Telegram error (attempt {attempt + 1}/{retries}): {e}, retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"âŒ Failed to send Telegram message: {e}")
                    return False
                    
            except Exception as e:
                logger.error(f"âŒ Telegram error: {e}")
                return False
        
        return False
    
    async def notify_bot_started(
        self,
        balance: float,
        stake: float = None,
        strategy_name: str = None,
        symbol_count: int = None,
        risk_text: str = None,
    ):
        """Notify that bot has started."""
        if strategy_name:
            strategy_mode = f"📊 {strategy_name}"
        else:
            strategy_mode = (
                "🛡️ Top-Down Structure"
                if getattr(config, "USE_TOPDOWN_STRATEGY", False)
                else "⚡ Classic Scalping"
            )

        if symbol_count is None:
            symbol_count = len(getattr(config, "SYMBOLS", []))

        if risk_text is None:
            use_topdown = getattr(config, "USE_TOPDOWN_STRATEGY", False)
            enable_cancellation = getattr(config, "ENABLE_CANCELLATION", False)

            if enable_cancellation and not use_topdown:
                risk_text = (
                    f"🛡️ <b>Cancellation Protection</b>\n"
                    f"   • Duration: {getattr(config, 'CANCELLATION_DURATION', 'N/A')}s\n"
                    f"   • Fee: {format_currency(getattr(config, 'CANCELLATION_FEE', 0))}"
                )
            elif use_topdown:
                risk_text = (
                    "🛡️ <b>Risk Management</b>\n"
                    "   • TP/SL: Dynamic (Structure)\n"
                    f"   • Min R:R: 1:{getattr(config, 'TOPDOWN_MIN_RR_RATIO', 'N/A')}"
                )
            else:
                # TAKE_PROFIT_PERCENT / STOP_LOSS_PERCENT may not exist for all strategies.
                tp_pct = getattr(config, "TAKE_PROFIT_PERCENT", None)
                sl_pct = getattr(config, "STOP_LOSS_PERCENT", None)
                if tp_pct is not None and sl_pct is not None:
                    risk_text = (
                        "🛡️ <b>Risk Management</b>\n"
                        f"   • TP: {tp_pct}%\n"
                        f"   • SL: {sl_pct}%"
                    )
                else:
                    risk_text = "🛡️ <b>Risk Management</b>\n   • TP/SL: Configured per strategy"

        message = (
            "🚀 <b>BOT STARTED</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 Account: <b>{config.DERIV_APP_ID}</b>\n"
            f"💰 Balance: <b>{format_currency(balance)}</b>\n\n"
            "⚙️ <b>Configuration</b>\n"
            f"   • Strategy: {strategy_mode}\n"
            f"   • Symbols: {symbol_count} Active\n"
            f"   • Stake: {format_currency(stake) if stake else 'USER_DEFINED'}\n\n"
            f"{risk_text}\n\n"
            f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        await self.send_message(message)

    async def notify_signal(self, signal: Dict):
        """Notify about trading signal (enriched context)."""
        direction = str(signal.get("signal", "UNKNOWN")).upper()
        score = self._to_float(signal.get("score", 0), 0.0)
        details = signal.get("details", {})
        symbol = signal.get("symbol", "UNKNOWN")

        if direction == "HOLD":
            return

        strategy_type = self._normalize_strategy_name(payload=signal)
        user_id = self._extract_user_id(signal)
        execution_reason = self._extract_execution_reason(
            signal,
            "Signal conditions matched and risk gate passed",
        )
        risk_summary = self._format_risk_summary(signal, strategy_type)

        emoji = "[LONG]" if direction in {"BUY", "UP", "CALL"} else "[SHORT]"
        min_strength = int(getattr(config, "MIN_SIGNAL_STRENGTH", 6) or 6)
        strength_bar = self._create_strength_bar(score, min_strength + 4)

        rsi = self._to_float(details.get("rsi", signal.get("rsi", 0)), 0.0)
        adx = self._to_float(
            details.get("adx", signal.get("adx", signal.get("stoch", 0))),
            0.0,
        )

        message = (
            f"{emoji} <b>SIGNAL DETECTED: {symbol}</b>\n"
            "--------------------\n"
            f"Strategy: <b>{strategy_type}</b>\n"
            f"User ID: <code>{user_id}</code>\n"
            f"Direction: <b>{direction}</b>\n"
            f"Strength: {strength_bar} ({score:.1f})\n\n"
            f"Why Executed: {execution_reason}\n"
            f"Risk: {risk_summary}\n\n"
            f"<b>Technical Indicators</b>\n"
            f"   • RSI: {rsi:.1f}\n"
            f"   • ADX: {adx:.1f}\n"
        )

        if "proximity" in details:
            message += f"   • Level Dist: {self._to_float(details['proximity']):.3f}%\n"

        message += f"\nTime: {datetime.now().strftime('%H:%M:%S')}"
        await self.send_message(message)

    async def notify_trade_opened(self, trade_info: Dict, strategy_type: str = "Conservative"):
        """Notify that a trade has been opened"""
        strategy_name = self._normalize_strategy_name(strategy_type, trade_info)
        prefix = "[SCALP] " if strategy_name == "Scalping" else ("[RF] " if strategy_name == "RiseFall" else "")
        direction = str(trade_info.get("direction", "UNKNOWN")).upper()
        emoji = "[LONG]" if direction in ("BUY", "UP", "CALL") else "[SHORT]"
        symbol = trade_info.get("symbol", "UNKNOWN")
        stake = self._to_float(trade_info.get("stake", 0), 0.0)
        user_id = self._extract_user_id(trade_info)
        execution_reason = self._extract_execution_reason(
            trade_info,
            "Signal conditions matched and order sent",
        )
        risk_summary = self._format_risk_summary(trade_info, strategy_name)

        # ------------------------------------------------------------------ #
        #  Rise/Fall contracts: show contract duration, not TP/SL percentages #
        # ------------------------------------------------------------------ #
        if strategy_name == "RiseFall":
            duration = trade_info.get("duration", "N/A")
            duration_unit = trade_info.get("duration_unit", "t")
            payout = self._to_float(trade_info.get("payout", 0), 0.0)

            message = (
                f"{prefix}{emoji} <b>TRADE OPENED: {symbol}</b>\n"
                "--------------------\n"
                f"Strategy: <b>{strategy_name}</b>\n"
                f"User ID: <code>{user_id}</code>\n"
                f"Direction: <b>{direction}</b>\n"
                f"Stake: {format_currency(stake)}\n"
                f"Duration: {duration}{duration_unit}\n"
                f"Max Payout: {format_currency(payout) if payout else 'N/A'}\n"
                f"Why Executed: {execution_reason}\n"
                f"Risk: {risk_summary}\n"
                f"\nID: <code>{trade_info.get('contract_id', 'N/A')}</code>\n"
                f"Time: {datetime.now().strftime('%H:%M:%S')}"
            )
            await self.send_message(message)
            return

        # ------------------------------------------------------------------ #
        #  Multiplier contracts: calculate projected TP/SL amounts            #
        # ------------------------------------------------------------------ #
        tp_amount = 0
        sl_risk = 0

        entry_spot = self._to_float(trade_info.get("entry_spot") or trade_info.get("entry_price", 0), 0.0)
        multiplier = self._to_float(trade_info.get("multiplier", 0), 0.0)

        # 1. Calculate from exact price levels (Dynamic/Top-Down)
        if entry_spot > 0 and trade_info.get("take_profit") and trade_info.get("stop_loss"):
            tp_price = self._to_float(trade_info["take_profit"], 0.0)
            sl_price = self._to_float(trade_info["stop_loss"], 0.0)
            tp_amount = stake * multiplier * (abs(tp_price - entry_spot) / entry_spot)
            sl_risk = stake * multiplier * (abs(entry_spot - sl_price) / entry_spot)

        # 2. Fallback: Use amount estimates if provided (Legacy)
        elif "take_profit_amount" in trade_info:
            tp_amount = self._to_float(trade_info["take_profit_amount"], 0.0)
            if "stop_loss_amount" in trade_info:
                sl_risk = self._to_float(trade_info["stop_loss_amount"], 0.0)

        # 3. Fallback: Estimate from config percentages if they exist
        # FIX: Use getattr — TAKE_PROFIT_PERCENT / STOP_LOSS_PERCENT may not be defined
        else:
            tp_pct = getattr(config, "TAKE_PROFIT_PERCENT", None)
            sl_pct = getattr(config, "STOP_LOSS_PERCENT", None)
            if tp_pct is not None:
                tp_amount = stake * multiplier * (tp_pct / 100)
            if sl_pct is not None:
                sl_risk = stake * multiplier * (sl_pct / 100)

        rr_ratio = f"1:{tp_amount / sl_risk:.1f}" if sl_risk > 0 else "N/A"
        mult_display = int(multiplier) if multiplier.is_integer() else multiplier

        message = (
            f"{prefix}{emoji} <b>TRADE OPENED: {symbol}</b>\n"
            "--------------------\n"
            f"Strategy: <b>{strategy_name}</b>\n"
            f"User ID: <code>{user_id}</code>\n"
            f"Direction: <b>{direction}</b>\n"
            f"Stake: {format_currency(stake)} (x{mult_display if multiplier else 0})\n"
            f"Entry: {self._to_float(trade_info.get('entry_price', 0), 0.0):.2f}\n"
            f"Why Executed: {execution_reason}\n\n"
            f"<b>Targets & Risk</b>\n"
            f"   • Target: +{format_currency(tp_amount)}\n"
            f"   • Risk: -{format_currency(sl_risk)}\n"
            f"   • Ratio: {rr_ratio}\n"
            f"   • Summary: {risk_summary}\n"
        )

        if trade_info.get("cancellation_enabled", False):
            cancel_duration = getattr(config, "CANCELLATION_DURATION", "N/A")
            message += f"\n<b>Cancellation Active</b> ({cancel_duration}s)\n"

        message += (
            f"\nID: <code>{trade_info.get('contract_id', 'N/A')}</code>\n"
            f"Time: {datetime.now().strftime('%H:%M:%S')}"
        )

        await self.send_message(message)

    async def notify_trade_closed(self, result: Dict, trade_info: Dict, strategy_type: str = "Conservative"):
        """Notify that a trade has been closed"""
        strategy_name = self._normalize_strategy_name(strategy_type, trade_info or result)
        prefix = "[SCALP] " if strategy_name == "Scalping" else ("[RF] " if strategy_name == "RiseFall" else "")
        status = result.get("status", "unknown")
        profit = result.get("profit")
        if profit is None:
            profit = 0.0
        else:
            profit = float(profit)

        contract_id = result.get("contract_id") or trade_info.get("contract_id")

        # Deduplication check
        if contract_id:
            dedup_key = f"{contract_id}_{status}"
            if dedup_key in self.processed_closed_trades:
                logger.debug(f"Duplicate notification prevented for {dedup_key}")
                return

            self.processed_closed_trades.add(dedup_key)
            if len(self.processed_closed_trades) > 100:
                self.processed_closed_trades.pop()

        symbol = trade_info.get("symbol", "UNKNOWN")
        user_id = self._extract_user_id(trade_info, result)
        execution_reason = self._extract_execution_reason(
            trade_info,
            "Signal conditions matched and risk gate approved",
        )
        risk_summary = self._format_risk_summary(trade_info, strategy_name)

        stake = trade_info.get("stake")
        if stake is None:
            stake = 1.0
        else:
            stake = float(stake)
            if stake == 0:
                stake = 1.0

        if profit > 0:
            emoji = "[WIN]"
            outcome = "WON"
        elif profit < 0:
            emoji = "[LOSS]"
            outcome = "LOST"
        else:
            emoji = "[FLAT]"
            outcome = "BREAK EVEN"

        roi = (profit / stake) * 100

        close_reason = result.get("exit_reason") or trade_info.get("closure_reason") or status
        if result.get("exit_reason") == "secure_profit_trailing_stop":
            close_reason = "secure_profit_trailing_stop"
        elif result.get("exit_reason") == "stagnation_exit":
            close_reason = "stagnation_exit"

        message = (
            f"{prefix}{emoji} <b>TRADE CLOSED ({outcome}): {symbol}</b>\n"
            "--------------------\n"
            f"Strategy: <b>{strategy_name}</b>\n"
            f"User ID: <code>{user_id}</code>\n"
            f"<b>Net Result: {format_currency(profit)}</b>\n"
            f"ROI: {roi:+.1f}%\n"
            "--------------------\n"
            f"Direction: {trade_info.get('direction', 'UNKNOWN')}\n"
            f"Exit Price: {self._to_float(result.get('current_price', 0), 0.0):.2f}\n"
            f"Why Executed: {execution_reason}\n"
            f"Close Reason: {str(close_reason).upper()}\n"
            f"Risk: {risk_summary}\n"
            f"Duration: {trade_info.get('duration', result.get('duration', 'N/A'))}s\n\n"
            f"Time: {datetime.now().strftime('%H:%M:%S')}"
        )
        await self.send_message(message)

    async def notify_daily_summary(self, stats: Dict):
        """Send daily trading summary"""
        win_rate = stats.get("win_rate", 0)
        total_pnl = stats.get("total_pnl", 0)

        if win_rate >= 80 and stats.get("total_trades", 0) > 3:
            badge = "🔥 CRUSHING IT"
        elif total_pnl > 0:
            badge = "✅ PROFITABLE"
        else:
            badge = "📉 RECOVERY NEEDED"

        message = (
            f"🗓️ <b>DAILY REPORT: {datetime.now().strftime('%Y-%m-%d')}</b>\n"
            "--------------------\n"
            f"💵 <b>Total P&L: {format_currency(total_pnl)}</b>\n"
            f"📊 Status: {badge}\n\n"
            "📈 <b>Statistics</b>\n"
            f"   - Trades: {stats.get('total_trades', 0)}\n"
            f"   - Win Rate: {win_rate:.1f}%\n"
            f"   - Wins: {stats.get('winning_trades', 0)}\n"
            f"   - Losses: {stats.get('losing_trades', 0)}\n\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}"
        )

        await self.send_message(message)
    
    async def notify_error(self, error_msg: str):
        """Notify about errors"""
        message = (
            "⚠️ <b>SYSTEM ALERT</b>\n"
            "--------------------\n"
            f"❌ <b>Error Detected</b>\n{error_msg}\n\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}"
        )
        await self.send_message(message)
    
    async def notify_connection_lost(self):
        """Notify that connection was lost"""
        message = (
            "🔌 <b>CONNECTION LOST</b>\n"
            "--------------------\n"
            "⚠️ The bot has lost connection to the server.\n"
            "🔄 Reconnecting...\n\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}"
        )
        await self.send_message(message)
    
    async def notify_connection_restored(self):
        """Notify that connection was restored"""
        message = (
            "⚡ <b>ONLINE</b>\n"
            "--------------------\n"
            "✅ Connection has been restored.\n"
            "🤖 Resuming trading operations.\n\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}"
        )
        await self.send_message(message)
    
    async def notify_bot_stopped(self, stats: Dict):
        """Notify that bot has stopped"""
        total_pnl = stats.get("total_pnl", 0)

        message = (
            "🛑 <b>BOT STOPPED</b>\n"
            "--------------------\n"
            f"💵 Final P&L: <b>{format_currency(total_pnl)}</b>\n"
            f"📊 Total Trades: {stats.get('total_trades', 0)}\n"
            f"🎯 Win Rate: {stats.get('win_rate', 0):.1f}%\n\n"
            f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

        await self.send_message(message)

    async def notify_approval_request(self, user_info: Dict):
        """Notify admin about a new user approval request"""
        email = user_info.get("email", "Unknown")
        user_id = user_info.get("id", "Unknown")

        message = (
            "👤 <b>NEW USER REQUEST</b>\n"
            "--------------------\n"
            f"📧 Email: <code>{email}</code>\n"
            f"🆔 ID: <code>{user_id}</code>\n\n"
            "⚠️ <b>Action Required</b>\n"
            "This user has requested access to the dashboard.\n"
            "Please review and approve via Supabase or Admin API.\n\n"
            f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        await self.send_message(message)
        
    async def notify_trade_open(self, *args, **kwargs):
        """Alias for notify_trade_opened"""
        if len(args) == 1 and not kwargs:
            # Handle single-argument call from tests
            return await self.notify_trade_opened(args[0], args[0])
        return await self.notify_trade_opened(*args, **kwargs)
        
    async def notify_trade_close(self, *args, **kwargs):
        """Alias for notify_trade_closed"""
        if len(args) == 1 and not kwargs:
            # Handle single-argument call from tests
            # Pass it as both result and trade_info
            return await self.notify_trade_closed(args[0], args[0])
        return await self.notify_trade_closed(*args, **kwargs)

# Create global instance
notifier = TelegramNotifier()



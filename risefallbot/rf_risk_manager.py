"""
Rise/Fall risk manager for the Step Index tick-sequence strategy.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
from typing import Dict, Tuple

from base_risk_manager import BaseRiskManager
from risefallbot import rf_config


logger = logging.getLogger("risefallbot.risk")


class RiseFallRiskManager(BaseRiskManager):
    """
    Enforces the existing single-trade lifecycle plus Step Index risk rules.
    """

    def __init__(self, user_id: str = None, overrides: Dict = None):
        self.user_id = user_id
        overrides = overrides or {}

        self.max_concurrent_per_symbol = min(
            overrides.get(
                "max_concurrent_per_symbol",
                rf_config.RF_MAX_CONCURRENT_PER_SYMBOL,
            ),
            rf_config.RF_MAX_CONCURRENT_PER_SYMBOL,
        )
        self.max_concurrent_total = min(
            overrides.get("max_concurrent_total", rf_config.RF_MAX_CONCURRENT_TOTAL),
            rf_config.RF_MAX_CONCURRENT_TOTAL,
        )
        self.cooldown_seconds = max(
            overrides.get("cooldown_seconds", rf_config.RF_COOLDOWN_SECONDS),
            rf_config.RF_COOLDOWN_SECONDS,
        )
        self.global_cooldown_seconds = max(
            overrides.get(
                "global_cooldown_seconds",
                getattr(rf_config, "RF_GLOBAL_COOLDOWN_SECONDS", 0),
            ),
            getattr(rf_config, "RF_GLOBAL_COOLDOWN_SECONDS", 0),
        )
        self.daily_loss_limit_mult = min(
            overrides.get(
                "daily_loss_limit_mult",
                getattr(rf_config, "RF_DAILY_LOSS_LIMIT_MULTIPLIER", 0.0),
            ),
            getattr(rf_config, "RF_DAILY_LOSS_LIMIT_MULTIPLIER", 0.0),
        )
        self.max_trades_per_day = min(
            overrides.get("max_trades_per_day", rf_config.RF_MAX_TRADES_PER_DAY),
            rf_config.RF_MAX_TRADES_PER_DAY,
        )
        self.max_consecutive_losses = min(
            overrides.get(
                "max_consecutive_losses",
                rf_config.RF_MAX_CONSECUTIVE_LOSSES,
            ),
            rf_config.RF_MAX_CONSECUTIVE_LOSSES,
        )
        self.loss_cooldown_seconds = max(
            overrides.get(
                "loss_cooldown_seconds",
                rf_config.RF_LOSS_COOLDOWN_SECONDS,
            ),
            rf_config.RF_LOSS_COOLDOWN_SECONDS,
        )
        self.session_max_losses = min(
            overrides.get(
                "session_max_losses",
                getattr(rf_config, "RF_SESSION_MAX_LOSSES", 4),
            ),
            getattr(rf_config, "RF_SESSION_MAX_LOSSES", 4),
        )
        self.session_reset_mode = str(
            overrides.get(
                "session_reset_mode",
                getattr(rf_config, "RF_SESSION_RESET_MODE", "daily"),
            )
        )

        self.active_trades: Dict[str, Dict] = {}
        self.daily_trade_count = 0
        self.daily_pnl = 0.0
        self.wins = 0
        self.losses = 0
        self.consecutive_losses = 0

        self._last_trade_close: Dict[str, datetime] = {}
        self._last_trade_close_global: datetime = datetime.min
        self._loss_cooldown_until: datetime = datetime.min
        self._loss_cooldown_started_at: datetime = datetime.min
        self._cooldown_reset_pending = False
        self._session_halted = False

        self._trade_mutex: asyncio.Lock = asyncio.Lock()
        self._trade_lock_active: bool = False
        self._locked_trade_info: Dict = {}
        self._locked_symbol: str = None

        self._halted: bool = False
        self._halt_reason: str = ""
        self._halt_timestamp: datetime = datetime.min

        self._pending_entry_timestamp: datetime = datetime.min
        self._last_daily_reset_date = datetime.now().date()
        self._pending_timeout_seconds = rf_config.RF_PENDING_TIMEOUT_SECONDS

        logger.info(
            "[RF-Risk] Initialized | max_total=%s max_per_symbol=%s "
            "loss_streak_limit=%s cooldown=%ss session_max_losses=%s",
            self.max_concurrent_total,
            self.max_concurrent_per_symbol,
            self.max_consecutive_losses,
            self.loss_cooldown_seconds,
            self.session_max_losses,
        )

    @property
    def trade_mutex(self) -> asyncio.Lock:
        return self._trade_mutex

    async def acquire_trade_lock(
        self,
        symbol: str,
        contract_id: str,
        stake: float = None,
        wait_for_lock: bool = True,
    ) -> bool:
        if self._trade_mutex.locked() and contract_id == "pending":
            elapsed = 0.0
            if self._pending_entry_timestamp != datetime.min:
                elapsed = (datetime.now() - self._pending_entry_timestamp).total_seconds()
            if elapsed > self._pending_timeout_seconds:
                logger.warning(
                    "[RF-Risk] Watchdog releasing stale pending lock after %.0fs",
                    elapsed,
                )
                if self._trade_mutex.locked():
                    self._trade_mutex.release()
                self._trade_lock_active = False
                self._locked_symbol = None
                self._locked_trade_info = {}
                self._pending_entry_timestamp = datetime.min
                if self._halted:
                    self._halted = False
                    self._halt_reason = ""
                    logger.info("[RF-Risk] Halt auto-cleared by watchdog")

        if self._halted:
            logger.error("[RF-Risk] Cannot acquire lock while halted: %s", self._halt_reason)
            return False

        logger.info(
            "[RF] STEP 1/6 | %s | ACQUIRING TRADE LOCK for %s#%s",
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            symbol,
            contract_id,
        )

        if not wait_for_lock and self._trade_mutex.locked():
            logger.info("[RF-Risk] Lock busy for %s#%s", symbol, contract_id)
            return False

        await self._trade_mutex.acquire()

        if contract_id == "pending":
            self._pending_entry_timestamp = datetime.now()

        can_trade, reason = self.can_trade(symbol=symbol, stake=stake)
        if not can_trade and reason != "trade_lock_active":
            logger.warning(
                "[RF-Risk] Post-acquire risk check failed for %s#%s: %s",
                symbol,
                contract_id,
                reason,
            )
            self._trade_mutex.release()
            return False

        if self.active_trades:
            existing = list(self.active_trades.values())[0]
            logger.error(
                "[RF-Risk] Active trade already exists: %s#%s",
                existing.get("symbol", "unknown"),
                existing.get("contract_id", "unknown"),
            )
            self._trade_mutex.release()
            return False

        self._trade_lock_active = True
        self._locked_symbol = symbol
        self._locked_trade_info = {"contract_id": contract_id, "symbol": symbol}

        logger.info(
            "[RF] STEP 1/6 | %s | TRADE LOCK ACQUIRED for %s#%s",
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            symbol,
            contract_id,
        )
        return True

    def release_trade_lock(self, reason: str = "lifecycle complete") -> None:
        if self._trade_mutex.locked():
            self._trade_mutex.release()
            self._trade_lock_active = False
            self._locked_symbol = None
            self._locked_trade_info = {}
            self._pending_entry_timestamp = datetime.min
            logger.info(
                "[RF] STEP 6/6 | %s | TRADE LOCK RELEASED | %s",
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                reason,
            )
        else:
            logger.warning("[RF-Risk] release_trade_lock called without mutex held")

    def halt(self, reason: str) -> None:
        self._halted = True
        self._halt_reason = reason
        self._halt_timestamp = datetime.now()
        logger.critical("[RF-Risk] SYSTEM HALTED: %s", reason)

    def clear_halt(self) -> None:
        self._halted = False
        self._halt_reason = ""
        self._halt_timestamp = datetime.min
        logger.info("[RF-Risk] Halt cleared")

    def note_qualifying_signal(self, symbol: str, signal: Dict) -> None:
        if not self._cooldown_reset_pending:
            return
        if datetime.now() < self._loss_cooldown_until:
            return

        sequence_start_epoch = signal.get("sequence_start_epoch")
        sequence_start = datetime.min
        if sequence_start_epoch is not None:
            try:
                sequence_start = datetime.fromtimestamp(float(sequence_start_epoch))
            except (TypeError, ValueError, OSError):
                sequence_start = datetime.min

        if (
            self._loss_cooldown_started_at != datetime.min
            and sequence_start != datetime.min
            and sequence_start <= self._loss_cooldown_started_at
        ):
            logger.info(
                "[RF-Risk] Post-cooldown signal on %s ignored because sequence is stale",
                symbol,
            )
            return

        self.consecutive_losses = 0
        self._cooldown_reset_pending = False
        logger.info("[RF-Risk] Cooldown reset cleared by fresh signal on %s", symbol)

    def can_trade(
        self,
        symbol: str = None,
        verbose: bool = False,
        stake: float = None,
    ) -> Tuple[bool, str]:
        now = datetime.now()
        ref_stake = stake if stake is not None and stake > 0 else rf_config.RF_DEFAULT_STAKE
        allowed_symbols = set(
            getattr(
                rf_config,
                "RF_SUPPORTED_SYMBOLS",
                getattr(rf_config, "RF_SYMBOLS", []),
            )
        )
        blocked_symbols = set(getattr(rf_config, "RF_BLOCKED_SYMBOLS", set()))

        if self._halted:
            return False, "system_halted"

        if symbol and (symbol in blocked_symbols or (allowed_symbols and symbol not in allowed_symbols)):
            return False, "symbol_not_allowed"

        if self._session_halted:
            return False, "session_loss_limit_reached"

        if self._trade_mutex.locked():
            return False, "trade_lock_active"

        if self.max_trades_per_day > 0 and self.daily_trade_count >= self.max_trades_per_day:
            return False, "daily_trade_limit_reached"

        if self.daily_loss_limit_mult > 0:
            daily_loss_limit = ref_stake * self.daily_loss_limit_mult
            if self.daily_pnl <= -daily_loss_limit:
                return False, "daily_loss_limit_reached"

        if now < self._loss_cooldown_until:
            return False, "loss_streak_cooldown_active"

        if len(self.active_trades) >= self.max_concurrent_total:
            return False, "trade_lock_active"

        if (
            self.global_cooldown_seconds > 0
            and self._last_trade_close_global != datetime.min
        ):
            elapsed = (now - self._last_trade_close_global).total_seconds()
            if elapsed < self.global_cooldown_seconds:
                return False, "global_cooldown_active"

        if symbol and self.max_concurrent_per_symbol > 0:
            active_for_symbol = sum(
                1 for trade in self.active_trades.values() if trade.get("symbol") == symbol
            )
            if active_for_symbol >= self.max_concurrent_per_symbol:
                return False, "trade_lock_active"

        if symbol and self.cooldown_seconds > 0:
            last_close = self._last_trade_close.get(symbol)
            if last_close:
                elapsed = (now - last_close).total_seconds()
                if elapsed < self.cooldown_seconds:
                    return False, "symbol_cooldown_active"

        if verbose:
            logger.info("[RF-Risk] Trading allowed for %s", symbol or "SYSTEM")
        return True, "OK"

    def ensure_daily_reset_if_needed(self) -> None:
        if self.session_reset_mode != "daily":
            return
        today = datetime.now().date()
        if self._last_daily_reset_date != today:
            self.reset_daily_stats()
            self._last_daily_reset_date = today

    def record_trade_open(self, trade_info: Dict) -> None:
        contract_id = trade_info.get("contract_id", "unknown")
        symbol = trade_info.get("symbol", "unknown")

        if not self._trade_mutex.locked():
            logger.critical(
                "[RF-Risk] record_trade_open called without mutex for %s#%s",
                symbol,
                contract_id,
            )
            return

        if self.active_trades:
            existing = list(self.active_trades.values())[0]
            logger.critical(
                "[RF-Risk] Duplicate trade prevented: new=%s#%s existing=%s#%s",
                symbol,
                contract_id,
                existing.get("symbol", "unknown"),
                existing.get("contract_id", "unknown"),
            )
            self.halt(f"Duplicate trade prevention: {symbol}#{contract_id} rejected")
            if self._trade_mutex.locked():
                self._trade_mutex.release()
                self._trade_lock_active = False
                self._locked_symbol = None
                self._locked_trade_info = {}
                self._pending_entry_timestamp = datetime.min
            return

        self.active_trades[contract_id] = {**trade_info, "open_time": datetime.now()}
        self.daily_trade_count += 1
        self._locked_trade_info = {"contract_id": contract_id, "symbol": symbol}
        self._locked_symbol = symbol

        logger.info(
            "[RF] STEP 3/6 | %s | TRADE TRACKED: %s#%s",
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            symbol,
            contract_id,
        )

    def record_trade_closed(self, result: Dict) -> None:
        contract_id = result.get("contract_id", "unknown")
        profit = result.get("profit", 0.0)
        status = result.get("status", "unknown")

        trade = self.active_trades.pop(contract_id, {})
        symbol = trade.get("symbol") or result.get("symbol", "unknown")

        self.daily_pnl += profit

        if status == "win":
            self.wins += 1
            self.consecutive_losses = 0
            self._cooldown_reset_pending = False
        elif status == "loss":
            self.losses += 1
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0
            self._cooldown_reset_pending = False

        self._last_trade_close[symbol] = datetime.now()
        self._last_trade_close_global = datetime.now()
        self._pending_entry_timestamp = datetime.min

        if (
            status == "loss"
            and self.max_consecutive_losses > 0
            and self.consecutive_losses >= self.max_consecutive_losses
        ):
            self._loss_cooldown_started_at = datetime.now()
            self._loss_cooldown_until = self._loss_cooldown_started_at + timedelta(
                seconds=self.loss_cooldown_seconds
            )
            self._cooldown_reset_pending = True
            logger.warning(
                "[RF-Risk] Loss streak cooldown active until %s",
                self._loss_cooldown_until.isoformat(),
            )

        if (
            status == "loss"
            and self.session_max_losses > 0
            and self.losses >= self.session_max_losses
        ):
            self._session_halted = True
            logger.warning(
                "[RF-Risk] Session loss limit reached (%s/%s)",
                self.losses,
                self.session_max_losses,
            )

        logger.info(
            "[RF] STEP 4/6 | %s | TRADE CLOSED: %s#%s status=%s pnl=%+.2f "
            "(W=%s L=%s streak=%s)",
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            symbol,
            contract_id,
            status,
            profit,
            self.wins,
            self.losses,
            self.consecutive_losses,
        )

    def get_active_trade_info(self) -> Dict:
        if self._trade_mutex.locked():
            return self._locked_trade_info.copy()
        return {}

    def is_trade_active(self) -> bool:
        return self._trade_mutex.locked() or bool(self.active_trades)

    def is_halted(self) -> bool:
        return self._halted

    def get_current_limits(self) -> Dict:
        return {
            "max_concurrent_per_symbol": self.max_concurrent_per_symbol,
            "max_concurrent_total": self.max_concurrent_total,
            "current_active_trades": len(self.active_trades),
            "max_trades_per_day": self.max_trades_per_day,
            "daily_trade_count": self.daily_trade_count,
            "daily_pnl": self.daily_pnl,
            "consecutive_losses": self.consecutive_losses,
            "max_consecutive_losses": self.max_consecutive_losses,
            "loss_cooldown_seconds": self.loss_cooldown_seconds,
            "losses": self.losses,
            "wins": self.wins,
            "mutex_locked": self._trade_mutex.locked(),
            "halted": self._halted,
            "session_halted": self._session_halted,
            "session_max_losses": self.session_max_losses,
            "cooldown_reset_pending": self._cooldown_reset_pending,
        }

    def reset_daily_stats(self) -> None:
        logger.info(
            "[RF-Risk] Daily reset | trades=%s pnl=%+.2f losses=%s",
            self.daily_trade_count,
            self.daily_pnl,
            self.losses,
        )
        self.daily_trade_count = 0
        self.daily_pnl = 0.0
        self.wins = 0
        self.losses = 0
        self.consecutive_losses = 0
        self._last_trade_close.clear()
        self._last_trade_close_global = datetime.min
        self._loss_cooldown_until = datetime.min
        self._loss_cooldown_started_at = datetime.min
        self._cooldown_reset_pending = False
        self._session_halted = False

    def get_statistics(self) -> Dict:
        total = self.wins + self.losses
        win_rate = (self.wins / total * 100) if total > 0 else 0.0
        return {
            "strategy": "RiseFall",
            "trades_today": self.daily_trade_count,
            "total_pnl": self.daily_pnl,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": win_rate,
            "consecutive_losses": self.consecutive_losses,
            "active_trades": len(self.active_trades),
            "mutex_locked": self._trade_mutex.locked(),
            "halted": self._halted,
            "session_halted": self._session_halted,
        }

    @property
    def has_active_trade(self) -> bool:
        return bool(self.active_trades)

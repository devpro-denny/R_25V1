"""
Rise/Fall Risk Manager
Per-symbol cooldown, max concurrent trades, daily cap, and consecutive-loss cooldown
rf_risk_manager.py
"""

from base_risk_manager import BaseRiskManager
from typing import Dict, Tuple
from datetime import datetime, timedelta
import logging

from risefallbot import rf_config

# Dedicated logger for Rise/Fall risk management
logger = logging.getLogger("risefallbot.risk")


class RiseFallRiskManager(BaseRiskManager):
    """
    Risk manager for Rise/Fall scalping strategy.
    
    Features:
        - 1 concurrent trade per symbol (4 total across RF_SYMBOLS)
        - 30-second cooldown per symbol after trade closes
        - 80 trades/day cap
        - Pause for 120 s after 5 consecutive losses
    """

    def __init__(self, user_id: str = None, overrides: Dict = None):
        """
        Initialize Rise/Fall risk manager.
        
        Args:
            user_id: Optional user identifier (multi-user support)
            overrides: Optional dict of parameter overrides
        """
        self.user_id = user_id
        overrides = overrides or {}

        # Core limits (allow overrides)
        self.max_concurrent_per_symbol = overrides.get(
            "max_concurrent_per_symbol", rf_config.RF_MAX_CONCURRENT_PER_SYMBOL
        )
        self.max_concurrent_total = overrides.get(
            "max_concurrent_total", rf_config.RF_MAX_CONCURRENT_TOTAL
        )
        self.cooldown_seconds = overrides.get(
            "cooldown_seconds", rf_config.RF_COOLDOWN_SECONDS
        )
        self.max_trades_per_day = overrides.get(
            "max_trades_per_day", rf_config.RF_MAX_TRADES_PER_DAY
        )
        self.max_consecutive_losses = overrides.get(
            "max_consecutive_losses", rf_config.RF_MAX_CONSECUTIVE_LOSSES
        )
        self.loss_cooldown_seconds = overrides.get(
            "loss_cooldown_seconds", rf_config.RF_LOSS_COOLDOWN_SECONDS
        )

        # Internal state
        self.active_trades: Dict[str, Dict] = {}  # contract_id -> trade_info
        self.daily_trade_count = 0
        self.daily_pnl = 0.0
        self.wins = 0
        self.losses = 0
        self.consecutive_losses = 0

        # Per-symbol timestamps of last trade close
        self._last_trade_close: Dict[str, datetime] = {}

        # Loss-streak cooldown timestamp
        self._loss_cooldown_until: datetime = datetime.min
        
        # Trade lock — enforces max 1 concurrent trade globally
        self._trade_lock_active: bool = False
        self._locked_trade_info: Dict = {}  # Info about currently locked trade
        self._locked_symbol: str = None  # Which symbol has the active trade

        logger.info(
            f"[RF-Risk] Initialized | max_concurrent_total={self.max_concurrent_total} "
            f"max_concurrent/symbol={self.max_concurrent_per_symbol} "
            f"cooldown={self.cooldown_seconds}s daily_cap={self.max_trades_per_day} "
            f"max_consec_loss={self.max_consecutive_losses} | "
            f"⚠️ SINGLE TRADE ENFORCEMENT: Only 1 concurrent trade globally"
        )

    # ------------------------------------------------------------------ #
    #  BaseRiskManager interface                                          #
    # ------------------------------------------------------------------ #

    def can_trade(self, symbol: str = None, verbose: bool = False) -> Tuple[bool, str]:
        """
        Check if a new trade is allowed.
        
        Args:
            symbol: Trading symbol to check (per-symbol cooldown + concurrency)
            verbose: If True, log detailed reasons
            
        Returns:
            (allowed, reason)
        """
        now = datetime.now()

        # 1. Daily cap
        if self.daily_trade_count >= self.max_trades_per_day:
            msg = f"Daily trade cap reached ({self.daily_trade_count}/{self.max_trades_per_day})"
            if verbose:
                logger.info(f"[RF-Risk] ❌ {msg}")
            return False, msg

        # 2. Consecutive-loss cooldown
        if now < self._loss_cooldown_until:
            remaining = (self._loss_cooldown_until - now).total_seconds()
            msg = f"Loss-streak cooldown ({remaining:.0f}s remaining)"
            if verbose:
                logger.info(f"[RF-Risk] ⏸️ {msg}")
            return False, msg

        # 3. Total concurrent limit (across all symbols)
        total_active = len(self.active_trades)
        if total_active >= self.max_concurrent_total:
            msg = f"Max total concurrent trades reached ({total_active}/{self.max_concurrent_total})"
            if verbose:
                logger.info(f"[RF-Risk] ⏸️ {msg}")
            return False, msg

        # 4. Per-symbol checks (if symbol provided)
        if symbol:
            # 3a. Concurrent limit per symbol
            active_for_symbol = sum(
                1 for t in self.active_trades.values()
                if t.get("symbol") == symbol
            )
            if active_for_symbol >= self.max_concurrent_per_symbol:
                msg = f"{symbol}: max concurrent trades ({active_for_symbol}/{self.max_concurrent_per_symbol})"
                if verbose:
                    logger.info(f"[RF-Risk] ⏸️ {msg}")
                return False, msg

            # 3b. Per-symbol cooldown
            last_close = self._last_trade_close.get(symbol)
            if last_close:
                elapsed = (now - last_close).total_seconds()
                if elapsed < self.cooldown_seconds:
                    remaining = self.cooldown_seconds - elapsed
                    msg = f"{symbol}: cooldown ({remaining:.0f}s remaining)"
                    if verbose:
                        logger.info(f"[RF-Risk] ⏸️ {msg}")
                    return False, msg

        return True, "OK"

    def record_trade_open(self, trade_info: Dict) -> None:
        """
        Record a new trade opening.
        ENFORCES: Only 1 concurrent trade globally.
        
        Args:
            trade_info: Dict with at least 'contract_id' and 'symbol'
        """
        contract_id = trade_info.get("contract_id", "unknown")
        symbol = trade_info.get("symbol", "unknown")

        # Enforce global trade lock — only 1 trade at a time
        if len(self.active_trades) > 0:
            logger.warning(
                f"[RF-Risk] ⚠️ TRADE LOCK VIOLATION: Attempting to open trade {symbol}#{contract_id} "
                f"but {len(self.active_trades)} trade(s) already active! Rejecting..."
            )
            return

        self.active_trades[contract_id] = {
            **trade_info,
            "open_time": datetime.now(),
        }
        self.daily_trade_count += 1
        self._trade_lock_active = True
        self._locked_symbol = symbol
        self._locked_trade_info = {"contract_id": contract_id, "symbol": symbol}

        logger.info(
            f"[RF-Risk] 🔒 TRADE LOCKED: {symbol} #{contract_id} "
            f"(active={len(self.active_trades)} daily={self.daily_trade_count}) "
            f"— No other trades allowed until this closes"
        )

    def record_trade_closed(self, result: Dict) -> None:
        """
        Record a trade closure.
        RELEASES: Global trade lock once trade is fully settled.
        
        Args:
            result: Dict with 'contract_id', 'profit', 'status'
                    status is 'win', 'loss', or 'breakeven'
        """
        contract_id = result.get("contract_id", "unknown")
        profit = result.get("profit", 0.0)
        status = result.get("status", "unknown")

        # Determine symbol from active trades
        trade = self.active_trades.pop(contract_id, {})
        symbol = trade.get("symbol") or result.get("symbol", "unknown")

        # Update P&L
        self.daily_pnl += profit

        # Win / loss tracking
        if status == "win":
            self.wins += 1
            self.consecutive_losses = 0
        elif status == "loss":
            self.losses += 1
            self.consecutive_losses += 1
        else:
            # breakeven — reset streak
            self.consecutive_losses = 0

        # Per-symbol cooldown starts now
        self._last_trade_close[symbol] = datetime.now()

        # Consecutive-loss cooldown
        if self.consecutive_losses >= self.max_consecutive_losses:
            self._loss_cooldown_until = datetime.now() + timedelta(
                seconds=self.loss_cooldown_seconds
            )
            logger.warning(
                f"[RF-Risk] ⚠️ {self.consecutive_losses} consecutive losses — "
                f"pausing for {self.loss_cooldown_seconds}s"
            )

        # Release global trade lock
        self._trade_lock_active = False
        self._locked_symbol = None
        self._locked_trade_info = {}

        logger.info(
            f"[RF-Risk] 🔓 TRADE UNLOCKED: {symbol} #{contract_id} "
            f"status={status} pnl={profit:+.2f} "
            f"(W={self.wins} L={self.losses} streak={self.consecutive_losses}) "
            f"— System ready for next trade"
        )

    def get_active_trade_info(self) -> Dict:
        """
        Get information about the currently active trade (if any).
        
        Returns:
            Dict with contract_id, symbol if trade is active, else empty dict
        """
        return self._locked_trade_info.copy() if self._trade_lock_active else {}

    def is_trade_active(self) -> bool:
        """Return True if a trade is currently locked (being monitored)."""
        return self._trade_lock_active or len(self.active_trades) > 0

    def get_current_limits(self) -> Dict:
        """Return current risk state snapshot."""
        return {
            "max_concurrent_per_symbol": self.max_concurrent_per_symbol,
            "current_active_trades": len(self.active_trades),
            "max_trades_per_day": self.max_trades_per_day,
            "daily_trade_count": self.daily_trade_count,
            "daily_pnl": self.daily_pnl,
            "consecutive_losses": self.consecutive_losses,
            "max_consecutive_losses": self.max_consecutive_losses,
            "cooldown_seconds": self.cooldown_seconds,
            "loss_cooldown_seconds": self.loss_cooldown_seconds,
            "wins": self.wins,
            "losses": self.losses,
        }

    def reset_daily_stats(self) -> None:
        """Reset daily counters (call at midnight)."""
        logger.info(
            f"[RF-Risk] 🔄 Daily reset | trades={self.daily_trade_count} "
            f"pnl={self.daily_pnl:+.2f}"
        )
        self.daily_trade_count = 0
        self.daily_pnl = 0.0
        self.wins = 0
        self.losses = 0
        self.consecutive_losses = 0
        self._last_trade_close.clear()
        self._loss_cooldown_until = datetime.min

    # ------------------------------------------------------------------ #
    #  Convenience helpers                                                #
    # ------------------------------------------------------------------ #

    def get_statistics(self) -> Dict:
        """Get human-readable statistics dict."""
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
        }

    @property
    def has_active_trade(self) -> bool:
        """True if any trade is currently open."""
        return len(self.active_trades) > 0

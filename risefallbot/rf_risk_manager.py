"""
Rise/Fall Risk Manager
Enforces Trading System Rules: single trade, no 3 consecutive losses

Rules enforced:
  1. Only one open trade at a time — mutex + active_trades check
  2. No new trade until current is fully closed — mutex blocks scan
  3. Contracts expire naturally (no early TP/SL exits)
  4. New trade only after current contract settles — lifecycle blocks until settlement
  5. Block after 2 consecutive losses — RF_MAX_CONSECUTIVE_LOSSES=2 + cooldown

rf_risk_manager.py
"""

import asyncio
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
        - asyncio.Lock mutex — only 1 concurrent trade globally (async-safe)
        - 30-second cooldown per symbol after trade closes
        - 80 trades/day cap
        - Pause for 120 s after 5 consecutive losses
        - Step-transition logging with timestamps
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

        # Core limits — overrides are CLAMPED to config maximums.
        # No override can weaken the risk rules defined in rf_config.
        self.max_concurrent_per_symbol = min(
            overrides.get("max_concurrent_per_symbol", rf_config.RF_MAX_CONCURRENT_PER_SYMBOL),
            rf_config.RF_MAX_CONCURRENT_PER_SYMBOL,
        )
        self.max_concurrent_total = min(
            overrides.get("max_concurrent_total", rf_config.RF_MAX_CONCURRENT_TOTAL),
            rf_config.RF_MAX_CONCURRENT_TOTAL,
        )
        self.cooldown_seconds = max(
            overrides.get("cooldown_seconds", rf_config.RF_COOLDOWN_SECONDS),
            rf_config.RF_COOLDOWN_SECONDS,  # Cannot reduce cooldown below config
        )
        self.global_cooldown_seconds = max(
            overrides.get("global_cooldown_seconds", getattr(rf_config, "RF_GLOBAL_COOLDOWN_SECONDS", 30)),
            getattr(rf_config, "RF_GLOBAL_COOLDOWN_SECONDS", 30),
        )
        self.daily_loss_limit_mult = min(
            overrides.get("daily_loss_limit_mult", getattr(rf_config, "RF_DAILY_LOSS_LIMIT_MULTIPLIER", 3.0)),
            getattr(rf_config, "RF_DAILY_LOSS_LIMIT_MULTIPLIER", 3.0),
        )
        self.max_trades_per_day = min(
            overrides.get("max_trades_per_day", rf_config.RF_MAX_TRADES_PER_DAY),
            rf_config.RF_MAX_TRADES_PER_DAY,
        )
        self.max_consecutive_losses = min(
            overrides.get("max_consecutive_losses", rf_config.RF_MAX_CONSECUTIVE_LOSSES),
            rf_config.RF_MAX_CONSECUTIVE_LOSSES,
        )
        self.loss_cooldown_seconds = max(
            overrides.get("loss_cooldown_seconds", rf_config.RF_LOSS_COOLDOWN_SECONDS),
            rf_config.RF_LOSS_COOLDOWN_SECONDS,  # Cannot reduce cooldown below config
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
        # Global last trade close (for RF_GLOBAL_COOLDOWN_SECONDS)
        self._last_trade_close_global: datetime = datetime.min

        # Loss-streak cooldown timestamp
        self._loss_cooldown_until: datetime = datetime.min
        
        # ═══════════════════════════════════════════════════════════════
        # PRIMARY CONCURRENCY GATE: asyncio.Lock (mutex)
        # This is the authority — not the boolean flag.
        # The lock is acquired before trade execution and released only
        # after confirmed DB write.
        # ═══════════════════════════════════════════════════════════════
        self._trade_mutex: asyncio.Lock = asyncio.Lock()

        # Secondary indicator (human-readable state, NOT the authority)
        self._trade_lock_active: bool = False
        self._locked_trade_info: Dict = {}  # Info about currently locked trade
        self._locked_symbol: str = None  # Which symbol has the active trade

        # Error halt flag — set when a critical step fails
        self._halted: bool = False
        self._halt_reason: str = ""
        self._halt_timestamp: datetime = datetime.min

        # Watchdog state for pending entries
        self._pending_entry_timestamp: datetime = datetime.min

        # Daily reset tracking (for midnight rollover)
        self._last_daily_reset_date: datetime = datetime.min.date()

        # Watchdog timeout (seconds) — if a 'pending' entry is older than this
        # with no matching live contract, forcibly release the lock
        self._pending_timeout_seconds = rf_config.RF_PENDING_TIMEOUT_SECONDS

        logger.info(
            f"[RF-Risk] Initialized | max_concurrent_total={self.max_concurrent_total} "
            f"max_concurrent/symbol={self.max_concurrent_per_symbol} "
            f"cooldown={self.cooldown_seconds}s global_cooldown={self.global_cooldown_seconds}s "
            f"daily_cap={self.max_trades_per_day} daily_loss_limit={self.daily_loss_limit_mult}x stake | "
            f"max_consec_loss={self.max_consecutive_losses} | "
            f"pending_timeout={self._pending_timeout_seconds}s | "
            f"⚠️ STRICT ENFORCEMENT: asyncio.Lock mutex + 6-step lifecycle + watchdog"
        )

    # ------------------------------------------------------------------ #
    #  Trade Mutex — async lock acquisition / release                     #
    # ------------------------------------------------------------------ #

    @property
    def trade_mutex(self) -> asyncio.Lock:
        """Expose the mutex for external lock checks (e.g. run loop)."""
        return self._trade_mutex

    async def acquire_trade_lock(
        self,
        symbol: str,
        contract_id: str,
        stake: float = None,
        wait_for_lock: bool = True,
    ) -> bool:
        """
        Acquire the trade mutex.

        This MUST be called before any trade execution. The caller must
        call release_trade_lock() only after the DB write is confirmed.

        Args:
            symbol:      Trading symbol
            contract_id: Contract ID (for logging, may be 'pending' pre-buy)
            stake:       Reference stake for risk checks (daily loss limit)
            wait_for_lock:
                - True: block until mutex is available
                - False: fail fast when mutex is already held

        Returns:
            True if acquired, False if system is halted
        """
        # ─────────────────────────────────────────────────────────────────
        # WATCHDOG: Detect stale pending entries and auto-recover
        # PRIORITY 4 FIX: Guard with datetime.min check to prevent false trigger on startup
        # ─────────────────────────────────────────────────────────────────
        if self._trade_mutex.locked() and contract_id == "pending":
            # _pending_entry_timestamp initializes to datetime.min, which would cause
            # elapsed time to be astronomically large and trigger false watchdog on startup
            if self._pending_entry_timestamp != datetime.min:
                elapsed = (datetime.now() - self._pending_entry_timestamp).total_seconds()
            else:
                elapsed = 0.0
            
            if elapsed > self._pending_timeout_seconds:
                logger.warning(
                    f"[RF-Risk] ⚠️ WATCHDOG TRIGGERED: pending entry stuck for "
                    f"{elapsed:.0f}s (timeout={self._pending_timeout_seconds}s) | "
                    f"Forcibly releasing stale lock and clearing halt"
                )
                # Force-release the stale lock
                if self._trade_mutex.locked():
                    self._trade_mutex.release()
                self._trade_lock_active = False
                self._locked_symbol = None
                self._locked_trade_info = {}
                # Clear any associated halt since the stale entry is being purged
                if self._halted:
                    self._halted = False
                    self._halt_reason = ""
                    logger.info("[RF-Risk] 🔄 Halt auto-cleared by watchdog")

        if self._halted:
            logger.error(
                f"[RF-Risk] ❌ HALTED — cannot acquire lock. Reason: {self._halt_reason}"
            )
            return False

        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logger.info(
            f"[RF] STEP 1/6 | {ts} | ACQUIRING TRADE LOCK for {symbol}#{contract_id}"
        )

        if not wait_for_lock and self._trade_mutex.locked():
            logger.info(
                f"[RF-Risk] ⏭️ Lock busy for {symbol}#{contract_id} - fast-fail (parallel scan mode)"
            )
            return False

        await self._trade_mutex.acquire()

        # Record timestamp if this is a "pending" entry
        if contract_id == "pending":
            self._pending_entry_timestamp = datetime.now()

        # ── DOUBLE-CHECK: Re-validate ALL risk rules after acquiring mutex ──
        # Between the pre-check can_trade() and now, conditions may have changed
        # (e.g., daily cap hit, loss-streak cooldown triggered by another path).
        can, reason = self.can_trade(symbol=symbol, stake=stake)
        # can_trade will return False for "mutex is held" — skip that specific check
        # since we just acquired it. Only fail on OTHER risk violations.
        if not can and "mutex" not in reason.lower():
            logger.warning(
                f"[RF-Risk] ❌ Post-acquire risk check FAILED: {reason} | "
                f"Releasing mutex immediately — trade will NOT execute"
            )
            self._trade_mutex.release()
            return False
        
        # ───────────────────────────────────────────────────────────────────────
        # RACE WINDOW HARDENING (PRIORITY 3)
        # Explicitly check active_trades independently of can_trade() logic.
        # This catches the race case where another symbol acquired the lock
        # between this symbol's pre-check and lock acquisition.
        # ───────────────────────────────────────────────────────────────────────
        if len(self.active_trades) > 0:
            existing = list(self.active_trades.values())
            existing_contract = existing[0].get("contract_id", "unknown")
            existing_symbol = existing[0].get("symbol", "unknown")
            logger.error(
                f"[RF-Risk] ❌ RACE WINDOW CAUGHT: Post-acquire check found active trade! "
                f"New: {symbol}#{contract_id} | Existing: {existing_symbol}#{existing_contract} | "
                f"Releasing mutex immediately — trade will NOT execute"
            )
            self._trade_mutex.release()
            return False

        self._trade_lock_active = True
        self._locked_symbol = symbol
        self._locked_trade_info = {"contract_id": contract_id, "symbol": symbol}

        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logger.info(
            f"[RF] STEP 1/6 | {ts} | ✅ TRADE LOCK ACQUIRED for {symbol}#{contract_id}"
        )
        return True

    def release_trade_lock(self, reason: str = "lifecycle complete") -> None:
        """
        Release the trade mutex. ONLY call after confirmed DB write.

        Args:
            reason: Why the lock is being released (for audit trail)
        """
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if self._trade_mutex.locked():
            self._trade_mutex.release()
            self._trade_lock_active = False
            self._locked_symbol = None
            self._locked_trade_info = {}
            logger.info(
                f"[RF] STEP 6/6 | {ts} | 🔓 TRADE LOCK RELEASED — {reason} — scan resuming"
            )
        else:
            logger.warning(
                f"[RF-Risk] ⚠️ release_trade_lock called but mutex was not held | {ts}"
            )

    def halt(self, reason: str) -> None:
        """
        Halt the system — prevents any new trade locks from being acquired.
        The current lock (if held) stays held until manual intervention.
        """
        self._halted = True
        self._halt_reason = reason
        self._halt_timestamp = datetime.now()
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logger.critical(
            f"[RF-Risk] 🚨 SYSTEM HALTED | {ts} | Reason: {reason} | "
            f"Lock held: {self._trade_mutex.locked()}"
        )

    def clear_halt(self) -> None:
        """Clear the halt flag after manual intervention."""
        self._halted = False
        self._halt_reason = ""
        self._halt_timestamp = datetime.min
        logger.info("[RF-Risk] ✅ Halt cleared — system can resume trading")

    # ------------------------------------------------------------------ #
    #  BaseRiskManager interface                                          #
    # ------------------------------------------------------------------ #

    def can_trade(self, symbol: str = None, verbose: bool = False, stake: float = None) -> Tuple[bool, str]:
        """
        Check if a new trade is allowed.
        
        Args:
            symbol: Trading symbol to check (per-symbol cooldown + concurrency)
            verbose: If True, log detailed reasons
            stake: Reference stake for daily loss limit (uses RF_DEFAULT_STAKE if None)
            
        Returns:
            (allowed, reason)
        """
        now = datetime.now()
        ref_stake = stake if stake is not None and stake > 0 else rf_config.RF_DEFAULT_STAKE

        # 0. System halted
        if self._halted:
            msg = f"System HALTED: {self._halt_reason}"
            if verbose:
                logger.info(f"[RF-Risk] 🚨 {msg}")
            return False, msg

        # 0b. Mutex-level check (async-safe authority)
        if self._trade_mutex.locked():
            msg = "Trade mutex is held — another trade is in its lifecycle"
            if verbose:
                logger.info(f"[RF-Risk] 🔒 {msg}")
            return False, msg

        # 1. Daily cap
        if self.daily_trade_count >= self.max_trades_per_day:
            msg = f"Daily trade cap reached ({self.daily_trade_count}/{self.max_trades_per_day})"
            if verbose:
                logger.info(f"[RF-Risk] ❌ {msg}")
            return False, msg

        # 2. Daily loss limit
        daily_loss_limit = ref_stake * self.daily_loss_limit_mult
        if self.daily_pnl <= -daily_loss_limit:
            msg = f"Daily loss limit reached (pnl={self.daily_pnl:+.2f} <= -{daily_loss_limit:.2f})"
            if verbose:
                logger.info(f"[RF-Risk] ❌ {msg}")
            return False, msg

        # 3. Consecutive-loss cooldown
        if now < self._loss_cooldown_until:
            remaining = (self._loss_cooldown_until - now).total_seconds()
            msg = f"Loss-streak cooldown ({remaining:.0f}s remaining)"
            if verbose:
                logger.info(f"[RF-Risk] ⏸️ {msg}")
            return False, msg

        # 4. Total concurrent limit (across all symbols)
        total_active = len(self.active_trades)
        if total_active >= self.max_concurrent_total:
            msg = f"Max total concurrent trades reached ({total_active}/{self.max_concurrent_total})"
            if verbose:
                logger.info(f"[RF-Risk] ⏸️ {msg}")
            return False, msg

        # 5. Global cooldown (after any trade)
        if self._last_trade_close_global and self._last_trade_close_global != datetime.min:
            elapsed = (now - self._last_trade_close_global).total_seconds()
            if elapsed < self.global_cooldown_seconds:
                remaining = self.global_cooldown_seconds - elapsed
                msg = f"Global cooldown ({remaining:.0f}s remaining)"
                if verbose:
                    logger.info(f"[RF-Risk] ⏸️ {msg}")
                return False, msg

        # 6. Per-symbol checks (if symbol provided)
        if symbol:
            blocked_symbols = set(getattr(rf_config, "RF_BLOCKED_SYMBOLS", set()))
            if symbol in blocked_symbols:
                msg = f"{symbol}: blocked from trading"
                if verbose:
                    logger.info(f"[RF-Risk] [STOP] {msg}")
                return False, msg

            # 6a. Concurrent limit per symbol
            active_for_symbol = sum(
                1 for t in self.active_trades.values()
                if t.get("symbol") == symbol
            )
            if active_for_symbol >= self.max_concurrent_per_symbol:
                msg = f"{symbol}: max concurrent trades ({active_for_symbol}/{self.max_concurrent_per_symbol})"
                if verbose:
                    logger.info(f"[RF-Risk] ⏸️ {msg}")
                return False, msg

            # 6b. Per-symbol cooldown
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

    def ensure_daily_reset_if_needed(self) -> None:
        """Call at start of each cycle: reset daily stats if we crossed midnight."""
        today = datetime.now().date()
        if self._last_daily_reset_date != today:
            self.reset_daily_stats()
            self._last_daily_reset_date = today

    def record_trade_open(self, trade_info: Dict) -> None:
        """
        Record a new trade opening.
        ENFORCES: Only 1 concurrent trade globally.
        REQUIRES: Trade mutex must be held by caller.
        REJECTS: Any attempt to open a duplicate trade (race condition).
        
        Args:
            trade_info: Dict with at least 'contract_id' and 'symbol'
        """
        contract_id = trade_info.get("contract_id", "unknown")
        symbol = trade_info.get("symbol", "unknown")

        # ASSERT: Mutex must be held — reject if not
        if not self._trade_mutex.locked():
            logger.critical(
                f"[RF-Risk] 🚨 CRITICAL VIOLATION: record_trade_open() called WITHOUT "
                f"holding the trade mutex! {symbol}#{contract_id} — Rejecting."
            )
            return

        # ENFORCE: No concurrent trades allowed — if any trade is active, REJECT
        if len(self.active_trades) > 0:
            existing = list(self.active_trades.values())
            existing_contract = existing[0].get("contract_id", "unknown")
            existing_symbol = existing[0].get("symbol", "unknown")
            logger.critical(
                f"[RF-Risk] 🚨 CRITICAL VIOLATION: Attempting to open duplicate trade! "
                f"New: {symbol}#{contract_id} | Existing: {existing_symbol}#{existing_contract} | "
                f"HALTING SYSTEM to prevent further corruption"
            )
            # Automatically halt to prevent further damage
            self.halt(f"Duplicate trade prevention: {symbol}#{contract_id} rejected")
            # Release mutex immediately — the finally block in _process_symbol
            # may not be reached on all rejection paths
            if self._trade_mutex.locked():
                self._trade_mutex.release()
                self._trade_lock_active = False
                self._locked_symbol = None
                self._locked_trade_info = {}
            return

        self.active_trades[contract_id] = {
            **trade_info,
            "open_time": datetime.now(),
        }
        self.daily_trade_count += 1

        # Update locked trade info (secondary indicator)
        self._locked_trade_info = {"contract_id": contract_id, "symbol": symbol}
        self._locked_symbol = symbol

        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logger.info(
            f"[RF] STEP 3/6 | {ts} | TRADE TRACKED: {symbol} #{contract_id} "
            f"(active={len(self.active_trades)} daily={self.daily_trade_count}) "
            f"— Risk management now enforced"
        )

    def record_trade_closed(self, result: Dict) -> None:
        """
        Record a trade closure.
        NOTE: This does NOT release the mutex. The mutex is only released
        after the DB write is confirmed in rf_bot.py.
        
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
        # Global cooldown starts now
        self._last_trade_close_global = datetime.now()

        # Consecutive-loss cooldown
        if self.consecutive_losses >= self.max_consecutive_losses:
            self._loss_cooldown_until = datetime.now() + timedelta(
                seconds=self.loss_cooldown_seconds
            )
            logger.warning(
                f"[RF-Risk] ⚠️ {self.consecutive_losses} consecutive losses — "
                f"pausing for {self.loss_cooldown_seconds}s"
            )

        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logger.info(
            f"[RF] STEP 4/6 | {ts} | TRADE CLOSED: {symbol} #{contract_id} "
            f"status={status} pnl={profit:+.2f} "
            f"(W={self.wins} L={self.losses} streak={self.consecutive_losses}) "
            f"— Awaiting DB write before lock release"
        )

    def get_active_trade_info(self) -> Dict:
        """
        Get information about the currently active trade (if any).
        
        Returns:
            Dict with contract_id, symbol if trade is active, else empty dict
        """
        if self._trade_mutex.locked():
            return self._locked_trade_info.copy()
        return {}

    def is_trade_active(self) -> bool:
        """Return True if a trade is currently locked (mutex held or active trades exist)."""
        return self._trade_mutex.locked() or len(self.active_trades) > 0

    def is_halted(self) -> bool:
        """Return True if the system is halted due to a critical error."""
        return self._halted

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
            "mutex_locked": self._trade_mutex.locked(),
            "halted": self._halted,
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
        self._last_trade_close_global = datetime.min
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
            "mutex_locked": self._trade_mutex.locked(),
            "halted": self._halted,
        }

    @property
    def has_active_trade(self) -> bool:
        """True if any trade is currently open."""
        return len(self.active_trades) > 0

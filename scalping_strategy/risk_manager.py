"""
Scalping Risk Manager Implementation
Independent risk management for scalping strategy with tighter limits.
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from base_risk_manager import BaseRiskManager
from utils import setup_logger

from . import config as scalping_config

logger = setup_logger()


class ScalpingRiskManager(BaseRiskManager):
    """
    Risk manager for scalping strategy with independent limits.
    """

    def __init__(self, user_id: str = None, **kwargs):
        self.user_id = user_id

        # Core limits
        self.max_concurrent_trades = scalping_config.SCALPING_MAX_CONCURRENT_TRADES
        self.max_concurrent_per_symbol = getattr(
            scalping_config, "SCALPING_MAX_CONCURRENT_PER_SYMBOL", 1
        )
        self.cooldown_seconds = scalping_config.SCALPING_COOLDOWN_SECONDS
        self.max_trades_per_day = scalping_config.SCALPING_MAX_TRADES_PER_DAY
        self.max_consecutive_losses = scalping_config.SCALPING_MAX_CONSECUTIVE_LOSSES
        self.loss_cooldown_seconds = getattr(
            scalping_config, "SCALPING_GLOBAL_LOSS_COOLDOWN_SECONDS", 3 * 60 * 60
        )
        self.daily_loss_multiplier = scalping_config.SCALPING_DAILY_LOSS_MULTIPLIER

        # Symbol and quality guards
        self.symbol_max_consecutive_losses = getattr(
            scalping_config, "SCALPING_SYMBOL_MAX_CONSECUTIVE_LOSSES", 2
        )
        self.symbol_loss_cooldown_seconds = getattr(
            scalping_config, "SCALPING_SYMBOL_LOSS_COOLDOWN_SECONDS", 45 * 60
        )
        self.short_loss_duration_seconds = getattr(
            scalping_config, "SCALPING_SHORT_LOSS_DURATION_SECONDS", 60
        )
        self.short_loss_lookback_seconds = getattr(
            scalping_config, "SCALPING_SHORT_LOSS_LOOKBACK_SECONDS", 2 * 60 * 60
        )
        self.short_loss_count_threshold = getattr(
            scalping_config, "SCALPING_SHORT_LOSS_COUNT_THRESHOLD", 2
        )
        self.short_loss_cooldown_seconds = getattr(
            scalping_config, "SCALPING_SHORT_LOSS_COOLDOWN_SECONDS", 30 * 60
        )

        # State tracking
        self.active_trades: List[str] = []
        self.daily_trade_count = 0
        self.daily_up_trade_count = 0
        self.daily_down_trade_count = 0
        self.daily_pnl = 0.0
        self.consecutive_losses = 0
        self.last_trade_time: Optional[datetime] = None
        self.stake = 50.0

        # Cooldown state
        self.loss_cooldown_until: datetime = datetime.min
        self.symbol_cooldown_until: Dict[str, datetime] = {}

        # Symbol loss tracking
        self.symbol_consecutive_losses: Dict[str, int] = {}
        self.symbol_short_loss_events: Dict[str, List[datetime]] = {}

        # Warning latches (avoid repeated warning spam)
        self._near_circuit_warning_emitted = False
        self._near_daily_loss_warning_emitted = False

        # Per-trade metadata
        self._trade_metadata: Dict[str, Dict] = {}
        self._trailing_state: Dict[str, Dict] = {}

        # Runaway trade protection
        self.recent_trade_timestamps: List[datetime] = []

        self._load_daily_stats_from_db()

    def _load_daily_stats_from_db(self) -> None:
        """
        Load today's trade stats from DB.
        """
        if not self.user_id:
            return

        try:
            from datetime import date

            from app.core.supabase import supabase

            today_start = datetime.combine(date.today(), datetime.min.time())
            result = (
                supabase.table("trades")
                .select("profit, status, created_at, symbol, signal")
                .eq("user_id", self.user_id)
                .gte("created_at", today_start.isoformat())
                .execute()
            )

            if not result.data:
                logger.info("No trades today yet, starting fresh")
                return

            rows = list(result.data)
            self.daily_trade_count = len(rows)
            self.daily_pnl = sum(float(t.get("profit", 0.0) or 0.0) for t in rows)

            for trade in rows:
                direction = str(trade.get("signal", "")).upper()
                if direction in {"UP", "BUY"}:
                    self.daily_up_trade_count += 1
                elif direction in {"DOWN", "SELL"}:
                    self.daily_down_trade_count += 1

            ordered = sorted(rows, key=lambda x: x.get("created_at", ""))
            self.consecutive_losses = 0
            for trade in ordered:
                normalized = self._normalize_status(
                    trade.get("status"), float(trade.get("profit", 0.0) or 0.0)
                )
                if normalized == "loss":
                    self.consecutive_losses += 1
                elif normalized == "win":
                    self.consecutive_losses = 0

            # If we boot with an already-breached loss streak, immediately
            # re-engage the circuit breaker cooldown for this session.
            if self.consecutive_losses >= self.max_consecutive_losses:
                self._activate_global_loss_cooldown(datetime.now())

            logger.info(
                "Loaded today's stats - Trades: %s, P&L: $%.2f, Consecutive Losses: %s",
                self.daily_trade_count,
                self.daily_pnl,
                self.consecutive_losses,
            )
        except Exception as e:
            logger.warning(f"Could not load daily stats from database: {e}")
            logger.info("Starting with zero counters")

    def set_bot_state(self, state):
        """Set BotState instance for API updates (no-op for scalping)."""
        return None

    def update_risk_settings(self, stake: float):
        """Update stake reference."""
        self.stake = stake
        logger.info(f"Scalping Risk Stake Updated: ${stake}")

    async def check_for_existing_positions(self, trade_engine) -> bool:
        """Scalping starts fresh by default."""
        return False

    def _normalize_status(self, status: Optional[str], pnl: float = 0.0) -> str:
        raw = str(status or "").strip().lower()
        win_aliases = {"win", "won", "profit", "take_profit", "tp"}
        loss_aliases = {"loss", "lost", "stop_loss", "sl"}
        neutral_aliases = {"breakeven", "break_even", "break-even", "sold", "closed", "draw"}

        if raw in win_aliases:
            return "win"
        if raw in loss_aliases:
            return "loss"
        if raw in neutral_aliases:
            if pnl > 0:
                return "win"
            if pnl < 0:
                return "loss"
            return "breakeven"

        if pnl > 0:
            return "win"
        if pnl < 0:
            return "loss"
        return "breakeven"

    def _apply_symbol_cooldown(self, symbol: str, until: datetime, reason: str) -> None:
        existing = self.symbol_cooldown_until.get(symbol, datetime.min)
        if until <= existing:
            return
        self.symbol_cooldown_until[symbol] = until
        remaining = int((until - datetime.now()).total_seconds())
        logger.warning(
            "[SCALPING][%s] Symbol cooldown applied (%ss): %s",
            symbol,
            max(remaining, 0),
            reason,
        )

    def _activate_global_loss_cooldown(self, now: datetime) -> None:
        self.loss_cooldown_until = now + timedelta(seconds=self.loss_cooldown_seconds)
        logger.error(
            "Circuit breaker triggered (%s consecutive losses). "
            "Blocking all new trades for %ss until %s",
            self.consecutive_losses,
            self.loss_cooldown_seconds,
            self.loss_cooldown_until.strftime("%Y-%m-%d %H:%M:%S"),
        )

    def _refresh_global_loss_cooldown(self, now: datetime) -> None:
        if self.loss_cooldown_until == datetime.min:
            return
        if now < self.loss_cooldown_until:
            return

        self.loss_cooldown_until = datetime.min
        self.consecutive_losses = 0
        self._near_circuit_warning_emitted = False
        logger.warning("Circuit-breaker cooldown expired. Trading resumed.")

    def _prune_short_loss_events(self, symbol: str, now: datetime) -> List[datetime]:
        events = self.symbol_short_loss_events.get(symbol, [])
        if not events:
            return []
        cutoff = now - timedelta(seconds=self.short_loss_lookback_seconds)
        kept = [ts for ts in events if ts >= cutoff]
        self.symbol_short_loss_events[symbol] = kept
        return kept

    def can_trade(self, symbol: str = None, verbose: bool = False) -> Tuple[bool, str]:
        """
        Check whether opening a new trade is allowed.
        """
        now = datetime.now()
        self._refresh_global_loss_cooldown(now)

        if symbol:
            blocked_symbols = set(getattr(scalping_config, "BLOCKED_SYMBOLS", set()))
            if symbol in blocked_symbols:
                return False, f"{symbol}: blocked from trading"
            if symbol not in scalping_config.SYMBOLS:
                return False, f"{symbol}: unsupported symbol"

        if now < self.loss_cooldown_until:
            remaining = int((self.loss_cooldown_until - now).total_seconds())
            return False, f"Circuit breaker cooldown active ({remaining}s remaining)"

        if len(self.active_trades) >= self.max_concurrent_trades:
            return False, f"Max concurrent trades reached ({self.max_concurrent_trades})"

        if symbol:
            active_for_symbol = 0
            for contract_id in self.active_trades:
                meta = self._trade_metadata.get(contract_id, {})
                if meta.get("symbol") == symbol:
                    active_for_symbol += 1
            if active_for_symbol >= self.max_concurrent_per_symbol:
                return (
                    False,
                    f"{symbol}: max concurrent trades reached "
                    f"({active_for_symbol}/{self.max_concurrent_per_symbol})",
                )

        if symbol:
            cooldown_until = self.symbol_cooldown_until.get(symbol, datetime.min)
            if now < cooldown_until:
                remaining = int((cooldown_until - now).total_seconds())
                return False, f"{symbol}: cooldown active ({remaining}s remaining)"

        if self.daily_trade_count >= self.max_trades_per_day:
            return False, f"Daily trade limit reached ({self.max_trades_per_day})"

        if self.last_trade_time:
            time_since_last = (now - self.last_trade_time).total_seconds()
            if time_since_last < self.cooldown_seconds:
                remaining = int(self.cooldown_seconds - time_since_last)
                return False, f"Cooldown active ({remaining}s remaining)"

        if self.consecutive_losses >= self.max_consecutive_losses:
            self._activate_global_loss_cooldown(now)
            remaining = int((self.loss_cooldown_until - now).total_seconds())
            return False, f"Circuit breaker cooldown active ({remaining}s remaining)"

        max_daily_loss = self.daily_loss_multiplier * self.stake
        if self.daily_pnl < -max_daily_loss:
            return False, f"Daily loss limit reached (${self.daily_pnl:.2f} < ${-max_daily_loss:.2f})"

        near_daily_limit = max_daily_loss > 0 and self.daily_pnl <= -(max_daily_loss * 0.8)
        if near_daily_limit and not self._near_daily_loss_warning_emitted:
            logger.warning(
                "[SCALPING][RISK] Daily loss nearing cap: P&L $%.2f vs cap $%.2f (80%% threshold)",
                self.daily_pnl,
                -max_daily_loss,
            )
            self._near_daily_loss_warning_emitted = True
        if not near_daily_limit:
            self._near_daily_loss_warning_emitted = False

        if len(self.recent_trade_timestamps) >= scalping_config.SCALPING_RUNAWAY_TRADE_COUNT:
            oldest_trade = self.recent_trade_timestamps[0]
            time_window = (now - oldest_trade).total_seconds() / 60.0
            if time_window < scalping_config.SCALPING_RUNAWAY_WINDOW_MINUTES:
                if verbose:
                    logger.warning(
                        "Runaway trade guardrail: %s trades in %.1f mins",
                        scalping_config.SCALPING_RUNAWAY_TRADE_COUNT,
                        time_window,
                    )
                return False, "Runaway trade protection activated"

        return True, "All checks passed"

    def can_open_trade(
        self,
        symbol: str,
        stake: float,
        take_profit: float = None,
        stop_loss: float = None,
        signal_dict: Dict = None,
    ) -> Tuple[bool, str]:
        """
        Validate trade opening with strategy/risk-specific gate checks.
        """
        can, reason = self.can_trade(symbol, verbose=True)
        if not can:
            return False, reason

        if stake <= 0:
            return False, "Stake must be positive"

        signal_dict = signal_dict or {}
        direction = str(signal_dict.get("signal", "")).upper()
        confidence = float(signal_dict.get("confidence", signal_dict.get("score", 0.0)) or 0.0)

        min_r50_down_conf = float(getattr(scalping_config, "SCALPING_R50_DOWN_MIN_CONFIDENCE", 9.0))
        if symbol == "R_50" and direction == "DOWN" and confidence < min_r50_down_conf:
            return (
                False,
                f"R_50 DOWN blocked: confidence {confidence:.1f} < {min_r50_down_conf:.1f}",
            )

        return True, "OK"

    def get_active_trade_info(self):
        """Return info about first active trade for monitoring."""
        if not self.active_trades:
            return None
        contract_id = self.active_trades[0]
        info = {"contract_id": contract_id, "symbol": "MULTI", "strategy": "Scalping"}
        info.update(self._trade_metadata.get(contract_id, {}))
        return info

    def get_cooldown_remaining(self, symbol: str = None) -> int:
        """Get max active cooldown remaining in seconds."""
        now = datetime.now()
        remaining_values = [0]

        if self.last_trade_time:
            time_since_last = (now - self.last_trade_time).total_seconds()
            remaining_values.append(max(0, int(self.cooldown_seconds - time_since_last)))

        if now < self.loss_cooldown_until:
            remaining_values.append(int((self.loss_cooldown_until - now).total_seconds()))

        if symbol:
            symbol_until = self.symbol_cooldown_until.get(symbol, datetime.min)
            if now < symbol_until:
                remaining_values.append(int((symbol_until - now).total_seconds()))

        return max(remaining_values)

    def get_statistics(self) -> Dict:
        """Get current statistics dictionary."""
        return {
            "total_trades": self.daily_trade_count,
            "total_pnl": self.daily_pnl,
            "daily_pnl": self.daily_pnl,
            "win_rate": 0.0,
            "consecutive_losses": self.consecutive_losses,
            "down_trades": self.daily_down_trade_count,
            "up_trades": self.daily_up_trade_count,
        }

    @property
    def has_active_trade(self) -> bool:
        return len(self.active_trades) > 0

    def record_trade_open(self, trade_info: Dict) -> None:
        """
        Record a newly opened trade.
        """
        now = datetime.now()
        contract_id = trade_info.get("contract_id")
        stake = float(trade_info.get("stake", self.stake) or self.stake)
        symbol = trade_info.get("symbol", "UNKNOWN")
        direction = str(trade_info.get("direction", trade_info.get("signal", ""))).upper()
        open_time = trade_info.get("open_time")
        if not isinstance(open_time, datetime):
            open_time = now

        if contract_id:
            self.active_trades.append(contract_id)
            self._trade_metadata[contract_id] = {
                "stake": stake,
                "symbol": symbol,
                "open_time": open_time,
                "direction": direction,
                "entry_price": trade_info.get("entry_price"),
                "multiplier": trade_info.get("multiplier"),
            }

        self.daily_trade_count += 1
        if direction in {"UP", "BUY"}:
            self.daily_up_trade_count += 1
        elif direction in {"DOWN", "SELL"}:
            self.daily_down_trade_count += 1

        self.last_trade_time = now
        self.stake = stake

        self.recent_trade_timestamps.append(now)
        if len(self.recent_trade_timestamps) > scalping_config.SCALPING_RUNAWAY_TRADE_COUNT:
            self.recent_trade_timestamps.pop(0)

        logger.info(
            "Trade opened - Contract: %s, Symbol: %s, Daily count: %s/%s",
            contract_id,
            symbol,
            self.daily_trade_count,
            self.max_trades_per_day,
        )
        logger.info(
            "Active trades: %s/%s",
            len(self.active_trades),
            self.max_concurrent_trades,
        )

    def record_trade_opened(self, trade_info: Dict) -> None:
        """Alias for base interface compatibility."""
        self.record_trade_open(trade_info)

    def record_trade_closed(self, result: Dict) -> None:
        """
        Record a closed trade and update all risk counters.
        """
        now = datetime.now()
        contract_id = result.get("contract_id")
        profit = float(result.get("profit", 0.0) or 0.0)
        raw_status = result.get("status", "unknown")

        meta = self._trade_metadata.get(contract_id, {})
        symbol = str(result.get("symbol") or meta.get("symbol") or "UNKNOWN")

        # Remove from active trades
        if contract_id in self.active_trades:
            self.active_trades.remove(contract_id)
        elif isinstance(result, str) and result in self.active_trades:
            self.active_trades.remove(result)

        # Duration: explicit result first, metadata fallback
        duration_seconds = result.get("duration")
        if duration_seconds is None:
            open_time = meta.get("open_time")
            if isinstance(open_time, datetime):
                duration_seconds = max(0, (now - open_time).total_seconds())
        try:
            duration_seconds = float(duration_seconds) if duration_seconds is not None else None
        except Exception:
            duration_seconds = None

        # Cleanup per-trade state
        self._trade_metadata.pop(contract_id, None)
        self._trailing_state.pop(contract_id, None)

        # Update P&L
        self.daily_pnl += profit

        normalized_status = self._normalize_status(raw_status, profit)

        if normalized_status == "loss":
            self.consecutive_losses += 1
            self.symbol_consecutive_losses[symbol] = self.symbol_consecutive_losses.get(symbol, 0) + 1
            logger.info(
                "Loss recorded - Consecutive losses: %s/%s",
                self.consecutive_losses,
                self.max_consecutive_losses,
            )

            if (
                self.consecutive_losses == self.max_consecutive_losses - 1
                and not self._near_circuit_warning_emitted
            ):
                logger.warning(
                    "[SCALPING][RISK] Warning: %s consecutive losses "
                    "(one away from circuit breaker threshold %s)",
                    self.consecutive_losses,
                    self.max_consecutive_losses,
                )
                self._near_circuit_warning_emitted = True

            if self.consecutive_losses >= self.max_consecutive_losses:
                self._activate_global_loss_cooldown(now)

            symbol_loss_streak = self.symbol_consecutive_losses.get(symbol, 0)
            if symbol_loss_streak >= self.symbol_max_consecutive_losses:
                until = now + timedelta(seconds=self.symbol_loss_cooldown_seconds)
                self._apply_symbol_cooldown(
                    symbol,
                    until,
                    (
                        f"{symbol_loss_streak} consecutive losses on {symbol} "
                        f"(threshold {self.symbol_max_consecutive_losses})"
                    ),
                )
                self.symbol_consecutive_losses[symbol] = 0

            if duration_seconds is not None and duration_seconds < self.short_loss_duration_seconds:
                events = self.symbol_short_loss_events.setdefault(symbol, [])
                events.append(now)
                events = self._prune_short_loss_events(symbol, now)
                if len(events) >= self.short_loss_count_threshold:
                    until = now + timedelta(seconds=self.short_loss_cooldown_seconds)
                    self._apply_symbol_cooldown(
                        symbol,
                        until,
                        (
                            f"{len(events)} losses under {self.short_loss_duration_seconds}s in "
                            f"last {int(self.short_loss_lookback_seconds / 60)}m"
                        ),
                    )
        elif normalized_status == "win":
            if self.consecutive_losses > 0:
                logger.info("Win recorded - Consecutive losses reset")
            self.consecutive_losses = 0
            self._near_circuit_warning_emitted = False
            self.symbol_consecutive_losses[symbol] = 0

        max_daily_loss = self.daily_loss_multiplier * self.stake
        near_daily_limit = max_daily_loss > 0 and self.daily_pnl <= -(max_daily_loss * 0.8)
        if near_daily_limit and not self._near_daily_loss_warning_emitted:
            logger.warning(
                "[SCALPING][RISK] Warning: daily loss nearing cap "
                "(P&L $%.2f vs cap $%.2f)",
                self.daily_pnl,
                -max_daily_loss,
            )
            self._near_daily_loss_warning_emitted = True
        if not near_daily_limit:
            self._near_daily_loss_warning_emitted = False

        logger.info(
            "Trade closed - Status: %s | P&L: $%.2f | Daily P&L: $%.2f | Duration: %ss",
            normalized_status,
            profit,
            self.daily_pnl,
            int(duration_seconds) if duration_seconds is not None else "n/a",
        )
        logger.info(
            "Active trades: %s/%s",
            len(self.active_trades),
            self.max_concurrent_trades,
        )

    def record_trade_close(
        self,
        contract_id: str,
        pnl: float,
        status: str,
        symbol: Optional[str] = None,
        duration: Optional[float] = None,
    ) -> None:
        """
        Compatibility wrapper used by runner.
        """
        result = {
            "contract_id": contract_id,
            "profit": pnl,
            "status": status,
        }
        if symbol is not None:
            result["symbol"] = symbol
        if duration is not None:
            result["duration"] = duration
        self.record_trade_closed(result)

    def get_current_limits(self) -> Dict:
        """
        Get current risk limits and counters.
        """
        return {
            "strategy": "Scalping",
            "max_concurrent_trades": self.max_concurrent_trades,
            "max_concurrent_per_symbol": self.max_concurrent_per_symbol,
            "current_concurrent_trades": len(self.active_trades),
            "max_trades_per_day": self.max_trades_per_day,
            "daily_trade_count": self.daily_trade_count,
            "daily_up_trade_count": self.daily_up_trade_count,
            "daily_down_trade_count": self.daily_down_trade_count,
            "max_consecutive_losses": self.max_consecutive_losses,
            "consecutive_losses": self.consecutive_losses,
            "daily_pnl": self.daily_pnl,
            "max_daily_loss": self.daily_loss_multiplier * self.stake,
            "cooldown_seconds": self.cooldown_seconds,
            "loss_cooldown_seconds": self.loss_cooldown_seconds,
            "loss_cooldown_until": (
                self.loss_cooldown_until.isoformat()
                if self.loss_cooldown_until != datetime.min
                else None
            ),
            "last_trade_time": self.last_trade_time.isoformat() if self.last_trade_time else None,
            "runaway_protection_window_minutes": scalping_config.SCALPING_RUNAWAY_WINDOW_MINUTES,
            "recent_trade_count": len(self.recent_trade_timestamps),
        }

    def should_close_trade(
        self,
        contract_id: str,
        current_pnl: float,
        current_price: float = None,
        previous_price: float = None,
    ) -> Dict:
        """
        Check emergency close conditions.
        """
        active_trade = None
        for i, contract in enumerate(self.active_trades):
            if contract == contract_id:
                active_trade = {"contract_id": contract_id, "index": i}
                break

        if not active_trade:
            return {"should_close": False, "reason": "Trade not found in active trades"}

        potential_daily_loss = self.daily_pnl + current_pnl
        max_daily_loss = self.daily_loss_multiplier * self.stake
        if potential_daily_loss <= -(max_daily_loss * 0.9):
            return {
                "should_close": True,
                "reason": "emergency_daily_loss",
                "message": f"Emergency: Daily loss approaching limit (${potential_daily_loss:.2f})",
                "current_pnl": current_pnl,
            }
        return {"should_close": False, "reason": "monitor_active"}

    def check_stagnation_exit(self, trade_info: Dict, current_pnl: float) -> Tuple[bool, str]:
        """
        Close stale losing trades.
        """
        open_time = trade_info.get("open_time")
        stake = trade_info.get("stake", self.stake)
        symbol = trade_info.get("symbol", "UNKNOWN")
        if not open_time:
            return False, ""

        time_open = (datetime.now() - open_time).total_seconds()
        if time_open < scalping_config.SCALPING_STAGNATION_EXIT_TIME:
            return False, ""

        if current_pnl >= 0:
            return False, ""

        loss_pct = abs((current_pnl / stake) * 100) if stake > 0 else 0
        if loss_pct > scalping_config.SCALPING_STAGNATION_LOSS_PCT:
            logger.warning(
                "[SCALP] Stagnation exit: %s open %ss, losing %.1f%% of stake",
                symbol,
                int(time_open),
                loss_pct,
            )
            return True, "stagnation_exit"
        return False, ""

    def check_trailing_profit(self, trade_info: Dict, current_pnl: float) -> Tuple[bool, str, bool]:
        """
        Trailing-profit exit logic.
        """
        contract_id = trade_info.get("contract_id")
        stake = trade_info.get("stake", self.stake)
        symbol = trade_info.get("symbol", "UNKNOWN")
        if not contract_id or stake <= 0:
            return False, "", False

        profit_pct = (current_pnl / stake) * 100
        state = self._trailing_state.get(contract_id)

        if profit_pct >= scalping_config.SCALPING_TRAIL_ACTIVATION_PCT and state is None:
            self._trailing_state[contract_id] = {
                "highest_profit_pct": profit_pct,
                "trailing_active": True,
            }
            trail_distance = self._get_trail_distance(profit_pct)
            trail_floor = profit_pct - trail_distance
            logger.info(
                "[SCALP] Trailing activated at %.1f%%, distance %.1f%%, floor %.1f%%",
                profit_pct,
                trail_distance,
                trail_floor,
            )
            return False, "", True

        if state is None:
            return False, "", False

        if profit_pct > state["highest_profit_pct"]:
            state["highest_profit_pct"] = profit_pct

        trail_distance = self._get_trail_distance(state["highest_profit_pct"])
        trail_floor = state["highest_profit_pct"] - trail_distance
        if profit_pct < trail_floor:
            logger.warning(
                "[SCALP] Trailing EXIT %s: profit %.1f%% (peak %.1f%%, distance %.1f%%, floor %.1f%%)",
                symbol,
                profit_pct,
                state["highest_profit_pct"],
                trail_distance,
                trail_floor,
            )
            return True, "trailing_profit_exit", False

        logger.debug(
            "[SCALP] Trailing %s: profit %.1f%% (peak %.1f%%, distance %.1f%%, floor %.1f%%)",
            symbol,
            profit_pct,
            state["highest_profit_pct"],
            trail_distance,
            trail_floor,
        )
        return False, "", False

    def _get_trail_distance(self, profit_pct: float) -> float:
        for min_pct, distance in scalping_config.SCALPING_TRAIL_TIERS:
            if profit_pct >= min_pct:
                return distance
        return scalping_config.SCALPING_TRAIL_TIERS[-1][1]

    def reset_daily_stats(self) -> None:
        """
        Reset daily counters.
        """
        logger.info("Resetting daily stats for scalping risk manager")
        self.daily_trade_count = 0
        self.daily_up_trade_count = 0
        self.daily_down_trade_count = 0
        self.daily_pnl = 0.0
        self.consecutive_losses = 0
        self.recent_trade_timestamps = []
        self.symbol_consecutive_losses = {}
        self.symbol_short_loss_events = {}
        self.symbol_cooldown_until = {}
        self._near_circuit_warning_emitted = False
        self._near_daily_loss_warning_emitted = False
        logger.info("Daily stats reset complete")

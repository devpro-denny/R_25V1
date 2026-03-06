"""
Bot Runner - Multi-Asset Parallel Scanner
Manages the lifecycle of the trading bot with multi-asset support
- Scans strategy symbol universe
- Parallel per-symbol analysis each cycle
- Global risk/position limit enforcement
- Single execution path protection
- Continuous monitoring of active trades
"""

import asyncio
import logging
import re
from datetime import datetime
from typing import Optional, Dict, List, Set
from enum import Enum

# Import existing bot modules
from data_fetcher import DataFetcher
from trade_engine import TradeEngine
import config

from app.bot.state import BotState
from app.bot.events import event_manager
from app.bot.telegram_bridge import telegram_bridge
from app.core.context import user_id_var, bot_type_var
from app.services.trades_service import UserTradesService
from functools import wraps


def _strategy_to_bot_type(strategy_name: Optional[str]) -> str:
    value = (strategy_name or "").strip().lower()
    if value == "scalping":
        return "scalping"
    if value == "conservative":
        return "conservative"
    if value == "risefall":
        return "risefall"
    return "system"


def with_user_context(func):
    @wraps(func)
    async def wrapper(self, *args, **kwargs):
        user_token = None
        bot_token = None
        if self.account_id:
            user_token = user_id_var.set(self.account_id)
        bot_token = bot_type_var.set(_strategy_to_bot_type(self._get_strategy_name()))
        try:
            return await func(self, *args, **kwargs)
        finally:
            if user_token:
                user_id_var.reset(user_token)
            if bot_token:
                bot_type_var.reset(bot_token)
    return wrapper

from utils import setup_logger

logger = setup_logger()

class BotStatus(str, Enum):
    """Bot status enumeration"""
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"

class BotRunner:
    """
    Multi-Asset Trading Bot Runner
    - Scans multiple symbols in parallel
    - Enforces global 1-trade position limit
    - First qualifying signal locks the system
    - Monitors active trades across all assets
    """
    
    def __init__(self, api_token: Optional[str] = None, account_id: Optional[str] = None,
                 strategy = None, risk_manager = None):
        # Backward compatibility: allow positional form
        # BotRunner(account_id, strategy, risk_manager).
        if (
            isinstance(api_token, str)
            and account_id is not None
            and not isinstance(account_id, str)
            and strategy is not None
            and risk_manager is None
        ):
            account_id, strategy, risk_manager, api_token = api_token, account_id, strategy, None
        self.is_running = False
        self.task: Optional[asyncio.Task] = None
        self.status = BotStatus.STOPPED
        self.start_time: Optional[datetime] = None
        self.error_message: Optional[str] = None
        
        # Identity
        self.account_id = account_id
        self.api_token = api_token or config.DERIV_API_TOKEN
        
        # Instance State
        self.state = BotState()
        
        # Bot components (initialized on start or injected)
        self.data_fetcher: Optional[DataFetcher] = None
        self.trade_engine: Optional[TradeEngine] = None
        
        # Strategy and risk manager injection (NEW)
        if strategy is None:
            # Default to conservative strategy
            from conservative_strategy import ConservativeStrategy
            self.strategy = ConservativeStrategy()
        else:
            self.strategy = strategy
        
        if risk_manager is None:
            # Will be initialized in _run_bot for backward compatibility
            self.risk_manager = None
        else:
            self.risk_manager = risk_manager
        
        # Multi-asset configuration
        self.symbols: List[str] = config.SYMBOLS
        self.asset_config: Dict = config.ASSET_CONFIG
        
        # User Configurable Settings
        self.user_stake: Optional[float] = None
        self.auto_execute_signals: bool = False
        if self.strategy and hasattr(self.strategy, "get_strategy_name"):
            try:
                self.active_strategy = self.strategy.get_strategy_name()
            except Exception:
                self.active_strategy = "Conservative"
        else:
            self.active_strategy = "Conservative"
        
        # Scanning statistics
        self.scan_count = 0
        self.signals_by_symbol: Dict[str, int] = {symbol: 0 for symbol in self.symbols}
        self.errors_by_symbol: Dict[str, int] = {symbol: 0 for symbol in self.symbols}
        self.scalping_total_symbol_checks: int = 0
        self.scalping_signals_generated: int = 0
        self.scalping_gate_counters: Dict[str, int] = {}
        
        # Logging control
        self.last_status_log: Dict[str, Dict] = {} # {symbol: {'msg': str, 'time': datetime}}
        # Per-contract active monitoring progress logs.
        self._active_progress_key_prefix = "active:"
        # Structured decision event throttling cache
        self._decision_log_state: Dict[str, Dict] = {}
        # Periodic DB-to-runtime recovery for persisted open trades.
        self._last_active_trade_recovery_at: datetime = datetime.min
        # Track consecutive broker-status misses per contract for fallback reconciliation.
        self._active_status_miss_counts: Dict[str, int] = {}
        # Protect trade execution path when symbol scans run concurrently.
        self._execution_mutex: asyncio.Lock = asyncio.Lock()
        # Protect cycle-level winner claim when multiple symbols signal concurrently.
        self._cycle_claim_mutex: asyncio.Lock = asyncio.Lock()
        self._cycle_signal_claimed: bool = False
        self._cycle_winner_symbol: Optional[str] = None
        
        # Telegram bridge
        self.telegram_bridge = telegram_bridge

        # Sync per-strategy market scope on init.
        self._sync_strategy_scope()

    def _get_strategy_name(self) -> str:
        """Resolve strategy name safely for structured decision events."""
        try:
            if self.strategy and hasattr(self.strategy, "get_strategy_name"):
                return self.strategy.get_strategy_name()
        except Exception:
            pass
        return getattr(self, "active_strategy", "Unknown") or "Unknown"

    def _is_scalping_strategy(self) -> bool:
        """Return True when active strategy is scalping."""
        return (self._get_strategy_name() or "").strip().lower() == "scalping"

    def set_auto_execute_signals(self, enabled: bool) -> None:
        """Update signal execution mode at runtime for multiplier strategies."""
        self.auto_execute_signals = bool(enabled)
        mode = "AUTO_EXECUTION" if self.auto_execute_signals else "MANUAL_SIGNAL"
        logger.info(
            "[%s][SYSTEM] Signal execution mode set to %s",
            self._get_strategy_name(),
            mode,
        )

    def _has_runtime_active_trade(self) -> bool:
        """Check active-trade state across risk-manager implementations."""
        if not self.risk_manager:
            return False

        has_active = getattr(self.risk_manager, "has_active_trade", None)
        if callable(has_active):
            try:
                return bool(has_active())
            except Exception:
                return False
        if has_active is not None:
            try:
                return bool(has_active)
            except Exception:
                return False

        active_trades = getattr(self.risk_manager, "active_trades", None)
        return isinstance(active_trades, list) and len(active_trades) > 0

    @staticmethod
    def _normalize_rejection_slug(text: str) -> str:
        normalized = re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")
        return normalized or "unknown_rejection"

    def _build_scalping_gate_counter_key(
        self,
        reason: str,
        gate: Optional[str] = None,
        reason_code: Optional[str] = None,
    ) -> str:
        """
        Build normalized gate counter key from strategy metadata or reason fallback.
        """
        if gate and reason_code:
            return f"{self._normalize_rejection_slug(gate)}:{self._normalize_rejection_slug(reason_code)}"

        lower_reason = str(reason or "").lower()

        reason_map = [
            ("no fresh crossover on 1h/5m", "gate_2_trend:no_fresh_crossover"),
            ("no fresh crossover on 5m", "gate_2_trend:no_fresh_crossover"),
            ("no 1h trend bias", "gate_2_trend:no_1h_trend_bias"),
            ("trend mismatch", "gate_2_trend:trend_mismatch"),
            ("no 1h break of structure", "gate_2_trend:no_1h_break_of_structure"),
            ("weak trend (adx", "gate_3_indicators:adx_below_threshold"),
            ("adx exhaustion", "gate_3_indicators:adx_exhaustion"),
            ("requires stronger adx", "gate_3_indicators:adx_symbol_threshold"),
            ("adx declining", "gate_3_indicators:adx_declining"),
            ("not in up range", "gate_3_indicators:rsi_out_of_range"),
            ("not in down range", "gate_3_indicators:rsi_out_of_range"),
            ("adverse pre-entry move", "gate_4_momentum:adverse_pre_entry_move"),
            ("no momentum breakout", "gate_4_momentum:no_momentum_breakout"),
            ("entry drift too high", "gate_4_momentum:entry_drift_too_high"),
            ("1m directional sequence", "gate_4_momentum:one_minute_directional_sequence"),
            ("weak body ratio", "gate_4_momentum:weak_body_ratio"),
            ("candle direction mismatch", "gate_4_momentum:candle_direction_mismatch"),
            ("parabolic spike detected", "gate_4_momentum:parabolic_spike"),
            ("5m structure not confirmed", "gate_4_structure:five_minute_structure_not_confirmed"),
            ("price not near any key zone", "gate_4_price_action:not_near_key_zone"),
            ("no 5m zone rejection confirmed", "gate_4_price_action:no_zone_rejection"),
            ("low r:r", "gate_5_risk:low_rr_ratio"),
            ("invalid stop loss", "gate_5_risk:invalid_stop_loss"),
        ]

        for pattern, mapped in reason_map:
            if pattern in lower_reason:
                return mapped

        if gate:
            return f"{self._normalize_rejection_slug(gate)}:{self._normalize_rejection_slug(reason_code or reason)}"

        return f"gate_unknown:{self._normalize_rejection_slug(reason_code or reason)}"

    def _record_scalping_strategy_outcome(self, signal: Optional[Dict]) -> None:
        """
        Track per-gate strategy outcomes for scalping opportunity frequency analysis.
        """
        if not self._is_scalping_strategy():
            return

        self.scalping_total_symbol_checks += 1

        if not isinstance(signal, dict):
            key = "gate_unknown:invalid_signal_payload"
            self.scalping_gate_counters[key] = self.scalping_gate_counters.get(key, 0) + 1
            return

        if signal.get("can_trade"):
            self.scalping_signals_generated += 1
            return

        details = signal.get("details")
        details = details if isinstance(details, dict) else {}

        reason = str(details.get("reason", "unknown_rejection"))
        gate = details.get("gate")
        reason_code = details.get("reason_code")
        key = self._build_scalping_gate_counter_key(reason=reason, gate=gate, reason_code=reason_code)
        self.scalping_gate_counters[key] = self.scalping_gate_counters.get(key, 0) + 1

    def get_scalping_gate_metrics(self) -> Dict[str, object]:
        """
        Return scalping opportunity frequency snapshot and gate-level counters.
        """
        total_checks = self.scalping_total_symbol_checks
        signals = self.scalping_signals_generated
        rejections = max(total_checks - signals, 0)
        rate_pct = round((signals / total_checks) * 100, 2) if total_checks > 0 else 0.0

        return {
            "scalping_total_symbol_checks": total_checks,
            "scalping_signals_generated": signals,
            "scalping_rejections": rejections,
            "scalping_opportunity_rate_pct": rate_pct,
            "scalping_gate_counters": dict(self.scalping_gate_counters),
        }

    def _sync_strategy_scope(self) -> None:
        """Bind runner symbol universe/config to currently injected strategy."""
        if self.strategy and hasattr(self.strategy, "get_symbols"):
            try:
                self.symbols = list(self.strategy.get_symbols())
            except Exception:
                self.symbols = list(config.SYMBOLS)
        else:
            self.symbols = list(config.SYMBOLS)

        if self.strategy and hasattr(self.strategy, "get_asset_config"):
            try:
                self.asset_config = dict(self.strategy.get_asset_config())
            except Exception:
                self.asset_config = dict(config.ASSET_CONFIG)
        else:
            self.asset_config = dict(config.ASSET_CONFIG)

        # Keep symbol counters aligned with active symbol universe.
        self.signals_by_symbol = {symbol: self.signals_by_symbol.get(symbol, 0) for symbol in self.symbols}
        self.errors_by_symbol = {symbol: self.errors_by_symbol.get(symbol, 0) for symbol in self.symbols}

    def _init_risk_manager_for_strategy(self):
        """Instantiate default risk manager matching the active strategy."""
        strategy_name = (self._get_strategy_name() or "Conservative").strip().lower()
        if strategy_name == "scalping":
            from scalping_risk_manager import ScalpingRiskManager

            return ScalpingRiskManager(user_id=self.account_id)

        # Multiplier default remains conservative.
        from conservative_risk_manager import ConservativeRiskManager

        return ConservativeRiskManager(user_id=self.account_id)

    @staticmethod
    def _parse_trade_datetime(value: Optional[object]) -> Optional[datetime]:
        """Parse DB/API datetime payloads into naive local datetime."""
        if isinstance(value, datetime):
            parsed = value
        elif value is None:
            return None
        elif isinstance(value, (int, float)):
            try:
                parsed = datetime.fromtimestamp(float(value))
            except Exception:
                return None
        else:
            text = str(value).strip()
            if not text:
                return None
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            try:
                parsed = datetime.fromisoformat(text)
            except Exception:
                return None

        if parsed.tzinfo is not None:
            parsed = parsed.astimezone().replace(tzinfo=None)
        return parsed

    def _get_runtime_active_contract_ids(self) -> Set[str]:
        """
        Collect active contract IDs from risk manager state.
        Supports both conservative (dict rows) and scalping (id list) formats.
        """
        ids: Set[str] = set()
        if not self.risk_manager:
            return ids

        active_trades = getattr(self.risk_manager, "active_trades", None)
        if not isinstance(active_trades, list):
            return ids

        for trade in active_trades:
            if isinstance(trade, dict):
                contract_id = trade.get("contract_id")
            else:
                contract_id = trade
            if contract_id in (None, ""):
                continue
            ids.add(str(contract_id))
        return ids

    def _restore_trade_for_monitoring(self, persisted_trade: Dict) -> bool:
        """
        Rebuild in-memory active-trade state from a persisted open DB row.
        """
        if not self.risk_manager or not isinstance(persisted_trade, dict):
            return False

        contract_id_raw = persisted_trade.get("contract_id")
        if contract_id_raw in (None, ""):
            return False
        contract_id = str(contract_id_raw)

        active_ids = self._get_runtime_active_contract_ids()
        if contract_id in active_ids:
            return False

        symbol = persisted_trade.get("symbol") or "UNKNOWN"
        direction = str(
            persisted_trade.get("signal")
            or persisted_trade.get("direction")
            or ""
        ).upper()
        if direction == "CALL":
            direction = "UP"
        elif direction == "PUT":
            direction = "DOWN"

        stake = persisted_trade.get("stake")
        try:
            stake = float(stake) if stake is not None else None
        except Exception:
            stake = None

        entry_price = persisted_trade.get("entry_price")
        if entry_price is None:
            entry_price = persisted_trade.get("entry_spot")
        try:
            entry_price = float(entry_price) if entry_price is not None else None
        except Exception:
            entry_price = None

        open_time = self._parse_trade_datetime(
            persisted_trade.get("timestamp") or persisted_trade.get("open_time")
        ) or datetime.now()
        entry_source = str(persisted_trade.get("entry_source") or "system")

        def _coerce_exit_flag(value: object, fallback: bool = True) -> bool:
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                if value == 1:
                    return True
                if value == 0:
                    return False
                return fallback
            if isinstance(value, str):
                normalized = value.strip().lower()
                if normalized in {"true", "1", "yes", "on"}:
                    return True
                if normalized in {"false", "0", "no", "off"}:
                    return False
            return fallback

        trailing_enabled = _coerce_exit_flag(
            persisted_trade.get("trailing_enabled"),
            True,
        )
        stagnation_enabled = _coerce_exit_flag(
            persisted_trade.get("stagnation_enabled"),
            True,
        )

        is_manual_tracking = entry_source.strip().lower() in {
            "manual_imported",
            "manual_tracking",
            "sync_import",
            "broker_sync",
        } or bool(persisted_trade.get("manual_tracking"))

        if self._is_scalping_strategy() and hasattr(self.risk_manager, "_trade_metadata"):
            active_trades = getattr(self.risk_manager, "active_trades", None)
            trade_metadata = getattr(self.risk_manager, "_trade_metadata", None)
            if not isinstance(active_trades, list) or not isinstance(trade_metadata, dict):
                return False

            active_trades.append(contract_id)
            existing_meta = trade_metadata.get(contract_id, {})
            trade_metadata[contract_id] = {
                **existing_meta,
                "stake": stake if stake is not None else existing_meta.get("stake", getattr(self.risk_manager, "stake", 0.0)),
                "symbol": symbol,
                "open_time": open_time,
                "direction": direction,
                "entry_price": entry_price,
                "multiplier": persisted_trade.get("multiplier", existing_meta.get("multiplier")),
                "risk_reward_ratio": persisted_trade.get("risk_reward_ratio", existing_meta.get("risk_reward_ratio")),
                "min_rr_required": persisted_trade.get("min_rr_required", existing_meta.get("min_rr_required")),
                "trailing_enabled": trailing_enabled,
                "stagnation_enabled": stagnation_enabled,
                "entry_source": entry_source,
                "manual_tracking": is_manual_tracking,
            }
        else:
            active_trades = getattr(self.risk_manager, "active_trades", None)
            if not isinstance(active_trades, list):
                return False
            active_trades.append(
                {
                    "timestamp": open_time,
                    "symbol": symbol,
                    "contract_id": contract_id,
                    "direction": direction,
                    "stake": stake if stake is not None else 0.0,
                    "entry_price": entry_price if entry_price is not None else 0.0,
                    "entry_spot": entry_price if entry_price is not None else 0.0,
                    "status": "open",
                    # Keep restored contracts aligned with normal conservative lifecycle.
                    "strategy": "topdown",
                    "phase": "committed",
                    "cancellation_enabled": False,
                    "cancellation_expiry": None,
                    "highest_unrealized_pnl": 0.0,
                    "has_been_profitable": False,
                    "trailing_enabled": trailing_enabled,
                    "stagnation_enabled": stagnation_enabled,
                    "entry_source": entry_source,
                    "manual_tracking": is_manual_tracking,
                }
            )

        if not any(
            isinstance(t, dict) and str(t.get("contract_id")) == contract_id
            for t in list(getattr(self.state, "active_trades", []))
        ):
            self.state.add_trade(
                {
                    "contract_id": contract_id,
                    "symbol": symbol,
                    "direction": direction,
                    "stake": stake,
                    "entry_price": entry_price,
                    "open_time": open_time.isoformat(),
                    "status": "open",
                    "strategy_type": self._get_strategy_name(),
                    "trailing_enabled": trailing_enabled,
                    "stagnation_enabled": stagnation_enabled,
                    "entry_source": entry_source,
                }
            )

        logger.warning(
            "[%s][SYSTEM] Recovered active trade from DB for monitoring: %s (%s)",
            self._get_strategy_name(),
            contract_id,
            symbol,
        )
        return True

    def _recover_runtime_active_trades(self, min_interval_seconds: int = 15) -> int:
        """
        Recover persisted open trades into runtime state during live bot operation.
        This protects monitoring continuity if runtime registration was missed.
        """
        if not self.account_id or not self.risk_manager:
            return 0

        now = datetime.now()
        if (
            self._last_active_trade_recovery_at != datetime.min
            and (now - self._last_active_trade_recovery_at).total_seconds() < max(min_interval_seconds, 1)
        ):
            return 0
        self._last_active_trade_recovery_at = now

        try:
            persisted_active = UserTradesService.get_user_active_trades(self.account_id, limit=50)
        except Exception as e:
            logger.warning(
                "[%s][SYSTEM] Runtime active-trade recovery failed: %s",
                self._get_strategy_name(),
                e,
            )
            return 0

        if not isinstance(persisted_active, list) or not persisted_active:
            return 0

        recovered = 0
        for persisted_trade in persisted_active:
            if self._restore_trade_for_monitoring(persisted_trade):
                recovered += 1

        if recovered:
            logger.info(
                "[%s][SYSTEM] Runtime active-trade recovery: %s contract(s) reattached for monitoring",
                self._get_strategy_name(),
                recovered,
            )
        return recovered

    async def _reconcile_active_trades_on_startup(self) -> None:
        """
        Reconcile persisted open trades against broker status at startup.

        - If still open: restore runtime state so monitoring resumes immediately.
        - If already closed: persist final status so stale DB open rows are repaired.
        """
        if not self.account_id or not self.trade_engine:
            return

        try:
            persisted_active = UserTradesService.get_user_active_trades(self.account_id, limit=50)
        except Exception as e:
            logger.warning(
                "[%s][SYSTEM] Failed to load persisted active trades: %s",
                self._get_strategy_name(),
                e,
            )
            return

        if not isinstance(persisted_active, list) or not persisted_active:
            return

        recovered_count = 0
        settled_count = 0

        for persisted_trade in persisted_active:
            if not isinstance(persisted_trade, dict):
                continue

            contract_id_raw = persisted_trade.get("contract_id")
            if contract_id_raw in (None, ""):
                continue
            contract_id = str(contract_id_raw)

            live_status: Optional[Dict] = None
            try:
                live_status = await self.trade_engine.get_trade_status(contract_id)
            except Exception as status_error:
                logger.warning(
                    "[%s][SYSTEM] Could not fetch status for persisted trade %s: %s",
                    self._get_strategy_name(),
                    contract_id,
                    status_error,
                )

            status_name = str((live_status or {}).get("status", "")).strip().lower()
            is_settled = bool((live_status or {}).get("is_sold")) or status_name in {
                "sold",
                "won",
                "lost",
                "closed",
                "settled",
            }

            if is_settled:
                pnl = 0.0
                try:
                    pnl = float((live_status or {}).get("profit", 0.0) or 0.0)
                except Exception:
                    pnl = 0.0

                normalized_status = str((live_status or {}).get("status") or "").strip().lower()
                if not normalized_status:
                    normalized_status = "won" if pnl > 0 else ("lost" if pnl < 0 else "sold")

                if contract_id in self._get_runtime_active_contract_ids():
                    try:
                        self.risk_manager.record_trade_close(contract_id, pnl, normalized_status)
                    except Exception as close_error:
                        logger.warning(
                            "[%s][SYSTEM] Failed runtime close reconciliation for %s: %s",
                            self._get_strategy_name(),
                            contract_id,
                            close_error,
                        )

                reconciled = dict(persisted_trade)
                if isinstance(live_status, dict):
                    reconciled.update(live_status)
                reconciled["contract_id"] = contract_id
                reconciled["symbol"] = reconciled.get("symbol") or persisted_trade.get("symbol")
                reconciled["signal"] = (
                    reconciled.get("signal")
                    or persisted_trade.get("signal")
                    or persisted_trade.get("direction")
                )
                reconciled["direction"] = reconciled.get("direction") or reconciled.get("signal")
                reconciled["profit"] = pnl
                reconciled["status"] = normalized_status
                reconciled["strategy_type"] = self._get_strategy_name()
                reconciled["entry_price"] = (
                    reconciled.get("entry_price")
                    or persisted_trade.get("entry_price")
                    or persisted_trade.get("entry_spot")
                )
                if reconciled.get("exit_price") is None:
                    reconciled["exit_price"] = (
                        (live_status or {}).get("current_spot")
                        or (live_status or {}).get("bid_price")
                    )

                sell_time = (live_status or {}).get("sell_time")
                if sell_time not in (None, ""):
                    try:
                        reconciled["timestamp"] = datetime.fromtimestamp(int(sell_time))
                    except Exception:
                        reconciled["timestamp"] = datetime.now()
                else:
                    reconciled["timestamp"] = datetime.now()

                saved_row = UserTradesService.save_trade(self.account_id, reconciled)
                if saved_row:
                    settled_count += 1
                    self.state.update_trade(contract_id, reconciled)
                    logger.info(
                        "[%s][SYSTEM] Reconciled stale open trade to closed: %s (%s, P&L %.2f)",
                        self._get_strategy_name(),
                        contract_id,
                        normalized_status,
                        pnl,
                    )
                    try:
                        result_for_notify = dict(reconciled)
                        result_for_notify.setdefault("contract_id", contract_id)
                        result_for_notify.setdefault("symbol", persisted_trade.get("symbol"))
                        result_for_notify.setdefault("user_id", self.account_id)
                        result_for_notify.setdefault("strategy_type", self._get_strategy_name())
                        result_for_notify.setdefault(
                            "execution_reason",
                            (
                                persisted_trade.get("execution_reason")
                                or "Trade tracking restored after restart; broker reported contract already closed"
                            ),
                        )
                        await self.telegram_bridge.notify_trade_closed(
                            result_for_notify,
                            pnl,
                            normalized_status,
                            strategy_type=self._get_strategy_name(),
                        )
                    except Exception as notify_error:
                        logger.warning(
                            "[%s][SYSTEM] Failed to send startup-close Telegram notification for %s: %s",
                            self._get_strategy_name(),
                            contract_id,
                            notify_error,
                        )
                continue

            if self._restore_trade_for_monitoring(persisted_trade):
                recovered_count += 1

        if recovered_count or settled_count:
            logger.info(
                "[%s][SYSTEM] Startup active-trade reconciliation complete: recovered=%s, settled=%s",
                self._get_strategy_name(),
                recovered_count,
                settled_count,
            )

    def _cycle_step(
        self,
        symbol: str,
        step: int,
        total_steps: int,
        message: str,
        emoji: str = "\u2139\ufe0f",
        level: str = "info",
    ) -> None:
        """Rise/Fall-style lifecycle log line for multiplier strategies."""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        strategy = self._get_strategy_name()
        line = f"[{strategy}][{symbol}] STEP {step}/{total_steps} | {ts} | {emoji} {message}"
        getattr(logger, level)(line)

    def _should_emit_decision(
        self, key: str, fingerprint: str, min_interval_seconds: int = 20
    ) -> bool:
        """Throttle repeated decision events for cleaner frontend timelines."""
        now = datetime.now()
        last = self._decision_log_state.get(key)
        if not last:
            self._decision_log_state[key] = {"fingerprint": fingerprint, "time": now}
            return True

        last_fingerprint = last.get("fingerprint")
        last_time = last.get("time", datetime.min)
        elapsed = (now - last_time).total_seconds()

        if fingerprint != last_fingerprint or elapsed >= min_interval_seconds:
            self._decision_log_state[key] = {"fingerprint": fingerprint, "time": now}
            return True

        return False

    async def _broadcast_decision(
        self,
        symbol: str,
        phase: str,
        decision: str,
        reason: Optional[str] = None,
        details: Optional[Dict] = None,
        severity: str = "info",
        throttle_key: Optional[str] = None,
        min_interval_seconds: int = 20,
    ) -> None:
        """
        Broadcast structured bot decision events for frontend consumption.
        """
        fingerprint = f"{phase}|{decision}|{reason or ''}"
        if throttle_key and not self._should_emit_decision(
            throttle_key, fingerprint, min_interval_seconds=min_interval_seconds
        ):
            return

        payload = {
            "type": "bot_decision",
            "bot": "multiplier",
            "strategy": self._get_strategy_name(),
            "symbol": symbol,
            "phase": phase,
            "decision": decision,
            "severity": severity,
            "message": reason or decision.replace("_", " "),
            "timestamp": datetime.now().isoformat(),
            "account_id": self.account_id,
        }
        if reason:
            payload["reason"] = reason
        if details:
            payload["details"] = details

        try:
            await event_manager.broadcast(payload)
        except Exception as e:
            logger.debug(f"Decision event broadcast skipped due to error: {e}")
    
    @with_user_context
    async def start_bot(
        self,
        api_token: Optional[str] = None,
        stake: Optional[float] = None,
        strategy_name: Optional[str] = None,
        auto_execute_signals: Optional[bool] = None,
    ) -> dict:
        """
        Start the trading bot
        Returns status dict
        """
        if self.is_running:
            return {
                "success": False,
                "message": "Bot is already running",
                "status": self.status.value
            }
        
        # Update token if provided
        if api_token:
            self.api_token = api_token

        # Update User Settings
        if stake:
            self.user_stake = stake
        # Ensure fallback if user_stake is still None (though main.py sends default)
        
        if strategy_name:
            self.active_strategy = strategy_name
        if auto_execute_signals is not None:
            self.set_auto_execute_signals(bool(auto_execute_signals))

        # Ensure runner scope and logging context reflect active strategy.
        self._sync_strategy_scope()
        self.active_strategy = self._get_strategy_name()
        
        # STRICT ENFORCEMENT: User Stake Must Be Present
        if self.user_stake is None:
            return {
                "success": False,
                "message": "Start failed: Stake amount not configured. Please set your stake in Settings.",
                "status": self.status.value
            }
            
        current_stake = self.user_stake
        
        # Risk settings will be applied in _run_bot after components are initialized
        # (self.risk_manager is None here until _run_bot starts)
            
        try:
            self._cycle_step(
                "SYSTEM",
                1,
                6,
                f"Startup requested for {self.account_id or 'default user'}",
                emoji="\U0001F680",
            )
            logger.info(f"[{self._get_strategy_name()}][SYSTEM] \U0001F4DA Symbols: {', '.join(self.symbols)}")
            logger.info(f"[{self._get_strategy_name()}][SYSTEM] \U0001F4B5 Stake: ${current_stake}")
            logger.info(
                "[%s][SYSTEM] %s",
                self._get_strategy_name(),
                "Auto signal execution enabled" if self.auto_execute_signals else "Manual signal mode enabled",
            )
            self.status = BotStatus.STARTING
            self.error_message = None
            self.state.update_status("starting")
            
            # Load historical trades from DB
            try:
                history = UserTradesService.get_user_trades(self.account_id, limit=100)
                if history:
                    # Update state with history 
                    # Note: We need to adapt the format slightly if needed, but BotState expects dicts
                    # We might want to populate stats based on this history too
                    self.state.trade_history = history
                    logger.info(f"[{self._get_strategy_name()}][SYSTEM] \U0001F5C2\ufe0f Loaded {len(history)} historical trades")
            except Exception as e:
                logger.warning(f"[{self._get_strategy_name()}][SYSTEM] \u26A0\ufe0f Failed to load history: {e}")

            # Create bot task
            self.task = asyncio.create_task(self._run_bot())
            
            # Wait for bot to fully initialize (network handshakes can exceed 10s).
            max_wait = max(int(getattr(config, "BOT_STARTUP_TIMEOUT_SECONDS", 25)), 5)
            for _ in range(max_wait):
                await asyncio.sleep(1)
                
                if self.is_running:
                    self._cycle_step("SYSTEM", 6, 6, "Bot started successfully", emoji="\u2705")
                    await event_manager.broadcast({
                        "type": "bot_status",
                        "status": "running",
                        "message": f"Multi-asset bot started - scanning {len(self.symbols)} symbols",
                        "symbols": self.symbols,
                        "account_id": self.account_id
                    })
                    
                    return {
                        "success": True,
                        "message": f"Bot started - scanning {len(self.symbols)} symbols",
                        "status": self.status.value,
                        "symbols": self.symbols
                    }
                
                if self.status == BotStatus.ERROR:
                    error_msg = self.error_message or "Bot initialization failed"
                    raise Exception(error_msg)

                if self.task and self.task.done():
                    task_error = None
                    try:
                        task_error = self.task.exception()
                    except asyncio.CancelledError:
                        task_error = asyncio.CancelledError()

                    if task_error:
                        raise Exception(f"Bot startup task failed: {task_error}")

                    raise Exception(self.error_message or "Bot startup task exited before running")
            
            raise Exception(f"Bot startup timeout ({max_wait}s)")
                
        except Exception as e:
            self._cycle_step("SYSTEM", 6, 6, f"Startup failed: {e}", emoji="\u274C", level="error")
            self.status = BotStatus.ERROR
            self.error_message = str(e)
            self.state.update_status("error", error=str(e))
            
            if self.task and not self.task.done():
                self.task.cancel()
            
            # Only notify telegram if this is the main/default bot or configured for it
            # For now, suppressing per-user telegram errors to avoid spam in admin channel
            # unless a bridge is configured per user.
            
            return {
                "success": False,
                "message": f"Failed to start bot: {e}",
                "status": self.status.value
            }
    
    @with_user_context
    async def stop_bot(self) -> dict:
        """
        Stop the trading bot gracefully
        Returns status dict
        """
        if not self.is_running:
            return {
                "success": False,
                "message": "Bot is not running",
                "status": self.status.value
            }
        
        try:
            self._cycle_step("SYSTEM", 1, 3, "Stop requested", emoji="\U0001F6D1")
            self.status = BotStatus.STOPPING
            self.state.update_status("stopping")
            
            # Cancel the bot task
            if self.task:
                self.task.cancel()
                try:
                    await self.task
                except asyncio.CancelledError:
                    pass
            
            # Disconnect bot components
            if self.data_fetcher:
                await self.data_fetcher.disconnect()
            if self.trade_engine:
                await self.trade_engine.disconnect()
            
            self.is_running = False
            self.status = BotStatus.STOPPED
            self.task = None
            self.start_time = None
            
            self.task = None
            self.start_time = None
            
            self.state.update_status("stopped")
            self._cycle_step("SYSTEM", 3, 3, "Bot stopped successfully", emoji="\u2705")
            
            # Notify Telegram with stats
            try:
                stats = self.state.get_statistics()
                stats['scan_summary'] = {
                    'total_scans': self.scan_count,
                    'signals_by_symbol': self.signals_by_symbol
                }
                await self.telegram_bridge.notify_bot_stopped(stats)
            except:
                pass
            
            await event_manager.broadcast({
                "type": "bot_status",
                "status": "stopped",
                "message": "Multi-asset bot stopped successfully",
                "account_id": self.account_id
            })
            
            return {
                "success": True,
                "message": "Bot stopped successfully",
                "status": self.status.value
            }
            
        except Exception as e:
            self._cycle_step("SYSTEM", 3, 3, f"Stop failed: {e}", emoji="\u274C", level="error")
            return {
                "success": False,
                "message": f"Error stopping bot: {e}",
                "status": self.status.value
            }
    
    async def restart_bot(self) -> dict:
        """
        Restart the trading bot
        Returns status dict
        """
        logger.info(f"[{self._get_strategy_name()}][SYSTEM] \u267B\ufe0f Restart requested")
        
        if self.is_running:
            stop_result = await self.stop_bot()
            if not stop_result["success"]:
                return stop_result
            
            await asyncio.sleep(3)
        
        return await self.start_bot()
    
    def get_status(self) -> dict:
        """Get current bot status with multi-asset info"""
        uptime = None
        if self.start_time:
            uptime = int((datetime.now() - self.start_time).total_seconds())
        
        # Get active trade info from risk manager
        active_trade_info = None
        if self.risk_manager and self.risk_manager.active_trades:
            # For status display, just show the first one or latest
            # Ideally we return all, but for backward compatibility, let's see.
            # RiskManager.get_active_trade_info() also needs fixing.
            # For now, let's call it and assume I fix it to use active_trades[0]
            active_trade_info = self.risk_manager.get_active_trade_info()
        
        return {
            "status": self.status.value,
            "is_running": self.is_running,
            "active_strategy": self._get_strategy_name(),
            "uptime_seconds": uptime,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "error_message": self.error_message,
            "error_message": self.error_message,
            "balance": self.state.balance,
            "active_trades": self.state.active_trades,
            "active_trades_count": len(self.state.active_trades),
            "active_trades": self.state.active_trades,
            "active_trades_count": len(self.state.active_trades),
            "statistics": self.state.get_statistics(),
            "config": {
                "stake": self.user_stake if self.user_stake else config.FIXED_STAKE,
                "strategy": self._get_strategy_name(),
                "auto_execute_signals": self.auto_execute_signals,
            },
            "multi_asset": {
                "symbols": self.symbols,
                "scan_count": self.scan_count,
                "active_symbol": active_trade_info['symbol'] if active_trade_info else None,
                "signals_by_symbol": self.signals_by_symbol,
                "errors_by_symbol": self.errors_by_symbol,
                "scalping_gate_metrics": self.get_scalping_gate_metrics() if self._is_scalping_strategy() else None,
            }
        }
    
    @with_user_context
    async def _run_bot(self):
        """
        Main bot loop - Multi-asset parallel scanner
        Continuously scans all symbols looking for first qualifying signal
        """
        try:
            self._cycle_step("SYSTEM", 1, 6, "Main loop starting", emoji="\U0001F504")
            
            # Initialize components with dynamic token
            token_to_use = self.api_token
            
            if not token_to_use:
                 error_msg = f"User {self.account_id} has no API token configured"
                 logger.error(error_msg)
                 raise ValueError(error_msg)
            
            # Initialize bot components
            try:
                self.data_fetcher = DataFetcher(
                    token_to_use,
                    config.DERIV_APP_ID
                )
                
                self.trade_engine = TradeEngine(
                    token_to_use,
                    config.DERIV_APP_ID,
                    risk_mode=(self._get_strategy_name() or "Conservative").strip().upper(),
                )
                
                # Only initialize risk_manager if not already injected
                if self.risk_manager is None:
                    self.risk_manager = self._init_risk_manager_for_strategy()
                
                # Set bot state for risk manager
                if hasattr(self.risk_manager, 'set_bot_state'):
                    self.risk_manager.set_bot_state(self.state)
                
                # Apply user stake if provided
                if self.user_stake:
                    if hasattr(self.risk_manager, 'update_risk_settings'):
                        self.risk_manager.update_risk_settings(self.user_stake)
                    if hasattr(self.risk_manager, 'stake'):
                        self.risk_manager.stake = self.user_stake
                    logger.info(f"[{self._get_strategy_name()}][SYSTEM] \U0001F6E1\ufe0f Risk limits updated for stake: ${self.user_stake}")
                
                self._cycle_step("SYSTEM", 2, 6, "Components initialized", emoji="\U0001F9E9")
            except Exception as e:
                self.status = BotStatus.ERROR
                self.error_message = f"Component initialization failed: {e}"
                self._cycle_step("SYSTEM", 2, 6, self.error_message, emoji="\u274C", level="error")
                return
            
            # Connect to Deriv API
            try:
                self._cycle_step("SYSTEM", 3, 6, "Connecting DataFetcher", emoji="\U0001F50C")
                data_connected = await self.data_fetcher.connect()
                if not data_connected:
                    reason = self.data_fetcher.last_error or "Unknown connection error"
                    raise Exception(f"DataFetcher failed to connect: {reason}")
                
                self._cycle_step("SYSTEM", 4, 6, "Connecting TradeEngine", emoji="\U0001F50C")
                trade_connected = await self.trade_engine.connect()
                if not trade_connected:
                    raise Exception("TradeEngine failed to connect (check logs for details)")
                
                self._cycle_step("SYSTEM", 4, 6, "Connected to Deriv API", emoji="\u2705")
            except Exception as e:
                self.status = BotStatus.ERROR
                self.error_message = f"Deriv API connection failed: {e}"
                self._cycle_step("SYSTEM", 4, 6, self.error_message, emoji="\u274C", level="error")
                return
            
            # Check for existing positions on startup
            try:
                has_existing = await self.risk_manager.check_for_existing_positions(self.trade_engine)
                if has_existing:
                    logger.warning(f"[{self._get_strategy_name()}][SYSTEM] \U0001F512 Existing position detected on startup")
            except Exception as e:
                logger.warning(f"[{self._get_strategy_name()}][SYSTEM] \u26A0\ufe0f Existing-position check failed: {e}")

            # Reconcile persisted open trades so restart resumes monitoring
            # and stale DB rows are closed when broker already settled them.
            try:
                await self._reconcile_active_trades_on_startup()
            except Exception as e:
                logger.warning(f"[{self._get_strategy_name()}][SYSTEM] \u26A0\ufe0f Active-trade reconciliation failed: {e}")
            
            # Get initial balance
            try:
                balance = await self.data_fetcher.get_balance()
                if balance:
                    self.state.update_balance(balance)
                    logger.info(f"[{self._get_strategy_name()}][SYSTEM] \U0001F4B0 Initial balance: ${balance:.2f}")
            except Exception as e:
                logger.warning(f"[{self._get_strategy_name()}][SYSTEM] \u26A0\ufe0f Initial balance fetch failed: {e}")
                balance = 0.0
            
            # Mark as running
            self.is_running = True
            self.status = BotStatus.RUNNING
            self.start_time = datetime.now()
            self.error_message = None
            self.state.update_status("running")
            
            self._cycle_step("SYSTEM", 5, 6, "Bot is now running", emoji="\u2705")
            logger.info(f"[{self._get_strategy_name()}][SYSTEM] \U0001F50E Scanning {len(self.symbols)} symbols per cycle")
            
            # Notify Telegram
            try:
                await self.telegram_bridge.notify_bot_started(
                    balance or 0.0,
                    self.user_stake,
                    self._get_strategy_name(),
                )
            except Exception as e:
                logger.warning(f"[{self._get_strategy_name()}][SYSTEM] \u26A0\ufe0f Telegram notification failed: {e}")
            
            # Broadcast to WebSockets
            await event_manager.broadcast({
                "type": "bot_status",
                "status": "running",
                "account_id": self.account_id,
                "message": f"Multi-asset bot started - scanning {len(self.symbols)} symbols",
                "balance": balance,
                "symbols": self.symbols
            })
            
            # Broadcast initial statistics
            initial_stats = self.state.get_statistics()
            await event_manager.broadcast({
                "type": "statistics",
                "stats": initial_stats,
                "strategy": self._get_strategy_name(),
                "timestamp": datetime.now().isoformat(),
                "account_id": self.account_id
            })
            
            # Main trading loop - MULTI-ASSET SEQUENTIAL SCANNER
            while self.is_running:
                try:
                    self.scan_count += 1
                    
                    # Execute multi-asset scan cycle
                    await self._multi_asset_scan_cycle()
                    
                    # Determine wait time based on risk manager state
                    cooldown = self.risk_manager.get_cooldown_remaining()
                    
                    # If actively monitoring a trade, check more frequently
                    if self.risk_manager.active_trades:
                        # Do NOT reuse entry cooldown here; active-trade protection
                        # (e.g., breakeven trailing exits) must run on a fixed fast cadence.
                        wait_time = max(int(getattr(config, "ACTIVE_TRADE_MONITOR_INTERVAL_SECONDS", 1)), 1)
                        logger.debug(f"[{self._get_strategy_name()}][SYSTEM] \u23F1\ufe0f Active trade monitor in {wait_time}s")
                    else:
                        wait_time = max(cooldown, 30)  # Standard 30s cycle when scanning
                        logger.debug(f"[{self._get_strategy_name()}][SYSTEM] \u23F1\ufe0f Next scan in {wait_time}s")
                    
                    # Sleep with cancellation check
                    for _ in range(int(wait_time)):
                        if not self.is_running:
                            break
                        await asyncio.sleep(1)
                    
                except asyncio.CancelledError:
                    logger.info(f"[{self._get_strategy_name()}][SYSTEM] \U0001F6D1 Bot loop cancelled")
                    break
                except Exception as e:
                    logger.error(f"[{self._get_strategy_name()}][SYSTEM] \u274C Scan cycle error: {e}")
                    
                    try:
                        await self.telegram_bridge.notify_error(str(e))
                    except:
                        pass
                    
                    await event_manager.broadcast({
                        "type": "error",
                        "message": str(e),
                        "timestamp": datetime.now().isoformat(),
                        "account_id": self.account_id
                    })
                    await asyncio.sleep(30)
            
        except asyncio.CancelledError:
            logger.info(f"[{self._get_strategy_name()}][SYSTEM] \U0001F6D1 Bot task cancelled")
        except Exception as e:
            logger.error(f"Fatal error in bot: {e}")
            self.status = BotStatus.ERROR
            self.error_message = str(e)
            self.state.update_status("error", error=str(e))
            
            try:
                await self.telegram_bridge.notify_error(f"Fatal error: {e}")
            except:
                pass
            
            await event_manager.broadcast({
                "type": "error",
                "message": f"Fatal error: {e}",
                "timestamp": datetime.now().isoformat(),
                "account_id": self.account_id
            })
        finally:
            self.is_running = False
            self._cycle_step("SYSTEM", 6, 6, "Main loop exited", emoji="\U0001F3C1")
    
    async def _multi_asset_scan_cycle(self):
        """
        CRITICAL: Multi-Asset Parallel Scanner
        
        Process:
        1. Check global trade permission (1-trade limit)
        2. If position active -> monitor only (skip scanning)
        3. If no position -> scan all symbols in parallel
        4. First qualifying signal -> execute and lock system
        5. All other symbols blocked until trade closes
        """
        
        # Recover persisted opens that may have missed runtime registration.
        self._recover_runtime_active_trades()

        # Step 1: Check global permission
        self._cycle_signal_claimed = False
        self._cycle_winner_symbol = None
        can_trade_global, reason = self.risk_manager.can_trade()
        
        # If we have an active trade, monitor it instead of scanning
        if self.risk_manager.active_trades:
            now = datetime.now()
            monitor_log_key = "system:active_trade_monitor_mode"
            last_marker = self.last_status_log.get(monitor_log_key, {"time": datetime.min})
            if (now - last_marker.get("time", datetime.min)).total_seconds() >= 15:
                logger.info(
                    "[%s][SYSTEM] Active trade(s) detected (%s) - monitoring exits while new entries remain gated",
                    self._get_strategy_name(),
                    len(self.risk_manager.active_trades),
                )
                self.last_status_log[monitor_log_key] = {
                    "msg": "active_trade_monitor_mode",
                    "time": now,
                }
            await self._broadcast_decision(
                symbol="SYSTEM",
                phase="scan",
                decision="no_trade",
                reason="Active trade is being monitored",
                details={"active_trades": len(self.risk_manager.active_trades)},
                throttle_key="scan:active_trade",
            )
            await self._monitor_active_trade()
            return
        
        if not can_trade_global:
            logger.info(f"[{self._get_strategy_name()}][SYSTEM] \u23F8\ufe0f Global trading paused: {reason}")
            await self._broadcast_decision(
                symbol="SYSTEM",
                phase="risk",
                decision="no_trade",
                reason=reason,
                details={"scope": "global_gate"},
                severity="warning",
                throttle_key="scan:global_gate",
            )
            return
        
        # Step 2: Parallel symbol scanning
        logger.info(
            f"[{self._get_strategy_name()}][SYSTEM] \U0001F50E CYCLE #{self.scan_count} | "
            f"Checking {len(self.symbols)} symbols"
        )
        logger.info(f"[{self._get_strategy_name()}][SYSTEM] \U0001F50D Scanning symbols for entry signals")

        async def _analyze_symbol_safe(symbol: str) -> bool:
            # Check if we can still trade (might have changed during loop)
            can_trade_now, _ = self.risk_manager.can_trade(symbol)
            if not can_trade_now:
                logger.debug(
                    f"[{self._get_strategy_name()}][{symbol}] \u26D4 Global state changed, skipping symbol"
                )
                return False

            try:
                return await self._analyze_symbol(symbol)
            except Exception as e:
                logger.error(
                    f"[{self._get_strategy_name()}][{symbol}] \u274C Symbol analysis failed: "
                    f"{type(e).__name__}: {e}",
                    exc_info=True,
                )
                self.errors_by_symbol[symbol] = self.errors_by_symbol.get(symbol, 0) + 1

                if self.errors_by_symbol[symbol] >= 5:
                    try:
                        await self.telegram_bridge.notify_error(
                            f"Multiple errors for {symbol}: {e}"
                        )
                    except Exception:
                        pass
                return False

        tasks = [asyncio.create_task(_analyze_symbol_safe(symbol)) for symbol in self.symbols]
        if tasks:
            results = await asyncio.gather(*tasks)
            winners = [symbol for symbol, ok in zip(self.symbols, results) if ok]
            if winners:
                winner = winners[0]
                logger.info(
                    f"[{self._get_strategy_name()}][{winner}] \U0001F3C1 First qualifying signal won this cycle"
                )
                logger.info(
                    f"[{self._get_strategy_name()}][SYSTEM] \U0001F512 Other symbols blocked until closure"
                )
        
        logger.debug(f"[{self._get_strategy_name()}][SYSTEM] \u2705 Scan cycle complete")
    
    async def _analyze_symbol(self, symbol: str) -> bool:
        """
        Analyze single symbol for entry signal
        
        Phase 1: Directional Bias (1w, 1d, 4h)
        Phase 2: Level Classification (1h, 5m)
        Phase 3: Entry Execution (1m Momentum + Retest)
        
        Returns:
            True if trade executed, False if no signal
        """
        self._cycle_step(symbol, 1, 6, "Fetching multi-timeframe data", emoji="\U0001F4E5")
        
        # Fetch multi-timeframe data for this symbol
        # Fetch multi-timeframe data for this symbol
        try:
            market_data = await self.data_fetcher.fetch_all_timeframes(symbol)
            
            # Validate we have all required timeframes
            required_timeframes = ['1m', '5m', '1h', '4h', '1d', '1w']  # Full Top-Down requirement
            if not all(tf in market_data for tf in required_timeframes):
                missing_tfs = [tf for tf in required_timeframes if tf not in market_data]
                self._cycle_step(
                    symbol,
                    1,
                    6,
                    f"Missing required timeframes: {', '.join(missing_tfs)}",
                    emoji="\u26A0\ufe0f",
                    level="warning",
                )
                await self._broadcast_decision(
                    symbol=symbol,
                    phase="data",
                    decision="no_trade",
                    reason="Missing required timeframes",
                    details={"missing_timeframes": missing_tfs},
                    severity="warning",
                    throttle_key=f"{symbol}:missing_timeframes",
                )
                return False
            
        except Exception as e:
            self._cycle_step(symbol, 1, 6, f"Data fetch failed: {e}", emoji="\u274C", level="error")
            raise  # Re-raise to be caught by caller
        
        # Extract timeframe data
        data_1m = market_data.get('1m')
        data_5m = market_data.get('5m')
        data_1h = market_data.get('1h')
        data_4h = market_data.get('4h')
        data_1d = market_data.get('1d')
        data_1w = market_data.get('1w')
        
        # Execute strategy analysis using injected strategy
        try:
            self._cycle_step(symbol, 2, 6, "Running strategy analysis", emoji="\U0001F9E0")
            # Get required timeframes for this strategy
            required_tfs = self.strategy.get_required_timeframes()
            
            # Build kwargs for strategy analyze method
            strategy_kwargs = {}
            for tf in required_tfs:
                strategy_kwargs[f'data_{tf.replace("m", "m").replace("h", "h").replace("d", "d").replace("w", "w")}'] = market_data.get(tf)
            strategy_kwargs['symbol'] = symbol
            
            # Call strategy analyze method
            signal = self.strategy.analyze(**strategy_kwargs)
            self._record_scalping_strategy_outcome(signal)

        except Exception as e:
            self._cycle_step(symbol, 2, 6, f"Strategy analysis failed: {e}", emoji="\u274C", level="error")
            raise

        if not isinstance(signal, dict):
            self._cycle_step(symbol, 3, 6, "No trade setup: Invalid strategy response", emoji="\u23ED\ufe0f")
            await self._broadcast_decision(
                symbol=symbol,
                phase="signal",
                decision="no_trade",
                reason="Invalid strategy response",
                throttle_key=f"{symbol}:invalid_strategy_payload",
            )
            return False
        
        if not signal.get('can_trade'):
            details = signal.get('details', {})
            reason = details.get('reason', 'Unknown')
            passed_checks = details.get('passed_checks', [])
            
            # Format reason with checks
            if passed_checks:
                checks_str = ", ".join(passed_checks)
                full_reason = f"{reason} (Checks Passed: {checks_str})"
            else:
                full_reason = reason
            
            # Smart Logging: Only log if reason changed or > 60s passed to avoid spam
            now = datetime.now()
            last_log = self.last_status_log.get(symbol, {'msg': '', 'time': datetime.min})
            
            should_log = False
            if full_reason != last_log['msg']:
                should_log = True
            elif (now - last_log['time']).total_seconds() > 60:
                should_log = True
                
            if should_log:
                self._cycle_step(symbol, 3, 6, f"No trade setup: {full_reason}", emoji="\u23ED\ufe0f")
                self.last_status_log[symbol] = {'msg': full_reason, 'time': now}
                await self._broadcast_decision(
                    symbol=symbol,
                    phase="signal",
                    decision="no_trade",
                    reason=full_reason,
                    details={"passed_checks": passed_checks},
                    throttle_key=f"{symbol}:signal_skip",
                )
            else:
                # Debug only for spammy updates
                logger.debug(f"[{self._get_strategy_name()}][{symbol}] \u23ED\ufe0f No signal: {full_reason}")
                
            return False
        
        # Parallel scans can detect multiple opportunities at once.
        # Only one symbol is allowed to claim the cycle for signal+execution.
        async with self._cycle_claim_mutex:
            if self._cycle_signal_claimed and self._cycle_winner_symbol != symbol:
                winner = self._cycle_winner_symbol or "unknown"
                self._cycle_step(
                    symbol,
                    3,
                    6,
                    f"Signal skipped: cycle already claimed by {winner}",
                    emoji="\u23ED\ufe0f",
                )
                await self._broadcast_decision(
                    symbol=symbol,
                    phase="signal",
                    decision="no_trade",
                    reason=f"Cycle already claimed by {winner}",
                    details={"gate": "cycle_winner_claimed", "winner": winner},
                    throttle_key=f"{symbol}:cycle_claimed",
                )
                return False

            if not self._cycle_signal_claimed:
                self._cycle_signal_claimed = True
                self._cycle_winner_symbol = symbol

        # We have a signal! Log it
        checks_passed = ", ".join(signal.get('details', {}).get('passed_checks', []))
        direction_emoji = "\U0001F7E2" if str(signal.get("signal", "")).upper() in {"BUY", "UP"} else "\U0001F534"
        self._cycle_step(
            symbol,
            3,
            6,
            f"Signal {signal['signal']} {direction_emoji} | Score {signal.get('score', 0):.2f} | Conf {signal.get('confidence', 0):.0f}%",
            emoji="\U0001F3AF",
        )
        logger.debug(f"   Checks: {checks_passed}")
        await self._broadcast_decision(
            symbol=symbol,
            phase="signal",
            decision="opportunity_detected",
            reason="All strategy checks aligned",
            details={
                "direction": signal.get("signal"),
                "score": signal.get("score", 0),
                "confidence": signal.get("confidence", 0),
                "checks_passed": signal.get("details", {}).get("passed_checks", []),
            },
            min_interval_seconds=0,
        )
        
        # Track signal
        self.signals_by_symbol[symbol] = self.signals_by_symbol.get(symbol, 0) + 1

        execution_mode = "auto" if self.auto_execute_signals else "manual"
        action_taken = "Trade Executed" if self.auto_execute_signals else "Awaiting Manual Entry"

        # Broadcast signal to WebSockets
        timestamp = datetime.now().isoformat()
        signal['timestamp'] = timestamp # CRITICAL: Track signal time for result linking
        signal['symbol'] = symbol
        signal['execution_mode'] = execution_mode
        signal['action_taken'] = action_taken
        signal['auto_execute_signals'] = self.auto_execute_signals
        signal['execution_reason'] = (
            "Manual mode active - awaiting manual entry"
            if not self.auto_execute_signals
            else "Strategy signal detected - proceeding with auto execution pipeline"
        )

        await event_manager.broadcast({
            "type": "signal",
            "symbol": symbol,
            "signal": signal['signal'],
            "score": signal.get('score', 0),
            "confidence": signal.get('confidence', 0),
            "execution_mode": execution_mode,
            "action_taken": action_taken,
            "auto_execute_signals": self.auto_execute_signals,
            "timestamp": timestamp,
            "account_id": self.account_id
        })
        
        # Record signal in state
        self.state.add_signal(signal)

        # Notify Telegram on every detected signal, regardless of execution mode.
        try:
            signal_for_notify = signal.copy()
            signal_for_notify['strategy_type'] = self._get_strategy_name()
            signal_for_notify['user_id'] = self.account_id
            if self.user_stake is not None:
                signal_for_notify['stake'] = self.user_stake
            await self.telegram_bridge.notify_signal(signal_for_notify)
        except Exception as e:
            logger.warning(f"[{self._get_strategy_name()}][SYSTEM] ⚠️ Signal Telegram notification failed: {e}")

        if not self.auto_execute_signals:
            self._cycle_step(
                symbol,
                4,
                6,
                "Manual mode active: signal sent, waiting for manual entry",
                emoji="🖐️",
                level="info",
            )
            await self._broadcast_decision(
                symbol=symbol,
                phase="execution",
                decision="no_trade",
                reason="Manual mode active - auto execution disabled",
                details={
                    "execution_mode": execution_mode,
                    "action_taken": action_taken,
                },
                min_interval_seconds=0,
            )
            await event_manager.broadcast({
                "type": "notification",
                "level": "info",
                "title": "Signal Ready (Manual Entry)",
                "message": f"{symbol} {signal.get('signal')} detected. Open manually then use Sync to track.",
                "timestamp": datetime.now().isoformat(),
                "account_id": self.account_id,
            })
            return False
        
        # Get symbol-specific configuration
        multiplier = self.asset_config.get(symbol, {}).get('multiplier')
        
        if not multiplier:
            self._cycle_step(symbol, 4, 6, "Missing multiplier in asset config", emoji="\u274C", level="error")
            await self._broadcast_decision(
                symbol=symbol,
                phase="risk",
                decision="no_trade",
                reason="Missing multiplier configuration",
                severity="error",
                throttle_key=f"{symbol}:missing_multiplier",
            )
            return False

        # Determine Stake (User Preference)
        base_stake = self.user_stake
        if base_stake is None:
             # Should not happen due to start_bot check, but safety first
             self._cycle_step(symbol, 4, 6, "Stake not configured", emoji="\u274C", level="error")
             await self._broadcast_decision(
                 symbol=symbol,
                 phase="risk",
                 decision="no_trade",
                 reason="Stake not configured",
                 severity="error",
                 throttle_key=f"{symbol}:stake_missing",
             )
             return False
             
        # CRITICAL FIX: Do NOT multiply by multiplier. 
        # The stake passed to Deriv API (amount) is the user's risk amount (cost), 
        # not the total exposure.
        stake = base_stake

        # Debug: Log signal structure before validation
        logger.debug(f"Signal structure - Entry: {signal.get('entry_price')}, TP: {signal.get('take_profit')}, SL: {signal.get('stop_loss')}")
        
        # CRITICAL FIX: Add symbol to signal before validation
        signal_for_validation = signal.copy()
        signal_for_validation['symbol'] = symbol

        # Parallel scans can detect multiple opportunities at once.
        # Ensure only one symbol can enter trade execution path at a time.
        if self._execution_mutex.locked():
            self._cycle_step(
                symbol,
                4,
                6,
                "Execution slot already claimed by another symbol",
                emoji="\u23ED\ufe0f",
            )
            await self._broadcast_decision(
                symbol=symbol,
                phase="risk",
                decision="no_trade",
                reason="Another symbol already started trade execution",
                details={"gate": "execution_slot_busy"},
                throttle_key=f"{symbol}:execution_slot_busy",
            )
            return False

        await self._execution_mutex.acquire()
        try:
            # Validate with risk manager (including global checks)
            can_open, validation_msg = self.risk_manager.can_open_trade(
                symbol=symbol,
                stake=stake,
                take_profit=signal.get('take_profit'),
                stop_loss=signal.get('stop_loss'),
                signal_dict=signal_for_validation
            )
            
            if not can_open:
                self._cycle_step(symbol, 4, 6, f"Risk gate blocked trade: {validation_msg}", emoji="\U0001F6D1", level="warning")
                await self._broadcast_decision(
                    symbol=symbol,
                    phase="risk",
                    decision="no_trade",
                    reason=validation_msg,
                    details={"gate": "can_open_trade"},
                    severity="warning",
                    throttle_key=f"{symbol}:trade_blocked",
                )
                return False
                
            # Build execution payload once.
            # Do not send pre-execution signal alerts here because final
            # proposal-spot RR checks can still reject the entry.
            passed_checks = signal.get("details", {}).get("passed_checks", [])
            if passed_checks:
                execution_reason = f"Checks passed: {', '.join(str(item) for item in passed_checks)}"
            else:
                execution_reason = "All strategy checks aligned and risk gate passed"
            signal_with_symbol = signal.copy()
            signal_with_symbol['symbol'] = symbol
            signal_with_symbol['stake'] = stake
            signal_with_symbol['multiplier'] = multiplier
            signal_with_symbol['strategy_type'] = self._get_strategy_name()
            signal_with_symbol['user_id'] = self.account_id
            signal_with_symbol['execution_reason'] = execution_reason
            
            # Execute trade!
            self._cycle_step(
                symbol,
                5,
                6,
                f"Executing {signal['signal']} | Stake ${stake:.2f} | Multiplier {multiplier}x",
                emoji="\U0001F680",
            )
            await self._broadcast_decision(
                symbol=symbol,
                phase="execution",
                decision="opportunity_taken",
                reason="Risk checks passed, executing trade",
                details={
                    "direction": signal.get("signal"),
                    "stake": stake,
                    "multiplier": multiplier,
                },
                min_interval_seconds=0,
            )
            
            try:
                # Execute trade using TradeEngine
                result = await self.trade_engine.execute_trade(
                    signal_with_symbol, 
                    self.risk_manager
                )
                
                if result:
                    # Trade executed and completed
                    pnl = result.get('profit', 0.0)
                    status = result.get('status', 'unknown')
                    contract_id = result.get('contract_id')
                    
                    result_emoji = "\u2705" if pnl > 0 else ("\u274C" if pnl < 0 else "\u2696\ufe0f")
                    self._cycle_step(
                        symbol,
                        6,
                        6,
                        f"Trade completed: {status} | P&L: ${pnl:.2f} | Contract: {contract_id}",
                        emoji=result_emoji,
                    )

                    # CRITICAL FIX: Add signal to result for DB persistence
                    if 'signal' not in result:
                        result['signal'] = signal_with_symbol['signal']
                    
                    # NEW: Add strategy_type to result for database
                    result['strategy_type'] = self.strategy.get_strategy_name()
                    
                    # Record trade closure
                    self.risk_manager.record_trade_close(contract_id, pnl, status)
                    self.state.update_trade(contract_id, result)


                    # Persist to Supabase with error handling
                    try:
                        saved = UserTradesService.save_trade(self.account_id, result)
                        if saved:
                            logger.info(f"[{self._get_strategy_name()}][{symbol}] \U0001F9FE Trade persisted to DB: {contract_id}")
                        else:
                            logger.error(
                                f"[{self._get_strategy_name()}][{symbol}] \u274C DB persistence failed for contract {contract_id} (no data returned)"
                            )
                            # Notify via Telegram
                            try:
                                await self.telegram_bridge.notify_error(
                                    f"Trade executed but DB save failed: {symbol} {status}"
                                )
                            except:
                                pass
                    except Exception as e:
                        logger.error(f"[{self._get_strategy_name()}][{symbol}] \u274C DB save exception for contract {contract_id}: {e}")
                        import traceback
                        logger.error(traceback.format_exc())
                        # Notify via Telegram
                        try:
                            await self.telegram_bridge.notify_error(
                                f"Trade executed but DB error: {symbol} - {str(e)}"
                            )
                        except:
                            pass

                    
                    # Notify Telegram
                    try:
                        # MERGE complete trade details into result for notification
                        result_for_notify = result.copy()
                        result_for_notify.update(signal_with_symbol) # Contains direction, stake, symbol
                        result_for_notify['strategy_type'] = self._get_strategy_name()
                        result_for_notify['user_id'] = self.account_id
                        result_for_notify.setdefault(
                            'execution_reason',
                            signal_with_symbol.get('execution_reason', 'Signal conditions matched and risk checks passed')
                        )
                        
                        # Ensure symbol is set (sometimes signal uses 'symbol', result uses 'symbol')
                        if 'symbol' not in result_for_notify:
                             result_for_notify['symbol'] = symbol
                        
                        await self.telegram_bridge.notify_trade_closed(result_for_notify, pnl, status, strategy_type=self.strategy.get_strategy_name())
                    except:
                        pass
                    
                    # Broadcast to WebSockets
                    await event_manager.broadcast({
                        "type": "trade_closed",
                        "symbol": symbol,
                        "trade": result,
                        "pnl": pnl,
                        "status": status,
                        "timestamp": datetime.now().isoformat(),
                        "account_id": self.account_id
                    })
                    
                    # Update statistics
                    stats = self.risk_manager.get_statistics()
                    self.state.update_statistics(stats)
                    
                    # CRITICAL: Update signal result and broadcast
                    signal_timestamp = signal_with_symbol.get('timestamp')
                    if signal_timestamp:
                        self.state.update_signal_result(signal_timestamp, status, pnl)
                        
                        await event_manager.broadcast({
                            "type": "signal_updated",
                            "timestamp": signal_timestamp,
                            "result": status,
                            "pnl": pnl,
                            "account_id": self.account_id
                        })

                        # Send UI Notification
                        notification_type = "success" if pnl > 0 else "error" if pnl < 0 else "info"
                        await event_manager.broadcast({
                            "type": "notification",
                            "level": notification_type,
                            "title": f"Trade {status.title()}",
                            "message": f"{symbol} trade closed. P&L: ${pnl:.2f}",
                            "timestamp": datetime.now().isoformat(),
                            "account_id": self.account_id
                        })
                    
                    await event_manager.broadcast({
                        "type": "statistics",
                        "stats": stats,
                        "timestamp": datetime.now().isoformat(),
                        "account_id": self.account_id
                    })
                    
                    return True  # Trade executed
                else:
                    self._cycle_step(symbol, 6, 6, "Trade execution failed (no result)", emoji="\u274C", level="error")
                    await self._broadcast_decision(
                        symbol=symbol,
                        phase="execution",
                        decision="opportunity_failed",
                        reason="Trade engine returned no result",
                        severity="error",
                        min_interval_seconds=0,
                    )
                    return False
                    
            except Exception as e:
                self._cycle_step(symbol, 6, 6, f"Trade execution failed: {type(e).__name__}: {e}", emoji="\u274C", level="error")
                logger.error(
                    f"[{self._get_strategy_name()}][{symbol}] TRADE_EXECUTION_FAILED traceback",
                    exc_info=True,
                )
                await self._broadcast_decision(
                    symbol=symbol,
                    phase="execution",
                    decision="opportunity_failed",
                    reason=f"{type(e).__name__}: {e}",
                    severity="error",
                    min_interval_seconds=0,
                )
                
                try:
                    await self.telegram_bridge.notify_error(f"{symbol} trade failed: {e}")
                except:
                    pass
                
                return False
        finally:
            if self._execution_mutex.locked():
                self._execution_mutex.release()
    
    async def _monitor_active_trade(self):
        """
        Monitor the currently active trade
        This runs when a trade is locked, checking its status
        """
        if not self.risk_manager or not self.trade_engine or not self._has_runtime_active_trade():
            return

        if not hasattr(self.risk_manager, "get_active_trade_info"):
            return

        active_info = self.risk_manager.get_active_trade_info()
        if not isinstance(active_info, dict):
            return

        symbol = str(active_info.get("symbol") or "UNKNOWN")
        contract_id_raw = active_info.get("contract_id")
        if contract_id_raw in (None, ""):
            return
        contract_id = str(contract_id_raw)
        progress_key = f"{self._active_progress_key_prefix}{contract_id}"

        def _to_float(value, default=0.0) -> float:
            try:
                return float(value)
            except Exception:
                return float(default)

        async def _close_trade_with_reason(
            exit_reason: str,
            close_message: str,
            default_execution_reason: str,
        ) -> bool:
            self._cycle_step(
                symbol,
                4,
                6,
                close_message,
                emoji="\u26A0\uFE0F",
                level="warning",
            )
            try:
                sell_result = await self.trade_engine.close_trade(contract_id)
                if not sell_result:
                    return False

                pnl = _to_float(sell_result.get("profit"), current_pnl)
                status = "won" if pnl > 0 else ("lost" if pnl < 0 else "break_even")

                if hasattr(self.risk_manager, "record_trade_close"):
                    self.risk_manager.record_trade_close(contract_id, pnl, status)
                self.state.update_trade(contract_id, sell_result)

                self._cycle_step(
                    symbol,
                    6,
                    6,
                    "Trade closed - system unlocked",
                    emoji="\U0001F513",
                )
                logger.info(f"[{self._get_strategy_name()}][{symbol}] P&L: ${pnl:.2f}")

                try:
                    result_for_db = sell_result.copy()
                    result_for_db.update(active_info)
                    result_for_db["strategy_type"] = self._get_strategy_name()
                    result_for_db["exit_reason"] = exit_reason
                    UserTradesService.save_trade(self.account_id, result_for_db)
                except Exception as save_error:
                    logger.error(
                        f"[{self._get_strategy_name()}][{symbol}] "
                        f"DB save failed for active-trade close: {save_error}"
                    )

                try:
                    result_for_notify = sell_result.copy()
                    result_for_notify.update(active_info)
                    result_for_notify["exit_reason"] = exit_reason
                    result_for_notify["strategy_type"] = self._get_strategy_name()
                    result_for_notify["user_id"] = self.account_id
                    result_for_notify.setdefault("execution_reason", default_execution_reason)
                    await self.telegram_bridge.notify_trade_closed(
                        result_for_notify,
                        pnl,
                        status,
                        strategy_type=self._get_strategy_name(),
                    )
                except Exception:
                    pass

                self.last_status_log.pop(progress_key, None)
                self._active_status_miss_counts.pop(contract_id, None)
                return True
            except Exception as close_error:
                self._cycle_step(
                    symbol,
                    6,
                    6,
                    f"Failed to close active trade: {close_error}",
                    emoji="\u274C",
                    level="error",
                )
                return False

        try:
            trade_status = await self.trade_engine.get_trade_status(contract_id)
            if not trade_status:
                miss_count = self._active_status_miss_counts.get(contract_id, 0) + 1
                self._active_status_miss_counts[contract_id] = miss_count
                miss_log_key = f"{progress_key}:status_miss"
                now = datetime.now()
                last_miss = self.last_status_log.get(miss_log_key, {"time": datetime.min})
                if (now - last_miss.get("time", datetime.min)).total_seconds() >= 15:
                    logger.warning(
                        "[%s][%s] Active monitor: broker status unavailable for contract %s (attempt %s)",
                        self._get_strategy_name(),
                        symbol,
                        contract_id,
                        miss_count,
                    )
                    self.last_status_log[miss_log_key] = {
                        "msg": "status_unavailable",
                        "time": now,
                    }

                # Fallback: if broker portfolio confirms this contract is no
                # longer open, force runtime/db transition to avoid stale rows.
                if miss_count >= 3:
                    try:
                        portfolio_resp = await self.trade_engine.portfolio({"portfolio": 1})
                        contracts = list((portfolio_resp or {}).get("portfolio", {}).get("contracts") or [])
                        open_ids = {
                            str(item.get("contract_id"))
                            for item in contracts
                            if isinstance(item, dict) and item.get("contract_id") not in (None, "")
                        }
                        if contract_id not in open_ids:
                            logger.warning(
                                "[%s][%s] Active monitor fallback: %s absent from broker open portfolio; marking closed",
                                self._get_strategy_name(),
                                symbol,
                                contract_id,
                            )
                            if hasattr(self.risk_manager, "record_trade_close"):
                                self.risk_manager.record_trade_close(contract_id, 0.0, "closed")
                            forced_close = dict(active_info)
                            forced_close.update(
                                {
                                    "contract_id": contract_id,
                                    "status": "closed",
                                    "profit": 0.0,
                                    "timestamp": datetime.now(),
                                    "strategy_type": self._get_strategy_name(),
                                    "exit_reason": "broker_status_unavailable_portfolio_closed",
                                }
                            )
                            self.state.update_trade(contract_id, forced_close)
                            UserTradesService.save_trade(self.account_id, forced_close)
                            self._active_status_miss_counts.pop(contract_id, None)
                            self.last_status_log.pop(progress_key, None)
                    except Exception as fallback_error:
                        logger.warning(
                            "[%s][%s] Active monitor fallback reconciliation failed for %s: %s",
                            self._get_strategy_name(),
                            symbol,
                            contract_id,
                            fallback_error,
                        )
                return
            self._active_status_miss_counts.pop(contract_id, None)

            status_name = str(trade_status.get("status", "")).strip().lower()
            is_sold = bool(trade_status.get("is_sold")) or status_name in {
                "sold",
                "won",
                "lost",
                "closed",
                "settled",
            }
            current_pnl = _to_float(trade_status.get("profit"), 0.0)
            current_spot = _to_float(trade_status.get("current_spot"), 0.0)

            open_time = self._parse_trade_datetime(
                active_info.get("open_time") or active_info.get("timestamp")
            )
            elapsed_seconds = (
                int((datetime.now() - open_time).total_seconds())
                if isinstance(open_time, datetime)
                else None
            )

            # Always emit active-trade progress logs so manual-tracked contracts
            # have visible monitoring updates in the same lifecycle as setup entries.
            progress_interval = max(
                int(getattr(config, "ACTIVE_TRADE_PROGRESS_LOG_INTERVAL_SECONDS", 15)),
                1,
            )
            now = datetime.now()
            last_progress = self.last_status_log.get(progress_key, {"time": datetime.min})
            if (now - last_progress.get("time", datetime.min)).total_seconds() >= progress_interval:
                age_text = f"{elapsed_seconds}s" if elapsed_seconds is not None else "n/a"
                logger.info(
                    "[%s][%s] Active contract %s | P&L: $%.2f | Spot: %.5f | Age: %s",
                    self._get_strategy_name(),
                    symbol,
                    contract_id,
                    current_pnl,
                    current_spot,
                    age_text,
                )
                self.last_status_log[progress_key] = {
                    "msg": "active_trade_progress",
                    "time": now,
                }

            if not is_sold:
                trade_info = {
                    "open_time": open_time or datetime.now(),
                    "stake": active_info.get("stake"),
                    "symbol": symbol,
                    "contract_id": contract_id,
                    "direction": active_info.get("direction"),
                    "entry_price": active_info.get("entry_price"),
                    "multiplier": active_info.get("multiplier"),
                    "risk_reward_ratio": active_info.get("risk_reward_ratio"),
                }

                if (
                    hasattr(self.risk_manager, "check_trailing_profit")
                    and hasattr(self.risk_manager, "check_stagnation_exit")
                ):
                    trailing_result = self.risk_manager.check_trailing_profit(
                        trade_info,
                        current_pnl,
                    )
                    if (
                        isinstance(trailing_result, (tuple, list))
                        and len(trailing_result) >= 3
                    ):
                        should_trail_exit, trail_reason, just_activated = trailing_result[:3]
                    else:
                        should_trail_exit, trail_reason, just_activated = False, "", False
                    if just_activated:
                        try:
                            await self.trade_engine.remove_take_profit(contract_id)
                        except Exception as remove_tp_error:
                            self._cycle_step(
                                symbol,
                                4,
                                6,
                                f"Failed to remove server-side TP: {remove_tp_error}",
                                emoji="\u274C",
                                level="error",
                            )

                    if should_trail_exit:
                        closed = await _close_trade_with_reason(
                            exit_reason=trail_reason or "trailing_profit_exit",
                            close_message="Trailing profit exit triggered - locking gains",
                            default_execution_reason=(
                                "Trade opened by signal alignment; closed by trailing profit rule"
                            ),
                        )
                        if closed:
                            return

                    stagnation_result = self.risk_manager.check_stagnation_exit(
                        trade_info,
                        current_pnl,
                    )
                    if (
                        isinstance(stagnation_result, (tuple, list))
                        and len(stagnation_result) >= 2
                    ):
                        should_stagnation_exit, stagnation_reason = stagnation_result[:2]
                    else:
                        should_stagnation_exit, stagnation_reason = False, ""
                    if should_stagnation_exit:
                        closed = await _close_trade_with_reason(
                            exit_reason=stagnation_reason or "stagnation_exit",
                            close_message="Stagnation exit triggered - closing trade",
                            default_execution_reason=(
                                "Trade opened by signal alignment; closed by stagnation protection"
                            ),
                        )
                        if closed:
                            return

                # Fallback path for conservative and generic risk managers:
                # apply normal should_close_trade checks for manual-tracked contracts.
                if hasattr(self.risk_manager, "should_close_trade"):
                    try:
                        exit_check = self.risk_manager.should_close_trade(
                            contract_id,
                            current_pnl,
                            current_spot,
                            current_spot,
                        )
                    except Exception as risk_error:
                        logger.warning(
                            "[%s][%s] Risk-exit evaluation failed for %s: %s",
                            self._get_strategy_name(),
                            symbol,
                            contract_id,
                            risk_error,
                        )
                        exit_check = None

                    if isinstance(exit_check, dict) and exit_check.get("should_close"):
                        close_reason = str(exit_check.get("reason") or "risk_manager_exit")
                        close_message = str(
                            exit_check.get("message")
                            or f"Risk manager requested close: {close_reason}"
                        )
                        closed = await _close_trade_with_reason(
                            exit_reason=close_reason,
                            close_message=close_message,
                            default_execution_reason=(
                                "Trade opened by signal alignment; closed by risk protection rule"
                            ),
                        )
                        if closed:
                            return

            if is_sold:
                logger.info(f"[{self._get_strategy_name()}][{symbol}] Trade detected as closed")
                pnl = current_pnl
                status = trade_status.get("status", "sold")
                if hasattr(self.risk_manager, "record_trade_close"):
                    self.risk_manager.record_trade_close(contract_id, pnl, status)
                self.state.update_trade(contract_id, trade_status)

                logger.info(f"[{self._get_strategy_name()}][{symbol}] Trade closed - system unlocked")
                logger.info(f"[{self._get_strategy_name()}][{symbol}] P&L: ${pnl:.2f}")

                try:
                    result_for_db = trade_status.copy()
                    result_for_db.update(active_info)
                    result_for_db["strategy_type"] = self._get_strategy_name()
                    UserTradesService.save_trade(self.account_id, result_for_db)
                except Exception as save_error:
                    logger.error(
                        f"[{self._get_strategy_name()}][{symbol}] "
                        f"DB save failed for externally closed trade: {save_error}"
                    )

                try:
                    result_for_notify = trade_status.copy()
                    result_for_notify.update(active_info)
                    result_for_notify["strategy_type"] = self._get_strategy_name()
                    result_for_notify["user_id"] = self.account_id
                    result_for_notify.setdefault(
                        "execution_reason",
                        "Trade opened by strategy signal and closed at broker settlement/limits",
                    )
                    await self.telegram_bridge.notify_trade_closed(
                        result_for_notify,
                        pnl,
                        status,
                        strategy_type=self._get_strategy_name(),
                    )
                except Exception:
                    pass

                self.last_status_log.pop(progress_key, None)
                self._active_status_miss_counts.pop(contract_id, None)

        except Exception as e:
            logger.warning(f"[{self._get_strategy_name()}][{symbol}] Could not monitor trade: {e}")

# Global bot runner instance - DEPRECATED / DEFAULT
# We keep this for backward compatibility if needed, using env vars
bot_runner = BotRunner()

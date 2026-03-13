"""
Rise/Fall Step Index strategy.

Entry model:
  - 3 consecutive upward ticks + 2-tick weakening confirmation -> FALL contract (PUT)
  - 3 consecutive downward ticks + 2-tick weakening confirmation -> RISE contract (CALL)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
import logging

import pandas as pd

from base_strategy import BaseStrategy
from risefallbot import rf_config


logger = logging.getLogger("risefallbot.strategy")


def _cfg_value(name: str, default):
    cfg_dict = getattr(rf_config, "__dict__", {})
    if isinstance(cfg_dict, dict) and name in cfg_dict:
        return cfg_dict[name]
    return default


def _cfg_int(name: str, default: int) -> int:
    try:
        return int(_cfg_value(name, default))
    except (TypeError, ValueError):
        return default


def _cfg_float(name: str, default: float) -> float:
    try:
        return float(_cfg_value(name, default))
    except (TypeError, ValueError):
        return default


def _cfg_bool(name: str, default: bool) -> bool:
    value = _cfg_value(name, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


class RiseFallStrategy(BaseStrategy):
    """Step Index tick-sequence reversal strategy."""

    def __init__(self):
        self.allowed_symbols = tuple(
            _cfg_value("RF_SUPPORTED_SYMBOLS", _cfg_value("RF_SYMBOLS", []))
        )
        self.sequence_length = _cfg_int("RF_TICK_SEQUENCE_LENGTH", 3)
        self.confirmation_ticks = max(_cfg_int("RF_CONFIRMATION_TICKS", 2), 1)
        self.burst_noise_lookback_moves = max(
            _cfg_int("RF_BURST_NOISE_LOOKBACK_MOVES", 4),
            0,
        )
        self.primary_window_points = self.sequence_length + self.confirmation_ticks + 1
        self.history_count = max(
            _cfg_int(
                "RF_TICK_HISTORY_COUNT",
                self.primary_window_points + self.burst_noise_lookback_moves,
            ),
            self.primary_window_points,
        )
        self.burst_max_seconds = _cfg_float("RF_BURST_MAX_SECONDS", 1.5)
        self.require_consecutive_direction = _cfg_bool(
            "RF_REQUIRE_CONSECUTIVE_DIRECTION",
            True,
        )
        self.require_fresh_signal_after_cooldown = _cfg_bool(
            "RF_REQUIRE_FRESH_SIGNAL_AFTER_COOLDOWN",
            True,
        )
        self.default_stake = _cfg_float("RF_DEFAULT_STAKE", 1.0)
        self.duration = _cfg_int("RF_CONTRACT_DURATION", 3)
        self.duration_unit = str(_cfg_value("RF_DURATION_UNIT", "t"))
        self._last_analysis: Dict[str, Dict[str, Any]] = {}
        self._last_qualifying_signature: Dict[str, str] = {}

    def _reject(
        self,
        symbol: str,
        code: str,
        reason: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        logger.info("[RF][%s] Reject setup | code=%s reason=%s", symbol, code, reason)
        self._set_analysis(
            symbol,
            decision="no_trade",
            reason=reason,
            code=code,
            details=details,
        )

    def _set_analysis(
        self,
        symbol: str,
        decision: str,
        reason: str,
        code: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._last_analysis[symbol] = {
            "decision": decision,
            "reason": reason,
            "code": code,
            "details": details or {},
        }

    def get_last_analysis(self, symbol: str) -> Dict[str, Any]:
        data = self._last_analysis.get(symbol, {})
        return dict(data) if isinstance(data, dict) else {}

    def _normalize_ticks(self, raw_ticks: Any) -> pd.DataFrame:
        if raw_ticks is None:
            return pd.DataFrame()
        if isinstance(raw_ticks, pd.DataFrame):
            df = raw_ticks.copy()
        else:
            df = pd.DataFrame(raw_ticks)

        if df.empty:
            return df

        price_column = None
        for candidate in ("quote", "price", "close"):
            if candidate in df.columns:
                price_column = candidate
                break
        if price_column is None:
            return pd.DataFrame()

        normalized = pd.DataFrame({"quote": pd.to_numeric(df[price_column], errors="coerce")})

        if "timestamp" in df.columns:
            normalized["timestamp"] = pd.to_numeric(df["timestamp"], errors="coerce")
        elif "epoch" in df.columns:
            normalized["timestamp"] = pd.to_numeric(df["epoch"], errors="coerce")
        else:
            normalized["timestamp"] = range(len(normalized))

        if "datetime" in df.columns:
            normalized["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
        else:
            normalized["datetime"] = pd.to_datetime(
                normalized["timestamp"],
                unit="s",
                errors="coerce",
            )

        normalized = normalized.dropna(subset=["quote", "timestamp"]).reset_index(drop=True)
        return normalized

    @staticmethod
    def _to_iso(value: Any) -> Optional[str]:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None
        if hasattr(value, "to_pydatetime"):
            value = value.to_pydatetime()
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)

    @staticmethod
    def _build_signature(window: pd.DataFrame) -> str:
        return "|".join(
            f"{float(row.timestamp):.6f}:{row.quote:.10f}"
            for row in window.itertuples(index=False)
        )

    @staticmethod
    def _direction(delta: float) -> int:
        if delta > 0:
            return 1
        if delta < 0:
            return -1
        return 0

    def _is_strict_burst(self, moves: List[float]) -> bool:
        if len(moves) != self.sequence_length:
            return False
        directions = [self._direction(move) for move in moves]
        if any(direction == 0 for direction in directions):
            return False
        return all(direction == directions[0] for direction in directions)

    @classmethod
    def _is_alternating(cls, moves: List[float]) -> bool:
        directions = [cls._direction(move) for move in moves]
        if len(directions) < 2 or any(direction == 0 for direction in directions):
            return False
        return all(
            directions[idx] != directions[idx - 1]
            for idx in range(1, len(directions))
        )

    @staticmethod
    def _burst_breaks_previous_region(
        pre_burst_prices: List[float],
        burst_end_price: float,
        burst_is_up: bool,
    ) -> bool:
        if not pre_burst_prices:
            return True
        if burst_is_up:
            return burst_end_price > max(pre_burst_prices)
        return burst_end_price < min(pre_burst_prices)

    def analyze(self, **kwargs) -> Optional[Dict]:
        ticks = self._normalize_ticks(kwargs.get("data_ticks", kwargs.get("data_1m")))
        symbol = kwargs.get("symbol", "unknown")
        stake = kwargs.get("stake", self.default_stake)

        if self.allowed_symbols and symbol not in self.allowed_symbols:
            logger.debug(f"[RF][{symbol}] Symbol not allowed for Step Index mode")
            self._reject(
                symbol,
                code="symbol_not_allowed",
                reason="Symbol not allowed",
                details={"allowed_symbols": list(self.allowed_symbols)},
            )
            return None

        required_points = self.history_count
        if ticks.empty or len(ticks) < required_points:
            self._reject(
                symbol,
                code="insufficient_tick_history",
                reason="Insufficient tick history",
                details={
                    "ticks_available": int(len(ticks)),
                    "ticks_required": int(required_points),
                    "primary_window_points": int(self.primary_window_points),
                    "burst_noise_lookback_moves": int(self.burst_noise_lookback_moves),
                },
            )
            return None

        history_window = ticks.tail(required_points).reset_index(drop=True)
        history_prices = [float(v) for v in history_window["quote"].tolist()]
        history_deltas = [
            round(history_prices[idx] - history_prices[idx - 1], 12)
            for idx in range(1, len(history_prices))
        ]
        analysis_window = history_window.tail(self.primary_window_points).reset_index(drop=True)
        prices = [float(v) for v in analysis_window["quote"].tolist()]
        deltas = [round(prices[idx] - prices[idx - 1], 12) for idx in range(1, len(prices))]
        signature = self._build_signature(analysis_window)
        burst_moves = deltas[: self.sequence_length]
        confirmation_moves = deltas[self.sequence_length :]
        burst_start_idx = len(history_window) - self.primary_window_points
        pre_burst_moves = history_deltas[
            max(0, burst_start_idx - self.burst_noise_lookback_moves) : burst_start_idx
        ]
        pre_burst_prices = [
            float(v)
            for v in history_window["quote"]
            .iloc[max(0, burst_start_idx - self.burst_noise_lookback_moves) : burst_start_idx + 1]
            .tolist()
        ]
        burst_end_price = prices[self.sequence_length]
        burst_elapsed_seconds = round(
            float(
                analysis_window["timestamp"].iloc[self.sequence_length]
                - analysis_window["timestamp"].iloc[0]
            ),
            6,
        )

        details = {
            "sequence_length": int(self.sequence_length),
            "confirmation_ticks": int(self.confirmation_ticks),
            "burst_noise_lookback_moves": int(self.burst_noise_lookback_moves),
            "tick_prices": prices,
            "tick_movements": deltas,
            "burst_movements": burst_moves,
            "confirmation_movements": confirmation_moves,
            "pre_burst_prices": pre_burst_prices,
            "pre_burst_movements": pre_burst_moves,
            "sequence_signature": signature,
            "sequence_started_at": self._to_iso(analysis_window["datetime"].iloc[0]),
            "sequence_ended_at": self._to_iso(analysis_window["datetime"].iloc[-1]),
            "sequence_start_epoch": float(analysis_window["timestamp"].iloc[0]),
            "sequence_end_epoch": float(analysis_window["timestamp"].iloc[-1]),
            "burst_elapsed_seconds": burst_elapsed_seconds,
            "burst_max_seconds": self.burst_max_seconds,
        }

        if self.require_consecutive_direction and not self._is_strict_burst(burst_moves):
            self._reject(
                symbol,
                code="burst_not_consecutive",
                reason="Burst ticks were not strictly consecutive in one direction",
                details=details,
            )
            return None

        if self.burst_max_seconds > 0 and burst_elapsed_seconds >= self.burst_max_seconds:
            self._reject(
                symbol,
                code="burst_too_slow",
                reason="Burst momentum formed too slowly",
                details=details,
            )
            return None

        burst_is_up = all(delta > 0 for delta in burst_moves)
        if (
            self.burst_noise_lookback_moves > 0
            and self._is_alternating(pre_burst_moves)
            and not self._burst_breaks_previous_region(
                pre_burst_prices=pre_burst_prices,
                burst_end_price=burst_end_price,
                burst_is_up=burst_is_up,
            )
        ):
            self._reject(
                symbol,
                code="mixed_tick_noise",
                reason="Burst did not break away from a noisy oscillating region",
                details=details,
            )
            return None

        rejection_confirmation = all(
            delta > 0 for delta in confirmation_moves
        ) if burst_is_up else all(delta < 0 for delta in confirmation_moves)

        if rejection_confirmation:
            self._reject(
                symbol,
                code="confirmation_continuation",
                reason="Confirmation ticks continued burst momentum",
                details=details,
            )
            return None

        if (
            self.require_fresh_signal_after_cooldown
            and self._last_qualifying_signature.get(symbol) == signature
        ):
            self._reject(
                symbol,
                code="signal_not_fresh",
                reason="Signal is not fresh",
                details=details,
            )
            return None

        if burst_is_up:
            contract_direction = "PUT"
            trade_label = "FALL"
            sequence_direction = "up"
        else:
            contract_direction = "CALL"
            trade_label = "RISE"
            sequence_direction = "down"

        self._last_qualifying_signature[symbol] = signature

        signal = {
            "symbol": symbol,
            "direction": contract_direction,
            "trade_label": trade_label,
            "sequence_direction": sequence_direction,
            "stake": stake,
            "duration": self.duration,
            "duration_unit": self.duration_unit,
            "tick_sequence": prices,
            "tick_movements": deltas,
            "burst_movements": burst_moves,
            "confirmation_movements": confirmation_moves,
            "pre_burst_movements": pre_burst_moves,
            "sequence_signature": signature,
            "sequence_started_at": details["sequence_started_at"],
            "sequence_ended_at": details["sequence_ended_at"],
            "sequence_start_epoch": details["sequence_start_epoch"],
            "sequence_end_epoch": details["sequence_end_epoch"],
            "burst_elapsed_seconds": burst_elapsed_seconds,
            "confidence": 10,
        }

        logger.info(
            f"[RF][{symbol}] Step sequence signal: {sequence_direction} burst with "
            f"weakening confirmation -> {trade_label} ({contract_direction})"
        )
        self._set_analysis(
            symbol,
            decision="signal",
            reason=f"{trade_label} signal accepted",
            code="signal_ready",
            details={
                **details,
                "direction": contract_direction,
                "trade_label": trade_label,
                "sequence_direction": sequence_direction,
            },
        )
        return signal

    def get_required_timeframes(self) -> List[str]:
        return ["ticks"]

    def get_strategy_name(self) -> str:
        return "RiseFall"

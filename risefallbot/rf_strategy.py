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
        self.allowed_symbols = tuple(_cfg_value("RF_SYMBOLS", []))
        self.sequence_length = _cfg_int("RF_TICK_SEQUENCE_LENGTH", 3)
        self.confirmation_ticks = max(_cfg_int("RF_CONFIRMATION_TICKS", 2), 1)
        self.history_count = max(
            _cfg_int(
                "RF_TICK_HISTORY_COUNT",
                self.sequence_length + self.confirmation_ticks + 1,
            ),
            self.sequence_length + self.confirmation_ticks + 1,
        )
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
            f"{int(row.timestamp)}:{row.quote:.10f}"
            for row in window.itertuples(index=False)
        )

    def analyze(self, **kwargs) -> Optional[Dict]:
        ticks = self._normalize_ticks(kwargs.get("data_ticks", kwargs.get("data_1m")))
        symbol = kwargs.get("symbol", "unknown")
        stake = kwargs.get("stake", self.default_stake)

        if self.allowed_symbols and symbol not in self.allowed_symbols:
            logger.debug(f"[RF][{symbol}] Symbol not allowed for Step Index mode")
            self._set_analysis(
                symbol,
                decision="no_trade",
                reason="Symbol not allowed",
                code="symbol_not_allowed",
                details={"allowed_symbols": list(self.allowed_symbols)},
            )
            return None

        required_points = self.sequence_length + self.confirmation_ticks + 1
        if ticks.empty or len(ticks) < required_points:
            logger.debug(f"[RF][{symbol}] Insufficient tick history")
            self._set_analysis(
                symbol,
                decision="no_trade",
                reason="Insufficient tick history",
                code="insufficient_tick_history",
                details={
                    "ticks_available": int(len(ticks)),
                    "ticks_required": int(required_points),
                },
            )
            return None

        window = ticks.tail(required_points).reset_index(drop=True)
        prices = [float(v) for v in window["quote"].tolist()]
        deltas = [round(prices[idx] - prices[idx - 1], 12) for idx in range(1, len(prices))]
        signature = self._build_signature(window)
        burst_moves = deltas[: self.sequence_length]
        confirmation_moves = deltas[self.sequence_length :]

        details = {
            "sequence_length": int(self.sequence_length),
            "confirmation_ticks": int(self.confirmation_ticks),
            "tick_prices": prices,
            "tick_movements": deltas,
            "burst_movements": burst_moves,
            "confirmation_movements": confirmation_moves,
            "sequence_signature": signature,
            "sequence_started_at": self._to_iso(window["datetime"].iloc[0]),
            "sequence_ended_at": self._to_iso(window["datetime"].iloc[-1]),
            "sequence_start_epoch": int(window["timestamp"].iloc[0]),
            "sequence_end_epoch": int(window["timestamp"].iloc[-1]),
        }

        if any(delta == 0 for delta in burst_moves):
            logger.debug(f"[RF][{symbol}] Flat tick detected in burst sequence")
            self._set_analysis(
                symbol,
                decision="no_trade",
                reason="Mixed or flat tick burst",
                code="mixed_tick_sequence",
                details=details,
            )
            return None

        if self.require_consecutive_direction and not (
            all(delta > 0 for delta in burst_moves) or all(delta < 0 for delta in burst_moves)
        ):
            logger.debug(f"[RF][{symbol}] Mixed tick burst")
            self._set_analysis(
                symbol,
                decision="no_trade",
                reason="Mixed tick burst",
                code="mixed_tick_sequence",
                details=details,
            )
            return None

        burst_is_up = all(delta > 0 for delta in burst_moves)
        rejection_confirmation = all(
            delta > 0 for delta in confirmation_moves
        ) if burst_is_up else all(delta < 0 for delta in confirmation_moves)

        if rejection_confirmation:
            logger.debug(f"[RF][{symbol}] Confirmation ticks continue burst momentum")
            self._set_analysis(
                symbol,
                decision="no_trade",
                reason="Confirmation ticks continued burst momentum",
                code="confirmation_rejected",
                details=details,
            )
            return None

        if (
            self.require_fresh_signal_after_cooldown
            and self._last_qualifying_signature.get(symbol) == signature
        ):
            logger.debug(f"[RF][{symbol}] Qualifying sequence already consumed")
            self._set_analysis(
                symbol,
                decision="no_trade",
                reason="Signal is not fresh",
                code="signal_not_fresh",
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
            "sequence_signature": signature,
            "sequence_started_at": details["sequence_started_at"],
            "sequence_ended_at": details["sequence_ended_at"],
            "sequence_start_epoch": details["sequence_start_epoch"],
            "sequence_end_epoch": details["sequence_end_epoch"],
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

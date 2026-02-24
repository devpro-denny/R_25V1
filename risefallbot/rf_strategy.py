"""
Rise/Fall scalping strategy.

Core signal engine:
  - EMA crossover
  - RSI threshold
  - Stochastic threshold

Optimization layers (config-gated):
  - zone proximity filter
  - momentum candle filter
  - structural bias alignment
  - scenario classification (breakout/retest/basic)
"""

from typing import Any, Dict, List, Optional
import logging

import pandas as pd

from base_strategy import BaseStrategy
from indicators import calculate_ema, calculate_rsi, calculate_stochastic
from risefallbot import rf_config
from risefallbot.candle_filters import is_momentum_candle
from risefallbot.zone_analyzer import (
    classify_scenario,
    detect_market_bias,
    get_key_zones,
    price_near_zone,
)


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
    """
    Rise/Fall strategy using triple-confirmation momentum signals.

    Base entry logic:
        CALL: EMA(fast) > EMA(slow) AND RSI < oversold AND Stoch < oversold
        PUT:  EMA(fast) < EMA(slow) AND RSI > overbought AND Stoch > overbought
    """

    def __init__(self):
        self.ema_fast = _cfg_int("RF_EMA_FAST", 5)
        self.ema_slow = _cfg_int("RF_EMA_SLOW", 13)
        self.rsi_period = _cfg_int("RF_RSI_PERIOD", 7)
        self.rsi_oversold = _cfg_float("RF_RSI_OVERSOLD", 30.0)
        self.rsi_overbought = _cfg_float("RF_RSI_OVERBOUGHT", 70.0)
        self.stoch_k_period = _cfg_int("RF_STOCH_K_PERIOD", 5)
        self.stoch_d_period = _cfg_int("RF_STOCH_D_PERIOD", 3)
        self.stoch_oversold = _cfg_float("RF_STOCH_OVERSOLD", 20.0)
        self.stoch_overbought = _cfg_float("RF_STOCH_OVERBOUGHT", 80.0)
        self.min_bars = _cfg_int("RF_MIN_BARS", 30)
        self.default_stake = _cfg_float("RF_DEFAULT_STAKE", 1.0)
        self.duration = _cfg_int("RF_CONTRACT_DURATION", 2)
        self.duration_unit = str(_cfg_value("RF_DURATION_UNIT", "m"))

        # Optimization parameters
        self.zone_lookback = _cfg_int("RF_ZONE_LOOKBACK", 50)
        self.zone_touch_tolerance = _cfg_float("RF_ZONE_TOUCH_TOLERANCE", 0.0003)
        self.zone_min_touches = _cfg_int("RF_ZONE_MIN_TOUCHES", 2)
        self.momentum_body_ratio = _cfg_float("RF_MOMENTUM_BODY_RATIO", 0.70)
        self.momentum_wick_ratio = _cfg_float("RF_MOMENTUM_WICK_RATIO", 0.25)
        self.momentum_avg_lookback = _cfg_int("RF_MOMENTUM_AVG_LOOKBACK", 5)
        self.enable_zone_filter = _cfg_bool("RF_ENABLE_ZONE_FILTER", False)
        self.enable_candle_filter = _cfg_bool("RF_ENABLE_CANDLE_FILTER", False)
        self.retest_lookback = _cfg_int("RF_RETEST_LOOKBACK", 5)
        self.allow_basic_scenario = _cfg_bool("RF_ALLOW_BASIC_SCENARIO", False)
        self._last_analysis: Dict[str, Dict[str, Any]] = {}

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
        """Return the latest analyze() decision metadata for the symbol."""
        data = self._last_analysis.get(symbol, {})
        return dict(data) if isinstance(data, dict) else {}

    def analyze(self, **kwargs) -> Optional[Dict]:
        """
        Analyze 1-minute OHLC data and return a Rise/Fall signal when valid.
        """
        df = kwargs.get("data_1m")
        symbol = kwargs.get("symbol", "unknown")
        stake = kwargs.get("stake", self.default_stake)

        if df is None or df.empty:
            logger.debug(f"[RF][{symbol}] No 1m data available")
            self._set_analysis(
                symbol,
                decision="no_trade",
                reason="No 1m data available",
                code="no_data",
            )
            return None

        if len(df) < self.min_bars:
            logger.debug(f"[RF][{symbol}] Warm-up: {len(df)}/{self.min_bars} bars")
            self._set_analysis(
                symbol,
                decision="no_trade",
                reason=f"Warm-up: {len(df)}/{self.min_bars} bars",
                code="warmup_insufficient_bars",
                details={"bars": int(len(df)), "min_bars": int(self.min_bars)},
            )
            return None

        # --- Compute indicators ---
        ema_fast = calculate_ema(df, self.ema_fast)
        ema_slow = calculate_ema(df, self.ema_slow)
        rsi = calculate_rsi(df, self.rsi_period)
        stoch_k, _ = calculate_stochastic(df, self.stoch_k_period, self.stoch_d_period)

        idx = -1
        ema_f = ema_fast.iloc[idx]
        ema_s = ema_slow.iloc[idx]
        rsi_val = rsi.iloc[idx]
        stoch_val = stoch_k.iloc[idx]

        if pd.isna(ema_f) or pd.isna(ema_s) or pd.isna(rsi_val) or pd.isna(stoch_val):
            logger.debug(f"[RF][{symbol}] Indicator NaN - skipping")
            self._set_analysis(
                symbol,
                decision="no_trade",
                reason="Indicator NaN - skipping",
                code="indicator_nan",
            )
            return None

        # --- Zone and candle analysis ---
        zones = get_key_zones(
            df,
            lookback=self.zone_lookback,
            tolerance=self.zone_touch_tolerance,
            min_touches=self.zone_min_touches,
        )
        market_bias = detect_market_bias(df)
        current_price = df["close"].iloc[idx]
        near_zone, matched_zone = price_near_zone(
            current_price,
            zones,
            self.zone_touch_tolerance,
        )
        has_momentum, candle_dir = is_momentum_candle(
            df,
            idx=idx,
            body_ratio=self.momentum_body_ratio,
            wick_ratio=self.momentum_wick_ratio,
            avg_lookback=self.momentum_avg_lookback,
        )

        if self.enable_zone_filter and not near_zone:
            logger.debug(f"[RF][{symbol}] Price not near key zone - waiting")
            self._set_analysis(
                symbol,
                decision="no_trade",
                reason="Price not near key zone",
                code="zone_miss",
                details={
                    "zones_count": int(len(zones)),
                    "current_price": float(current_price),
                },
            )
            return None

        if self.enable_candle_filter and not has_momentum:
            logger.debug(f"[RF][{symbol}] No momentum candle - waiting")
            self._set_analysis(
                symbol,
                decision="no_trade",
                reason="No momentum candle",
                code="candle_quality_fail",
                details={"candle_direction": candle_dir},
            )
            return None

        logger.info(
            f"[RF][{symbol}] EMA{self.ema_fast}={ema_f:.4f} EMA{self.ema_slow}={ema_s:.4f} "
            f"RSI({self.rsi_period})={rsi_val:.1f} Stoch%K={stoch_val:.1f}"
        )

        # --- Triple-confirmation core logic ---
        direction = None
        if (
            ema_f > ema_s
            and rsi_val < self.rsi_oversold
            and stoch_val < self.stoch_oversold
        ):
            direction = "CALL"
        elif (
            ema_f < ema_s
            and rsi_val > self.rsi_overbought
            and stoch_val > self.stoch_overbought
        ):
            direction = "PUT"

        if direction is None:
            logger.debug(f"[RF][{symbol}] No triple-confirmation - no signal")
            self._set_analysis(
                symbol,
                decision="no_trade",
                reason="Triple-confirmation not met",
                code="triple_confirmation_fail",
            )
            return None

        # Structural bias alignment (part of zone layer)
        if self.enable_zone_filter:
            if direction == "CALL" and market_bias == "bearish":
                logger.debug(f"[RF][{symbol}] CALL against bearish bias - skipping")
                self._set_analysis(
                    symbol,
                    decision="no_trade",
                    reason="CALL against bearish market bias",
                    code="bias_mismatch",
                    details={"direction": direction, "market_bias": market_bias},
                )
                return None
            if direction == "PUT" and market_bias == "bullish":
                logger.debug(f"[RF][{symbol}] PUT against bullish bias - skipping")
                self._set_analysis(
                    symbol,
                    decision="no_trade",
                    reason="PUT against bullish market bias",
                    code="bias_mismatch",
                    details={"direction": direction, "market_bias": market_bias},
                )
                return None

        # Candle-direction alignment (part of candle layer)
        if self.enable_candle_filter:
            if direction == "CALL" and candle_dir != "bullish":
                logger.debug(f"[RF][{symbol}] Candle direction mismatch for CALL")
                self._set_analysis(
                    symbol,
                    decision="no_trade",
                    reason="Candle direction mismatch for CALL",
                    code="candle_direction_mismatch",
                    details={"direction": direction, "candle_direction": candle_dir},
                )
                return None
            if direction == "PUT" and candle_dir != "bearish":
                logger.debug(f"[RF][{symbol}] Candle direction mismatch for PUT")
                self._set_analysis(
                    symbol,
                    decision="no_trade",
                    reason="Candle direction mismatch for PUT",
                    code="candle_direction_mismatch",
                    details={"direction": direction, "candle_direction": candle_dir},
                )
                return None

        scenario = classify_scenario(
            df,
            zones,
            direction,
            idx=idx,
            retest_lookback=self.retest_lookback,
        )
        logger.debug(f"[RF][{symbol}] Scenario classified: {scenario}")
        if self.enable_zone_filter and not self.allow_basic_scenario and scenario == "basic":
            logger.debug(
                f"[RF][{symbol}] Basic scenario without structural trigger - waiting"
            )
            self._set_analysis(
                symbol,
                decision="no_trade",
                reason="Basic scenario without structural trigger",
                code="basic_scenario_filtered",
                details={"scenario": scenario},
            )
            return None

        signal = {
            "symbol": symbol,
            "direction": direction,
            "stake": stake,
            "duration": self.duration,
            "duration_unit": self.duration_unit,
            "ema_fast": ema_f,
            "ema_slow": ema_s,
            "rsi": rsi_val,
            "stoch": stoch_val,
            "zone_level": matched_zone["level"] if matched_zone else None,
            "zone_type": matched_zone["type"] if matched_zone else None,
            "market_bias": market_bias,
            "candle_momentum": has_momentum,
            "candle_direction": candle_dir,
            "scenario": scenario,
            "confidence": 10,
        }
        logger.info(
            f"[RF][{symbol}] Signal: {direction} | Stake: ${stake} | Scenario: {scenario}"
        )
        self._set_analysis(
            symbol,
            decision="signal",
            reason=f"Signal {direction} accepted",
            code="signal_ready",
            details={
                "direction": direction,
                "scenario": scenario,
                "market_bias": market_bias,
                "zone_type": matched_zone["type"] if matched_zone else None,
                "zone_level": float(matched_zone["level"]) if matched_zone else None,
                "candle_direction": candle_dir,
                "candle_momentum": bool(has_momentum),
            },
        )
        return signal

    def get_required_timeframes(self) -> List[str]:
        return ["1m"]

    def get_strategy_name(self) -> str:
        return "RiseFall"

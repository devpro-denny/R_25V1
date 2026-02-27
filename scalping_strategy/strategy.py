"""
Scalping Strategy Implementation
3-timeframe analysis with focused entry-quality filters.
"""

from datetime import datetime
from importlib import import_module
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from base_strategy import BaseStrategy
from indicators import calculate_adx as _default_calculate_adx
from indicators import calculate_rsi as _default_calculate_rsi
from utils import setup_logger

from . import config as scalping_config

logger = setup_logger()


class ScalpingStrategy(BaseStrategy):
    """
    Scalping strategy using 3 timeframes (1h, 5m, 1m).
    """

    def __init__(self):
        """Initialize scalping strategy."""
        pass

    def analyze(self, **kwargs) -> Optional[Dict]:
        """
        Analyze market data for scalping opportunities.

        Args:
            **kwargs: Must include data_1h, data_5m, data_1m, symbol

        Returns:
            Signal dict if trade should be executed, otherwise a rejected signal dict.
        """
        data_1h = kwargs.get("data_1h")
        data_5m = kwargs.get("data_5m")
        data_1m = kwargs.get("data_1m")
        symbol = kwargs.get("symbol", "R_50")

        def _step_log(step: int, message: str, level: str = "info") -> None:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            line = f"[SCALPING][{symbol}] STEP {step}/6 | {ts} | {message}"
            getattr(logger, level)(line)

        # STEP 1/6: Validate input data
        _step_log(1, "Starting analysis")
        if not all([data_1h is not None, data_5m is not None, data_1m is not None]):
            logger.error(f"[SCALPING][{symbol}] Missing required timeframe data (1h, 5m, 1m)")
            return {
                "can_trade": False,
                "details": {"reason": "Missing required timeframe data (1h, 5m, 1m)"},
            }

        if len(data_1h) < 50 or len(data_5m) < 50 or len(data_1m) < 50:
            logger.error(f"[SCALPING][{symbol}] Insufficient data (need at least 50 candles per timeframe)")
            return {"can_trade": False, "details": {"reason": "Insufficient data (need >50 candles)"}}

        # STEP 2/6: 1h directional bias + 5m fresh trigger + 1h structure confirmation
        trend_1h = self._determine_bias(data_1h, "1h", fast_period=20, slow_period=50)
        trend_5m = self._determine_trend(
            data_5m,
            "5m",
            fast_period=9,
            slow_period=21,
            crossover_lookback=5,
            allow_alignment_fallback=True,
            min_slope_pct=getattr(scalping_config, "SCALPING_5M_EMA_SLOPE_MIN_PCT", 0.0),
        )

        if trend_1h is None:
            _step_log(2, "No 1h trend bias (EMA20/50)")
            return {"can_trade": False, "details": {"reason": "No 1h trend bias (EMA20/50)"}}

        if trend_5m is None:
            _step_log(2, "No fresh crossover on 5m")
            return {"can_trade": False, "details": {"reason": "No fresh crossover on 5m"}}

        if trend_1h != trend_5m:
            _step_log(2, f"Trend mismatch (1h: {trend_1h}, 5m: {trend_5m})")
            return {
                "can_trade": False,
                "details": {"reason": f"Trend mismatch (1h: {trend_1h}, 5m: {trend_5m})"},
            }

        direction = trend_1h
        if not self._check_1h_break_of_structure(data_1h, direction):
            _step_log(2, f"No 1h break of structure for {direction}")
            return {"can_trade": False, "details": {"reason": f"No 1h break of structure for {direction}"}}

        _step_log(2, f"Trend aligned: {direction}")

        # STEP 3/6: Indicator validation (RSI/ADX) using closed candle values.
        package_module = import_module("scalping_strategy")
        calculate_rsi = getattr(package_module, "calculate_rsi", _default_calculate_rsi)
        calculate_adx = getattr(package_module, "calculate_adx", _default_calculate_adx)

        rsi_series = calculate_rsi(data_1m, period=14)
        adx_series = calculate_adx(data_1m, period=14)

        rsi_1m = None
        if rsi_series is not None and len(rsi_series) >= 2:
            rsi_1m = float(rsi_series.iloc[-2])
        if rsi_1m is None or np.isnan(rsi_1m):
            logger.warning(f"[SCALPING][{symbol}] RSI fallback applied (50)")
            rsi_1m = 50.0

        adx_1m = None
        if adx_series is not None and len(adx_series) >= 2:
            adx_1m = float(adx_series.iloc[-2])
        if adx_1m is None or np.isnan(adx_1m):
            logger.warning(f"[SCALPING][{symbol}] ADX fallback applied (0)")
            adx_1m = 0.0

        logger.debug(f"[SCALPING][{symbol}] Indicators | RSI={rsi_1m:.2f} ADX={adx_1m:.2f}")

        if adx_1m < scalping_config.SCALPING_ADX_THRESHOLD:
            _step_log(3, f"Weak trend (ADX {adx_1m:.1f} < {scalping_config.SCALPING_ADX_THRESHOLD})")
            return {
                "can_trade": False,
                "details": {
                    "reason": f"Weak trend (ADX {adx_1m:.1f} < {scalping_config.SCALPING_ADX_THRESHOLD})"
                },
            }

        adx_max_threshold = float(getattr(scalping_config, "SCALPING_ADX_MAX_THRESHOLD", 0.0) or 0.0)
        if adx_max_threshold > 0 and adx_1m > adx_max_threshold:
            _step_log(3, f"ADX exhaustion ({adx_1m:.1f} > {adx_max_threshold:.1f})")
            return {
                "can_trade": False,
                "details": {"reason": f"ADX exhaustion ({adx_1m:.1f} > {adx_max_threshold:.1f})"},
            }

        if symbol == "stpRNG4":
            stprng4_min_adx = float(
                getattr(scalping_config, "SCALPING_STPRNG4_MIN_ADX", scalping_config.SCALPING_ADX_THRESHOLD)
            )
            if adx_1m < stprng4_min_adx:
                _step_log(3, f"stpRNG4 requires stronger ADX ({adx_1m:.1f} < {stprng4_min_adx:.1f})")
                return {
                    "can_trade": False,
                    "details": {
                        "reason": (
                            f"stpRNG4 requires stronger ADX ({adx_1m:.1f} < {stprng4_min_adx:.1f})"
                        )
                    },
                }

        adx_slope = 0.0
        if adx_series is not None and len(adx_series) >= 3:
            adx_prev = float(adx_series.iloc[-3])
            if not np.isnan(adx_prev):
                adx_slope = adx_1m - adx_prev
                if adx_slope < scalping_config.SCALPING_ADX_SLOPE_MIN:
                    _step_log(3, f"ADX declining (slope {adx_slope:.2f})")
                    return {
                        "can_trade": False,
                        "details": {"reason": f"ADX declining (slope {adx_slope:.2f})"},
                    }

        if direction == "UP":
            if not (scalping_config.SCALPING_RSI_UP_MIN <= rsi_1m <= scalping_config.SCALPING_RSI_UP_MAX):
                _step_log(3, f"RSI {rsi_1m:.1f} not in UP range")
                return {"can_trade": False, "details": {"reason": f"RSI {rsi_1m:.1f} not in UP range"}}
        else:
            if not (
                scalping_config.SCALPING_RSI_DOWN_MIN <= rsi_1m <= scalping_config.SCALPING_RSI_DOWN_MAX
            ):
                _step_log(3, f"RSI {rsi_1m:.1f} not in DOWN range")
                return {"can_trade": False, "details": {"reason": f"RSI {rsi_1m:.1f} not in DOWN range"}}

        _step_log(3, "Indicator gate passed")

        # STEP 4/6: Momentum, structure, and price-action location checks.
        signal_candle = data_1m.iloc[-2]
        signal_open = float(signal_candle["open"])
        signal_close = float(signal_candle["close"])
        signal_high = float(signal_candle["high"])
        signal_low = float(signal_candle["low"])
        current_price = float(data_1m["close"].iloc[-1])  # live price used for actual entry

        atr_1m = self._calculate_atr(data_1m.iloc[:-1], period=14)
        base_threshold = scalping_config.ASSET_CONFIG.get(symbol, {}).get("movement_threshold_pct", 0.7)
        movement_threshold = base_threshold * scalping_config.SCALPING_ASSET_MOVEMENT_MULTIPLIER

        max_entry_drift_atr = float(getattr(scalping_config, "SCALPING_MAX_ENTRY_DRIFT_ATR", 0.0) or 0.0)
        if atr_1m > 0 and max_entry_drift_atr > 0:
            directional_drift = (
                current_price - signal_close if direction == "UP" else signal_close - current_price
            )
            max_drift = atr_1m * max_entry_drift_atr
            if directional_drift > max_drift:
                _step_log(
                    4,
                    (
                        f"Entry drift too high ({directional_drift:.5f} > {max_drift:.5f}) "
                        f"[{max_entry_drift_atr:.2f} ATR]"
                    ),
                )
                return {
                    "can_trade": False,
                    "details": {
                        "reason": (
                            f"Entry drift too high ({directional_drift:.5f} > {max_drift:.5f}) "
                            f"[{max_entry_drift_atr:.2f} ATR]"
                        )
                    },
                }

        price_5_candles_ago = float(data_1m["close"].iloc[-7])
        if price_5_candles_ago == 0:
            _step_log(4, "Invalid reference price for movement check")
            return {"can_trade": False, "details": {"reason": "Invalid reference price for movement check"}}

        price_change_pct = (signal_close - price_5_candles_ago) / price_5_candles_ago * 100
        adverse_move = (
            (direction == "UP" and price_change_pct < -movement_threshold)
            or (direction == "DOWN" and price_change_pct > movement_threshold)
        )
        if adverse_move:
            _step_log(4, f"Adverse pre-entry move ({price_change_pct:.2f}%)")
            return {
                "can_trade": False,
                "details": {"reason": f"Adverse pre-entry move ({price_change_pct:.2f}%)"},
            }

        last_candle_size = abs(signal_close - signal_open)
        momentum_threshold = atr_1m * scalping_config.SCALPING_MOMENTUM_THRESHOLD
        if last_candle_size < momentum_threshold:
            _step_log(4, "No momentum breakout")
            return {"can_trade": False, "details": {"reason": "No momentum breakout"}}

        signal_range = signal_high - signal_low
        body_ratio = 1.0
        if signal_range > 0:
            body_ratio = last_candle_size / signal_range
            if body_ratio < scalping_config.SCALPING_BODY_RATIO_MIN:
                _step_log(4, f"Weak body ratio ({body_ratio:.2f})")
                return {"can_trade": False, "details": {"reason": f"Weak body ratio ({body_ratio:.2f})"}}

        if direction == "UP" and signal_close <= signal_open:
            _step_log(4, "Candle direction mismatch (UP trade, bearish candle)")
            return {
                "can_trade": False,
                "details": {"reason": "Candle direction mismatch (UP trade, bearish candle)"},
            }
        if direction == "DOWN" and signal_close >= signal_open:
            _step_log(4, "Candle direction mismatch (DOWN trade, bullish candle)")
            return {
                "can_trade": False,
                "details": {"reason": "Candle direction mismatch (DOWN trade, bullish candle)"},
            }

        if self._is_parabolic_spike(data_1m, atr_1m):
            _step_log(4, "Parabolic spike detected")
            return {"can_trade": False, "details": {"reason": "Parabolic spike detected"}}

        if not self._confirm_5m_structure(data_5m, direction):
            _step_log(4, f"5m structure not confirmed for {direction}")
            return {
                "can_trade": False,
                "details": {"reason": f"5m structure not confirmed for {direction}"},
            }

        zones = self._get_5m_zones(data_5m)
        near_zone, matched_zone = self._price_near_zone(
            signal_close,
            zones,
            scalping_config.SCALPING_ZONE_TOLERANCE_PCT,
        )
        if not near_zone:
            _step_log(4, "Price not near any key zone")
            return {
                "can_trade": False,
                "details": {"reason": "Price not near any key zone - waiting"},
            }

        if not self._confirm_zone_rejection(data_5m, matched_zone, direction):
            zone_level = float(matched_zone["level"]) if matched_zone else signal_close
            _step_log(4, f"No 5m zone rejection confirmed at {zone_level:.5f}")
            return {
                "can_trade": False,
                "details": {"reason": f"No 5m zone rejection confirmed at {zone_level:.5f}"},
            }

        sequence_candles = int(
            getattr(scalping_config, "SCALPING_1M_DIRECTIONAL_SEQUENCE_CANDLES", 3)
        )
        if not self._confirm_1m_directional_sequence(data_1m, direction, sequence_candles):
            _step_log(4, f"No {sequence_candles}-candle 1m directional sequence for {direction}")
            return {
                "can_trade": False,
                "details": {
                    "reason": f"No {sequence_candles}-candle 1m directional sequence for {direction}"
                },
            }

        pattern = self._detect_1m_pattern(data_1m, direction)
        confidence = 7.0
        if pattern in ("engulfing", "pin_bar"):
            confidence = min(10.0, confidence + 1.5)

        _step_log(4, "Structure gate passed")

        # STEP 5/6: Build TP/SL + validate R:R
        sl_distance = atr_1m * scalping_config.SCALPING_SL_ATR_MULTIPLIER
        tp_distance = atr_1m * scalping_config.SCALPING_TP_ATR_MULTIPLIER

        if direction == "UP":
            sl_price = current_price - sl_distance
            tp_price = current_price + tp_distance
        else:
            sl_price = current_price + sl_distance
            tp_price = current_price - tp_distance

        risk = abs(current_price - sl_price)
        reward = abs(tp_price - current_price)

        if risk == 0:
            _step_log(5, "Invalid stop-loss (risk=0)")
            return {"can_trade": False, "details": {"reason": "Invalid Stop Loss (Risk=0)"}}

        rr_ratio = reward / risk
        if rr_ratio < scalping_config.SCALPING_MIN_RR_RATIO:
            _step_log(5, f"Low R:R ({rr_ratio:.2f} < {scalping_config.SCALPING_MIN_RR_RATIO})")
            return {
                "can_trade": False,
                "details": {"reason": f"Low R:R ({rr_ratio:.2f} < {scalping_config.SCALPING_MIN_RR_RATIO})"},
            }

        _step_log(
            5,
            (
                f"Risk plan ready (Entry {current_price:.5f}, TP {tp_price:.5f}, "
                f"SL {sl_price:.5f}, R:R {rr_ratio:.2f})"
            ),
        )

        # STEP 6/6: Emit signal
        zone_level = float(matched_zone["level"]) if matched_zone else None
        zone_type = str(matched_zone.get("type")) if matched_zone else None
        signal = {
            "can_trade": True,
            "signal": direction,
            "symbol": symbol,
            "take_profit": tp_price,
            "stop_loss": sl_price,
            "risk_reward_ratio": rr_ratio,
            "min_rr_required": scalping_config.SCALPING_MIN_RR_RATIO,
            "score": confidence,
            "confidence": confidence,
            "entry_price": current_price,
            "details": {
                "reason": (
                    f"Scalping signal - {direction} trend, RSI {rsi_1m:.1f}, "
                    f"ADX {adx_1m:.1f}, R:R {rr_ratio:.2f}"
                ),
                "rsi": rsi_1m,
                "adx": adx_1m,
                "adx_slope": adx_slope,
                "body_ratio": body_ratio,
                "zone_level": zone_level,
                "zone_type": zone_type,
                "pa_pattern": pattern,
            },
        }

        _step_log(6, f"Signal generated ({direction}) | Confidence {signal['confidence']:.1f}")
        return signal

    def _determine_bias(
        self,
        df: pd.DataFrame,
        timeframe_name: str,
        fast_period: int = 20,
        slow_period: int = 50,
    ) -> Optional[str]:
        """
        Determine directional EMA bias on the last closed bar.

        Returns:
            'UP', 'DOWN', or None when EMAs are equal/insufficient data.
        """
        min_required = slow_period + 5
        if len(df) < min_required:
            logger.debug(f"[SCALPING][{timeframe_name}] Insufficient candles for bias detection")
            return None

        ema_fast = self._calculate_ema(df, fast_period)
        ema_slow = self._calculate_ema(df, slow_period)
        if ema_fast is None or ema_slow is None:
            return None

        current_fast = float(ema_fast.iloc[-2])
        current_slow = float(ema_slow.iloc[-2])

        if current_fast > current_slow:
            return "UP"
        if current_fast < current_slow:
            return "DOWN"
        return None

    def _determine_trend(
        self,
        df: pd.DataFrame,
        timeframe_name: str,
        fast_period: int = 9,
        slow_period: int = 21,
        crossover_lookback: int = 1,
        allow_alignment_fallback: bool = False,
        min_slope_pct: float = 0.0,
    ) -> Optional[str]:
        """
        Determine trend using fresh crossover first, with optional EMA
        alignment+slope fallback on the latest closed bar.

        Returns:
            'UP', 'DOWN', or None if no qualifying signal is present.
        """
        lookback = max(int(crossover_lookback), 1)
        min_required = max(slow_period + 5, lookback + 3)
        if len(df) < min_required:
            logger.debug(f"[SCALPING][{timeframe_name}] Insufficient candles for trend detection")
            return None

        ema_fast = self._calculate_ema(df, fast_period)
        ema_slow = self._calculate_ema(df, slow_period)
        if ema_fast is None or ema_slow is None:
            return None

        # Evaluate closed-candle cross events from most recent to older bars.
        for step in range(lookback):
            current_idx = -2 - step
            prev_idx = current_idx - 1
            if abs(prev_idx) > len(ema_fast):
                break

            prev_fast = float(ema_fast.iloc[prev_idx])
            prev_slow = float(ema_slow.iloc[prev_idx])
            current_fast = float(ema_fast.iloc[current_idx])
            current_slow = float(ema_slow.iloc[current_idx])

            crossed_up = (prev_fast <= prev_slow) and (current_fast > current_slow)
            crossed_down = (prev_fast >= prev_slow) and (current_fast < current_slow)

            if crossed_up:
                return "UP"
            if crossed_down:
                return "DOWN"

        if not allow_alignment_fallback:
            return None

        # Fallback: accept EMA alignment only when slope confirms momentum.
        prev_fast = float(ema_fast.iloc[-3])
        prev_slow = float(ema_slow.iloc[-3])
        current_fast = float(ema_fast.iloc[-2])
        current_slow = float(ema_slow.iloc[-2])

        if prev_fast == 0 or prev_slow == 0:
            return None

        fast_slope_pct = ((current_fast - prev_fast) / abs(prev_fast)) * 100.0
        slow_slope_pct = ((current_slow - prev_slow) / abs(prev_slow)) * 100.0
        slope_threshold = max(float(min_slope_pct), 0.0)

        aligned_up = (
            current_fast > current_slow
            and fast_slope_pct >= slope_threshold
            and slow_slope_pct >= slope_threshold
        )
        if aligned_up:
            return "UP"

        aligned_down = (
            current_fast < current_slow
            and fast_slope_pct <= -slope_threshold
            and slow_slope_pct <= -slope_threshold
        )
        if aligned_down:
            return "DOWN"

        return None

    def _calculate_ema(self, df: pd.DataFrame, period: int) -> Optional[pd.Series]:
        """
        Calculate Exponential Moving Average.

        Args:
            df: DataFrame with 'close' column
            period: EMA period

        Returns:
            pd.Series containing EMA values
        """
        if len(df) < period:
            return None
        return df["close"].ewm(span=period, adjust=False).mean()

    def _calculate_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        """
        Calculate ATR for volatility measurement.

        Args:
            df: DataFrame with OHLC data
            period: ATR period

        Returns:
            ATR value
        """
        if df is None or len(df) < period + 1:
            return 0.001

        high_low = df["high"] - df["low"]
        high_close = np.abs(df["high"] - df["close"].shift())
        low_close = np.abs(df["low"] - df["close"].shift())

        true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        true_range = true_range.dropna()
        if len(true_range) < period:
            return 0.001

        atr = true_range.rolling(period).mean().iloc[-1]
        return atr if not np.isnan(atr) else 0.001

    def _is_parabolic_spike(self, df: pd.DataFrame, atr: float) -> bool:
        """
        Detect parabolic spikes:
        - 3 large closed candles in a row (> 2x ATR), or
        - a single closed candle with body > 3x ATR.
        """
        if len(df) < 4 or atr <= 0:
            return False

        last_3_closed = df.iloc[-4:-1]
        large_candle_count = 0
        for _, row in last_3_closed.iterrows():
            candle_size = abs(float(row["close"]) - float(row["open"]))
            if candle_size > (atr * 2.0):
                large_candle_count += 1

        if large_candle_count >= 3:
            return True

        last_body = abs(float(df["close"].iloc[-2]) - float(df["open"].iloc[-2]))
        return last_body > (atr * 3.0)

    def _confirm_5m_structure(self, df_5m: pd.DataFrame, direction: str) -> bool:
        """
        Confirm price is on the correct side of 5m EMA(21).
        """
        if df_5m is None or len(df_5m) < 25:
            return True

        ema21 = self._calculate_ema(df_5m, 21)
        if ema21 is None or len(ema21) < 2:
            return True

        last_close = float(df_5m["close"].iloc[-2])
        ema21_val = float(ema21.iloc[-2])
        if direction == "UP" and last_close < ema21_val:
            return False
        if direction == "DOWN" and last_close > ema21_val:
            return False
        return True

    def _check_1h_break_of_structure(self, df_1h: pd.DataFrame, direction: str) -> bool:
        """
        Confirm 1h structure progression:
        - UP requires latest swing high > previous swing high.
        - DOWN requires latest swing low < previous swing low.
        """
        if df_1h is None or len(df_1h) < 10:
            return True

        highs = df_1h["high"].values
        lows = df_1h["low"].values

        swing_highs = [
            highs[i]
            for i in range(2, len(highs) - 2)
            if highs[i] > highs[i - 1]
            and highs[i] > highs[i - 2]
            and highs[i] > highs[i + 1]
            and highs[i] > highs[i + 2]
        ]
        swing_lows = [
            lows[i]
            for i in range(2, len(lows) - 2)
            if lows[i] < lows[i - 1]
            and lows[i] < lows[i - 2]
            and lows[i] < lows[i + 1]
            and lows[i] < lows[i + 2]
        ]

        if direction == "UP" and len(swing_highs) >= 2:
            return float(swing_highs[-1]) > float(swing_highs[-2])
        if direction == "DOWN" and len(swing_lows) >= 2:
            return float(swing_lows[-1]) < float(swing_lows[-2])
        return True

    def _get_5m_zones(self, df_5m: pd.DataFrame, lookback: int = 50) -> List[Dict[str, Any]]:
        """
        Build key 5m support/resistance zones from extremes and pivots.
        """
        if df_5m is None or len(df_5m) < 10:
            return []

        frame = df_5m.tail(lookback).reset_index(drop=True)
        zones: List[Dict[str, Any]] = []

        zones.append({"level": float(frame["high"].max()), "type": "resistance", "source": "extreme"})
        zones.append({"level": float(frame["low"].min()), "type": "support", "source": "extreme"})

        highs = frame["high"].values
        lows = frame["low"].values
        for i in range(2, len(frame) - 2):
            if (
                highs[i] > highs[i - 1]
                and highs[i] > highs[i - 2]
                and highs[i] > highs[i + 1]
                and highs[i] > highs[i + 2]
            ):
                zones.append({"level": float(highs[i]), "type": "resistance", "source": "pivot"})
            if (
                lows[i] < lows[i - 1]
                and lows[i] < lows[i - 2]
                and lows[i] < lows[i + 1]
                and lows[i] < lows[i + 2]
            ):
                zones.append({"level": float(lows[i]), "type": "support", "source": "pivot"})
        return zones

    def _price_near_zone(
        self,
        price: float,
        zones: List[Dict[str, Any]],
        tolerance_pct: float = 0.0015,
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Check whether price is near at least one zone.
        """
        best_zone: Optional[Dict[str, Any]] = None
        best_diff = float("inf")
        for zone in zones:
            level = float(zone.get("level", 0.0))
            if level <= 0:
                continue
            diff = abs(price - level) / level
            if diff <= tolerance_pct and diff < best_diff:
                best_diff = diff
                best_zone = zone
        return best_zone is not None, best_zone

    def _confirm_zone_rejection(
        self,
        df_5m: pd.DataFrame,
        matched_zone: Optional[Dict[str, Any]],
        direction: str,
    ) -> bool:
        """
        Confirm last closed 5m candle rejected the matched zone.
        """
        if df_5m is None or len(df_5m) < 5 or matched_zone is None:
            return True

        zone_level = float(matched_zone["level"])
        zone_type = str(matched_zone.get("type", ""))
        last_5m = df_5m.iloc[-2]

        l_open = float(last_5m["open"])
        l_close = float(last_5m["close"])
        l_low = float(last_5m["low"])
        l_high = float(last_5m["high"])
        l_range = l_high - l_low
        if l_range == 0:
            return True

        body_ratio = abs(l_close - l_open) / l_range

        if direction == "UP" and zone_type in ("support", "middle"):
            touched_zone = l_low <= zone_level * 1.001
            rejected = l_close > zone_level and body_ratio >= 0.50
            return touched_zone and rejected

        if direction == "DOWN" and zone_type in ("resistance", "middle"):
            touched_zone = l_high >= zone_level * 0.999
            rejected = l_close < zone_level and body_ratio >= 0.50
            return touched_zone and rejected

        return True

    def _confirm_1m_directional_sequence(
        self,
        df_1m: pd.DataFrame,
        direction: str,
        sequence_candles: int = 3,
    ) -> bool:
        """
        Require directional close progression on recent closed 1m candles.
        """
        steps = max(int(sequence_candles), 1)
        needed_closes = steps + 1
        if df_1m is None or len(df_1m) < needed_closes + 1:
            return False

        closes = [float(df_1m["close"].iloc[-2 - i]) for i in range(needed_closes)]
        # closes[0] is most recent closed candle; iterate oldest -> newest
        closes = list(reversed(closes))

        if direction == "DOWN":
            return all(curr < prev for prev, curr in zip(closes, closes[1:]))
        if direction == "UP":
            return all(curr > prev for prev, curr in zip(closes, closes[1:]))
        return False

    def _detect_1m_pattern(self, df_1m: pd.DataFrame, direction: str) -> str:
        """
        Detect lightweight price-action pattern on signal candle.
        """
        if df_1m is None or len(df_1m) < 3:
            return "none"

        cur = df_1m.iloc[-2]
        prev = df_1m.iloc[-3]

        c_open = float(cur["open"])
        c_close = float(cur["close"])
        c_high = float(cur["high"])
        c_low = float(cur["low"])
        p_open = float(prev["open"])
        p_close = float(prev["close"])

        c_body = abs(c_close - c_open)
        c_range = c_high - c_low
        upper_wick = c_high - max(c_open, c_close)
        lower_wick = min(c_open, c_close) - c_low

        if direction == "UP" and c_open < p_close and c_close > p_open and c_close > c_open:
            return "engulfing"
        if direction == "DOWN" and c_open > p_close and c_close < p_open and c_close < c_open:
            return "engulfing"

        if c_body > 0 and c_range > 0:
            if direction == "UP" and lower_wick >= c_body * 2.0:
                if (min(c_open, c_close) - c_low) / c_range >= 0.55:
                    return "pin_bar"
            if direction == "DOWN" and upper_wick >= c_body * 2.0:
                if (c_high - max(c_open, c_close)) / c_range >= 0.55:
                    return "pin_bar"
        return "none"

    def get_required_timeframes(self) -> List[str]:
        """
        Get list of timeframes required by scalping strategy.

        Returns:
            ['1h', '5m', '1m']
        """
        return scalping_config.SCALPING_TIMEFRAMES

    def get_symbols(self) -> List[str]:
        """Return scalping symbol universe from local scalping config."""
        blocked = set(getattr(scalping_config, "BLOCKED_SYMBOLS", set()))
        rollout_symbols = list(getattr(scalping_config, "SCALPING_ROLLOUT_SYMBOLS", []) or [])
        symbols = rollout_symbols if rollout_symbols else list(scalping_config.SYMBOLS)
        return [symbol for symbol in symbols if symbol not in blocked]

    def get_asset_config(self) -> Dict:
        """Return scalping asset configuration."""
        return dict(scalping_config.ASSET_CONFIG)

    def get_strategy_name(self) -> str:
        """
        Get strategy name.

        Returns:
            'Scalping'
        """
        return "Scalping"

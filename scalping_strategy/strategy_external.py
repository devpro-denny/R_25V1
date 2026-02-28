"""
Scalping Strategy Implementation
3-timeframe analysis — date-19 signal logic preserved,
current package structure / config / closed-candle discipline retained.
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
    Signal logic mirrors the date-19 version (simple EMA alignment,
    no zone / structure / sequence / body-ratio gates).
    Package structure, closed-candle discipline, and config references
    are from the current production version.
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
            line = f"[SCALPING][{symbol}] STEP {step}/9 | {ts} | {message}"
            getattr(logger, level)(line)

        # ------------------------------------------------------------------
        # STEP 1/9: Validate input data
        # ------------------------------------------------------------------
        _step_log(1, "Starting analysis")
        if not all([data_1h is not None, data_5m is not None, data_1m is not None]):
            logger.error(f"[SCALPING][{symbol}] Missing required timeframe data (1h, 5m, 1m)")
            return {
                "can_trade": False,
                "details": {"reason": "Missing required timeframe data (1h, 5m, 1m)"},
            }

        if len(data_1h) < 50 or len(data_5m) < 50 or len(data_1m) < 50:
            logger.error(
                f"[SCALPING][{symbol}] Insufficient data (need at least 50 candles per timeframe)"
            )
            return {
                "can_trade": False,
                "details": {"reason": "Insufficient data (need >50 candles)"},
            }

        # ------------------------------------------------------------------
        # STEP 2/9: Trend alignment — 1h and 5m EMA must agree
        # Uses simple EMA alignment (date-19 logic), not fresh-crossover gate.
        # ------------------------------------------------------------------
        trend_1h = self._determine_trend(data_1h, "1h")
        trend_5m = self._determine_trend(data_5m, "5m")

        if trend_1h is None or trend_5m is None:
            _step_log(2, "Could not determine trend on 1h or 5m")
            return {
                "can_trade": False,
                "details": {"reason": "Could not determine trend"},
            }

        if trend_1h != trend_5m:
            _step_log(2, f"Trend mismatch (1h: {trend_1h}, 5m: {trend_5m})")
            return {
                "can_trade": False,
                "details": {"reason": f"Trend mismatch (1h: {trend_1h}, 5m: {trend_5m})"},
            }

        direction = trend_1h  # "UP" or "DOWN"
        _step_log(2, f"Trend aligned: {direction}")

        # ------------------------------------------------------------------
        # STEP 3/9: RSI and ADX — load via package hook (current structure),
        # read from last *closed* candle (iloc[-2], current discipline).
        # ------------------------------------------------------------------
        package_module = import_module("scalping_strategy")
        calculate_rsi = getattr(package_module, "calculate_rsi", _default_calculate_rsi)
        calculate_adx = getattr(package_module, "calculate_adx", _default_calculate_adx)

        rsi_series = calculate_rsi(data_1m, period=14)
        adx_series = calculate_adx(data_1m, period=14)

        rsi_1m: float
        adx_1m: float

        if rsi_series is not None and len(rsi_series) >= 2:
            rsi_1m = float(rsi_series.iloc[-2])
        else:
            rsi_1m = float("nan")
        if np.isnan(rsi_1m):
            logger.warning(f"[SCALPING][{symbol}] RSI fallback applied (50)")
            rsi_1m = 50.0

        if adx_series is not None and len(adx_series) >= 2:
            adx_1m = float(adx_series.iloc[-2])
        else:
            adx_1m = float("nan")
        if np.isnan(adx_1m):
            logger.warning(f"[SCALPING][{symbol}] ADX fallback applied (0)")
            adx_1m = 0.0

        logger.debug(f"[SCALPING][{symbol}] Indicators | RSI={rsi_1m:.2f} ADX={adx_1m:.2f}")

        # ------------------------------------------------------------------
        # STEP 4/9: ADX threshold
        # ------------------------------------------------------------------
        if adx_1m < scalping_config.SCALPING_ADX_THRESHOLD:
            _step_log(
                4,
                f"Weak trend (ADX {adx_1m:.1f} < {scalping_config.SCALPING_ADX_THRESHOLD})",
            )
            return {
                "can_trade": False,
                "details": {
                    "reason": (
                        f"Weak trend (ADX {adx_1m:.1f} < {scalping_config.SCALPING_ADX_THRESHOLD})"
                    )
                },
            }
        _step_log(4, f"ADX gate passed ({adx_1m:.1f})")

        # ------------------------------------------------------------------
        # STEP 5/9: RSI range validation
        # ------------------------------------------------------------------
        if direction == "UP":
            if not (scalping_config.SCALPING_RSI_UP_MIN <= rsi_1m <= scalping_config.SCALPING_RSI_UP_MAX):
                _step_log(5, f"RSI {rsi_1m:.1f} not in UP range")
                return {
                    "can_trade": False,
                    "details": {"reason": f"RSI {rsi_1m:.1f} not in UP range"},
                }
        else:
            if not (
                scalping_config.SCALPING_RSI_DOWN_MIN <= rsi_1m <= scalping_config.SCALPING_RSI_DOWN_MAX
            ):
                _step_log(5, f"RSI {rsi_1m:.1f} not in DOWN range")
                return {
                    "can_trade": False,
                    "details": {"reason": f"RSI {rsi_1m:.1f} not in DOWN range"},
                }
        _step_log(5, f"RSI gate passed ({rsi_1m:.1f})")

        # ------------------------------------------------------------------
        # STEP 6/9: Price movement filter (date-19 uses absolute pct, not
        # directional / adverse-move logic).
        # Uses live price (iloc[-1]) consistent with date-19 behaviour.
        # ------------------------------------------------------------------
        atr_1m = self._calculate_atr(data_1m, period=14)
        current_price = float(data_1m["close"].iloc[-1])

        base_threshold = scalping_config.ASSET_CONFIG.get(symbol, {}).get(
            "movement_threshold_pct", 0.7
        )
        movement_threshold = base_threshold * scalping_config.SCALPING_ASSET_MOVEMENT_MULTIPLIER

        price_5_candles_ago = float(data_1m["close"].iloc[-6])
        if price_5_candles_ago == 0:
            _step_log(6, "Invalid reference price for movement check")
            return {
                "can_trade": False,
                "details": {"reason": "Invalid reference price for movement check"},
            }

        price_change_pct = abs(
            (current_price - price_5_candles_ago) / price_5_candles_ago * 100
        )
        if price_change_pct > movement_threshold:
            _step_log(6, f"Price moved {price_change_pct:.2f}% > threshold {movement_threshold:.2f}%")
            return {
                "can_trade": False,
                "details": {
                    "reason": f"Price movement too high ({price_change_pct:.2f}%)"
                },
            }
        _step_log(6, f"Movement gate passed ({price_change_pct:.2f}%)")

        # ------------------------------------------------------------------
        # STEP 7/9: Momentum breakout — date-19 uses live candle (iloc[-1]).
        # ------------------------------------------------------------------
        last_candle_size = abs(
            float(data_1m["close"].iloc[-1]) - float(data_1m["open"].iloc[-1])
        )
        momentum_threshold = atr_1m * scalping_config.SCALPING_MOMENTUM_THRESHOLD

        if last_candle_size < momentum_threshold:
            _step_log(
                7,
                f"No momentum breakout (candle {last_candle_size:.5f} < {momentum_threshold:.5f})",
            )
            return {
                "can_trade": False,
                "details": {"reason": "No momentum breakout"},
            }
        _step_log(7, "Momentum breakout confirmed")

        # ------------------------------------------------------------------
        # STEP 8/9: Parabolic spike detection (date-19 version: 3 consecutive
        # large candles using last 3 live candles).
        # ------------------------------------------------------------------
        if self._is_parabolic_spike(data_1m, atr_1m):
            _step_log(8, "Parabolic spike detected")
            return {
                "can_trade": False,
                "details": {"reason": "Parabolic spike detected"},
            }
        _step_log(8, "No parabolic spike")

        # ------------------------------------------------------------------
        # STEP 9/9: Build TP/SL + validate R:R, then emit signal.
        # Uses live price (current_price) for entry — same as date-19.
        # ------------------------------------------------------------------
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
            _step_log(9, "Invalid stop-loss (risk=0)")
            return {
                "can_trade": False,
                "details": {"reason": "Invalid Stop Loss (Risk=0)"},
            }

        rr_ratio = reward / risk
        if rr_ratio < scalping_config.SCALPING_MIN_RR_RATIO:
            _step_log(
                9,
                f"Low R:R ({rr_ratio:.2f} < {scalping_config.SCALPING_MIN_RR_RATIO})",
            )
            return {
                "can_trade": False,
                "details": {
                    "reason": (
                        f"Low R:R ({rr_ratio:.2f} < {scalping_config.SCALPING_MIN_RR_RATIO})"
                    )
                },
            }

        _step_log(
            9,
            (
                f"Signal generated ({direction}) | Entry {current_price:.5f} "
                f"TP {tp_price:.5f} SL {sl_price:.5f} R:R {rr_ratio:.2f}"
            ),
        )

        signal = {
            "can_trade": True,
            "signal": direction,
            "symbol": symbol,
            "take_profit": tp_price,
            "stop_loss": sl_price,
            "risk_reward_ratio": rr_ratio,
            "score": 7.0,
            "confidence": 7.0,
            "entry_price": current_price,
            "details": {
                "reason": (
                    f"Scalping signal - {direction} trend, RSI {rsi_1m:.1f}, "
                    f"ADX {adx_1m:.1f}, R:R {rr_ratio:.2f}"
                ),
                "rsi": rsi_1m,
                "adx": adx_1m,
            },
        }
        return signal

    # ------------------------------------------------------------------
    # Trend detection — date-19 simple EMA9/21 alignment
    # (no crossover requirement, no fallback slope check)
    # ------------------------------------------------------------------
    def _determine_trend(
        self,
        df: pd.DataFrame,
        timeframe_name: str,
    ) -> Optional[str]:
        """
        Determine trend using simple EMA9 vs EMA21 alignment on the last
        closed bar (iloc[-2]).  This is the date-19 logic — no fresh
        crossover requirement.

        Returns:
            'UP', 'DOWN', or None.
        """
        if len(df) < 25:
            logger.debug(
                f"[SCALPING][{timeframe_name}] Insufficient candles for trend detection"
            )
            return None

        ema_fast = self._calculate_ema(df, 9)
        ema_slow = self._calculate_ema(df, 21)

        if ema_fast is None or ema_slow is None:
            return None

        # Use last closed candle consistent with current closed-candle discipline
        current_fast = float(ema_fast.iloc[-2])
        current_slow = float(ema_slow.iloc[-2])

        if current_fast > current_slow:
            return "UP"
        if current_fast < current_slow:
            return "DOWN"
        return None

    def _calculate_ema(self, df: pd.DataFrame, period: int) -> Optional[pd.Series]:
        """Calculate Exponential Moving Average."""
        if len(df) < period:
            return None
        return df["close"].ewm(span=period, adjust=False).mean()

    def _calculate_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        """Calculate ATR for volatility measurement."""
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
        Detect parabolic spike — date-19 version.
        Checks last 3 live candles (iloc[-3:]) for 3 consecutive bodies > 2× ATR.
        """
        if len(df) < 3 or atr <= 0:
            return False

        last_3 = df.iloc[-3:]
        large_candle_count = 0
        for _, row in last_3.iterrows():
            candle_size = abs(float(row["close"]) - float(row["open"]))
            if candle_size > atr * 2.0:
                large_candle_count += 1

        return large_candle_count >= 3

    # ------------------------------------------------------------------
    # BaseStrategy interface
    # ------------------------------------------------------------------
    def get_required_timeframes(self) -> List[str]:
        """Returns ['1h', '5m', '1m']."""
        return scalping_config.SCALPING_TIMEFRAMES

    def get_symbols(self) -> List[str]:
        """Return scalping symbol universe."""
        return list(scalping_config.SYMBOLS)

    def get_asset_config(self) -> Dict:
        """Return scalping asset configuration."""
        return dict(scalping_config.ASSET_CONFIG)

    def get_strategy_name(self) -> str:
        """Returns 'Scalping'."""
        return "Scalping"

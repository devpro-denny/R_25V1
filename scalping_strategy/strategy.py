"""
Scalping Strategy Implementation
3-timeframe analysis with relaxed thresholds for more frequent trading
"""

from base_strategy import BaseStrategy
from typing import Dict, List, Optional
from datetime import datetime
import pandas as pd
import numpy as np
from importlib import import_module
from utils import setup_logger
from . import config as scalping_config
from indicators import calculate_rsi as _default_calculate_rsi, calculate_adx as _default_calculate_adx

logger = setup_logger()


class ScalpingStrategy(BaseStrategy):
    """
    Scalping strategy using 3 timeframes (1h, 5m, 1m) with relaxed validation rules.
    Trades more frequently than conservative strategy with tighter risk management.
    """
    
    def __init__(self):
        """Initialize scalping strategy"""
        pass
    
    def analyze(self, **kwargs) -> Optional[Dict]:
        """
        Analyze market data for scalping opportunities.

        Args:
            **kwargs: Must include data_1h, data_5m, data_1m, symbol

        Returns:
            Signal dict if trade should be executed, None otherwise
        """
        data_1h = kwargs.get('data_1h')
        data_5m = kwargs.get('data_5m')
        data_1m = kwargs.get('data_1m')
        symbol = kwargs.get('symbol', 'R_50')

        def _step_log(step: int, message: str, emoji: str = "ℹ️", level: str = "info") -> None:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            line = f"[SCALPING][{symbol}] STEP {step}/6 | {ts} | {emoji} {message}"
            getattr(logger, level)(line)

        # STEP 1/6: Validate input data
        _step_log(1, "Starting analysis", emoji="🔎")
        if not all([data_1h is not None, data_5m is not None, data_1m is not None]):
            logger.error(f"[SCALPING][{symbol}] ❌ Missing required timeframe data (1h, 5m, 1m)")
            return {'can_trade': False, 'details': {'reason': 'Missing required timeframe data (1h, 5m, 1m)'}}

        if len(data_1h) < 50 or len(data_5m) < 50 or len(data_1m) < 50:
            logger.error(f"[SCALPING][{symbol}] ❌ Insufficient data (need at least 50 candles per timeframe)")
            return {'can_trade': False, 'details': {'reason': 'Insufficient data (need >50 candles)'}}

        current_price = data_1m['close'].iloc[-1]
        logger.debug(f"[SCALPING][{symbol}] 💰 Price={current_price:.5f}")

        # STEP 2/6: Trend alignment
        trend_1h = self._determine_trend(data_1h, '1h')
        trend_5m = self._determine_trend(data_5m, '5m')

        if trend_1h is None or trend_5m is None:
            _step_log(2, "Trend unavailable", emoji="❌")
            return {'can_trade': False, 'details': {'reason': 'Could not determine trend'}}

        if trend_1h != trend_5m:
            _step_log(2, f"Trend mismatch (1h: {trend_1h}, 5m: {trend_5m})", emoji="❌")
            return {'can_trade': False, 'details': {'reason': f'Trend mismatch (1h: {trend_1h}, 5m: {trend_5m})'}}

        direction = trend_1h
        _step_log(2, f"Trend aligned: {direction}", emoji="✅")

        # STEP 3/6: Indicator validation (RSI/ADX)
        package_module = import_module("scalping_strategy")
        calculate_rsi = getattr(package_module, "calculate_rsi", _default_calculate_rsi)
        calculate_adx = getattr(package_module, "calculate_adx", _default_calculate_adx)

        rsi_series = calculate_rsi(data_1m, period=14)
        adx_series = calculate_adx(data_1m, period=14)

        rsi_1m = rsi_series.iloc[-1] if rsi_series is not None and not rsi_series.empty else None
        adx_1m = adx_series.iloc[-1] if adx_series is not None and not adx_series.empty else None

        if rsi_1m is None or np.isnan(rsi_1m):
            logger.warning(f"[SCALPING][{symbol}] ⚠️ RSI fallback applied (50)")
            rsi_1m = 50.0

        if adx_1m is None or np.isnan(adx_1m):
            logger.warning(f"[SCALPING][{symbol}] ⚠️ ADX fallback applied (0)")
            adx_1m = 0.0

        logger.debug(f"[SCALPING][{symbol}] 📊 Indicators | RSI={rsi_1m:.2f} ADX={adx_1m:.2f}")

        if adx_1m < scalping_config.SCALPING_ADX_THRESHOLD:
            _step_log(
                3,
                f"Weak trend (ADX {adx_1m:.1f} < {scalping_config.SCALPING_ADX_THRESHOLD})",
                emoji="❌",
            )
            return {
                'can_trade': False,
                'details': {'reason': f'Weak trend (ADX {adx_1m:.1f} < {scalping_config.SCALPING_ADX_THRESHOLD})'}
            }

        if direction == "UP":
            if not (scalping_config.SCALPING_RSI_UP_MIN <= rsi_1m <= scalping_config.SCALPING_RSI_UP_MAX):
                _step_log(3, f"RSI {rsi_1m:.1f} not in UP range", emoji="❌")
                return {'can_trade': False, 'details': {'reason': f'RSI {rsi_1m:.1f} not in UP range'}}
        else:
            if not (scalping_config.SCALPING_RSI_DOWN_MIN <= rsi_1m <= scalping_config.SCALPING_RSI_DOWN_MAX):
                _step_log(3, f"RSI {rsi_1m:.1f} not in DOWN range", emoji="❌")
                return {'can_trade': False, 'details': {'reason': f'RSI {rsi_1m:.1f} not in DOWN range'}}

        _step_log(3, "Indicator gate passed", emoji="✅")

        # STEP 4/6: Momentum and structure checks
        atr_1m = self._calculate_atr(data_1m, period=14)

        base_threshold = scalping_config.ASSET_CONFIG.get(symbol, {}).get('movement_threshold_pct', 0.7)
        movement_threshold = base_threshold * scalping_config.SCALPING_ASSET_MOVEMENT_MULTIPLIER

        price_5_candles_ago = data_1m['close'].iloc[-6]
        price_change_pct = abs((current_price - price_5_candles_ago) / price_5_candles_ago * 100)

        if price_change_pct > movement_threshold:
            _step_log(
                4,
                f"Price movement too high ({price_change_pct:.2f}% > {movement_threshold:.2f}%)",
                emoji="❌",
            )
            return {'can_trade': False, 'details': {'reason': f'Price movement too high ({price_change_pct:.2f}%)'}}

        last_candle_size = abs(data_1m['close'].iloc[-1] - data_1m['open'].iloc[-1])
        momentum_threshold = atr_1m * scalping_config.SCALPING_MOMENTUM_THRESHOLD

        if last_candle_size < momentum_threshold:
            _step_log(4, "No momentum breakout", emoji="❌")
            return {'can_trade': False, 'details': {'reason': 'No momentum breakout'}}

        if self._is_parabolic_spike(data_1m, atr_1m):
            _step_log(4, "Parabolic spike detected", emoji="❌")
            return {'can_trade': False, 'details': {'reason': 'Parabolic spike detected'}}

        _step_log(4, "Structure gate passed", emoji="✅")

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
            _step_log(5, "Invalid stop-loss (risk=0)", emoji="❌")
            return {'can_trade': False, 'details': {'reason': 'Invalid Stop Loss (Risk=0)'}}

        rr_ratio = reward / risk
        if rr_ratio < scalping_config.SCALPING_MIN_RR_RATIO:
            _step_log(
                5,
                f"Low R:R ({rr_ratio:.2f} < {scalping_config.SCALPING_MIN_RR_RATIO})",
                emoji="❌",
            )
            return {
                'can_trade': False,
                'details': {'reason': f'Low R:R ({rr_ratio:.2f} < {scalping_config.SCALPING_MIN_RR_RATIO})'}
            }

        _step_log(
            5,
            f"Risk plan ready (Entry {current_price:.5f}, TP {tp_price:.5f}, SL {sl_price:.5f}, R:R {rr_ratio:.2f})",
            emoji="✅",
        )

        # STEP 6/6: Emit signal
        signal = {
            'can_trade': True,
            'signal': direction,
            'symbol': symbol,
            'take_profit': tp_price,
            'stop_loss': sl_price,
            'risk_reward_ratio': rr_ratio,
            'score': 7.0,
            'confidence': 7.0,
            'entry_price': current_price,
            'details': {
                'reason': f"Scalping signal - {direction} trend, RSI {rsi_1m:.1f}, ADX {adx_1m:.1f}, R:R {rr_ratio:.2f}",
                'rsi': rsi_1m,
                'adx': adx_1m,
            }
        }

        signal_emoji = "🟢" if direction == "UP" else "🔴"
        _step_log(6, f"Signal generated ({direction}) | Confidence {signal['confidence']:.1f}", emoji=signal_emoji)

        return signal
    def _determine_trend(self, df: pd.DataFrame, timeframe_name: str) -> Optional[str]:
        """
        Determine trend based on EMA logic (EMA 9 vs EMA 21).
        
        Args:
            df: DataFrame with OHLC data
            timeframe_name: Name for logging
        
        Returns:
            'BULLISH', 'BEARISH', or None
        """
        if len(df) < 25:
            return None
        
        # Calculate EMAs
        ema_fast = self._calculate_ema(df, 9)
        ema_slow = self._calculate_ema(df, 21)
        
        if ema_fast is None or ema_slow is None:
            return None
            
        current_fast = ema_fast.iloc[-1]
        current_slow = ema_slow.iloc[-1]
        prev_fast = ema_fast.iloc[-2]
        prev_slow = ema_slow.iloc[-2]
        
        # Bullish: Fast EMA > Slow EMA
        if current_fast > current_slow:
            return "UP"
        
        # Bearish: Fast EMA < Slow EMA
        if current_fast < current_slow:
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
        return df['close'].ewm(span=period, adjust=False).mean()
    
    def _calculate_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        """
        Calculate ATR for volatility measurement.
        
        Args:
            df: DataFrame with OHLC data
            period: ATR period
        
        Returns:
            ATR value
        """
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = ranges.max(axis=1)
        atr = true_range.rolling(period).mean().iloc[-1]
        
        return atr if not np.isnan(atr) else 0.001
    
    def _is_parabolic_spike(self, df: pd.DataFrame, atr: float) -> bool:
        """
        Detect parabolic spike (3+ consecutive large candles).
        
        Args:
            df: DataFrame with OHLC data
            atr: Current ATR value
        
        Returns:
            True if parabolic spike detected
        """
        if len(df) < 3:
            return False
        
        # Check last 3 candles
        last_3_candles = df.iloc[-3:]
        large_candle_count = 0
        
        for idx, row in last_3_candles.iterrows():
            candle_size = abs(row['close'] - row['open'])
            if candle_size > (atr * 2.0):  # 2x ATR threshold
                large_candle_count += 1
        
        return large_candle_count >= 3
    
    def get_required_timeframes(self) -> List[str]:
        """
        Get list of timeframes required by scalping strategy.
        
        Returns:
            ['1h', '5m', '1m']
        """
        return scalping_config.SCALPING_TIMEFRAMES

    def get_symbols(self) -> List[str]:
        """Return scalping symbol universe from local scalping config."""
        return list(scalping_config.SYMBOLS)

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


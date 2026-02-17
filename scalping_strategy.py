"""
Scalping Strategy Implementation
3-timeframe analysis with relaxed thresholds for more frequent trading
"""

from base_strategy import BaseStrategy
from typing import Dict, List, Optional
import pandas as pd
import numpy as np
import logging
import config
import scalping_config
from indicators import calculate_rsi, calculate_adx

logger = logging.getLogger(__name__)


class ScalpingStrategy(BaseStrategy):
    """
    Scalping strategy using 3 timeframes (1h, 5m, 1m) with relaxed validation rules.
    Trades more frequently than conservative strategy with tighter risk management.
    """
    
    def __init__(self):
        """Initialize scalping strategy"""
        logger.info("✅ Scalping strategy initialized")
        logger.info(f"📊 Timeframes: {scalping_config.SCALPING_TIMEFRAMES}")
        logger.info(f"📈 ADX Threshold: {scalping_config.SCALPING_ADX_THRESHOLD}")
        logger.info(f"📉 RSI Range UP: {scalping_config.SCALPING_RSI_UP_MIN}-{scalping_config.SCALPING_RSI_UP_MAX}")
        logger.info(f"📊 RSI Range DOWN: {scalping_config.SCALPING_RSI_DOWN_MIN}-{scalping_config.SCALPING_RSI_DOWN_MAX}")
    
    def analyze(self, **kwargs) -> Optional[Dict]:
        """
        Analyze market data for scalping opportunities.
        
        Args:
            **kwargs: Must include data_1h, data_5m, data_1m, symbol
        
        Returns:
            Signal dict if trade should be executed, None otherwise
        """
        # Extract required data
        data_1h = kwargs.get('data_1h')
        data_5m = kwargs.get('data_5m')
        data_1m = kwargs.get('data_1m')
        symbol = kwargs.get('symbol', 'R_50')
        
        # Validate required data
        if not all([data_1h is not None, data_5m is not None, data_1m is not None]):
            logger.error("❌ Scalping: Missing required timeframe data (1h, 5m, 1m)")
            return None
        
        if len(data_1h) < 50 or len(data_5m) < 50 or len(data_1m) < 50:
            logger.error("❌ Scalping: Insufficient data (need at least 50 candles per timeframe)")
            return None
        
        logger.info(f"\n{'='*60}\n[SCALPING] 🎯 Analysis: {symbol}\n{'='*60}")
        
        # Get current price
        current_price = data_1m['close'].iloc[-1]
        logger.info(f"[SCALPING] 💰 Current Price: {current_price}")
        
        # =================================================================
        # CHECK 1: Trend Alignment (1h and 5m must agree)
        # =================================================================
        trend_1h = self._determine_trend(data_1h, '1h')
        trend_5m = self._determine_trend(data_5m, '5m')
        
        if trend_1h is None or trend_5m is None:
            logger.info("[SCALPING] ❌ CHECK 1 FAILED: Could not determine trend")
            return None
        
        if trend_1h != trend_5m:
            logger.info(f"[SCALPING] ❌ CHECK 1 FAILED: Trend mismatch - 1h: {trend_1h}, 5m: {trend_5m}")
            return None
        
        direction = trend_1h
        logger.info(f"[SCALPING] ✅ CHECK 1 PASSED: Trend aligned - {direction}")
        
        # =================================================================
        # CHECK 2-4: RSI and ADX validation
        # =================================================================
        # Calculate indicators with fallback
        rsi_1m = calculate_rsi(data_1m, period=14)
        adx_1m = calculate_adx(data_1m, period=14)
        
        # Fallback on calculation failure
        if rsi_1m is None or np.isnan(rsi_1m):
            logger.warning("⚠️ RSI calculation failed, using fallback RSI=50")
            rsi_1m = 50.0
        
        if adx_1m is None or np.isnan(adx_1m):
            logger.warning("⚠️ ADX calculation failed, using fallback ADX=0")
            adx_1m = 0.0
        
        logger.info(f"[SCALPING] 📊 Indicators - RSI: {rsi_1m:.2f}, ADX: {adx_1m:.2f}")
        
        # CHECK 2: ADX Threshold
        if adx_1m < scalping_config.SCALPING_ADX_THRESHOLD:
            logger.info(f"[SCALPING] ❌ CHECK 2 FAILED: ADX {adx_1m:.2f} < {scalping_config.SCALPING_ADX_THRESHOLD}")
            return None
        logger.info(f"[SCALPING] ✅ CHECK 2 PASSED: ADX {adx_1m:.2f} >= {scalping_config.SCALPING_ADX_THRESHOLD}")
        
        # CHECK 3 & 4: RSI Range validation
        if direction == "BULLISH":
            if not (scalping_config.SCALPING_RSI_UP_MIN <= rsi_1m <= scalping_config.SCALPING_RSI_UP_MAX):
                logger.info(f"[SCALPING] ❌ CHECK 3 FAILED: RSI {rsi_1m:.2f} not in UP range [{scalping_config.SCALPING_RSI_UP_MIN}-{scalping_config.SCALPING_RSI_UP_MAX}]")
                return None
            logger.info(f"[SCALPING] ✅ CHECK 3 PASSED: RSI {rsi_1m:.2f} in UP range")
        else:  # BEARISH
            if not (scalping_config.SCALPING_RSI_DOWN_MIN <= rsi_1m <= scalping_config.SCALPING_RSI_DOWN_MAX):
                logger.info(f"[SCALPING] ❌ CHECK 4 FAILED: RSI {rsi_1m:.2f} not in DOWN range [{scalping_config.SCALPING_RSI_DOWN_MIN}-{scalping_config.SCALPING_RSI_DOWN_MAX}]")
                return None
            logger.info(f"[SCALPING] ✅ CHECK 4 PASSED: RSI {rsi_1m:.2f} in DOWN range")
        
        # =================================================================
        # CHECK 5: Price Movement Filter
        # =================================================================
        atr_1m = self._calculate_atr(data_1m, period=14)
        
        # Get movement threshold for this symbol
        base_threshold = config.ASSET_CONFIG.get(symbol, {}).get('movement_threshold_pct', 0.7)
        movement_threshold = base_threshold * scalping_config.SCALPING_ASSET_MOVEMENT_MULTIPLIER
        
        # Calculate recent price movement
        price_5_candles_ago = data_1m['close'].iloc[-6]
        price_change_pct = abs((current_price - price_5_candles_ago) / price_5_candles_ago * 100)
        
        if price_change_pct > movement_threshold:
            logger.info(f"[SCALPING] ❌ CHECK 5 FAILED: Price moved {price_change_pct:.2f}% > threshold {movement_threshold:.2f}%")
            return None
        logger.info(f"[SCALPING] ✅ CHECK 5 PASSED: Price movement {price_change_pct:.2f}% <= {movement_threshold:.2f}%")
        
        # =================================================================
        # CHECK 6: Momentum Breakout
        # =================================================================
        last_candle_size = abs(data_1m['close'].iloc[-1] - data_1m['open'].iloc[-1])
        momentum_threshold = atr_1m * scalping_config.SCALPING_MOMENTUM_THRESHOLD
        
        if last_candle_size < momentum_threshold:
            logger.info(f"[SCALPING] ❌ CHECK 6 FAILED: Candle size {last_candle_size:.5f} < {momentum_threshold:.5f} ({scalping_config.SCALPING_MOMENTUM_THRESHOLD}x ATR)")
            return None
        logger.info(f"[SCALPING] ✅ CHECK 6 PASSED: Momentum breakout confirmed")
        
        # =================================================================
        # CHECK 7: Parabolic Spike Detection
        # =================================================================
        if self._is_parabolic_spike(data_1m, atr_1m):
            logger.info("[SCALPING] ❌ CHECK 7 FAILED: Parabolic spike detected (3+ large candles)")
            return None
        logger.info("[SCALPING] ✅ CHECK 7 PASSED: No parabolic spike")
        
        # =================================================================
        # CHECK 8: Calculate TP/SL (ATR-based with structure override)
        # =================================================================
        sl_distance = atr_1m * scalping_config.SCALPING_SL_ATR_MULTIPLIER
        tp_distance = atr_1m * scalping_config.SCALPING_TP_ATR_MULTIPLIER
        
        if direction == "BULLISH":
            sl_price = current_price - sl_distance
            tp_price = current_price + tp_distance
        else:  # BEARISH
            sl_price = current_price + sl_distance
            tp_price = current_price - tp_distance
        
        logger.info(f"[SCALPING] 📍 TP/SL - Entry: {current_price:.5f}, TP: {tp_price:.5f}, SL: {sl_price:.5f}")
        
        # =================================================================
        # CHECK 9: Minimum R:R Ratio
        # =================================================================
        risk = abs(current_price - sl_price)
        reward = abs(tp_price - current_price)
        
        if risk == 0:
            logger.info("[SCALPING] ❌ CHECK 9 FAILED: Risk is zero (invalid SL)")
            return None
        
        rr_ratio = reward / risk
        
        if rr_ratio < scalping_config.SCALPING_MIN_RR_RATIO:
            logger.info(f"[SCALPING] ❌ CHECK 9 FAILED: R:R {rr_ratio:.2f} < {scalping_config.SCALPING_MIN_RR_RATIO}")
            return None
        logger.info(f"[SCALPING] ✅ CHECK 9 PASSED: R:R ratio {rr_ratio:.2f} >= {scalping_config.SCALPING_MIN_RR_RATIO}")
        
        # =================================================================
        # All checks passed - return signal
        # =================================================================
        signal_direction = "UP" if direction == "BULLISH" else "DOWN"
        
        signal = {
            'signal': signal_direction,
            'symbol': symbol,
            'take_profit': tp_price,
            'stop_loss': sl_price,
            'risk_reward_ratio': rr_ratio,
            'confidence': 7.0,  # Scalping has moderate confidence
            'entry_price': current_price,
            'reason': f"Scalping signal - {direction} trend, RSI {rsi_1m:.1f}, ADX {adx_1m:.1f}, R:R {rr_ratio:.2f}"
        }
        
        logger.info(f"\n{'='*60}")
        logger.info(f"[SCALPING] ✅ SIGNAL GENERATED: {signal_direction} on {symbol}")
        logger.info(f"[SCALPING] 📊 R:R: {rr_ratio:.2f}, Confidence: {signal['confidence']}")
        logger.info(f"{'='*60}\n")
        
        return signal
    
    def _determine_trend(self, df: pd.DataFrame, timeframe_name: str) -> Optional[str]:
        """
        Determine trend based on swing highs/lows.
        
        Args:
            df: DataFrame with OHLC data
            timeframe_name: Name for logging
        
        Returns:
            'BULLISH', 'BEARISH', or None
        """
        if len(df) < 30:
            return None
        
        # Get swing points
        highs = df['high'].rolling(window=5, center=True).max()
        lows = df['low'].rolling(window=5, center=True).min()
        
        # Get recent swing points
        recent_highs = highs.iloc[-20:].dropna()
        recent_lows = lows.iloc[-20:].dropna()
        
        if len(recent_highs) < 2 or len(recent_lows) < 2:
            return None
        
        # Check for higher highs and higher lows (bullish)
        if recent_highs.iloc[-1] > recent_highs.iloc[-2] and recent_lows.iloc[-1] > recent_lows.iloc[-2]:
            return "BULLISH"
        
        # Check for lower highs and lower lows (bearish)
        if recent_highs.iloc[-1] < recent_highs.iloc[-2] and recent_lows.iloc[-1] < recent_lows.iloc[-2]:
            return "BEARISH"
        
        return None
    
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
    
    def get_strategy_name(self) -> str:
        """
        Get strategy name.
        
        Returns:
            'Scalping'
        """
        return "Scalping"

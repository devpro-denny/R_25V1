"""
Technical Indicators for Deriv R_25 Trading Bot
Implements ATR, RSI, ADX, SMA, EMA, and other indicators
indicators.py
"""

import pandas as pd
import numpy as np
from typing import Tuple

def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Calculate Average True Range (ATR)
    
    Args:
        df: DataFrame with 'high', 'low', 'close' columns
        period: ATR period (default 14)
    
    Returns:
        Series with ATR values
    """
    high = df['high']
    low = df['low']
    close = df['close']
    
    # Calculate True Range
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    
    # Calculate ATR using EMA
    atr = true_range.ewm(span=period, adjust=False).mean()
    
    return atr

def calculate_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Calculate Relative Strength Index (RSI)
    
    Args:
        df: DataFrame with 'close' column
        period: RSI period (default 14)
    
    Returns:
        Series with RSI values (0-100)
    """
    close = df['close']
    
    # Calculate price changes
    delta = close.diff()
    
    # Separate gains and losses
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    
    # Calculate average gain and loss
    avg_gain = gain.ewm(span=period, adjust=False).mean()
    avg_loss = loss.ewm(span=period, adjust=False).mean()
    
    # Calculate RS and RSI
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    
    return rsi

def calculate_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Calculate Average Directional Index (ADX)
    
    Args:
        df: DataFrame with 'high', 'low', 'close' columns
        period: ADX period (default 14)
    
    Returns:
        Series with ADX values
    """
    high = df['high']
    low = df['low']
    close = df['close']
    
    # Calculate +DM and -DM
    plus_dm = high.diff()
    minus_dm = -low.diff()
    
    plus_dm[plus_dm < 0] = 0
    minus_dm[minus_dm < 0] = 0
    
    # Calculate True Range
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    
    # Smooth the values
    atr = true_range.ewm(span=period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(span=period, adjust=False).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(span=period, adjust=False).mean() / atr)
    
    # Calculate DX and ADX
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
    adx = dx.ewm(span=period, adjust=False).mean()
    
    return adx

def calculate_sma(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """
    Calculate Simple Moving Average (SMA)
    
    Args:
        df: DataFrame with 'close' column
        period: SMA period
    
    Returns:
        Series with SMA values
    """
    return df['close'].rolling(window=period).mean()

def calculate_ema(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """
    Calculate Exponential Moving Average (EMA)
    
    Args:
        df: DataFrame with 'close' column
        period: EMA period
    
    Returns:
        Series with EMA values
    """
    return df['close'].ewm(span=period, adjust=False).mean()

def calculate_bollinger_bands(df: pd.DataFrame, period: int = 20, 
                              std_dev: float = 2.0) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    Calculate Bollinger Bands
    
    Args:
        df: DataFrame with 'close' column
        period: Period for moving average
        std_dev: Number of standard deviations
    
    Returns:
        Tuple of (upper_band, middle_band, lower_band)
    """
    middle_band = df['close'].rolling(window=period).mean()
    std = df['close'].rolling(window=period).std()
    
    upper_band = middle_band + (std * std_dev)
    lower_band = middle_band - (std * std_dev)
    
    return upper_band, middle_band, lower_band

def calculate_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, 
                   signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    Calculate MACD (Moving Average Convergence Divergence)
    
    Args:
        df: DataFrame with 'close' column
        fast: Fast EMA period
        slow: Slow EMA period
        signal: Signal line period
    
    Returns:
        Tuple of (macd_line, signal_line, histogram)
    """
    ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
    ema_slow = df['close'].ewm(span=slow, adjust=False).mean()
    
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    
    return macd_line, signal_line, histogram

def is_bullish_candle(df: pd.DataFrame, index: int) -> bool:
    """
    Check if candle at index is bullish
    
    Args:
        df: DataFrame with 'open' and 'close' columns
        index: Index of candle to check
    
    Returns:
        True if bullish, False otherwise
    """
    if index < 0 or index >= len(df):
        return False
    return df.iloc[index]['close'] > df.iloc[index]['open']

def is_bearish_candle(df: pd.DataFrame, index: int) -> bool:
    """
    Check if candle at index is bearish
    
    Args:
        df: DataFrame with 'open' and 'close' columns
        index: Index of candle to check
    
    Returns:
        True if bearish, False otherwise
    """
    if index < 0 or index >= len(df):
        return False
    return df.iloc[index]['close'] < df.iloc[index]['open']

def get_candle_body(df: pd.DataFrame, index: int) -> float:
    """
    Get the body size of a candle
    
    Args:
        df: DataFrame with 'open' and 'close' columns
        index: Index of candle
    
    Returns:
        Absolute body size
    """
    if index < 0 or index >= len(df):
        return 0.0
    return abs(df.iloc[index]['close'] - df.iloc[index]['open'])

def get_candle_range(df: pd.DataFrame, index: int) -> float:
    """
    Get the total range of a candle (high - low)
    
    Args:
        df: DataFrame with 'high' and 'low' columns
        index: Index of candle
    
    Returns:
        Candle range
    """
    if index < 0 or index >= len(df):
        return 0.0
    return df.iloc[index]['high'] - df.iloc[index]['low']

def calculate_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calculate all technical indicators and add to DataFrame
    
    Args:
        df: DataFrame with OHLC data
    
    Returns:
        DataFrame with all indicators added
    """
    # Create a copy to avoid modifying original
    df = df.copy()
    
    # Calculate indicators
    df['atr'] = calculate_atr(df)
    df['rsi'] = calculate_rsi(df)
    df['adx'] = calculate_adx(df)
    df['sma_100'] = calculate_sma(df, period=100)
    df['ema_20'] = calculate_ema(df, period=20)
    
    # Bollinger Bands
    df['bb_upper'], df['bb_middle'], df['bb_lower'] = calculate_bollinger_bands(df)
    

    # MACD
    df['macd'], df['macd_signal'], df['macd_hist'] = calculate_macd(df)
    
    return df

def get_trend_direction(df: pd.DataFrame) -> str:
    """
    Determine overall trend direction
    
    Args:
        df: DataFrame with indicators
    
    Returns:
        'UP', 'DOWN', or 'SIDEWAYS'
    """
    if len(df) < 100:
        return 'SIDEWAYS'
    
    last_row = df.iloc[-1]
    
    # Check if we have required columns
    if 'close' not in df.columns or 'sma_100' not in df.columns:
        return 'SIDEWAYS'
    
    close = last_row['close']
    sma = last_row['sma_100']
    
    # Strong trend
    if close > sma * 1.002:  # 0.2% above
        return 'UP'
    elif close < sma * 0.998:  # 0.2% below
        return 'DOWN'
    else:
        return 'SIDEWAYS'


# ==================== ENTRY TIMING DETECTION ====================

def detect_price_movement(df: pd.DataFrame, lookback: int = 20) -> Tuple[float, float, bool]:
    """
    Detect recent price movement to identify if entry is too late.
    
    Args:
        df: DataFrame with OHLC data
        lookback: Number of candles to analyze (default 20)
    
    Returns:
        Tuple of (movement_pct, movement_pips, is_parabolic)
        - movement_pct: Percentage change from lookback start to current
        - movement_pips: Absolute pip movement
        - is_parabolic: True if detected parabolic spike (3+ large candles)
    """
    if len(df) < lookback + 15:  # Need extra for ATR calculation
        return 0.0, 0.0, False
    
    recent_data = df.tail(lookback)
    
    # Calculate total movement
    start_price = recent_data.iloc[0]['close']
    current_price = recent_data.iloc[-1]['close']
    
    movement_pct = ((current_price - start_price) / start_price) * 100
    movement_pips = abs(current_price - start_price)
    
    # Detect parabolic movement
    # Definition: 3+ consecutive large candles (> 2x ATR) in same direction
    atr = calculate_atr(df).iloc[-1]
    
    if pd.isna(atr) or atr == 0:
        return movement_pct, movement_pips, False
    
    parabolic_threshold = 2.0  # Candles > 2x ATR
    min_parabolic_candles = 3
    
    large_bull_candles = 0
    large_bear_candles = 0
    max_consecutive_bull = 0
    max_consecutive_bear = 0
    
    for i in range(len(recent_data)):
        row = recent_data.iloc[i]
        body_size = abs(row['close'] - row['open'])
        
        if body_size > atr * parabolic_threshold:
            if row['close'] > row['open']:  # Bullish
                large_bull_candles += 1
                max_consecutive_bull = max(max_consecutive_bull, large_bull_candles)
                large_bear_candles = 0  # Reset
            else:  # Bearish
                large_bear_candles += 1
                max_consecutive_bear = max(max_consecutive_bear, large_bear_candles)
                large_bull_candles = 0  # Reset
        else:
            large_bull_candles = 0
            large_bear_candles = 0
    
    is_parabolic = (max_consecutive_bull >= min_parabolic_candles or 
                    max_consecutive_bear >= min_parabolic_candles)
    
    return movement_pct, movement_pips, is_parabolic


def detect_consolidation(df: pd.DataFrame, lookback: int = 20, 
                         atr_threshold: float = 0.6) -> Tuple[bool, float, float]:
    """
    Detect if price is consolidating (range-bound).
    
    Args:
        df: DataFrame with OHLC data
        lookback: Number of candles to analyze
        atr_threshold: ATR multiplier threshold (0.6 = current ATR < 60% of average)
    
    Returns:
        Tuple of (is_consolidating, range_high, range_low)
        - is_consolidating: True if price is in consolidation
        - range_high: Upper bound of consolidation range
        - range_low: Lower bound of consolidation range
    """
    if len(df) < lookback + 15:
        return False, 0.0, 0.0
    
    recent_data = df.tail(lookback)
    
    # Calculate ATR for volatility check
    current_atr = calculate_atr(df).iloc[-1]
    historical_atr = calculate_atr(df.tail(lookback * 2)).mean()
    
    if pd.isna(current_atr) or pd.isna(historical_atr) or historical_atr == 0:
        return False, 0.0, 0.0
    
    # Check if current volatility is low
    atr_ratio = current_atr / historical_atr
    is_low_volatility = atr_ratio < atr_threshold
    
    # Define range bounds
    range_high = recent_data['high'].max()
    range_low = recent_data['low'].min()
    range_size = range_high - range_low
    
    # Additional check: price should be oscillating within the range
    # (not trending in one direction)
    current_price = recent_data.iloc[-1]['close']
    price_position = (current_price - range_low) / range_size if range_size > 0 else 0.5
    
    # Consolidation if: low volatility AND price not at extremes
    is_consolidating = is_low_volatility and (0.2 < price_position < 0.8)
    
    return is_consolidating, range_high, range_low


def detect_exhaustion(df: pd.DataFrame, rsi_val: float, 
                      current_price: float, direction: str,
                      lookback: int = 10) -> Tuple[bool, str]:
    """
    Detect if price is at an exhaustion point (overbought/oversold extreme).
    
    Args:
        df: DataFrame with OHLC data
        rsi_val: Current RSI value
        current_price: Current price
        direction: Signal direction ('UP' or 'DOWN')
        lookback: Candles to check for extremes
    
    Returns:
        Tuple of (is_exhausted, reason)
        - is_exhausted: True if at exhaustion point
        - reason: Description of exhaustion condition
    """
    if len(df) < lookback:
        return False, "Insufficient data"
    
    recent_data = df.tail(lookback)
    
    # Check 1: RSI extremes
    if direction == "UP":
        # For UP signals, check if RSI is too high (overbought)
        if rsi_val > 75:
            return True, f"RSI overbought at {rsi_val:.1f}"
    else:  # DOWN
        # For DOWN signals, check if RSI is too low (oversold)
        if rsi_val < 25:
            return True, f"RSI oversold at {rsi_val:.1f}"
    
    # Check 2: Price at recent extremes
    recent_high = recent_data['high'].max()
    recent_low = recent_data['low'].min()
    
    if direction == "UP":
        # Buying near recent high = potential exhaustion
        distance_from_high = (recent_high - current_price) / recent_high * 100
        if distance_from_high < 0.1:  # Within 0.1% of recent high
            return True, f"Price at recent high ({distance_from_high:.2f}% from peak)"
    else:  # DOWN
        # Selling near recent low = potential exhaustion
        distance_from_low = (current_price - recent_low) / recent_low * 100
        if distance_from_low < 0.1:  # Within 0.1% of recent low
            return True, f"Price at recent low ({distance_from_low:.2f}% from bottom)"
    
    # Check 3: Momentum divergence (simplified)
    # If price makes new high/low but momentum (body size) is decreasing
    last_3_bodies = [abs(recent_data.iloc[i]['close'] - recent_data.iloc[i]['open']) 
                     for i in range(-3, 0)]
    
    if len(last_3_bodies) == 3:
        # Check if candle bodies are decreasing (loss of momentum)
        if last_3_bodies[0] > last_3_bodies[1] > last_3_bodies[2]:
            avg_body = sum(last_3_bodies) / 3
            if last_3_bodies[2] < avg_body * 0.5:  # Latest candle much smaller
                return True, "Momentum divergence - weakening candles"
    
    return False, "No exhaustion detected"


# Testing

if __name__ == "__main__":
    # Create sample data
    print("Testing indicators module...")
    
    np.random.seed(42)
    dates = pd.date_range(start='2024-01-01', periods=200, freq='1T')
    
    # Generate sample OHLC data
    close_prices = 100 + np.cumsum(np.random.randn(200) * 0.1)
    data = {
        'timestamp': dates,
        'open': close_prices + np.random.randn(200) * 0.05,
        'high': close_prices + abs(np.random.randn(200) * 0.1),
        'low': close_prices - abs(np.random.randn(200) * 0.1),
        'close': close_prices
    }
    
    df = pd.DataFrame(data)
    
    # Calculate indicators
    print("Calculating ATR...")
    df['atr'] = calculate_atr(df)
    print(f"Latest ATR: {df['atr'].iloc[-1]:.4f}")
    
    print("Calculating RSI...")
    df['rsi'] = calculate_rsi(df)
    print(f"Latest RSI: {df['rsi'].iloc[-1]:.2f}")
    
    print("Calculating ADX...")
    df['adx'] = calculate_adx(df)
    print(f"Latest ADX: {df['adx'].iloc[-1]:.2f}")
    
    print("Calculating SMA...")
    df['sma_100'] = calculate_sma(df, 100)
    print(f"Latest SMA(100): {df['sma_100'].iloc[-1]:.4f}")
    
    print("Calculating EMA...")
    df['ema_20'] = calculate_ema(df, 20)
    print(f"Latest EMA(20): {df['ema_20'].iloc[-1]:.4f}")
    
    # Test candle patterns
    print("\nTesting candle patterns...")
    is_bull = is_bullish_candle(df, -1)
    is_bear = is_bearish_candle(df, -1)
    print(f"Last candle - Bullish: {is_bull}, Bearish: {is_bear}")
    
    # Test trend
    trend = get_trend_direction(df)
    print(f"Current trend: {trend}")
    
    # Calculate all at once
    print("\nCalculating all indicators...")
    df_full = calculate_all_indicators(df)
    print(f"Columns: {list(df_full.columns)}")
    
    print("\n✅ Indicators module test complete!")
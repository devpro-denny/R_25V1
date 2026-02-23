"""
Candlestick quality filters for the Rise/Fall strategy.
"""

from typing import Optional, Tuple

import pandas as pd


def is_momentum_candle(
    df: pd.DataFrame,
    idx: int = -1,
    body_ratio: float = 0.60,
    wick_ratio: float = 0.25,
) -> Tuple[bool, Optional[str]]:
    """
    Return (is_momentum, direction) for the candle at idx.

    direction:
      - "bullish" when close > open
      - "bearish" when close < open
      - None when close == open or data is invalid
    """
    if df is None or df.empty:
        return False, None

    try:
        candle = df.iloc[idx]
        o = float(candle["open"])
        h = float(candle["high"])
        l = float(candle["low"])
        c = float(candle["close"])
    except (IndexError, KeyError, TypeError, ValueError):
        return False, None

    rng = h - l
    if rng <= 0:
        return False, None

    body = abs(c - o)
    upper_wick = max(0.0, h - max(o, c))
    lower_wick = max(0.0, min(o, c) - l)
    max_wick = max(upper_wick, lower_wick)

    b_ratio = body / rng
    w_ratio = max_wick / rng

    if c > o:
        direction = "bullish"
    elif c < o:
        direction = "bearish"
    else:
        direction = None

    is_momentum = (
        direction is not None
        and b_ratio >= float(body_ratio)
        and w_ratio <= float(wick_ratio)
    )
    return is_momentum, direction

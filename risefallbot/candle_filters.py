"""
Candlestick quality filters for the Rise/Fall strategy.
"""

from typing import Optional, Tuple

import pandas as pd


def _resolve_index(length: int, idx: int) -> Optional[int]:
    """Resolve positive/negative iloc index to an absolute position."""
    if length <= 0:
        return None
    pos = int(idx)
    if pos < 0:
        pos = length + pos
    if pos < 0 or pos >= length:
        return None
    return pos


def is_momentum_candle(
    df: pd.DataFrame,
    idx: int = -1,
    body_ratio: float = 0.70,
    wick_ratio: float = 0.25,
    avg_lookback: int = 5,
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

    pos = _resolve_index(len(df), idx)
    if pos is None:
        return False, None

    try:
        candle = df.iloc[pos]
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

    avg_body_ok = True
    lookback = max(0, int(avg_lookback))
    if lookback > 0:
        prev_start = max(0, pos - lookback)
        prev = df.iloc[prev_start:pos]
        if not prev.empty:
            prev_bodies = (prev["close"].astype(float) - prev["open"].astype(float)).abs()
            avg_body = float(prev_bodies.mean()) if not prev_bodies.empty else 0.0
            avg_body_ok = body > avg_body

    is_momentum = (
        direction is not None
        and b_ratio >= float(body_ratio)
        and w_ratio <= float(wick_ratio)
        and avg_body_ok
    )
    return is_momentum, direction

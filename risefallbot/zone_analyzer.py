"""
Spatial zone analysis for the Rise/Fall strategy.
"""

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from risefallbot import rf_config


Zone = Dict[str, float | int | str]
Pivot = Tuple[int, float]


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


def _relative_diff(a: float, b: float) -> float:
    base = (abs(float(a)) + abs(float(b))) / 2.0
    if base == 0:
        return 0.0 if float(a) == float(b) else float("inf")
    return abs(float(a) - float(b)) / base


def find_pivot_highs(df: pd.DataFrame, left: int = 2, right: int = 2) -> List[Pivot]:
    """Find local maxima where high[i] is above highs on both sides."""
    if df is None or df.empty or len(df) < (left + right + 1):
        return []

    highs = df["high"].astype(float).values
    pivots: List[Pivot] = []
    for i in range(left, len(highs) - right):
        cur = highs[i]
        if all(cur > highs[i - j] for j in range(1, left + 1)) and all(
            cur > highs[i + j] for j in range(1, right + 1)
        ):
            pivots.append((i, float(cur)))
    return pivots


def find_pivot_lows(df: pd.DataFrame, left: int = 2, right: int = 2) -> List[Pivot]:
    """Find local minima where low[i] is below lows on both sides."""
    if df is None or df.empty or len(df) < (left + right + 1):
        return []

    lows = df["low"].astype(float).values
    pivots: List[Pivot] = []
    for i in range(left, len(lows) - right):
        cur = lows[i]
        if all(cur < lows[i - j] for j in range(1, left + 1)) and all(
            cur < lows[i + j] for j in range(1, right + 1)
        ):
            pivots.append((i, float(cur)))
    return pivots


def cluster_levels(pivots: List[Pivot], tolerance: float) -> List[List[Pivot]]:
    """Cluster pivots by relative distance in price."""
    if not pivots:
        return []

    tol = max(0.0, float(tolerance))
    sorted_pivots = sorted(pivots, key=lambda x: x[1])
    clusters: List[List[Pivot]] = [[sorted_pivots[0]]]

    for pivot in sorted_pivots[1:]:
        ref_price = clusters[-1][-1][1]
        if _relative_diff(pivot[1], ref_price) <= tol:
            clusters[-1].append(pivot)
        else:
            clusters.append([pivot])
    return clusters


def _build_zone(cluster: List[Pivot], zone_type: str) -> Zone:
    return {
        "level": float(np.mean([p[1] for p in cluster])),
        "type": zone_type,
        "touches": int(len(cluster)),
    }


def _merge_middle_zones(zones: List[Zone], tolerance: float) -> List[Zone]:
    """
    Merge nearby support+resistance zones into a "middle" zone.
    """
    if not zones:
        return []

    tol = max(0.0, float(tolerance))
    used: set[int] = set()
    merged: List[Zone] = []

    for i, zone in enumerate(zones):
        if i in used:
            continue

        paired: Optional[Zone] = None
        for j in range(i + 1, len(zones)):
            if j in used:
                continue
            other = zones[j]
            if zone["type"] == other["type"]:
                continue
            if _relative_diff(float(zone["level"]), float(other["level"])) <= tol:
                paired = {
                    "level": float(np.mean([float(zone["level"]), float(other["level"])])),
                    "type": "middle",
                    "touches": int(zone["touches"]) + int(other["touches"]),
                }
                used.add(j)
                break

        if paired is not None:
            used.add(i)
            merged.append(paired)
        else:
            merged.append(zone)

    return sorted(merged, key=lambda z: float(z["level"]))


def rolling_extreme_zones(df: pd.DataFrame, lookback: int = 50) -> List[Zone]:
    """
    Return hard outer boundaries using absolute extremes of the rolling window:
      - support: lowest low
      - resistance: highest high
    """
    if df is None or df.empty:
        return []

    lb = max(2, int(lookback))
    frame = df.tail(lb)
    try:
        highest_high = float(frame["high"].astype(float).max())
        lowest_low = float(frame["low"].astype(float).min())
    except (KeyError, TypeError, ValueError):
        return []

    zones: List[Zone] = [
        {"level": lowest_low, "type": "support", "touches": 1},
        {"level": highest_high, "type": "resistance", "touches": 1},
    ]
    return sorted(zones, key=lambda z: float(z["level"]))


def get_key_zones(
    df: pd.DataFrame,
    lookback: Optional[int] = None,
    tolerance: Optional[float] = None,
    min_touches: Optional[int] = None,
) -> List[Zone]:
    """
    Build key zones from:
      1) absolute rolling extremes (hard boundaries)
      2) pivot-based cluster zones (inner structure)
    """
    if df is None or df.empty:
        return []

    lb = max(5, int(lookback if lookback is not None else _cfg_int("RF_ZONE_LOOKBACK", 50)))
    tol = max(
        0.0,
        float(
            tolerance
            if tolerance is not None
            else _cfg_float("RF_ZONE_TOUCH_TOLERANCE", 0.0003)
        ),
    )
    min_t = max(
        1,
        int(min_touches if min_touches is not None else _cfg_int("RF_ZONE_MIN_TOUCHES", 2)),
    )

    frame = df.tail(lb).reset_index(drop=True)
    pivot_highs = find_pivot_highs(frame)
    pivot_lows = find_pivot_lows(frame)

    zones: List[Zone] = rolling_extreme_zones(frame, lookback=lb)
    for cluster in cluster_levels(pivot_highs, tol):
        if len(cluster) >= min_t:
            zones.append(_build_zone(cluster, "resistance"))
    for cluster in cluster_levels(pivot_lows, tol):
        if len(cluster) >= min_t:
            zones.append(_build_zone(cluster, "support"))

    return _merge_middle_zones(zones, tol)


def price_near_zone(price: float, zones: List[Zone], tolerance: float) -> Tuple[bool, Optional[Zone]]:
    """
    Return (True, closest_zone) when price is within tolerance of any zone.
    """
    if not zones:
        return False, None

    tol = max(0.0, float(tolerance))
    price_f = float(price)
    matches: List[Tuple[float, Zone]] = []
    for zone in zones:
        level = float(zone["level"])
        if _relative_diff(price_f, level) <= tol:
            matches.append((_relative_diff(price_f, level), zone))

    if not matches:
        return False, None

    matches.sort(key=lambda x: x[0])
    return True, matches[0][1]


def detect_market_bias(df: pd.DataFrame, lookback: int = 20) -> str:
    """
    Detect direction using higher-high/lower-low counts.
    """
    if df is None or df.empty or len(df) < 3:
        return "neutral"

    lb = max(3, int(lookback))
    recent = df.tail(lb)
    highs = recent["high"].astype(float).values
    lows = recent["low"].astype(float).values

    hh = sum(1 for i in range(1, len(highs)) if highs[i] > highs[i - 1])
    lh = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])
    hl = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
    ll = sum(1 for i in range(1, len(lows)) if lows[i] < lows[i - 1])

    if lh > hh and ll > hl:
        return "bearish"
    if hh > lh and hl > ll:
        return "bullish"
    return "neutral"


def classify_scenario(
    df: pd.DataFrame,
    zones: List[Zone],
    direction: str,
    idx: int = -1,
    retest_lookback: Optional[int] = None,
) -> str:
    """
    Return one of: "breakout", "retest", "basic".
    """
    if df is None or df.empty:
        return "basic"

    tol = _cfg_float("RF_ZONE_TOUCH_TOLERANCE", 0.0003)
    lookback = int(
        retest_lookback
        if retest_lookback is not None
        else _cfg_int("RF_RETEST_LOOKBACK", 5)
    )

    try:
        price = float(df["close"].iloc[idx])
    except (IndexError, KeyError, TypeError, ValueError):
        return "basic"

    recent = df.tail(max(lookback + 1, 2)).iloc[:-1]
    for zone in zones:
        level = float(zone["level"])
        if _relative_diff(price, level) > tol:
            continue

        for _, row in recent.iterrows():
            try:
                close_val = float(row["close"])
            except (KeyError, TypeError, ValueError):
                continue

            if zone["type"] == "support" and close_val < level * (1 - tol):
                return "retest"
            if zone["type"] == "resistance" and close_val > level * (1 + tol):
                return "retest"
            if zone["type"] == "middle":
                if close_val < level * (1 - tol) or close_val > level * (1 + tol):
                    return "retest"

    bias = detect_market_bias(df)
    if direction == "PUT" and bias == "bearish":
        return "breakout"
    if direction == "CALL" and bias == "bullish":
        return "breakout"
    return "basic"

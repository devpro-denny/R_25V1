"""
Strategy Module for Deriv Multi-Asset Trading Bot

Implements the Top-Down Market Structure Analysis strategy:
1. Phase 1: Directional Bias (Weekly + Daily)
2. Phase 2: Level Classification & Price Magnets (All TFs)
3. Phase 3: Entry Execution (Momentum Close + Weak Retest)

strategy.py - REFACTORED FOR MULTI-ASSET TOP-DOWN ANALYSIS
"""

import pandas as pd
import numpy as np
import logging
from typing import Dict, List, Optional, Tuple, Any

from . import config
from utils import setup_logger
from indicators import calculate_rsi, calculate_adx

logger = setup_logger()

class TradingStrategy:
    """
    Implements Top-Down Market Structure Analysis.
    Decides 'UP', 'DOWN', or None based on confluence of 6 timeframes.
    """

    def __init__(self):
        # Configuration parameters
        self.min_rr_ratio = config.TOPDOWN_MIN_RR_RATIO
        self.momentum_threshold = config.MOMENTUM_CLOSE_THRESHOLD
        self.weak_retest_pct = config.WEAK_RETEST_MAX_PCT
        self.middle_zone_pct = config.MIDDLE_ZONE_PCT
        
        # Lookback settings
        self.swing_lookback = config.SWING_LOOKBACK
        self.min_level_touches = config.MIN_LEVEL_TOUCHES
        self.max_sl_distance_pct = config.TOPDOWN_MAX_SL_DISTANCE_PCT

    def analyze(self, data_1m: pd.DataFrame, data_5m: pd.DataFrame, 
                data_1h: pd.DataFrame, data_4h: pd.DataFrame, 
                data_1d: pd.DataFrame, data_1w: pd.DataFrame,
                symbol: str = None) -> Dict[str, Any]:
        """
        Main analysis method accepting all 6 timeframes.
        
        Args:
            data_1m to data_1w: DataFrames for each timeframe
            symbol: Asset symbol (e.g., 'R_75') for asset-specific filtering
        
        Returns:
            Dict containing signal, levels, and trade parameters.
        """
        passed_checks = []
        
        response = {
            "can_trade": False,
            "signal": None,
            "score": 0,
            "confidence": 0,
            "take_profit": None,
            "stop_loss": None,
            "risk_reward_ratio": 0.0,
            "details": {
                "passed_checks": passed_checks
            }
        }

        # DEBUG: Log strategy execution (helps identify unexpected calls)
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"[CONSERVATIVE] 🔍 TradingStrategy.analyze() called for symbol: {symbol}")
 
        # ---------------------------------------------------------
        # MANDATORY LOGGING HEADER (Simplified)
        # ---------------------------------------------------------
        # Only print specific header if meaningful progress is expected or for debugging
        # For now, we reduce it to a simple separator if verbose execution is triggered later,
        # but here we keep it silent until a check fails or passes significant stages.
        
        # 0. Data Validation
        missing_dfs = []
        if data_1m is None or data_1m.empty: missing_dfs.append("1m")
        if data_5m is None or data_5m.empty: missing_dfs.append("5m")
        if data_1h is None or data_1h.empty: missing_dfs.append("1h")
        if data_4h is None or data_4h.empty: missing_dfs.append("4h")
        if data_1d is None or data_1d.empty: missing_dfs.append("1d")
        if data_1w is None or data_1w.empty: missing_dfs.append("1w")
        
        if missing_dfs:
            print(f"[CONSERVATIVE] ❌ Data Missing: {', '.join(missing_dfs)}")
            response["details"]["reason"] = "Insufficient data across timeframes"
            return response
            
        passed_checks.append("Data Validated")

        current_price = data_1m['close'].iloc[-1]

        # ---------------------------------------------------------
        # 0.1 Indicator Calculation & Pre-Filtering
        # ---------------------------------------------------------
        try:
            rsi_val = calculate_rsi(data_5m).iloc[-1] if not data_5m.empty else 50
            adx_val = calculate_adx(data_5m).iloc[-1] if not data_5m.empty else 0
            if pd.isna(adx_val): adx_val = 0
            passed_checks.append("Indicators Calculated")
        except Exception as e:
            logger.error(f"Indicator calculation failed: {e}")
            rsi_val = 50
            adx_val = 0

        # ADX Filter (Trend Strength)
        if adx_val < config.ADX_THRESHOLD:
            print(f"[CONSERVATIVE] ⚠️ Trend Weak: ADX {adx_val:.1f} < {config.ADX_THRESHOLD}")
            response["details"]["reason"] = f"Trend too weak (ADX {adx_val:.1f} < {config.ADX_THRESHOLD})"
            response["details"]["adx"] = round(adx_val, 2)
            response["details"]["rsi"] = round(rsi_val, 2)
            return response
        
        passed_checks.append(f"Trend Strength (ADX {adx_val:.1f} > {config.ADX_THRESHOLD})")

        # ---------------------------------------------------------
        # Phase 0.5: Entry Timing Validation (LATE ENTRY FILTER)
        # ---------------------------------------------------------
        from indicators import detect_price_movement, detect_consolidation, detect_exhaustion

        # Filter 1: Check price movement over recent candles
        movement_pct, movement_pips, is_parabolic = detect_price_movement(data_1m, lookback=20)
        
        # Reject parabolic spikes immediately (buying tops/selling bottoms)
        if is_parabolic:
            print(f"[CONSERVATIVE] ❌ Parabolic Spike Detected - Late Entry Rejected")
            response["details"]["reason"] = "Parabolic spike detected - entry too late"
            response["details"]["movement_pct"] = round(movement_pct, 2)
            response["details"]["is_parabolic"] = True
            return response
        
        # Get asset-specific movement threshold (or global fallback)
        max_movement = config.MAX_PRICE_MOVEMENT_PCT  # Default global threshold
        
        # DEBUG: Print symbol parameter
        print(f"[DEBUG] Symbol parameter received: '{symbol}'")
        
        if symbol and hasattr(config, 'ASSET_CONFIG'):
            asset_config = config.ASSET_CONFIG.get(symbol, {})
            asset_threshold = asset_config.get('movement_threshold_pct')
            print(f"[DEBUG] Asset config for {symbol}: {asset_config}")
            print(f"[DEBUG] Extracted threshold: {asset_threshold}")
            if asset_threshold:
                max_movement = asset_threshold
                print(f"[CONSERVATIVE] ✓ Using {symbol}-specific threshold: {max_movement}%")
            else:
                print(f"[CONSERVATIVE] ⚠️ No threshold found for {symbol}, using global {max_movement}%")
        else:
            print(f"[CONSERVATIVE] ⚠️ Using global threshold (symbol={symbol}, hasattr={hasattr(config, 'ASSET_CONFIG')})")
        
        # Reject if price already moved significantly
        if abs(movement_pct) > max_movement:
            print(f"[CONSERVATIVE] ❌ Price Moved {abs(movement_pct):.2f}% - Late Entry Rejected")
            response["details"]["reason"] = f"Price already moved {abs(movement_pct):.2f}% - late entry rejected (max {max_movement}%)"
            response["details"]["movement_pct"] = round(movement_pct, 2)
            return response
        
        passed_checks.append(f"Price Movement Check ({abs(movement_pct):.2f}% < {max_movement}%)")
        
        # Filter 2: Check for consolidation (preferred entry structure)
        is_consolidating, range_high, range_low = detect_consolidation(
            data_5m, 
            lookback=getattr(config, 'CONSOLIDATION_LOOKBACK', 20),
            atr_threshold=getattr(config, 'CONSOLIDATION_ATR_MULTIPLIER', 0.6)
        )
        
        # Store consolidation context for later (not a hard filter, but informational)
        consolidation_context = {
            'is_consolidating': is_consolidating,
            'range_high': range_high,
            'range_low': range_low
        }
        
        if is_consolidating:
            logger.debug(f"[STRATEGY] Consolidation detected: {range_low:.2f} - {range_high:.2f}")
        
        # Optional: Require consolidation base
        require_base = getattr(config, 'REQUIRE_CONSOLIDATION_BASE', False)
        warn_no_base = getattr(config, 'WARN_NO_CONSOLIDATION', True)
        
        if require_base and not is_consolidating:
            # Strict mode: reject if no consolidation
            response["details"]["reason"] = "No consolidation base - entry quality too low"
            return response
        elif warn_no_base and not is_consolidating:
            # Warning mode: log but allow
            logger.warning(f"⚠️ No consolidation base detected - entry quality may be lower")
            passed_checks.append("No Consolidation Base (Warning)")
        else:
            passed_checks.append("Consolidation Check Passed")

        # ---------------------------------------------------------
        # Phase 1: Directional Bias (Weekly + Daily)
        # ---------------------------------------------------------
        # ---------------------------------------------------------
        # Phase 1: Directional Bias (Weekly + Daily)
        # ---------------------------------------------------------
        weekly_trend = self._determine_trend(data_1w, "Weekly")
        daily_trend = self._determine_trend(data_1d, "Daily")

        # Bias Confirmation
        if weekly_trend == "UP" and daily_trend == "UP":
            bias = "UP"
            signal_direction = "UP"
            passed_checks.append("Trend Alignment (Bullish)")
            
            # RSI Momentum Check (UP): RSI within valid range for momentum
            rsi_max_threshold = getattr(config, 'RSI_MAX_THRESHOLD', 75)
            if rsi_val < config.RSI_BUY_THRESHOLD:
                 print(f"[STRATEGY] ⚠️ RSI Weak (UP): {rsi_val:.1f} < {config.RSI_BUY_THRESHOLD}")
                 response["details"]["reason"] = f"RSI too weak for UP ({rsi_val:.1f} < {config.RSI_BUY_THRESHOLD})"
                 response["details"]["rsi"] = round(rsi_val, 2)
                 return response
            if rsi_val > rsi_max_threshold:
                 print(f"[STRATEGY] ⚠️ RSI Overbought: {rsi_val:.1f} > {rsi_max_threshold}")
                 response["details"]["reason"] = f"RSI Overbought ({rsi_val:.1f} > {rsi_max_threshold})"
                 return response
            
            passed_checks.append("RSI Momentum (UP)")
            
        elif weekly_trend == "DOWN" and daily_trend == "DOWN":
            bias = "DOWN"
            signal_direction = "DOWN"
            passed_checks.append("Trend Alignment (Bearish)")
            
            # RSI Momentum Check (DOWN): RSI within valid range for momentum
            rsi_min_threshold = getattr(config, 'RSI_MIN_THRESHOLD', 25)
            if rsi_val > config.RSI_SELL_THRESHOLD:
                 print(f"[STRATEGY] ⚠️ RSI Weak (DOWN): {rsi_val:.1f} > {config.RSI_SELL_THRESHOLD}")
                 response["details"]["reason"] = f"RSI too weak for DOWN ({rsi_val:.1f} > {config.RSI_SELL_THRESHOLD})"
                 response["details"]["rsi"] = round(rsi_val, 2)
                 return response
            if rsi_val < rsi_min_threshold:
                 print(f"[STRATEGY] ⚠️ RSI Oversold: {rsi_val:.1f} < {rsi_min_threshold}")
                 response["details"]["reason"] = f"RSI Oversold ({rsi_val:.1f} < {rsi_min_threshold})"
                 return response
            
            passed_checks.append("RSI Momentum (DOWN)")
            
        else:
            print(f"[CONSERVATIVE] ⚠️ Trend Conflict: W:{weekly_trend} | D:{daily_trend}")
            response["details"]["reason"] = f"Trend Conflict - Weekly: {weekly_trend}, Daily: {daily_trend}"
            return response
        
        # ---------------------------------------------------------
        # Phase 2: Level Classification & Price Magnets
        # ---------------------------------------------------------
        # Gather levels from higher timeframes to find Structure and Targets
        structure_levels = []
        structure_levels.extend(self._find_levels(data_1w, "1w"))
        structure_levels.extend(self._find_levels(data_1d, "1d"))
        structure_levels.extend(self._find_levels(data_4h, "4h"))
        structure_levels.extend(self._find_levels(data_1h, "1h")) # Added 1h levels for closer targets
        
        # Identify Targets (TP) and Structure Points (SL)
        target_level, sl_level = self._identify_tp_sl_levels(
            structure_levels, current_price, signal_direction, 
            data_1d, data_4h, data_1h, data_5m
        )

        if not target_level:
            print("[CONSERVATIVE] ⚠️ No Target Found")
            # response["details"]["reason"] = "No clear Structure Level found for Target"
            # return response
            # Note: Previously this returned early. 
            # If we want detailed logging just for failures we can keep it.
            response["details"]["reason"] = "No clear Structure Level found for Target"
            return response
            
        if not sl_level:
            print("[CONSERVATIVE] ⚠️ No Stop Loss Found")
            response["details"]["reason"] = "No clear Structure Swing Point found for Stop Loss"
            return response

        # Calc Distance
        tp_dist = abs(target_level - current_price) / current_price * 100
        passed_checks.append("Market Structure (TP/SL Found)")

        # ---------------------------------------------------------
        # Phase 3: Entry Execution Criteria (1m/5m)
        # ---------------------------------------------------------
        
        # 1. Check Middle Zone (Dead Zone)
        # Identify the trading range we are currently in
        range_support, range_resistance = self._find_trading_range(structure_levels, current_price)
        
        if self._is_in_middle_zone(current_price, range_support, range_resistance):
            # Only skip if we are NOT in a breakout scenario
            # Breakout logic below might override this if we are crossing a level
            is_mid_zone = True
            logger.debug(f"[STRATEGY] Price in middle zone - momentum breakout required for entry")
        else:
            is_mid_zone = False
            # Don't add to passed_checks yet - validate entry trigger first

        # 2. Find nearest active execution level (could be a 1h or 4h level we are breaking)
        # We add 1h and 5m levels for precise execution
        execution_levels = self._find_levels(data_1h, "1h") + self._find_levels(data_5m, "5m")
        nearest_exec_level = self._find_nearest_level(current_price, execution_levels)
        
        if not nearest_exec_level:
             # Fallback to structure levels if no local levels found
            nearest_exec_level = self._find_nearest_level(current_price, structure_levels)

        if not nearest_exec_level:
            response["details"]["reason"] = "No execution level found"
            return response

        passed_checks.append(f"Key Level Found ({nearest_exec_level:.2f})")

        # 2.5. Validate Entry Distance to Level (NEW)
        # Prevent entries too close to swing points (quick reversals)
        proximity_valid, proximity_reason, distance_pct = self._validate_level_proximity(
            current_price, nearest_exec_level, signal_direction, symbol
        )
        
        if not proximity_valid:
            response["details"]["reason"] = f"Entry proximity invalid: {proximity_reason}"
            return response
        
        passed_checks.append(f"Entry Proximity OK ({distance_pct:.3f}%)")

        # 3. Check Momentum Breakout & Weak Retest
        # We look at recent history in 1m data to see if we just broke this level
        entry_valid, entry_reason = self._check_entry_trigger(
            data_1m, nearest_exec_level, signal_direction
        )

        # Logic: If we have a valid breakout/retest, we acknowledge middle zone status
        if not entry_valid:
            if is_mid_zone:
                response["details"]["reason"] = f"Middle Zone + Invalid Entry: {entry_reason}"
            else:
                response["details"]["reason"] = entry_reason
            return response
            
        print("[CONSERVATIVE] ✅ Momentum Breakout Confirmed")
        passed_checks.append("Momentum Breakout Confirmed")
        
        # Document middle zone override if applicable
        if is_mid_zone:
            logger.debug(f"⚠️ Entry in middle zone but breakout validated - entry quality: CAUTION")
            passed_checks.append("Momentum Override Middle Zone")
        else:
            passed_checks.append("Entry at Structure Boundary")

        # ---------------------------------------------------------
        # Final Calculations & Confluence Score
        # ---------------------------------------------------------
        
        # Calculate R:R
        distance_to_tp = abs(target_level - current_price)
        distance_to_sl = abs(current_price - sl_level)
        
        if distance_to_sl == 0:
            rr_ratio = 0.0
        else:
            rr_ratio = distance_to_tp / distance_to_sl

        if rr_ratio < self.min_rr_ratio:
            print(f"[STRATEGY] ⚠️ R:R Too Low: 1:{rr_ratio:.2f} < 1:{self.min_rr_ratio}")
            response["details"]["reason"] = f"Poor R:R Ratio ({rr_ratio:.2f} < {self.min_rr_ratio})"
            response["take_profit"] = target_level
            response["stop_loss"] = sl_level
            response["risk_reward_ratio"] = round(rr_ratio, 2)
            return response

        # print("Result: PASS")
        passed_checks.append(f"R:R Ratio OK ({rr_ratio:.2f})")

        # Confluence Score calculation
        score = 0
        score += 2 if weekly_trend == bias else 0
        score += 2 if daily_trend == bias else 0
        score += 1 if self._determine_trend(data_4h, "4h") == bias else 0
        score += 1 if self._determine_trend(data_1h, "1h") == bias else 0
        score += 2 # Entry trigger valid
        
        # Untested levels bonus (still valuable for scoring, even if not forced for TP)
        is_magnet = any(l['price'] == target_level and not l['tested'] for l in structure_levels)
        if is_magnet:
            score += 2

        # Calculate Indicators for Logging (using 5m data for stability)
        try:
            rsi_val = calculate_rsi(data_5m).iloc[-1] if not data_5m.empty else 0
            adx_val = calculate_adx(data_5m).iloc[-1] if not data_5m.empty else 0
        except Exception:
            rsi_val = 0
            adx_val = 0

        # Construct Final Response
        response["can_trade"] = True
        response["signal"] = signal_direction
        response["entry_price"] = current_price
        response["take_profit"] = target_level
        response["stop_loss"] = sl_level
        response["risk_reward_ratio"] = round(rr_ratio, 2)
        response["score"] = score
        response["confidence"] = min((score / 10.0) * 100, 100)
        response["details"] = {
            "reason": "Confluence Confirmed",
            "bias": bias,
            "entry_type": "Breakout + Weak Retest",
            "magnet_target": is_magnet,
            "passed_checks": passed_checks,
            "rsi": round(rsi_val, 2),
            "adx": round(adx_val, 2)
        }

        return response

    # --------------------------------------------------------------------------
    # Helper Methods - Trend & Structure
    # --------------------------------------------------------------------------

    def _determine_trend(self, df: pd.DataFrame, timeframe_name: str) -> str:
        """
        Determines trend based on Swing Highs/Lows.
        Bullish: Higher Highs + Higher Lows
        Bearish: Lower Highs + Lower Lows
        """
        if len(df) < self.swing_lookback:
            return "NEUTRAL"

        highs, lows = self._get_swing_points(df)
        
        if len(highs) < 2 or len(lows) < 2:
            return "NEUTRAL"

        last_high = highs[-1]
        prev_high = highs[-2]
        last_low = lows[-1]
        prev_low = lows[-2]

        if last_high > prev_high and last_low > prev_low:
            return "UP"
        elif last_high < prev_high and last_low < prev_low:
            return "DOWN"
        
        return "NEUTRAL"

    def _get_swing_points(self, df: pd.DataFrame) -> Tuple[List[float], List[float]]:
        """
        Identifies swing highs and lows using a window method.
        """
        highs = []
        lows = []
        window = config.MIN_SWING_WINDOW

        # Use numpy for faster rolling window check
        high_col = df['high'].values
        low_col = df['low'].values
        
        for i in range(window, len(df) - window):
            current_high = high_col[i]
            current_low = low_col[i]
            
            # Check for local high
            if all(current_high > high_col[i-x] for x in range(1, window+1)) and \
               all(current_high > high_col[i+x] for x in range(1, window+1)):
                highs.append(current_high)

            # Check for local low
            if all(current_low < low_col[i-x] for x in range(1, window+1)) and \
               all(current_low < low_col[i+x] for x in range(1, window+1)):
                lows.append(current_low)
                
        return highs, lows

    def _find_levels(self, df: pd.DataFrame, timeframe: str) -> List[Dict]:
        """
        Identifies support/resistance levels and checks if they are tested.
        """
        if df.empty:
            return []
            
        levels = []
        highs, lows = self._get_swing_points(df)
        raw_levels = sorted(highs + lows)
        
        if not raw_levels:
            return []

        merged_levels = []
        current_cluster = [raw_levels[0]]
        
        # Merge levels within 0.15% proximity (LEVEL_PROXIMITY_PCT)
        proximity = config.LEVEL_PROXIMITY_PCT / 100.0

        for i in range(1, len(raw_levels)):
            if raw_levels[i] <= current_cluster[-1] * (1 + proximity):
                current_cluster.append(raw_levels[i])
            else:
                avg_price = sum(current_cluster) / len(current_cluster)
                merged_levels.append(avg_price)
                current_cluster = [raw_levels[i]]
        
        if current_cluster:
            merged_levels.append(sum(current_cluster) / len(current_cluster))

        # Classify Tested vs Untested
        # A level is "Tested" if price touched it multiple times
        # A level is "Untested" (Magnet) if it was broken and never retested
        
        for price in merged_levels:
            touch_count = 0
            # Look at last 50 candles for touches
            recent_candles = df.tail(50)
            for _, row in recent_candles.iterrows():
                # Check if High/Low touched the level within small tolerance
                if abs(row['high'] - price) / price < 0.0005 or \
                   abs(row['low'] - price) / price < 0.0005:
                    touch_count += 1
            
            levels.append({
                'price': price,
                'timeframe': timeframe,
                'tested': touch_count >= self.min_level_touches,
                'touches': touch_count
            })
            
        return levels

    # --------------------------------------------------------------------------
    # Helper Methods - Targets & SL
    # --------------------------------------------------------------------------

    def _identify_tp_sl_levels(self, levels: List[Dict], current_price: float, 
                               direction: str, 
                               daily_data: pd.DataFrame,
                               data_4h: pd.DataFrame,
                               data_1h: pd.DataFrame,
                               data_5m: pd.DataFrame) -> Tuple[Optional[float], Optional[float]]:
        """
        TP: Nearest Structure Level (Tested or Untested).
        SL: Price behind last Swing Point (Prioritize 1H -> 4H -> Daily).
        """
        target = None
        stop = None

        # Filter levels by direction
        if direction == "UP":
            # Target: Levels ABOVE current price
            potential_tps = [l for l in levels if l['price'] > current_price]
            # Sort by proximity
            potential_tps.sort(key=lambda x: x['price'])
            
            # Prioritize NEAREST level that satisfies min distance
            if potential_tps:
                # Default to None, look for valid level
                for level in potential_tps:
                    dist_pct = abs(level['price'] - current_price) / current_price * 100
                    if dist_pct >= config.MIN_TP_DISTANCE_PCT:
                        target = level['price']
                        break
                
                # If all levels are too close, we might want to default to the furthest one 
                # or just leave as None (no valid target).
                # Logic: If we have levels but all are < 0.2%, maybe the volatility is super low.
                # Let's fallback to the last (furthest) one if nothing qualified, 
                # effectively targeting the "next" available if we ran out.
                # However, strict adherence says we skip "too close". 
                # If everything is too close, we shouldn't trade.
                pass

            # SL: Last Swing Low BELOW current price (5M -> 1H -> 4H -> Daily)
            # Try 5M first (Scalping precision)
            h, l = self._get_swing_points(data_5m)
            valid = [x for x in l if x < current_price]
            if valid:
                stop = valid[-1]

            if not stop:
                # Try 1H
                h, l = self._get_swing_points(data_1h)
                valid = [x for x in l if x < current_price]
                if valid:
                    stop = valid[-1]
            
            if not stop:
                # Try 4H
                h, l = self._get_swing_points(data_4h)
                valid = [x for x in l if x < current_price]
                if valid:
                    stop = valid[-1]
            
            if not stop:
                # Fallback to Daily
                h, l = self._get_swing_points(daily_data)
                valid = [x for x in l if x < current_price]
                if valid:
                    stop = valid[-1]

        else: # DOWN
            # Target: Levels BELOW current price
            potential_tps = [l for l in levels if l['price'] < current_price]
            # Sort by proximity (descending)
            potential_tps.sort(key=lambda x: x['price'], reverse=True)
            
            # Prioritize NEAREST level that satisfies min distance
            if potential_tps:
                for level in potential_tps:
                    dist_pct = abs(level['price'] - current_price) / current_price * 100
                    if dist_pct >= config.MIN_TP_DISTANCE_PCT:
                        target = level['price']
                        break

            # SL: Last Swing High ABOVE current price (5M -> 1H -> 4H -> Daily)
            # Try 5M first
            h, l = self._get_swing_points(data_5m)
            valid = [x for x in h if x > current_price]
            if valid:
                stop = valid[-1]

            if not stop:
                # Try 1H
                h, l = self._get_swing_points(data_1h)
                valid = [x for x in h if x > current_price]
                if valid:
                    stop = valid[-1]
            
            if not stop:
                # Try 4H
                h, l = self._get_swing_points(data_4h)
                valid = [x for x in h if x > current_price]
                if valid:
                    stop = valid[-1]
            
            if not stop:
                # Fallback to Daily
                h, l = self._get_swing_points(daily_data)
                valid = [x for x in h if x > current_price]
                if valid:
                    stop = valid[-1]

        # Validate Max SL Distance with Smart Clamping
        # If structural SL is too far, we clamp it to the max safe limits (0.5%) 
        # to ensure we don't miss the trade opportunity, while staying safe.
        if stop:
            dist_pct = abs(current_price - stop) / current_price * 100
            
            if dist_pct > self.max_sl_distance_pct:
                logger.warning(f"⚠️ Structural SL too wide ({dist_pct:.2f}%). Clamping to {self.max_sl_distance_pct}% to secure entry.")
                
                # Clamp SL to the max allowed distance
                if current_price > stop: # UP Trade: SL is below
                    stop = current_price * (1 - self.max_sl_distance_pct/100)
                else: # DOWN Trade: SL is above
                    stop = current_price * (1 + self.max_sl_distance_pct/100)
                
                # Note: TP remains at structural level, so R:R will arguably Improve
                
        return target, stop

    def _find_trading_range(self, levels: List[Dict], current_price: float) -> Tuple[Optional[float], Optional[float]]:
        """Finds the nearest support and resistance to define the current range."""
        supports = [l['price'] for l in levels if l['price'] < current_price]
        resistances = [l['price'] for l in levels if l['price'] > current_price]
        
        nearest_support = max(supports) if supports else None
        nearest_resistance = min(resistances) if resistances else None
        
        return nearest_support, nearest_resistance

    # --------------------------------------------------------------------------
    # Helper Methods - Entry Execution
    # --------------------------------------------------------------------------

    def _is_in_middle_zone(self, current_price: float, level_a: Optional[float], level_b: Optional[float]) -> bool:
        """
        Checks if price is in the 'dead zone' (middle 40%) between levels.
        """
        if not level_a or not level_b:
            return False
            
        lower = min(level_a, level_b)
        upper = max(level_a, level_b)
        range_size = upper - lower
        
        if range_size == 0:
            return False
            
        dist_from_lower = (current_price - lower) / range_size
        
        # Dead zone logic: 
        # If MIDDLE_ZONE_PCT is 40%, we avoid 30% to 70%.
        # 0.5 +/- (0.4 / 2) -> 0.3 to 0.7
        half_zone = self.middle_zone_pct / 200.0 # /100 for pct, /2 for half
        dead_zone_start = 0.5 - half_zone
        dead_zone_end = 0.5 + half_zone
        
        return dead_zone_start < dist_from_lower < dead_zone_end

    def _find_nearest_level(self, current_price: float, levels: List[Dict]) -> Optional[float]:
        if not levels:
            return None
        sorted_levels = sorted(levels, key=lambda x: abs(x['price'] - current_price))
        return sorted_levels[0]['price']

    def _validate_level_proximity(self, current_price: float, level_price: Optional[float], 
                                  direction: str, symbol: str = None) -> Tuple[bool, str, float]:
        """
        Ensure entry is at reasonable distance from level.
        Prevents entries too close to support/resistance (quick reversals).
        
        Returns: (is_valid, reason, distance_pct)
        """
        if not level_price or level_price == 0:
            return False, "No level price provided", 0.0
        
        distance_pct = abs(current_price - level_price) / current_price * 100
        
        # Get asset-specific threshold (or global fallback)
        max_distance = config.MAX_ENTRY_DISTANCE_PCT  # Default global threshold
        
        if symbol and hasattr(config, 'ASSET_CONFIG'):
            asset_config = config.ASSET_CONFIG.get(symbol, {})
            asset_threshold = asset_config.get('entry_distance_pct')
            if asset_threshold:
                max_distance = asset_threshold
                logger.debug(f"[STRATEGY] Using {symbol}-specific entry distance: {max_distance}%")
            else:
                logger.debug(f"[STRATEGY] No entry_distance_pct for {symbol}, using global {max_distance}%")
        
        if direction == "UP":
            # For UP: Entry should be above level
            if current_price < level_price:
                return False, f"Price {current_price:.2f} below level {level_price:.2f}", distance_pct
            # Entry too far from level (likely chasing)
            if distance_pct > max_distance:
                return False, f"Entry too far from level ({distance_pct:.3f}% > {max_distance}%)", distance_pct
                
        else:  # DOWN
            # For DOWN: Entry should be below level
            if current_price > level_price:
                return False, f"Price {current_price:.2f} above level {level_price:.2f}", distance_pct
            # Entry too far from level (likely chasing)
            if distance_pct > max_distance:
                return False, f"Entry too far from level ({distance_pct:.3f}% > {max_distance}%)", distance_pct
        
        return True, f"Proximity valid ({distance_pct:.3f}%)", distance_pct


    def _check_entry_trigger(self, df_1m: pd.DataFrame, level_price: Optional[float], direction: str) -> Tuple[bool, str]:
        """
        Validates Entry:
        1. Momentum Close > 1.5x ATR (Breakout)
        2. Weak Retest (Pullback < 30%)
        """
        if not level_price:
             return False, "No reference level for entry"

        # Calculate ATR for momentum check
        atr = self._calculate_atr(df_1m)
        if atr == 0:
            return False, "ATR calculation failed"

        # Look back 20 candles for a breakout
        lookback = 20
        recent_data = df_1m.tail(lookback)
        
        breakout_found = False
        breakout_idx = -1
        
        # Check for Momentum Breakout Candle
        # Logic: Candle must CLOSE beyond level, and body > 1.5x ATR
        for i in range(len(recent_data)):
            row = recent_data.iloc[i]
            
            # Candle body size
            body_size = abs(row['close'] - row['open'])
            is_momentum = body_size >= (self.momentum_threshold * atr)
            
            if direction == "UP":
                # Breakout UP: Close > Level
                if row['close'] > level_price and is_momentum:
                    # Filter: Must not have opened way above (gap). Should be a crossing or surge.
                    if row['open'] < level_price or abs(row['open'] - level_price) < (atr * 2):
                         breakout_found = True
                         breakout_idx = i
                         # Keep searching for the MOST RECENT breakout? 
                         # Actually we want the first one that started this move, 
                         # but if there are multiple, the latest one is fine.
            else: # DOWN
                # Breakout DOWN: Close < Level
                if row['close'] < level_price and is_momentum:
                    if row['open'] > level_price or abs(row['open'] - level_price) < (atr * 2):
                        breakout_found = True
                        breakout_idx = i

        if not breakout_found:
             return False, f"No momentum breakout (>{self.momentum_threshold}x ATR) of level {level_price:.2f}"

        # Check for Weak Retest
        # Logic: From breakout candle to now, has price pulled back?
        # A 'Weak Retest' means price touched or came near level, but didn't collapse.
        
        post_breakout_data = recent_data.iloc[breakout_idx+1:]
        current_close = df_1m['close'].iloc[-1]
        
        if post_breakout_data.empty:
            # We are ON the breakout candle. 
            # If it's a huge candle, entering at close might be chasing.
            # But the strategy implies momentum entry is allowed.
            return True, "Fresh Momentum Breakout"

        # Check Depth of Retest
        if direction == "UP":
            # Lowest point since breakout
            min_pullback = post_breakout_data['low'].min()
            min_pullback = min(min_pullback, current_close)
            
            # If pullback went way below level, it failed.
            # Max tolerance: Level - (0.5 * ATR)
            if min_pullback < level_price - (atr * 0.5):
                return False, "Breakout failed (Deep retracement)"
                
            # Current price must be above level (or very close)
            if current_close < level_price - (atr * 0.2):
                return False, "Price currently below breakout level"
                
        else: # DOWN
            # Highest point since breakout
            max_pullback = post_breakout_data['high'].max()
            max_pullback = max(max_pullback, current_close)
            
            if max_pullback > level_price + (atr * 0.5):
                return False, "Breakout failed (Deep retracement)"
                
            if current_close > level_price + (atr * 0.2):
                return False, "Price currently above breakout level"

        # WEAK_RETEST_MAX_PCT check (optional stricter check)
        # Check if current price is within retest percentage of the impulse
        # For simplicity, ensuring we are holding the level is the key 'Weak Retest' validation here.
        
        return True, "Momentum Breakout + Weak Retest Confirmed"

    def _calculate_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        """Calculate ATR for volatility measurement."""
        if len(df) < period + 1:
            return 0.0
            
        high = df['high']
        low = df['low']
        close = df['close']
        
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean().iloc[-1]
        
        return atr if not pd.isna(atr) else 0.0

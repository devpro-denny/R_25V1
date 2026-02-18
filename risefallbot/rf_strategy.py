"""
Rise/Fall Scalping Strategy
Triple-confirmation momentum scalping using EMA crossover, RSI, and Stochastic
rf_strategy.py
"""

from base_strategy import BaseStrategy
from typing import Dict, List, Optional
import pandas as pd
import logging

from indicators import calculate_ema, calculate_rsi, calculate_stochastic
from risefallbot import rf_config

# Dedicated logger for Rise/Fall strategy
logger = logging.getLogger("risefallbot.strategy")


class RiseFallStrategy(BaseStrategy):
    """
    Rise/Fall scalping strategy using triple-confirmation momentum signals.
    
    Entry logic:
        CALL: EMA(5) > EMA(13) AND RSI(7) < 30 AND Stoch %K(5,3) < 20
        PUT:  EMA(5) < EMA(13) AND RSI(7) > 70 AND Stoch %K(5,3) > 80
    
    Contract type: Rise/Fall (CALL/PUT), not multipliers.
    """

    def __init__(self):
        """Initialize Rise/Fall strategy with config parameters."""
        self.ema_fast = rf_config.RF_EMA_FAST
        self.ema_slow = rf_config.RF_EMA_SLOW
        self.rsi_period = rf_config.RF_RSI_PERIOD
        self.rsi_oversold = rf_config.RF_RSI_OVERSOLD
        self.rsi_overbought = rf_config.RF_RSI_OVERBOUGHT
        self.stoch_k_period = rf_config.RF_STOCH_K_PERIOD
        self.stoch_d_period = rf_config.RF_STOCH_D_PERIOD
        self.stoch_oversold = rf_config.RF_STOCH_OVERSOLD
        self.stoch_overbought = rf_config.RF_STOCH_OVERBOUGHT
        self.min_bars = rf_config.RF_MIN_BARS
        self.default_stake = rf_config.RF_DEFAULT_STAKE
        self.duration = rf_config.RF_CONTRACT_DURATION
        self.duration_unit = rf_config.RF_DURATION_UNIT

    def analyze(self, **kwargs) -> Optional[Dict]:
        """
        Analyze 1-minute market data for Rise/Fall scalping opportunities.
        
        Args:
            **kwargs: Must include:
                - data_1m: DataFrame with OHLC columns
                - symbol: Trading symbol (e.g., 'R_10')
                - stake: (optional) Override default stake
        
        Returns:
            Signal dict if triple-confirmation met, None otherwise.
            Signal dict keys: symbol, direction (CALL/PUT), stake, duration, duration_unit
        """
        df = kwargs.get("data_1m")
        symbol = kwargs.get("symbol", "unknown")
        stake = kwargs.get("stake", self.default_stake)

        if df is None or df.empty:
            logger.debug(f"[RF][{symbol}] No 1m data available")
            return None

        if len(df) < self.min_bars:
            logger.debug(
                f"[RF][{symbol}] Warm-up: {len(df)}/{self.min_bars} bars"
            )
            return None

        # --- Compute indicators ---
        ema_fast = calculate_ema(df, self.ema_fast)
        ema_slow = calculate_ema(df, self.ema_slow)
        rsi = calculate_rsi(df, self.rsi_period)
        stoch_k, _ = calculate_stochastic(df, self.stoch_k_period, self.stoch_d_period)

        # Use latest completed bar (second-to-last if streaming, last if historical)
        idx = -1
        ema_f = ema_fast.iloc[idx]
        ema_s = ema_slow.iloc[idx]
        rsi_val = rsi.iloc[idx]
        stoch_val = stoch_k.iloc[idx]

        # Guard against NaN
        if pd.isna(ema_f) or pd.isna(ema_s) or pd.isna(rsi_val) or pd.isna(stoch_val):
            logger.debug(f"[RF][{symbol}] Indicator NaN — skipping")
            return None

        logger.info(
            f"[RF][{symbol}] EMA5={ema_f:.4f} EMA13={ema_s:.4f} "
            f"RSI(7)={rsi_val:.1f} Stoch%K={stoch_val:.1f}"
        )

        # --- Triple-confirmation logic ---
        direction = None

        # CALL: bullish EMA crossover + RSI oversold + Stochastic oversold
        if (ema_f > ema_s
                and rsi_val < self.rsi_oversold
                and stoch_val < self.stoch_oversold):
            direction = "CALL"

        # PUT: bearish EMA crossover + RSI overbought + Stochastic overbought
        elif (ema_f < ema_s
              and rsi_val > self.rsi_overbought
              and stoch_val > self.stoch_overbought):
            direction = "PUT"

        if direction is None:
            logger.debug(f"[RF][{symbol}] No triple-confirmation — no signal")
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
            "confidence": 10,  # Max confidence (triple-confirmation passed)
        }
        logger.info(f"[RF][{symbol}] 🎯 Signal: {direction} | Stake: ${stake}")
        return signal

    def get_required_timeframes(self) -> List[str]:
        """Rise/Fall uses 1-minute candles only."""
        return ["1m"]

    def get_strategy_name(self) -> str:
        """Strategy identifier."""
        return "RiseFall"

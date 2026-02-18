"""
Rise/Fall Scalping Bot Configuration
All Rise/Fall strategy-specific constants and thresholds

⚠️ SINGLE CONCURRENT TRADE ENFORCEMENT:
   The bot STRICTLY enforces only 1 concurrent trade across ALL symbols.
   When a trade is OPEN on any symbol (R_10, R_25, R_50, R_100):
      • The system enters LOCKED state (trade lock active)
      • NO new signals will be processed on any other symbol
      • The bot continuously MONITORS the locked trade until it closes (win/loss/timeout)
      • Only after the locked trade closes can the next trade be executed
   
   This is controlled by:
      • RF_MAX_CONCURRENT_TOTAL = 1 (global limit)
      • RF_MAX_CONCURRENT_PER_SYMBOL = 1 (per-symbol limit)
      • RiseFallRiskManager._trade_lock_active (state tracking)
      • rf_bot.py cycle loop (enforces during signal scan)

rf_config.py
"""

import os

# ==================== SYMBOLS ====================
# Synthetic indices supported for Rise/Fall contracts
RF_SYMBOLS = ["R_10", "R_25", "R_50", "R_100"]

# ==================== TIMEFRAME ====================
RF_TIMEFRAME = "1m"  # 1-minute candles only
RF_CANDLE_COUNT = 50  # Candles to fetch (must be > RF_MIN_BARS)

# ==================== INDICATOR PARAMETERS ====================
# EMA crossover (trend filter)
RF_EMA_FAST = 5
RF_EMA_SLOW = 13

# RSI (momentum oscillator)
RF_RSI_PERIOD = 7
RF_RSI_OVERSOLD = 30   # Below this → CALL opportunity
RF_RSI_OVERBOUGHT = 70  # Above this → PUT opportunity

# Stochastic %K (momentum confirmation)
RF_STOCH_K_PERIOD = 5
RF_STOCH_D_PERIOD = 3
RF_STOCH_OVERSOLD = 20   # Below this → CALL confirmation
RF_STOCH_OVERBOUGHT = 80  # Above this → PUT confirmation

# ==================== CONTRACT PARAMETERS ====================
RF_DEFAULT_STAKE = 1.00      # Default stake in USD
RF_CONTRACT_DURATION = 5     # Contract duration
RF_DURATION_UNIT = "m"       # Duration unit: minutes

# ==================== RISK MANAGEMENT ====================
RF_MAX_CONCURRENT_PER_SYMBOL = 1   # Max 1 trade per symbol at a time
RF_MAX_CONCURRENT_TOTAL = 1        # Max 1 trade total across ALL symbols
RF_COOLDOWN_SECONDS = 30           # Seconds between trades per symbol
RF_MAX_TRADES_PER_DAY = 80         # Daily trade cap across all symbols
RF_MIN_BARS = 30                   # Minimum bars before trading (warm-up)

# Consecutive loss protection
RF_MAX_CONSECUTIVE_LOSSES = 5      # Pause after N consecutive losses
RF_LOSS_COOLDOWN_SECONDS = 120     # Cooldown after hitting loss streak (2 min)

# Take-profit: sell contract early when profit reaches this % of stake
RF_TAKE_PROFIT_PCT = 0.50          # 50% — e.g. $1 stake → sell at $0.50 profit

# Stop-loss: sell contract early when loss reaches this % of stake
RF_STOP_LOSS_PCT = 0.40            # 40% — e.g. $1 stake → sell at -$0.40 loss

# ==================== LOGGING ====================
RF_LOG_FILE = "risefall_bot.log"
RF_LOG_LEVEL = "INFO"

# ==================== WEBSOCKET ====================
RF_WS_URL = "wss://ws.derivws.com/websockets/v3"
RF_WS_TIMEOUT = 30
RF_APP_ID = os.getenv("DERIV_APP_ID", "1089")

# ==================== BOT LOOP ====================
RF_SCAN_INTERVAL = 10  # Seconds between scan cycles

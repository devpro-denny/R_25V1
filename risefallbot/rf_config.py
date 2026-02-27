"""
Rise/Fall scalping bot configuration.
All Rise/Fall strategy-specific constants and thresholds.
"""

import os

# ==================== SYMBOLS ====================
# Synthetic indices supported for Rise/Fall contracts
# 1HZ100V and 1HZ30V are intentionally blocked and must never be traded.
RF_BLOCKED_SYMBOLS = {"1HZ100V", "1HZ30V"}
RF_SYMBOLS = ["R_25", "R_50", "R_75", "R_100", "1HZ25V", "1HZ50V", "1HZ75V", "1HZ90V", "stpRNG5", "stpRNG4"]

# ==================== TIMEFRAME ====================
RF_TIMEFRAME = "1m"   # 1-minute candles only
RF_CANDLE_COUNT = 50  # Candles to fetch (must be > RF_MIN_BARS)

# ==================== INDICATOR PARAMETERS ====================
# EMA crossover (trend filter)
RF_EMA_FAST = 5
RF_EMA_SLOW = 13

# RSI (momentum oscillator)
RF_RSI_PERIOD = 7
RF_RSI_OVERSOLD = 35    # Below this -> CALL opportunity (balanced preset)
RF_RSI_OVERBOUGHT = 65  # Above this -> PUT opportunity (balanced preset)

# Stochastic %K (momentum confirmation)
RF_STOCH_K_PERIOD = 5
RF_STOCH_D_PERIOD = 3
RF_STOCH_OVERSOLD = 25    # Below this -> CALL confirmation (balanced preset)
RF_STOCH_OVERBOUGHT = 75  # Above this -> PUT confirmation (balanced preset)

# ==================== ZONE ANALYSIS ====================
# Spatial filter: identify key support/resistance levels and trade only nearby.
RF_ZONE_LOOKBACK = 50             # Bars to scan for key horizontal zones
RF_ZONE_TOUCH_TOLERANCE = 0.0008  # Price % tolerance for zone proximity (balanced preset)
RF_ZONE_MIN_TOUCHES = 1           # Minimum touches required for a valid zone (balanced preset)

# Candle quality filter: strong body with controlled wick size.
RF_MOMENTUM_BODY_RATIO = 0.60     # Minimum body/range ratio (balanced preset)
RF_MOMENTUM_WICK_RATIO = 0.35     # Maximum wick/range ratio (balanced preset)
RF_MOMENTUM_AVG_LOOKBACK = 3      # Previous candles used for avg body comparison (balanced preset)

# Optimization feature flags.
RF_ENABLE_ZONE_FILTER = True      # Gate signals through zone analysis
RF_ENABLE_CANDLE_FILTER = True    # Gate signals through momentum candle check
RF_RETEST_LOOKBACK = 5            # Bars to inspect for retest scenario
RF_ALLOW_BASIC_SCENARIO = True    # Allow basic scenario when zone filter is enabled (balanced preset)

# ==================== CONTRACT PARAMETERS ====================
RF_DEFAULT_STAKE = 1.00   # Default stake in USD
RF_CONTRACT_DURATION = 2  # Contract duration
RF_DURATION_UNIT = "m"    # Duration unit: minutes

# ==================== RISK MANAGEMENT ====================
RF_MAX_CONCURRENT_PER_SYMBOL = 1  # Max 1 trade per symbol at a time
RF_MAX_CONCURRENT_TOTAL = 1       # Max 1 trade total across all symbols
RF_COOLDOWN_SECONDS = 30          # Seconds between trades per symbol
RF_MAX_TRADES_PER_DAY = 30        # Daily trade cap across all symbols
RF_MIN_BARS = 30                  # Minimum bars before trading (warm-up)

# Watchdog timeout for stale pending entries.
# If a pending entry is older than this with no matching contract, auto-release lock.
RF_PENDING_TIMEOUT_SECONDS = 60

# Consecutive loss protection (Rule 5: prevent 3 consecutive losses).
# After 2 losses, block next trade to avoid a 3rd consecutive loss.
RF_MAX_CONSECUTIVE_LOSSES = 2
RF_LOSS_COOLDOWN_SECONDS = 21600  # 6 hours

# Daily loss limit: stop trading when daily PnL <= -(multiplier * stake)
RF_DAILY_LOSS_LIMIT_MULTIPLIER = 3.0

# Global cooldown: minimum seconds between any trades (across all symbols)
RF_GLOBAL_COOLDOWN_SECONDS = 30

# Max stake cap (safety limit)
RF_MAX_STAKE = 100.0  # Maximum stake per trade (USD)

# ==================== LOGGING ====================
RF_LOG_FILE = "logs/risefall/risefall_bot.log"
RF_LOG_LEVEL = "INFO"

# ==================== DB WRITE RETRY ====================
RF_DB_WRITE_MAX_RETRIES = 3  # Max attempts to write trade to DB
RF_DB_WRITE_RETRY_DELAY = 2  # Seconds between retry attempts

# ==================== WEBSOCKET ====================
RF_WS_URL = "wss://ws.derivws.com/websockets/v3"
RF_WS_TIMEOUT = 30
RF_APP_ID = os.getenv("DERIV_APP_ID", "1089")

# ==================== BOT LOOP ====================
RF_SCAN_INTERVAL = 10  # Seconds between scan cycles

# ==================== CROSS-PROCESS LOCK ====================
# When True, _start_risefall_bot inserts a row into rf_bot_sessions
# (Supabase) before launching the task. A second worker that tries to
# insert will fail, preventing duplicate instances across processes.
RF_ENFORCE_DB_LOCK = True
RF_DB_LOCK_TTL_SECONDS = 900  # Reclaim lock rows older than 15 minutes

# Graceful shutdown: seconds to wait for an in-progress lifecycle to
# finish before hard-cancelling the asyncio task on restart.
RF_GRACEFUL_SHUTDOWN_TIMEOUT = 15

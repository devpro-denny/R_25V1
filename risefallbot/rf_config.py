"""
Rise/Fall Scalping Bot Configuration
All Rise/Fall strategy-specific constants and thresholds

══════════════════════════════════════════════════════════════════════════════
TRADING SYSTEM RULES (must be enforced):
══════════════════════════════════════════════════════════════════════════════
1. Only one open trade at a time across all assets.
2. Once a trade is opened, no other trade until the current trade is fully closed.
3. Let contracts expire naturally without early exit.
4. A new trade can only be opened after the current trade is completely closed.
5. Prevent three consecutive losing trades: after two consecutive losses, block
   the next trade until the loss-streak cooldown expires.
══════════════════════════════════════════════════════════════════════════════

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
RF_CONTRACT_DURATION = 2     # Contract duration
RF_DURATION_UNIT = "m"       # Duration unit: minutes

# ==================== RISK MANAGEMENT ====================
RF_MAX_CONCURRENT_PER_SYMBOL = 1   # Max 1 trade per symbol at a time
RF_MAX_CONCURRENT_TOTAL = 1        # Max 1 trade total across ALL symbols
RF_COOLDOWN_SECONDS = 30           # Seconds between trades per symbol
RF_MAX_TRADES_PER_DAY = 30         # Daily trade cap across all symbols
RF_MIN_BARS = 30                   # Minimum bars before trading (warm-up)

# Watchdog timeout for stale pending entries
# If a 'pending' entry is older than this with no matching contract, auto-release lock
RF_PENDING_TIMEOUT_SECONDS = 60    # 60 seconds — detect hung state and recover

# Consecutive loss protection (Rule 5: prevent 3 consecutive losses)
# After 2 losses, block next trade to avoid a 3rd consecutive loss
RF_MAX_CONSECUTIVE_LOSSES = 2      # Block after 2 consecutive losses
RF_LOSS_COOLDOWN_SECONDS = 21600   # Cooldown after hitting loss streak (6 hours)

# Daily loss limit: stop trading when daily P&L <= -(multiplier × stake)
RF_DAILY_LOSS_LIMIT_MULTIPLIER = 3.0  # Stop when daily loss >= 3× stake

# Global cooldown: minimum seconds between ANY trades (across all symbols)
RF_GLOBAL_COOLDOWN_SECONDS = 30   # After any trade closes, wait before next trade

# Max stake cap (safety limit)
RF_MAX_STAKE = 100.0   # Maximum stake per trade (USD)

# ==================== LOGGING ====================
RF_LOG_FILE = "risefall_bot.log"
RF_LOG_LEVEL = "INFO"

# ==================== DB WRITE RETRY ====================
RF_DB_WRITE_MAX_RETRIES = 3        # Max attempts to write trade to DB
RF_DB_WRITE_RETRY_DELAY = 2        # Seconds between retry attempts

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

# Graceful shutdown: seconds to wait for an in-progress lifecycle to
# finish before hard-cancelling the asyncio task on restart.
RF_GRACEFUL_SHUTDOWN_TIMEOUT = 15

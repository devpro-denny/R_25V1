"""
Rise/Fall bot configuration for Step Index tick-sequence reversals.
"""

import os

# ==================== SYMBOLS ====================
RF_SYMBOLS = [
    "stpRNG",
    "stpRNG2",
]
RF_LEGACY_SYMBOL_ALIASES = {
    "R_100S": "stpRNG",
    "R_200S": "stpRNG2",
}
RF_SUPPORTED_SYMBOLS = tuple(RF_SYMBOLS) + tuple(RF_LEGACY_SYMBOL_ALIASES)
# Keep the generic alias in sync for modules/tests that read SYMBOLS.
SYMBOLS = RF_SYMBOLS
RF_BLOCKED_SYMBOLS = set()

# ==================== STEP INDEX ENTRY MODEL ====================
RF_TICK_SEQUENCE_LENGTH = 4
RF_CONFIRMATION_TICKS = 2
RF_BURST_NOISE_LOOKBACK_MOVES = 4
# Step Index streams at a fixed cadence, so burst speed carries no signal value.
RF_BURST_MAX_SECONDS = 0
RF_TICK_HISTORY_COUNT = (
    RF_BURST_NOISE_LOOKBACK_MOVES
    + RF_TICK_SEQUENCE_LENGTH
    + RF_CONFIRMATION_TICKS
    + 1
)
RF_REQUIRE_CONSECUTIVE_DIRECTION = True
RF_REQUIRE_FRESH_SIGNAL_AFTER_COOLDOWN = True

# ==================== CONTRACT PARAMETERS ====================
RF_DEFAULT_STAKE = 1.00
RF_DEFAULT_DURATION = 5
RF_CONTRACT_DURATION = RF_DEFAULT_DURATION
# Deriv uses "t" as the API code for tick duration.
RF_DURATION_UNIT = "t"
RF_DURATION_UNIT_LABEL = "ticks"

# ==================== RISK MANAGEMENT ====================
RF_MAX_CONCURRENT_PER_SYMBOL = 1
RF_MAX_CONCURRENT_TOTAL = 1
RF_MAX_CONCURRENT_TRADES = 1

# Standard per-symbol/global cooldowns are disabled for the Step Index model.
RF_COOLDOWN_SECONDS = 0
RF_GLOBAL_COOLDOWN_SECONDS = 0

# Cap Rise/Fall entries to 10 per day.
RF_MAX_TRADES_PER_DAY = 10
RF_DAILY_LOSS_LIMIT_MULTIPLIER = 0.0

RF_PENDING_TIMEOUT_SECONDS = 60
RF_MAX_CONSECUTIVE_LOSSES = 2
RF_LOSS_COOLDOWN_SECONDS = 10 * 60
RF_LOSS_STREAK_LIMIT = RF_MAX_CONSECUTIVE_LOSSES
RF_LOSS_STREAK_COOLDOWN_MINUTES = 10
RF_SESSION_MAX_LOSSES = 4
RF_SESSION_RESET_MODE = "daily"

RF_MAX_STAKE = 100.0

# ==================== LOGGING ====================
RF_LOG_FILE = "logs/risefall/risefall_bot.log"
RF_LOG_LEVEL = "INFO"

# ==================== DB WRITE RETRY ====================
RF_DB_WRITE_MAX_RETRIES = 3
RF_DB_WRITE_RETRY_DELAY = 2

# ==================== WEBSOCKET ====================
RF_WS_URL = "wss://ws.derivws.com/websockets/v3"
RF_WS_TIMEOUT = 30
RF_APP_ID = os.getenv("DERIV_APP_ID", "1089")

# ==================== BOT LOOP ====================
RF_SCAN_INTERVAL = 1

# ==================== CROSS-PROCESS LOCK ====================
RF_ENFORCE_DB_LOCK = True
RF_DB_LOCK_TTL_SECONDS = 900

# Graceful shutdown: seconds to wait for an in-progress lifecycle to
# finish before hard-cancelling the asyncio task on restart.
RF_GRACEFUL_SHUTDOWN_TIMEOUT = 15

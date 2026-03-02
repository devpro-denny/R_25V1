"""
Scalping Bot Configuration
All scalping-specific constants and thresholds
"""

# Dedicated symbol universe for scalping (kept local for independence).
# 1HZ100V and 1HZ30V are intentionally blocked and must never be traded.
BLOCKED_SYMBOLS = {"1HZ100V", "1HZ30V", "R_100", "R_25", "1HZ50V", "stpRNG4"}
SYMBOLS = ["R_75", "1HZ25V", "1HZ75V", "1HZ90V", "stpRNG5"]

# Empty rollout list means: trade full scalping symbol universe.
SCALPING_ROLLOUT_SYMBOLS = []

# Dedicated asset config for scalping (duplicated intentionally for isolation).
ASSET_CONFIG = {
    "R_25": {
        "multiplier": 160,
        "description": "Volatility 25 Index",
        "tick_size": 0.01,
        "movement_threshold_pct": 0.5,
        "entry_distance_pct": 0.5,
    },
    "R_50": {
        "multiplier": 80,
        "description": "Volatility 50 Index",
        "tick_size": 0.01,
        "movement_threshold_pct": 0.7,
        "entry_distance_pct": 0.7,
    },
    "R_75": {
        "multiplier": 50,
        "description": "Volatility 75 Index",
        "tick_size": 0.01,
        "movement_threshold_pct": 0.8,
        "entry_distance_pct": 0.8,
    },
    "R_100": {
        "multiplier": 40,
        "description": "Volatility 100 Index",
        "tick_size": 0.01,
        "movement_threshold_pct": 1.0,
        "entry_distance_pct": 1.0,
    },
    "1HZ25V": {
        "multiplier": 160,
        "description": "Volatility 25 (1s) Index",
        "tick_size": 0.01,
        "movement_threshold_pct": 0.9,
        "entry_distance_pct": 0.9,
    },
    "1HZ50V": {
        "multiplier": 80,
        "description": "Volatility 50 (1s) Index",
        "tick_size": 0.01,
        "movement_threshold_pct": 1.1,
        "entry_distance_pct": 1.1,
    },
    "1HZ75V": {
        "multiplier": 50,
        "description": "Volatility 75 (1s) Index",
        "tick_size": 0.01,
        "movement_threshold_pct": 1.2,
        "entry_distance_pct": 1.2,
    },
    "1HZ90V": {
        "multiplier": 45,
        "description": "Volatility 90 (1s) Index",
        "tick_size": 0.01,
        "movement_threshold_pct": 1.3,
        "entry_distance_pct": 1.3,
    },
    "stpRNG5": {
        "multiplier": 100,
        "description": "Step Index 500",
        "tick_size": 0.1,
        "movement_threshold_pct": 0.8,
        "entry_distance_pct": 0.8,
    },
    "stpRNG4": {
        "multiplier": 200,
        "description": "Step Index 400",
        "tick_size": 0.1,
        "movement_threshold_pct": 0.7,
        "entry_distance_pct": 0.7,
    },
}

# ==================== SCALPING STRATEGY PARAMETERS ====================
# Scalping bot uses relaxed thresholds for more frequent trading

SCALPING_TIMEFRAMES = ["1h", "5m", "1m"]
SCALPING_ADX_THRESHOLD = 25
SCALPING_ADX_MAX_THRESHOLD = 34
SCALPING_STPRNG4_MIN_ADX = 25
SCALPING_RSI_UP_MIN = 58
SCALPING_RSI_UP_MAX = 72
SCALPING_RSI_DOWN_MIN = 28
SCALPING_RSI_DOWN_MAX = 42
SCALPING_MAX_PRICE_MOVEMENT_PCT = 1.2
SCALPING_MOMENTUM_THRESHOLD = 1.0  # ATR multiplier
SCALPING_MIN_RR_RATIO = 1.5
# Floating-point guard so values effectively equal to min R:R are not rejected.
SCALPING_RR_TOLERANCE = 0.01
# Final report recommendation (Feb 25-27, 2026):
# widen both SL/TP proportionally to preserve 1.5 R:R while reducing premature stop-outs.
SCALPING_SL_ATR_MULTIPLIER = 2.0
SCALPING_TP_ATR_MULTIPLIER = 3.0
SCALPING_BODY_RATIO_MIN = 0.65
SCALPING_ADX_SLOPE_MIN = -2.0
SCALPING_ZONE_TOLERANCE_PCT = 0.0015
SCALPING_1M_DIRECTIONAL_SEQUENCE_CANDLES = 3
SCALPING_MAX_ENTRY_DRIFT_ATR = 0.35
# 5m EMA fallback minimum slope (percent change per closed candle) used when
# there is no recent fresh crossover.
SCALPING_5M_EMA_SLOPE_MIN_PCT = 0.005

# Asset-specific movement thresholds (conservative × 1.7)
SCALPING_ASSET_MOVEMENT_MULTIPLIER = 1.7

# Temporary directional guard for R_50 DOWN setups.
SCALPING_R50_DOWN_MIN_CONFIDENCE = 9.0

# Directional safety gate from the final improvement report:
# suspend DOWN signals everywhere except explicit allowlist symbols.
SCALPING_DOWN_DIRECTION_FILTER_ENABLED = True
SCALPING_DOWN_ALLOWED_SYMBOLS = {"R_75"}

# Per-symbol ADX minimum overrides (directional). If no symbol override exists,
# the global SCALPING_ADX_THRESHOLD is used.
SCALPING_SYMBOL_ADX_OVERRIDES = {
    "1HZ75V": {"DOWN": 50, "UP": 25},
    "1HZ25V": {"DOWN": 25, "UP": 25},
    "1HZ90V": {"UP": 20},
}

# ==================== SCALPING RISK MANAGEMENT ====================
# Portfolio-wide concurrent cap across all symbols.
SCALPING_MAX_CONCURRENT_TRADES = 1
# Per-symbol concurrent cap (single asset may only have one open trade).
SCALPING_MAX_CONCURRENT_PER_SYMBOL = 1
SCALPING_COOLDOWN_SECONDS = 30
SCALPING_MAX_TRADES_PER_DAY = 80
SCALPING_MAX_CONSECUTIVE_LOSSES = 3
SCALPING_GLOBAL_LOSS_COOLDOWN_SECONDS = 3 * 60 * 60
SCALPING_DAILY_LOSS_MULTIPLIER = 2.0

# Symbol-level cooldown after repeated losses on the same symbol.
SCALPING_SYMBOL_MAX_CONSECUTIVE_LOSSES = 2
SCALPING_SYMBOL_LOSS_COOLDOWN_SECONDS = 45 * 60
SCALPING_SINGLE_LOSS_COOLDOWN_SECONDS = 10 * 60

# Fast-loss suppression: if losses close too quickly, pause that symbol.
SCALPING_SHORT_LOSS_DURATION_SECONDS = 60
SCALPING_SHORT_LOSS_LOOKBACK_SECONDS = 2 * 60 * 60
SCALPING_SHORT_LOSS_COUNT_THRESHOLD = 2
SCALPING_SHORT_LOSS_COOLDOWN_SECONDS = 30 * 60

# Rolling regime guard (3-day win-rate monitor) to halt trading
# when recent market conditions degrade.
SCALPING_PERFORMANCE_WINDOW_DAYS = 3
SCALPING_PERFORMANCE_MIN_TRADES = 10
SCALPING_PERFORMANCE_MIN_WIN_RATE_PCT = 35.0
SCALPING_PERFORMANCE_COOLDOWN_SECONDS = 3 * 60 * 60

# ==================== RUNAWAY TRADE PROTECTION ====================
SCALPING_RUNAWAY_WINDOW_MINUTES = 10
SCALPING_RUNAWAY_TRADE_COUNT = 10

# ==================== STAGNATION EXIT ====================
# Final report recommendation (Feb 25-27, 2026):
# cut stagnation losers earlier without touching winners (which are positive early).
SCALPING_STAGNATION_EXIT_TIME = 120  # seconds
SCALPING_STAGNATION_LOSS_PCT = 3.0  # percentage of stake
SCALPING_STAGNATION_RR_GRACE_THRESHOLD = 2.5
SCALPING_STAGNATION_EXTRA_TIME = 0  # disabled by default for strict 75s/3.0% behavior

SCALPING_SYMBOL_STAGNATION_OVERRIDES = {
    "stpRNG5": 180,
    "R_75": 150,
}

# ==================== TRAILING PROFIT ====================
SCALPING_TRAIL_ACTIVATION_PCT = 6.0

# Dynamic trailing distance tiers: (min_profit_pct, trail_distance_pct)
# As profit grows, the trail widens to give big winners room to breathe.
# Tiers are checked from highest to lowest; first match wins.
SCALPING_TRAIL_TIERS = [
    (30.0, 7.0),   # 30%+ profit -> 7% trail distance
    (15.0, 5.0),   # 15-30% profit -> 5% trail distance
    (12.0, 3.0),   # 12-15% profit -> 3% trail distance (default)
    (6.0, 2.0),    # 6-12% profit -> 2% trail distance
]

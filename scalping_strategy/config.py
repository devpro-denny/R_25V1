"""
Scalping Bot Configuration
All scalping-specific constants and thresholds
"""

# Dedicated symbol universe for scalping (kept local for independence).
SYMBOLS = ["R_25", "R_50", "R_75", "R_100", "1HZ100V", "1HZ25V", "1HZ30V", "1HZ50V", "1HZ75V", "1HZ90V", "stpRNG5", "stpRNG4"]

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
    "1HZ100V": {
        "multiplier": 40,
        "description": "Volatility 100 (1s) Index",
        "tick_size": 0.01,
        "movement_threshold_pct": 1.5,
        "entry_distance_pct": 1.5,
    },
    "1HZ25V": {
        "multiplier": 160,
        "description": "Volatility 25 (1s) Index",
        "tick_size": 0.01,
        "movement_threshold_pct": 0.9,
        "entry_distance_pct": 0.9,
    },
    "1HZ30V": {
        "multiplier": 140,
        "description": "Volatility 30 (1s) Index",
        "tick_size": 0.01,
        "movement_threshold_pct": 1.0,
        "entry_distance_pct": 1.0,
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
SCALPING_ADX_THRESHOLD = 18
SCALPING_RSI_UP_MIN = 55
SCALPING_RSI_UP_MAX = 75
SCALPING_RSI_DOWN_MIN = 22
SCALPING_RSI_DOWN_MAX = 45
SCALPING_MAX_PRICE_MOVEMENT_PCT = 1.2
SCALPING_MOMENTUM_THRESHOLD = 1.0  # ATR multiplier
SCALPING_MIN_RR_RATIO = 1.5
SCALPING_SL_ATR_MULTIPLIER = 1.5
SCALPING_TP_ATR_MULTIPLIER = 2.25
SCALPING_BODY_RATIO_MIN = 0.65
SCALPING_ADX_SLOPE_MIN = -2.0
SCALPING_ZONE_TOLERANCE_PCT = 0.0015
SCALPING_1M_DIRECTIONAL_SEQUENCE_CANDLES = 3
# 5m EMA fallback minimum slope (percent change per closed candle) used when
# there is no recent fresh crossover.
SCALPING_5M_EMA_SLOPE_MIN_PCT = 0.005

# Asset-specific movement thresholds (conservative × 1.7)
SCALPING_ASSET_MOVEMENT_MULTIPLIER = 1.7

# Temporary directional guard for R_50 DOWN setups.
SCALPING_R50_DOWN_MIN_CONFIDENCE = 9.0

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

# Fast-loss suppression: if losses close too quickly, pause that symbol.
SCALPING_SHORT_LOSS_DURATION_SECONDS = 60
SCALPING_SHORT_LOSS_LOOKBACK_SECONDS = 2 * 60 * 60
SCALPING_SHORT_LOSS_COUNT_THRESHOLD = 2
SCALPING_SHORT_LOSS_COOLDOWN_SECONDS = 30 * 60

# ==================== RUNAWAY TRADE PROTECTION ====================
SCALPING_RUNAWAY_WINDOW_MINUTES = 10
SCALPING_RUNAWAY_TRADE_COUNT = 10

# ==================== STAGNATION EXIT ====================
SCALPING_STAGNATION_EXIT_TIME = 120  # seconds (2 minutes)
SCALPING_STAGNATION_LOSS_PCT = 5.0  # percentage of stake

# ==================== TRAILING PROFIT ====================
SCALPING_TRAIL_ACTIVATION_PCT = 12.0   # Trail activates at 12% profit of stake

# Dynamic trailing distance tiers: (min_profit_pct, trail_distance_pct)
# As profit grows, the trail widens to give big winners room to breathe.
# Tiers are checked from highest to lowest; first match wins.
SCALPING_TRAIL_TIERS = [
    (30.0, 7.0),   # 30%+ profit -> 7% trail distance
    (15.0, 5.0),   # 15-30% profit -> 5% trail distance
    (12.0, 3.0),   # 12-15% profit -> 3% trail distance (default)
]

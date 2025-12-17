"""
Configuration Settings for Deriv R_25 Multipliers Trading Bot
Loads API credentials from .env file
config.py - FIXED VERSION
"""

import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# ==================== API CREDENTIALS ====================
# These are loaded from .env file for security
API_TOKEN = os.getenv("API_TOKEN")
APP_ID = os.getenv("APP_ID", "1089")  # Default to public app ID if not set

# For backward compatibility, also check old names
DERIV_API_TOKEN = API_TOKEN if API_TOKEN else os.getenv("DERIV_API_TOKEN")
DERIV_APP_ID = APP_ID if APP_ID and APP_ID != "1089" else os.getenv("DERIV_APP_ID", "1089")

# Validate that API token is set
if not DERIV_API_TOKEN or DERIV_API_TOKEN == "your_api_token_here":
    raise ValueError(
        "API_TOKEN not set! Please add your API token to .env file.\n"
        "Your .env file should contain:\n"
        "APP_ID=1089\n"
        "API_TOKEN=your_actual_token_here"
    )

# ==================== TRADING PARAMETERS ====================
SYMBOL = "R_25"                    # Volatility 25 Index
MARKET = "synthetic_index"         # Market type
CONTRACT_TYPE = "MULTUP"           # Multiplier Up (Buy)
CONTRACT_TYPE_DOWN = "MULTDOWN"    # Multiplier Down (Sell)

# ==================== RISK MANAGEMENT ====================
FIXED_STAKE = 10.0                  # Stake amount per trade (USD)
MULTIPLIER = 160                   # Conservative multiplier (160x)
TAKE_PROFIT_PERCENT = 20.0        # Take profit as percentage (20%)
STOP_LOSS_PERCENT = 8.0           # Stop loss as percentage (8%)
MAX_LOSS_PER_TRADE = 3.0          # Maximum loss per trade (USD)
COOLDOWN_SECONDS = 120             # Wait time between trades (2 minutes)
MAX_TRADES_PER_DAY = 50           # Maximum trades allowed per day
MAX_DAILY_LOSS = 30.0             # Stop trading if daily loss exceeds this

# Valid multipliers for R_25 (for reference)
VALID_MULTIPLIERS = [160, 400, 800, 1200, 1600]

# ==================== DATA FETCHING ====================
# FIXED: Increased CANDLES_5M to support SMA(100) calculation
# SMA(100) needs 100 candles + buffer for valid data after dropna
CANDLES_1M = 150                   # Number of 1-minute candles to fetch
CANDLES_5M = 120                   # INCREASED from 50 to 120 for SMA(100)
MAX_RETRIES = 3                    # Maximum retry attempts for API calls
RETRY_DELAY = 2                    # Seconds to wait between retries

# ==================== STRATEGY PARAMETERS ====================
# ATR Validation Ranges
ATR_MIN_1M = 0.05                 # Minimum 1m ATR
ATR_MAX_1M = 1.5                 # Maximum 1m ATR
ATR_MIN_5M = 0.10                 # Minimum 5m ATR
ATR_MAX_5M = 2.5               # Maximum 5m ATR

# RSI Thresholds
RSI_BUY_THRESHOLD = 60            # RSI must be above this for BUY
RSI_SELL_THRESHOLD = 40           # RSI must be below this for SELL

# ADX Threshold
ADX_THRESHOLD = 18                # Minimum ADX for trend confirmation

# Moving Averages
SMA_PERIOD = 100                  # Simple Moving Average period
EMA_PERIOD = 20                   # Exponential Moving Average period

# Signal Scoring
MINIMUM_SIGNAL_SCORE = 6          # Minimum score to execute trade

# Filters
VOLATILITY_SPIKE_MULTIPLIER = 2.5  # ATR multiplier for spike detection
WEAK_CANDLE_MULTIPLIER = 0.3      # ATR multiplier for weak candle filter

# ==================== TRADE MONITORING ====================
MAX_TRADE_DURATION = 3600          # Maximum trade duration (1 hour)
MONITOR_INTERVAL = 5               # Check trade status every 5 seconds

# ==================== LOGGING ====================
LOG_FILE = "trading_bot.log"
LOG_LEVEL = "DEBUG"                # Changed to DEBUG to see detailed ATR values and analysis

# ==================== WEBSOCKET ====================
WS_URL = "wss://ws.derivws.com/websockets/v3"
WS_TIMEOUT = 30                    # WebSocket connection timeout

# ==================== VALIDATION ====================
def validate_config():
    """Validate configuration settings"""
    errors = []
    
    # Check API credentials
    if not DERIV_API_TOKEN:
        errors.append("API_TOKEN is not set in .env file")
    
    # Validate risk parameters
    if FIXED_STAKE <= 0:
        errors.append("FIXED_STAKE must be positive")
    if TAKE_PROFIT_PERCENT <= 0:
        errors.append("TAKE_PROFIT_PERCENT must be positive")
    if STOP_LOSS_PERCENT <= 0:
        errors.append("STOP_LOSS_PERCENT must be positive")
    if MULTIPLIER not in VALID_MULTIPLIERS:
        errors.append(f"MULTIPLIER must be one of {VALID_MULTIPLIERS}")
    
    # Validate thresholds
    if not (0 < RSI_BUY_THRESHOLD < 100):
        errors.append("RSI_BUY_THRESHOLD must be between 0 and 100")
    if not (0 < RSI_SELL_THRESHOLD < 100):
        errors.append("RSI_SELL_THRESHOLD must be between 0 and 100")
    if RSI_SELL_THRESHOLD >= RSI_BUY_THRESHOLD:
        errors.append("RSI_SELL_THRESHOLD must be less than RSI_BUY_THRESHOLD")
    
    # Validate ATR ranges
    if ATR_MIN_1M >= ATR_MAX_1M:
        errors.append("ATR_MIN_1M must be less than ATR_MAX_1M")
    if ATR_MIN_5M >= ATR_MAX_5M:
        errors.append("ATR_MIN_5M must be less than ATR_MAX_5M")
    
    # Validate data fetching for SMA calculation
    if CANDLES_1M < SMA_PERIOD + 20:
        errors.append(f"CANDLES_1M ({CANDLES_1M}) should be at least {SMA_PERIOD + 20} for SMA({SMA_PERIOD}) calculation")
    if CANDLES_5M < SMA_PERIOD + 20:
        errors.append(f"CANDLES_5M ({CANDLES_5M}) should be at least {SMA_PERIOD + 20} for SMA({SMA_PERIOD}) calculation")
    
    if errors:
        raise ValueError("Configuration validation failed:\n" + "\n".join(f"  - {e}" for e in errors))
    
    return True

# Run validation on import
if __name__ == "__main__":
    try:
        validate_config()
        print("✅ Configuration validation passed!")
        print(f"📊 Symbol: {SYMBOL}")
        print(f"💰 Stake: ${FIXED_STAKE}")
        print(f"🎯 Take Profit: {TAKE_PROFIT_PERCENT}%")
        print(f"🛑 Stop Loss: {STOP_LOSS_PERCENT}%")
        print(f"📈 Multiplier: {MULTIPLIER}x")
        print(f"⏰ Cooldown: {COOLDOWN_SECONDS}s")
        print(f"🔢 Max Daily Trades: {MAX_TRADES_PER_DAY}")
        print(f"📊 1m Candles: {CANDLES_1M}")
        print(f"📊 5m Candles: {CANDLES_5M}")
        print(f"🔐 APP_ID: {DERIV_APP_ID}")
        if DERIV_API_TOKEN:
            print(f"🔐 API Token: {'*' * 20}{DERIV_API_TOKEN[-4:]}")
        else:
            print("❌ API Token: NOT SET")
    except ValueError as e:
        print(f"❌ Configuration Error:\n{e}")
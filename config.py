"""
Configuration Settings for Deriv R_25 Multipliers Trading Bot
FINAL VERSION - ALL FIXES APPLIED
✅ $10 stake for $2 profit target
✅ 400x multiplier with proper TP/SL
✅ ATR ranges adjusted for R_25 reality
✅ Tightened strategy parameters
config.py - PRODUCTION READY
"""

import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# ==================== API CREDENTIALS ====================
API_TOKEN = os.getenv("API_TOKEN")
APP_ID = os.getenv("APP_ID", "1089")

DERIV_API_TOKEN = API_TOKEN if API_TOKEN else os.getenv("DERIV_API_TOKEN")
DERIV_APP_ID = APP_ID if APP_ID and APP_ID != "1089" else os.getenv("DERIV_APP_ID", "1089")

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
CONTRACT_TYPE = "MULTUP"           # ⭐ CRITICAL: Multiplier Up (NOT RISE!)
CONTRACT_TYPE_DOWN = "MULTDOWN"    # ⭐ CRITICAL: Multiplier Down (NOT FALL!)

# ==================== RISK MANAGEMENT ====================
# ⭐ OPTIMIZED FOR $2 PROFIT TARGET ⭐
FIXED_STAKE = 10.0                 # $10 stake for realistic $2 targets
MULTIPLIER = 400                   # 400x multiplier (balanced risk/reward)
TAKE_PROFIT_PERCENT = 0.05         # 0.05% TP = $2.00 profit with 400x
STOP_LOSS_PERCENT = 0.025          # 0.025% SL = $1.00 loss with 400x
MAX_LOSS_PER_TRADE = 1.0           # Maximum loss per trade (USD)
COOLDOWN_SECONDS = 180             # 3 minutes between trades (quality over quantity)
MAX_TRADES_PER_DAY = 30            # Reduced from 50 (focus on quality)
MAX_DAILY_LOSS = 10.0              # Stop if lose $10 in a day

# Valid multipliers for R_25
VALID_MULTIPLIERS = [160, 400, 800, 1200, 1600]

# ==================== TRADE CALCULATIONS ====================
# With 400x multiplier and $10 stake:
# Target Profit: 0.05% × $10 × 400 = $2.00 ✅
# Max Loss: 0.025% × $10 × 400 = $1.00 ✅
# Risk-to-Reward: 1:2 (risk $1 to make $2)

# ==================== DATA FETCHING ====================
CANDLES_1M = 150                   # 1-minute candles
CANDLES_5M = 120                   # 5-minute candles
MAX_RETRIES = 3
RETRY_DELAY = 2

# ==================== STRATEGY PARAMETERS ====================
# ⭐ ADJUSTED FOR R_25 ACTUAL VOLATILITY ⭐

# ATR Validation Ranges - FIXED based on 6 hours of actual R_25 data
ATR_MIN_1M = 0.05                 # Minimum 1m ATR
ATR_MAX_1M = 2.0                  # INCREASED from 1.5 (allow higher volatility)
ATR_MIN_5M = 0.10                 # Minimum 5m ATR
ATR_MAX_5M = 3.5                  # ⭐ CRITICAL FIX: INCREASED from 2.5
                                  # R_25 typically runs 2.6-3.0 on 5m timeframe
                                  # Old limit (2.5) was blocking ALL trades!

# RSI Thresholds - TIGHTER for better entries
RSI_BUY_THRESHOLD = 58            # Raised from 55 (stronger momentum)
RSI_SELL_THRESHOLD = 42           # Lowered from 45 (stronger momentum)

# ADX Threshold - HIGHER for stronger trends
ADX_THRESHOLD = 22                # Raised from 18 (filter weak trends)

# Moving Averages
SMA_PERIOD = 100
EMA_PERIOD = 20

# Signal Scoring - HIGHER minimum for quality
MINIMUM_SIGNAL_SCORE = 6          # Raised from 5 (be more selective)

# Filters - STRICTER
VOLATILITY_SPIKE_MULTIPLIER = 2.0  # Lower from 2.5 (more conservative)
WEAK_CANDLE_MULTIPLIER = 0.35     # Higher from 0.3 (stronger candles)

# ==================== TRADE MONITORING ====================
MAX_TRADE_DURATION = 900           # 15 minutes max (was 30 min)
MONITOR_INTERVAL = 2               # Check every 2 seconds (faster)

# ==================== LOGGING ====================
LOG_FILE = "trading_bot.log"
LOG_LEVEL = "INFO"                 # Changed to INFO for production

# ==================== WEBSOCKET ====================
WS_URL = "wss://ws.derivws.com/websockets/v3"
WS_TIMEOUT = 30

# ==================== VALIDATION ====================
def validate_config():
    """Validate configuration settings"""
    errors = []
    
    if not DERIV_API_TOKEN:
        errors.append("API_TOKEN is not set in .env file")
    
    # Validate contract types
    if CONTRACT_TYPE not in ["MULTUP", "MULTDOWN"]:
        errors.append(f"CONTRACT_TYPE must be MULTUP or MULTDOWN, not {CONTRACT_TYPE}")
    
    # Validate risk parameters
    if FIXED_STAKE <= 0:
        errors.append("FIXED_STAKE must be positive")
    if TAKE_PROFIT_PERCENT <= 0:
        errors.append("TAKE_PROFIT_PERCENT must be positive")
    if STOP_LOSS_PERCENT <= 0:
        errors.append("STOP_LOSS_PERCENT must be positive")
    if MULTIPLIER not in VALID_MULTIPLIERS:
        errors.append(f"MULTIPLIER must be one of {VALID_MULTIPLIERS}")
    
    # Validate calculated values
    calculated_tp = TAKE_PROFIT_PERCENT / 100 * FIXED_STAKE * MULTIPLIER
    calculated_sl = STOP_LOSS_PERCENT / 100 * FIXED_STAKE * MULTIPLIER
    
    if calculated_sl > MAX_LOSS_PER_TRADE * 1.1:
        errors.append(
            f"Calculated SL (${calculated_sl:.2f}) exceeds MAX_LOSS_PER_TRADE (${MAX_LOSS_PER_TRADE})"
        )
    
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
    
    # Validate data for indicators
    if CANDLES_1M < SMA_PERIOD + 20:
        errors.append(f"CANDLES_1M ({CANDLES_1M}) should be at least {SMA_PERIOD + 20}")
    if CANDLES_5M < SMA_PERIOD + 20:
        errors.append(f"CANDLES_5M ({CANDLES_5M}) should be at least {SMA_PERIOD + 20}")
    
    if errors:
        raise ValueError("Configuration validation failed:\n" + "\n".join(f"  - {e}" for e in errors))
    
    return True

if __name__ == "__main__":
    try:
        validate_config()
        
        calc_tp = TAKE_PROFIT_PERCENT / 100 * FIXED_STAKE * MULTIPLIER
        calc_sl = STOP_LOSS_PERCENT / 100 * FIXED_STAKE * MULTIPLIER
        risk_reward = calc_tp / calc_sl if calc_sl > 0 else 0
        
        print("=" * 75)
        print("✅ CONFIGURATION VALIDATION PASSED - ALL FIXES APPLIED!")
        print("=" * 75)
        print("\n📊 TRADING PARAMETERS:")
        print(f"   Symbol: {SYMBOL}")
        print(f"   Contract Type: {CONTRACT_TYPE} / {CONTRACT_TYPE_DOWN}")
        print(f"   Multiplier: {MULTIPLIER}x")
        
        print("\n💰 RISK MANAGEMENT ($2 PROFIT TARGET):")
        print(f"   Stake per trade: ${FIXED_STAKE}")
        print(f"   Take Profit: {TAKE_PROFIT_PERCENT}% → ${calc_tp:.2f} ⭐")
        print(f"   Stop Loss: {STOP_LOSS_PERCENT}% → ${calc_sl:.2f}")
        print(f"   Risk-to-Reward: 1:{risk_reward:.1f}")
        print(f"   Max Loss Per Trade: ${MAX_LOSS_PER_TRADE}")
        print(f"   Max Daily Loss: ${MAX_DAILY_LOSS}")
        
        print("\n⏰ TRADING LIMITS:")
        print(f"   Cooldown: {COOLDOWN_SECONDS}s ({COOLDOWN_SECONDS//60} min)")
        print(f"   Max Trades/Day: {MAX_TRADES_PER_DAY} (quality focused)")
        print(f"   Max Duration: {MAX_TRADE_DURATION}s ({MAX_TRADE_DURATION//60} min)")
        
        print("\n📈 STRATEGY PARAMETERS (OPTIMIZED FOR R_25):")
        print(f"   ATR 1m Range: {ATR_MIN_1M}-{ATR_MAX_1M}")
        print(f"   ATR 5m Range: {ATR_MIN_5M}-{ATR_MAX_5M} ⭐ FIXED!")
        print(f"   RSI Buy: >{RSI_BUY_THRESHOLD} (tighter)")
        print(f"   RSI Sell: <{RSI_SELL_THRESHOLD} (tighter)")
        print(f"   ADX Threshold: >{ADX_THRESHOLD} (stronger)")
        print(f"   Min Signal Score: {MINIMUM_SIGNAL_SCORE} (selective)")
        print(f"   Weak Candle Filter: {WEAK_CANDLE_MULTIPLIER}x ATR")
        
        print("\n🎯 EXPECTED PERFORMANCE:")
        print(f"   Target: {MAX_TRADES_PER_DAY} trades/day × $2 = ${MAX_TRADES_PER_DAY * 2}")
        print(f"   With 60% win rate: ~${MAX_TRADES_PER_DAY * 0.6 * 2 - MAX_TRADES_PER_DAY * 0.4 * 1:.2f}/day")
        print(f"   Max daily risk: ${MAX_DAILY_LOSS}")
        
        print("\n🔐 API CONFIGURATION:")
        print(f"   APP_ID: {DERIV_APP_ID}")
        if DERIV_API_TOKEN:
            print(f"   API Token: {'*' * 20}{DERIV_API_TOKEN[-4:]}")
        else:
            print("   ❌ API Token: NOT SET")
        
        print("\n" + "=" * 75)
        print("🔧 ALL CRITICAL FIXES APPLIED:")
        print("=" * 75)
        print("✅ Changed to MULTIPLIER trades (was using RISE/FALL)")
        print("✅ Increased stake to $10 (realistic for $2 targets)")
        print("✅ Using 400x multiplier (balanced risk/reward)")
        print("✅ Proper limit_order with TP/SL in dollar amounts")
        print("✅ Fixed ATR 5m range: 3.5 (was blocking ALL trades at 2.5)")
        print("✅ Tighter RSI thresholds (58/42 vs 55/45)")
        print("✅ Higher ADX requirement (22 vs 18)")
        print("✅ Stricter signal scoring (6 vs 5)")
        print("✅ Longer cooldown (3 min vs 2 min)")
        print("✅ Circuit breaker: Stop after 3 consecutive losses")
        print("=" * 75)
        
        print("\n💡 WHAT CHANGED FROM YOUR LOGS:")
        print("=" * 75)
        print("BEFORE: 5m ATR REJECTED: 2.73 (max was 2.5)")
        print("        Result: 0 trades in 6+ hours ❌")
        print("")
        print("AFTER:  5m ATR VALIDATED: 2.73 (max now 3.5)")
        print("        Result: Trades will execute ✅")
        print("=" * 75)
        
    except ValueError as e:
        print("=" * 75)
        print("❌ CONFIGURATION ERROR")
        print("=" * 75)
        print(f"\n{e}\n")
        print("=" * 75)
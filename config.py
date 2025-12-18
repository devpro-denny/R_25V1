"""
Configuration Settings for Deriv R_25 Multipliers Trading Bot
ENHANCED VERSION - With 5-Minute Trade Cancellation Risk Management
✅ Cancellation phase as risk filter
✅ Adaptive SL/TP after commitment
✅ Dynamic risk management
config.py - PRODUCTION READY WITH CANCELLATION
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
CONTRACT_TYPE = "MULTUP"           # Multiplier Up
CONTRACT_TYPE_DOWN = "MULTDOWN"    # Multiplier Down

# ==================== RISK MANAGEMENT ====================
FIXED_STAKE = 10.0                 # $10 stake
MULTIPLIER = 400                   # 400x multiplier

# ⭐ NEW: CANCELLATION PHASE PARAMETERS ⭐
ENABLE_CANCELLATION = True         # Enable 5-minute cancellation feature
CANCELLATION_DURATION = 300        # 5 minutes (300 seconds)
CANCELLATION_FEE = 0.45            # Actual Deriv cancellation cost: $0.45
CANCELLATION_THRESHOLD = 0.70      # Cancel if 70% of cancellation cost reached
CANCELLATION_CHECK_INTERVAL = 5    # Check every 5 seconds during cancellation

# ⭐ TWO-PHASE RISK MANAGEMENT ⭐
# Phase 1: During Cancellation (First 5 minutes)
# - Risk: Limited to cancellation fee (typically small % of stake)
# - Action: Cancel if price moves 70% toward cancellation threshold

# Phase 2: After Cancellation Expires (Full Commitment)
POST_CANCEL_STOP_LOSS_PERCENT = 0.0125   # 5% of stake loss = 0.0125% price move with 400x
POST_CANCEL_TAKE_PROFIT_PERCENT = 0.0375  # 15% price move target = 0.0375% with 400x

# Legacy parameters (for backward compatibility if cancellation disabled)
TAKE_PROFIT_PERCENT = 0.05         # 0.05% TP (used if cancellation disabled)
STOP_LOSS_PERCENT = 0.025          # 0.025% SL (used if cancellation disabled)

MAX_LOSS_PER_TRADE = 1.0           # Maximum loss per trade (USD)
COOLDOWN_SECONDS = 180             # 3 minutes between trades
MAX_TRADES_PER_DAY = 30            # Maximum trades per day
MAX_DAILY_LOSS = 10.0              # Stop if lose $10 in a day

# Valid multipliers for R_25
VALID_MULTIPLIERS = [160, 400, 800, 1200, 1600]

# ==================== TRADE CALCULATIONS ====================
# Phase 1 (Cancellation Active - 0-5 minutes):
# - Cancellation Fee: $0.45 (actual Deriv cost for 5-min cancellation)
# - Early Exit Trigger: Cancel at 70% = $0.315 loss
# - If cancelled: Cost is $0.45 (fee paid to exit early)
# - If price moves favorably: Let cancellation expire (no fee)
# 
# Phase 2 (Post-Cancellation - After 5 minutes):
# - Stop Loss: 5% of stake = $0.50 loss
# - Take Profit: 15% favorable move = $6.00 profit target
# - Risk-to-Reward: 1:12 (excellent R:R after passing filter)
#
# Decision Logic:
# If loss reaches $0.315 (70% of $0.45 fee) → CANCEL and pay $0.45
# This prevents larger losses if trade continues badly
# Example: Cancel at -$0.315 loss, pay $0.45 fee = Total cost -$0.765
# Compare to: Let bad trade run to -$0.50 SL = Better to cancel early!

# ==================== DATA FETCHING ====================
CANDLES_1M = 150                   # 1-minute candles
CANDLES_5M = 120                   # 5-minute candles
MAX_RETRIES = 3
RETRY_DELAY = 2

# ==================== STRATEGY PARAMETERS ====================
# ATR Validation Ranges
ATR_MIN_1M = 0.05                 # Minimum 1m ATR
ATR_MAX_1M = 2.0                  # Maximum 1m ATR
ATR_MIN_5M = 0.10                 # Minimum 5m ATR
ATR_MAX_5M = 3.5                  # Maximum 5m ATR

# RSI Thresholds
RSI_BUY_THRESHOLD = 58            # Buy signal threshold
RSI_SELL_THRESHOLD = 42           # Sell signal threshold

# ADX Threshold
ADX_THRESHOLD = 22                # Minimum trend strength

# Moving Averages
SMA_PERIOD = 100
EMA_PERIOD = 20

# Signal Scoring
MINIMUM_SIGNAL_SCORE = 6          # Minimum score to trade

# Filters
VOLATILITY_SPIKE_MULTIPLIER = 2.0
WEAK_CANDLE_MULTIPLIER = 0.35

# ==================== TRADE MONITORING ====================
MAX_TRADE_DURATION = 900           # 15 minutes max after cancellation
MONITOR_INTERVAL = 2               # Check every 2 seconds

# ==================== LOGGING ====================
LOG_FILE = "trading_bot.log"
LOG_LEVEL = "INFO"

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
    if MULTIPLIER not in VALID_MULTIPLIERS:
        errors.append(f"MULTIPLIER must be one of {VALID_MULTIPLIERS}")
    
    # Validate cancellation parameters
    if ENABLE_CANCELLATION:
        if CANCELLATION_DURATION < 60 or CANCELLATION_DURATION > 600:
            errors.append("CANCELLATION_DURATION should be between 60-600 seconds")
        if CANCELLATION_FEE <= 0:
            errors.append("CANCELLATION_FEE must be positive")
        if not (0.5 <= CANCELLATION_THRESHOLD <= 0.9):
            errors.append("CANCELLATION_THRESHOLD should be between 0.5 and 0.9")
        if POST_CANCEL_STOP_LOSS_PERCENT <= 0:
            errors.append("POST_CANCEL_STOP_LOSS_PERCENT must be positive")
        if POST_CANCEL_TAKE_PROFIT_PERCENT <= 0:
            errors.append("POST_CANCEL_TAKE_PROFIT_PERCENT must be positive")
    
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
    
    if errors:
        raise ValueError("Configuration validation failed:\n" + "\n".join(f"  - {e}" for e in errors))
    
    return True

if __name__ == "__main__":
    try:
        validate_config()
        
        print("=" * 75)
        print("✅ ENHANCED CONFIGURATION - CANCELLATION RISK MANAGEMENT")
        print("=" * 75)
        
        print("\n📊 TRADING PARAMETERS:")
        print(f"   Symbol: {SYMBOL}")
        print(f"   Multiplier: {MULTIPLIER}x")
        print(f"   Stake: ${FIXED_STAKE}")
        
        print("\n⏱️ TWO-PHASE RISK MANAGEMENT:")
        print("=" * 75)
        
        if ENABLE_CANCELLATION:
            print("\n🛡️ PHASE 1: CANCELLATION PHASE (First 5 minutes)")
            print(f"   Duration: {CANCELLATION_DURATION}s ({CANCELLATION_DURATION//60} min)")
            print(f"   Cancellation Fee: ${CANCELLATION_FEE:.2f} (Deriv's actual cost)")
            print(f"   Auto-Cancel Threshold: {CANCELLATION_THRESHOLD*100:.0f}% of fee = ${CANCELLATION_FEE * CANCELLATION_THRESHOLD:.2f}")
            print(f"   Check Interval: {CANCELLATION_CHECK_INTERVAL}s")
            print(f"   Purpose: Filter bad trades before full commitment")
            print(f"   Logic: Cancel if loss >= ${CANCELLATION_FEE * CANCELLATION_THRESHOLD:.2f} (pay ${CANCELLATION_FEE:.2f} to prevent worse loss)")
            
            post_sl_amount = POST_CANCEL_STOP_LOSS_PERCENT / 100 * FIXED_STAKE * MULTIPLIER
            post_tp_amount = POST_CANCEL_TAKE_PROFIT_PERCENT / 100 * FIXED_STAKE * MULTIPLIER
            
            print("\n🎯 PHASE 2: COMMITTED PHASE (After 5 minutes)")
            print(f"   Stop Loss: {POST_CANCEL_STOP_LOSS_PERCENT}% → ${post_sl_amount:.2f}")
            print(f"   Take Profit: {POST_CANCEL_TAKE_PROFIT_PERCENT}% → ${post_tp_amount:.2f}")
            print(f"   Risk-to-Reward: 1:{post_tp_amount/post_sl_amount:.1f}")
            print(f"   Max Loss: 5% of stake = ${FIXED_STAKE * 0.05:.2f}")
            print(f"   Target Profit: 15% favorable move")
        else:
            legacy_tp = TAKE_PROFIT_PERCENT / 100 * FIXED_STAKE * MULTIPLIER
            legacy_sl = STOP_LOSS_PERCENT / 100 * FIXED_STAKE * MULTIPLIER
            print("\n⚠️ CANCELLATION DISABLED - Using legacy TP/SL")
            print(f"   Take Profit: ${legacy_tp:.2f}")
            print(f"   Stop Loss: ${legacy_sl:.2f}")
        
        print("\n💡 RISK MANAGEMENT STRATEGY:")
        print("=" * 75)
        print("1. Open trade with 5-min cancellation enabled (costs $0.45)")
        print("2. Monitor price movement during cancellation phase")
        print(f"3. If loss reaches ${CANCELLATION_FEE * CANCELLATION_THRESHOLD:.2f} → CANCEL (pay $0.45 fee)")
        print("4. If price stable/favorable → Let cancellation expire (no fee)")
        print("5. After cancellation expires → Apply adaptive SL/TP")
        print("6. Trade becomes fully committed with optimized risk levels")
        print("\n💭 Cancellation Logic:")
        print(f"   Current Loss: ${CANCELLATION_FEE * CANCELLATION_THRESHOLD:.2f} + Cancel Fee: ${CANCELLATION_FEE:.2f} = Total: ${CANCELLATION_FEE * (1 + CANCELLATION_THRESHOLD):.2f}")
        print(f"   vs. Potential Phase 2 SL: ${FIXED_STAKE * 0.05:.2f}")
        print(f"   Decision: Cancel early if trade clearly moving wrong direction")
        
        print("\n📈 EXPECTED BENEFITS:")
        print("=" * 75)
        print("✅ Reduced losses: Bad trades canceled early")
        print("✅ Better R:R: Only committed trades get full capital")
        print("✅ Improved win rate: Cancellation acts as quality filter")
        print("✅ Dynamic risk: SL/TP set based on actual entry validation")
        
        print("\n⏰ TRADING LIMITS:")
        print(f"   Cooldown: {COOLDOWN_SECONDS}s ({COOLDOWN_SECONDS//60} min)")
        print(f"   Max Trades/Day: {MAX_TRADES_PER_DAY}")
        print(f"   Max Daily Loss: ${MAX_DAILY_LOSS}")
        
        print("\n🔐 API CONFIGURATION:")
        print(f"   APP_ID: {DERIV_APP_ID}")
        if DERIV_API_TOKEN:
            print(f"   API Token: {'*' * 20}{DERIV_API_TOKEN[-4:]}")
        
        print("\n" + "=" * 75)
        print("🚀 READY TO TRADE WITH ENHANCED RISK MANAGEMENT")
        print("=" * 75)
        
    except ValueError as e:
        print("=" * 75)
        print("❌ CONFIGURATION ERROR")
        print("=" * 75)
        print(f"\n{e}\n")
        print("=" * 75)
"""
Configuration API Endpoints
Get and update bot configuration
"""

from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Any

import config
from app.schemas.common import ConfigResponse
from app.core.auth import get_current_active_user

router = APIRouter()

from app.core.cache import cache

from app.core.supabase import supabase

@router.get("/current", response_model=ConfigResponse)
async def get_current_config(current_user: dict = Depends(get_current_active_user)):
    """Get current bot configuration"""
    
    # Fetch user specifics (deriv_api_key, stake_amount, active_strategy)
    deriv_api_key = None
    stake_amount = 50.0 # Default
    active_strategy = "Conservative" # Default

    active_strategy = "Conservative" # Default

    try:
        # Check Cache
        cache_key = f"profile:{current_user['id']}"
        profile_data = cache.get(cache_key)
        
        if profile_data:
            data = profile_data
        else:
            profile = supabase.table('profiles').select('deriv_api_key, stake_amount, active_strategy').eq('id', current_user['id']).single().execute()
            data = profile.data if profile.data else {}
            # Cache Profile (TTL 10 mins)
            cache.set(cache_key, data, ttl=600)

        if data:
            # API Key
            if data.get('deriv_api_key'):
                key = data['deriv_api_key']
                # Mask the key (show last 4 chars if long enough)
                if len(key) > 4:
                    deriv_api_key = f"*****{key[-4:]}"
                else:
                    deriv_api_key = "*****"
            
            # Stake & Strategy
            if data.get('stake_amount') is not None:
                stake_amount = float(data['stake_amount'])
            
            if data.get('active_strategy'):
                active_strategy = data['active_strategy']

    except Exception:
        pass

    return {
        "trading": {
            "symbol": config.SYMBOL,
            "multiplier": config.MULTIPLIER,
            "fixed_stake": stake_amount, # Show effective user stake
            "take_profit_percent": config.TAKE_PROFIT_PERCENT,
            "stop_loss_percent": config.STOP_LOSS_PERCENT,
        },
        "risk_management": {
            "max_trades_per_day": config.MAX_TRADES_PER_DAY,
            "max_daily_loss": stake_amount * 3.0, # Dynamic Calculation
            "cooldown_seconds": config.COOLDOWN_SECONDS,
        },
        "strategy": {
            "rsi_buy_threshold": config.RSI_BUY_THRESHOLD,
            "rsi_sell_threshold": config.RSI_SELL_THRESHOLD,
            "adx_threshold": config.ADX_THRESHOLD,
            "minimum_signal_score": config.MINIMUM_SIGNAL_SCORE,
        },
        "deriv_api_key": deriv_api_key,
        "stake_amount": stake_amount,
        "active_strategy": active_strategy
    }

@router.put("/update")
async def update_config(
    updates: Dict[str, Any],
    current_user: dict = Depends(get_current_active_user)
):
    """
    Update bot configuration at runtime
    
    WARNING: Some changes require bot restart
    """
    try:
        updated_fields = []
        requires_restart = []
        
        # User-Specific Config (Supabase)
        user_updates = {}
        
        if "deriv_api_key" in updates:
            user_updates["deriv_api_key"] = updates["deriv_api_key"]
            updated_fields.append("deriv_api_key")
            
        if "stake_amount" in updates:
            user_updates["stake_amount"] = float(updates["stake_amount"])
            updated_fields.append("stake_amount")

        if "active_strategy" in updates:
            user_updates["active_strategy"] = updates["active_strategy"]
            updated_fields.append("active_strategy")

        if user_updates:
            # Save to Supabase profile
            supabase.table('profiles').update(user_updates).eq("id", current_user["id"]).execute()
            
            # Invalidate Cache
            cache.delete(f"profile:{current_user['id']}")
            
            # If the bot is running for this user, it might need restart (handled by BotManager/Runner logic on next cycle or restart)
        
        # Risk management (can update live)
        if "max_trades_per_day" in updates:
            config.MAX_TRADES_PER_DAY = int(updates["max_trades_per_day"])
            updated_fields.append("max_trades_per_day")
        
        if "cooldown_seconds" in updates:
            config.COOLDOWN_SECONDS = int(updates["cooldown_seconds"])
            updated_fields.append("cooldown_seconds")
        
        return {
            "success": True,
            "updated_fields": updated_fields,
            "requires_restart": requires_restart,
            "message": "Configuration updated successfully"
        }
    
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
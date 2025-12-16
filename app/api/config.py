"""
Configuration API Endpoints
Get and update bot configuration
"""

from fastapi import APIRouter, HTTPException
from typing import Dict, Any

import config
from app.schemas.common import ConfigResponse

router = APIRouter()

@router.get("/current", response_model=ConfigResponse)
async def get_current_config():
    """Get current bot configuration"""
    return {
        "trading": {
            "symbol": config.SYMBOL,
            "multiplier": config.MULTIPLIER,
            "fixed_stake": config.FIXED_STAKE,
            "fixed_tp": config.FIXED_TP,
            "max_loss_per_trade": config.MAX_LOSS_PER_TRADE,
        },
        "risk_management": {
            "max_trades_per_day": config.MAX_TRADES_PER_DAY,
            "max_daily_loss": config.MAX_DAILY_LOSS,
            "cooldown_seconds": config.COOLDOWN_SECONDS,
        },
        "strategy": {
            "rsi_buy_threshold": config.RSI_BUY_THRESHOLD,
            "rsi_sell_threshold": config.RSI_SELL_THRESHOLD,
            "adx_threshold": config.ADX_THRESHOLD,
            "minimum_signal_score": config.MINIMUM_SIGNAL_SCORE,
        }
    }

@router.put("/update")
async def update_config(updates: Dict[str, Any]):
    """
    Update bot configuration at runtime
    
    WARNING: Some changes require bot restart
    """
    try:
        updated_fields = []
        requires_restart = []
        
        # Trading config (requires restart)
        if "fixed_stake" in updates:
            config.FIXED_STAKE = float(updates["fixed_stake"])
            updated_fields.append("fixed_stake")
            requires_restart.append("fixed_stake")
        
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
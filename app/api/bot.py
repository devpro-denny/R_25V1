"""
Bot Control API Endpoints - PROTECTED
Start, stop, restart, and get bot status
Requires authentication to access
"""

from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

from app.bot.manager import bot_manager
from app.schemas.bot import BotStatusResponse, BotControlResponse
from app.core.auth import get_current_active_user
from app.core.supabase import supabase
from app.core.deriv_api_key_crypto import (
    decrypt_deriv_api_key,
    encrypt_deriv_api_key,
    is_encrypted_deriv_api_key,
)

router = APIRouter()


def _load_start_profile(user_id: str):
    """
    Load profile with backward-compatible fallback when
    auto_execute_signals column is not yet available.
    """
    try:
        return (
            supabase.table('profiles')
            .select('deriv_api_key, stake_amount, active_strategy, auto_execute_signals')
            .eq('id', user_id)
            .single()
            .execute()
        )
    except Exception as e:
        error_text = str(e).lower()
        if "auto_execute_signals" not in error_text:
            raise
        logger.warning(
            "profiles.auto_execute_signals missing; falling back to legacy start profile select for user %s",
            user_id,
        )
        return (
            supabase.table('profiles')
            .select('deriv_api_key, stake_amount, active_strategy')
            .eq('id', user_id)
            .single()
            .execute()
        )

@router.post("/start", response_model=BotControlResponse)
async def start_bot(current_user: dict = Depends(get_current_active_user)):
    """
    Start the trading bot
    
    **Requires authentication**
    
    Only authenticated users can start the bot.
    """
    # Fetch API Key from profile
    # Fetch API Key, Stake, Strategy, and execution mode from profile
    api_key = None
    stake_amount = 50.0
    active_strategy = "Conservative"
    auto_execute_signals = False

    try:
        profile = _load_start_profile(current_user['id'])
        if profile.data:
            stored_key = profile.data.get("deriv_api_key")
            api_key = decrypt_deriv_api_key(stored_key)
            if profile.data.get('stake_amount') is not None:
                stake_amount = float(profile.data['stake_amount'])
            if profile.data.get('active_strategy'):
                active_strategy = profile.data['active_strategy']
            auto_execute_signals = bool(profile.data.get("auto_execute_signals", False))

            # Backward-compatible migration: auto-encrypt legacy plaintext keys.
            if stored_key and not is_encrypted_deriv_api_key(stored_key):
                try:
                    supabase.table("profiles").update(
                        {"deriv_api_key": encrypt_deriv_api_key(api_key)}
                    ).eq("id", current_user["id"]).execute()
                except Exception as migration_error:
                    logger.warning(
                        f"Failed to auto-migrate plaintext Deriv API key for "
                        f"user {current_user['id']}: {migration_error}"
                    )
    except Exception as e:
        logger.error(f"Error fetching profile for user {current_user['id']}: {e}")

    # Enforce API Key existence in DB
    if not api_key:
        raise HTTPException(
            status_code=400, 
            detail="No Deriv API Key found. Please add your API Token in Settings first."
        )

    result = await bot_manager.start_bot(
        current_user['id'], 
        api_token=api_key,
        stake=stake_amount,
        strategy_name=active_strategy,
        auto_execute_signals=auto_execute_signals,
    )
    
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    
    # Add user info to response
    result["started_by"] = current_user["email"]
    result["timestamp"] = datetime.now().isoformat()
    
    return result

@router.post("/stop", response_model=BotControlResponse)
async def stop_bot(current_user: dict = Depends(get_current_active_user)):
    """
    Stop the trading bot
    
    **Requires authentication**
    """
    result = await bot_manager.stop_bot(current_user['id'])
    
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    
    # Add user info to response
    result["stopped_by"] = current_user["email"]
    result["timestamp"] = datetime.now().isoformat()
    
    return result

@router.post("/restart", response_model=BotControlResponse)
async def restart_bot(current_user: dict = Depends(get_current_active_user)):
    """
    Restart the trading bot
    
    **Requires authentication**
    """
    result = await bot_manager.restart_bot(current_user['id'])
    
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    
    # Add user info to response
    result["restarted_by"] = current_user["email"]
    result["timestamp"] = datetime.now().isoformat()
    
    return result

@router.get("/status", response_model=BotStatusResponse)
async def get_bot_status(current_user: dict = Depends(get_current_active_user)):
    """
    Get current bot status
    
    **Requires authentication**
    Returns detailed information about the bot's current state.
    """
    status = bot_manager.get_status(current_user['id'])
    
    # Add active_strategy and effective_limits if bot is running
    bot = bot_manager._bots.get(current_user['id'])
    if bot and bot.strategy and bot.risk_manager:
        status["active_strategy"] = bot.strategy.get_strategy_name()
        status["effective_limits"] = bot.risk_manager.get_current_limits()
    else:
        status["active_strategy"] = None
        status["effective_limits"] = {}
    
    # Add user info to response
    status["viewed_by"] = current_user["email"]
    status["timestamp"] = datetime.now().isoformat()
    
    return status

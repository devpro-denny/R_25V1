"""
Bot Control API Endpoints - PROTECTED
Start, stop, restart, and get bot status
Requires authentication to access
"""

from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime

from app.bot.runner import bot_runner
from app.schemas.bot import BotStatusResponse, BotControlResponse
from app.core.auth import get_current_active_user

router = APIRouter()

@router.post("/start", response_model=BotControlResponse)
async def start_bot(current_user: dict = Depends(get_current_active_user)):
    """
    Start the trading bot
    
    **Requires authentication**
    
    Only authenticated users can start the bot.
    """
    result = await bot_runner.start_bot()
    
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    
    # Add user info to response
    result["started_by"] = current_user["username"]
    result["timestamp"] = datetime.now().isoformat()
    
    return result

@router.post("/stop", response_model=BotControlResponse)
async def stop_bot(current_user: dict = Depends(get_current_active_user)):
    """
    Stop the trading bot
    
    **Requires authentication**
    
    Only authenticated users can stop the bot.
    """
    result = await bot_runner.stop_bot()
    
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    
    # Add user info to response
    result["stopped_by"] = current_user["username"]
    result["timestamp"] = datetime.now().isoformat()
    
    return result

@router.post("/restart", response_model=BotControlResponse)
async def restart_bot(current_user: dict = Depends(get_current_active_user)):
    """
    Restart the trading bot
    
    **Requires authentication**
    
    Only authenticated users can restart the bot.
    """
    result = await bot_runner.restart_bot()
    
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    
    # Add user info to response
    result["restarted_by"] = current_user["username"]
    result["timestamp"] = datetime.now().isoformat()
    
    return result

@router.get("/status", response_model=BotStatusResponse)
async def get_bot_status(current_user: dict = Depends(get_current_active_user)):
    """
    Get current bot status
    
    **Requires authentication**
    
    Returns detailed information about the bot's current state,
    including running status, uptime, and statistics.
    """
    status = bot_runner.get_status()
    
    # Add user info to response
    status["viewed_by"] = current_user["username"]
    status["timestamp"] = datetime.now().isoformat()
    
    return status
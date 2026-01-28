"""
Trades API Endpoints
View active trades, history, and statistics
"""

from fastapi import APIRouter, Query, Depends, HTTPException
from typing import List

from app.bot.manager import bot_manager
from app.schemas.trades import TradeResponse, TradeStatsResponse
from app.core.serializers import prepare_response  # ← ADD THIS LINE
from app.core.auth import get_current_active_user

router = APIRouter()

@router.get("/active", response_model=List[TradeResponse])
async def get_active_trades(
    current_user: dict = Depends(get_current_active_user)  # ← ADD AUTH
):
    """Get all active trades"""
    trades = bot_manager.get_bot(current_user['id']).state.get_active_trades()
    return prepare_response(
        trades,
        id_fields=['contract_id']  # ← Convert contract_id to string
    )

@router.get("/history", response_model=List[TradeResponse])
async def get_trade_history(
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(get_current_active_user)
):
    """Get trade history from persistent storage"""
    # Fetch from Supabase via Service
    from app.services.trades_service import UserTradesService
    history = UserTradesService.get_user_trades(current_user['id'], limit)
    
    return prepare_response(
        history,
        id_fields=['contract_id']
    )

@router.get("/stats", response_model=TradeStatsResponse)
async def get_trade_stats(
    current_user: dict = Depends(get_current_active_user)
):
    """Get trading statistics from persistent storage"""
    try:
        from app.services.trades_service import UserTradesService
        import logging
        logger = logging.getLogger(__name__)
        
        logger.info(f"📊 Fetching stats for user: {current_user.get('id')}")
        
        stats = UserTradesService.get_user_stats(current_user['id'])
        
        if not stats:
            logger.error(f"❌ Stats returned None for user {current_user.get('id')}")
            # Return empty stats instead of failing
            stats = {
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate": 0.0,
                "total_pnl": 0.0,
                "daily_pnl": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "largest_win": 0.0,
                "largest_loss": 0.0,
                "profit_factor": 0.0
            }
            
        return prepare_response(stats)
    except Exception as e:
        import traceback
        error_msg = f"❌ Critical error in get_trade_stats: {str(e)}\n{traceback.format_exc()}"
        print(error_msg) # Force print to stdout for Railway logs
        logging.getLogger(__name__).error(error_msg)
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {str(e)}")

@router.get("/stats/debug")
async def debug_trade_stats(
    current_user: dict = Depends(get_current_active_user)
):
    """Debug endpoint to inspect raw stats data"""
    try:
        from app.services.trades_service import UserTradesService
        from app.core.supabase import supabase
        
        user_id = current_user['id']
        
        # 1. Check raw count
        count_res = supabase.table("trades").select("*", count="exact").eq("user_id", user_id).execute()
        
        # 2. Run service method
        stats = UserTradesService.get_user_stats(user_id)
        
        return {
            "user_id": user_id,
            "trade_count_supabase": count_res.count,
            "service_stats_result": stats,
            "raw_data_sample": count_res.data[:2] if count_res.data else []
        }
    except Exception as e:
        import traceback
        return {
            "error": str(e),
            "traceback": traceback.format_exc()
        }
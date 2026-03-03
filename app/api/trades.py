"""
Trades API Endpoints
View active trades, history, and statistics
"""

from fastapi import APIRouter, Query, Depends, HTTPException
from typing import List
from datetime import datetime

from app.bot.manager import bot_manager
from app.schemas.trades import (
    TradeExitControlsResponse,
    TradeExitControlsUpdate,
    TradeResponse,
    TradeStatsResponse,
)
from app.core.serializers import prepare_response  # ← ADD THIS LINE
from app.core.auth import get_current_active_user
from app.services.trades_service import UserTradesService

router = APIRouter()

@router.get("/active", response_model=List[TradeResponse])
async def get_active_trades(
    current_user: dict = Depends(get_current_active_user)  # ← ADD AUTH
):
    """Get all active trades"""
    user_id = current_user["id"]
    bot = None
    bots_map = getattr(bot_manager, "_bots", None)
    if isinstance(bots_map, dict):
        bot = bots_map.get(user_id)
    else:
        # Test fallback when bot_manager is mocked
        bot = bot_manager.get_bot(user_id)

    trades = bot.state.get_active_trades() if bot else []

    # Fallback: some strategies track active trade state in risk_manager metadata
    # rather than BotState.active_trades.
    if (
        not trades
        and bot
        and bot.risk_manager
        and hasattr(bot.risk_manager, "get_active_trade_info")
    ):
        active_info = bot.risk_manager.get_active_trade_info()
        if active_info and active_info.get("contract_id"):
            strategy_name = None
            if bot.strategy and hasattr(bot.strategy, "get_strategy_name"):
                try:
                    strategy_name = bot.strategy.get_strategy_name()
                except Exception:
                    strategy_name = None

            trades = [{
                "contract_id": str(active_info.get("contract_id")),
                "symbol": active_info.get("symbol", "UNKNOWN"),
                "direction": active_info.get("direction", "UP"),
                "strategy_type": strategy_name,
                "stake": active_info.get("stake"),
                "entry_price": active_info.get("entry_price"),
                "status": "open",
                "timestamp": active_info.get("open_time") or active_info.get("timestamp"),
                "trailing_enabled": active_info.get("trailing_enabled"),
                "stagnation_enabled": active_info.get("stagnation_enabled"),
            }]

    # Persisted fallback: keep active trades visible even when bot is stopped/restarted.
    if not trades:
        trades = UserTradesService.get_user_active_trades(user_id)

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
    """Enhanced debug endpoint to comprehensively inspect stats calculation"""
    debug_info = {
        "timestamp": datetime.now().isoformat(),
        "user_info": {},
        "database_queries": {},
        "service_results": {},
        "cache_status": {},
        "calculations": {},
        "sample_data": {},
        "errors": []
    }
    
    try:
        from app.services.trades_service import UserTradesService
        from app.core.supabase import supabase
        from app.core.cache import cache
        
        user_id = current_user['id']
        
        # User Information
        debug_info["user_info"] = {
            "user_id": user_id,
            "email": current_user.get('email'),
            "role": current_user.get('role')
        }
        
        # 1. Database Queries
        try:
            # Total count
            count_res = supabase.table("trades").select("*", count="exact").eq("user_id", user_id).execute()
            debug_info["database_queries"]["total_count"] = count_res.count
            
            # Get all trades for detailed analysis
            all_trades_res = supabase.table("trades").select("*").eq("user_id", user_id).order("timestamp", desc=True).execute()
            all_trades = all_trades_res.data if all_trades_res.data else []
            
            debug_info["database_queries"]["fetched_count"] = len(all_trades)
            debug_info["database_queries"]["query_status"] = "success"
            
            # Breakdown by status
            status_breakdown = {}
            for trade in all_trades:
                status = trade.get('status', 'unknown')
                status_breakdown[status] = status_breakdown.get(status, 0) + 1
            debug_info["database_queries"]["status_breakdown"] = status_breakdown
            
        except Exception as e:
            debug_info["errors"].append({
                "stage": "database_queries",
                "error": str(e),
                "traceback": __import__('traceback').format_exc()
            })
        
        # 2. Cache Status
        try:
            cache_key = f"stats:{user_id}"
            cached_stats = cache.get(cache_key)
            debug_info["cache_status"] = {
                "cache_key": cache_key,
                "is_cached": cached_stats is not None,
                "cached_value": cached_stats if cached_stats else "Not in cache"
            }
        except Exception as e:
            debug_info["errors"].append({
                "stage": "cache_check",
                "error": str(e)
            })
        
        # 3. Service Method Results
        try:
            stats = UserTradesService.get_user_stats(user_id)
            debug_info["service_results"] = {
                "stats": stats,
                "is_none": stats is None,
                "is_empty": stats == {} if stats else True
            }
        except Exception as e:
            debug_info["errors"].append({
                "stage": "service_method",
                "error": str(e),
                "traceback": __import__('traceback').format_exc()
            })
        
        # 4. Manual Calculation Breakdown
        try:
            if all_trades:
                # Calculate detailed breakdown
                profits = [t.get('profit', 0) for t in all_trades]
                wins = [p for p in profits if p > 0]
                losses = [abs(p) for p in profits if p < 0]
                breakevens = [p for p in profits if p == 0]
                
                debug_info["calculations"] = {
                    "total_trades": len(all_trades),
                    "trades_with_profit_data": len([t for t in all_trades if 'profit' in t and t['profit'] is not None]),
                    "win_count": len(wins),
                    "loss_count": len(losses),
                    "breakeven_count": len(breakevens),
                    "win_rate_percent": (len(wins) / len(all_trades) * 100) if all_trades else 0,
                    "total_pnl": sum(profits),
                    "gross_profit": sum(wins) if wins else 0,
                    "gross_loss": sum(losses) if losses else 0,
                    "avg_win": (sum(wins) / len(wins)) if wins else 0,
                    "avg_loss": (sum(losses) / len(losses)) if losses else 0,
                    "largest_win": max(wins) if wins else 0,
                    "largest_loss": max(losses) if losses else 0,
                    "profit_factor": (sum(wins) / sum(losses)) if (wins and losses and sum(losses) > 0) else 0
                }
        except Exception as e:
            debug_info["errors"].append({
                "stage": "manual_calculations",
                "error": str(e),
                "traceback": __import__('traceback').format_exc()
            })
        
        # 5. Sample Data
        try:
            debug_info["sample_data"] = {
                "first_3_trades": all_trades[:3] if all_trades else [],
                "last_3_trades": all_trades[-3:] if len(all_trades) >= 3 else all_trades,
                "sample_profit_values": [t.get('profit') for t in all_trades[:5]] if all_trades else []
            }
        except Exception as e:
            debug_info["errors"].append({
                "stage": "sample_data",
                "error": str(e)
            })
        
        # 6. Date Range Analysis (if available)
        try:
            if all_trades:
                timestamps = [t.get('timestamp') for t in all_trades if t.get('timestamp')]
                if timestamps:
                    debug_info["date_analysis"] = {
                        "oldest_trade": min(timestamps),
                        "newest_trade": max(timestamps),
                        "total_span_days": (datetime.fromisoformat(max(timestamps).replace('Z', '+00:00')) - 
                                          datetime.fromisoformat(min(timestamps).replace('Z', '+00:00'))).days
                    }
        except Exception as e:
            debug_info["errors"].append({
                "stage": "date_analysis",
                "error": str(e)
            })
        
        return prepare_response(debug_info)
        
    except Exception as e:
        import traceback
        return prepare_response({
            "critical_error": str(e),
            "traceback": traceback.format_exc(),
            "partial_debug_info": debug_info
        })


@router.patch("/active/{contract_id}/exit-controls", response_model=TradeExitControlsResponse)
async def update_active_trade_exit_controls(
    contract_id: str,
    payload: TradeExitControlsUpdate,
    current_user: dict = Depends(get_current_active_user),
):
    """Toggle trailing/stagnation runtime controls for an active trade."""
    bot = bot_manager._bots.get(current_user["id"])
    if not bot or not bot.is_running or not bot.risk_manager:
        raise HTTPException(status_code=404, detail="No running bot for this user")

    risk_manager = bot.risk_manager
    if not hasattr(risk_manager, "set_trade_exit_controls"):
        raise HTTPException(
            status_code=400,
            detail="Active strategy does not support runtime exit controls",
        )

    updated = risk_manager.set_trade_exit_controls(
        contract_id=contract_id,
        trailing_enabled=payload.trailing_enabled,
        stagnation_enabled=payload.stagnation_enabled,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Active trade not found")

    return prepare_response(updated, id_fields=["contract_id"])

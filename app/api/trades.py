"""
Trades API Endpoints
View active trades, history, and statistics
"""

from fastapi import APIRouter, Query, Depends, HTTPException
from typing import Any, Dict, List, Optional, Set
from datetime import datetime
import logging

import config
from app.bot.events import event_manager
from app.bot.manager import bot_manager
from app.bot.telegram_bridge import telegram_bridge as default_telegram_bridge
from app.schemas.trades import (
    TradeExitControlsResponse,
    TradeExitControlsUpdate,
    TradeResponse,
    TradeSyncResponse,
    TradeStatsResponse,
)
from app.core.serializers import prepare_response
from app.core.auth import get_current_active_user
from app.core.deriv_api_key_crypto import decrypt_deriv_api_key
from app.core.supabase import supabase
from app.services.trades_service import UserTradesService
from trade_engine import TradeEngine

router = APIRouter()


def _normalize_direction(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    raw = str(value).strip().upper()
    if raw in {"UP", "BUY", "CALL", "RISE"}:
        return "UP"
    if raw in {"DOWN", "SELL", "PUT", "FALL"}:
        return "DOWN"
    return None


def _normalize_strategy(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        from strategy_registry import normalize_strategy_name

        return normalize_strategy_name(text)
    except Exception:
        return text


def _get_user_bot(user_id: str):
    bots_map = getattr(bot_manager, "_bots", None)
    if isinstance(bots_map, dict):
        return bots_map.get(user_id)
    return bot_manager.get_bot(user_id)


def _to_float(value: Optional[object]) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_datetime(value: Optional[object]) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value))
        except Exception:
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except Exception:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def _normalize_multiplier_direction(contract_type: Optional[str]) -> Optional[str]:
    raw = str(contract_type or "").strip().upper()
    if raw == "MULTUP":
        return "UP"
    if raw == "MULTDOWN":
        return "DOWN"
    return None


def _collect_runtime_active_contract_ids(bot: Any) -> Set[str]:
    ids: Set[str] = set()
    if not bot or not getattr(bot, "risk_manager", None):
        return ids

    risk_manager = bot.risk_manager
    active_trades = getattr(risk_manager, "active_trades", None)
    if isinstance(active_trades, list):
        for trade in active_trades:
            contract_id = trade.get("contract_id") if isinstance(trade, dict) else trade
            if contract_id not in (None, ""):
                ids.add(str(contract_id))

    if hasattr(risk_manager, "get_active_trade_info"):
        active_info = risk_manager.get_active_trade_info()
        if isinstance(active_info, dict):
            contract_id = active_info.get("contract_id")
            if contract_id not in (None, ""):
                ids.add(str(contract_id))

    return ids


def _register_runtime_tracking(bot: Any, trade_payload: Dict[str, Any]) -> bool:
    """
    Register imported trades into in-memory runtime so normal monitor/exit logic applies.
    """
    if not bot or not bot.is_running or not getattr(bot, "risk_manager", None):
        return False
    if not hasattr(bot.risk_manager, "record_trade_open"):
        return False

    contract_id = str(trade_payload.get("contract_id") or "").strip()
    if not contract_id:
        return False

    if contract_id in _collect_runtime_active_contract_ids(bot):
        return False

    bot.risk_manager.record_trade_open(trade_payload)

    if getattr(bot, "state", None):
        exists_in_state = any(
            isinstance(row, dict) and str(row.get("contract_id")) == contract_id
            for row in list(getattr(bot.state, "active_trades", []))
        )
        if not exists_in_state and hasattr(bot.state, "add_trade"):
            bot.state.add_trade(
                {
                    "contract_id": contract_id,
                    "symbol": trade_payload.get("symbol"),
                    "direction": trade_payload.get("direction"),
                    "strategy_type": trade_payload.get("strategy_type"),
                    "stake": trade_payload.get("stake"),
                    "entry_price": trade_payload.get("entry_price"),
                    "status": "open",
                    "timestamp": trade_payload.get("timestamp"),
                    "trailing_enabled": True,
                    "stagnation_enabled": True,
                    "entry_source": trade_payload.get("entry_source"),
                    "multiplier": trade_payload.get("multiplier"),
                }
            )

    return True


def _load_user_trading_context(user_id: str) -> Dict[str, Optional[str]]:
    """
    Return decrypted Deriv API key and user strategy profile defaults.
    """
    context: Dict[str, Optional[str]] = {"deriv_api_key": None, "active_strategy": None}
    try:
        response = (
            supabase.table("profiles")
            .select("deriv_api_key,active_strategy")
            .eq("id", user_id)
            .single()
            .execute()
        )
        data = dict(response.data or {})
        encrypted_key = data.get("deriv_api_key")
        context["deriv_api_key"] = decrypt_deriv_api_key(encrypted_key) if encrypted_key else None
        context["active_strategy"] = _normalize_strategy(data.get("active_strategy"))
    except Exception as e:
        logging.getLogger(__name__).warning(
            "Failed to load trading context for user %s: %s",
            user_id,
            e,
        )
    return context


def _normalize_active_trade_rows(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for trade in trades:
        if not isinstance(trade, dict):
            continue
        row = dict(trade)
        contract_id = row.get("contract_id")
        if contract_id not in (None, ""):
            row["contract_id"] = str(contract_id)

        direction = _normalize_direction(row.get("direction") or row.get("signal"))
        if direction:
            row["direction"] = direction
            row["signal"] = direction

        if row.get("trailing_enabled") is None:
            row["trailing_enabled"] = True
        if row.get("stagnation_enabled") is None:
            row["stagnation_enabled"] = True
        if not row.get("entry_source"):
            row["entry_source"] = "system"

        normalized.append(row)
    return normalized

@router.get("/active", response_model=List[TradeResponse])
async def get_active_trades(
    current_user: dict = Depends(get_current_active_user)
):
    """Get all active trades"""
    user_id = current_user["id"]
    bot = _get_user_bot(user_id)

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
                "multiplier": active_info.get("multiplier"),
                "entry_source": active_info.get("entry_source") or "system",
            }]

    # Persisted fallback: keep active trades visible even when bot is stopped/restarted.
    if not trades:
        trades = UserTradesService.get_user_active_trades(user_id)

    trades = _normalize_active_trade_rows(list(trades or []))

    return prepare_response(
        trades,
        id_fields=['contract_id']
    )


@router.post("/active/sync", response_model=TradeSyncResponse)
async def sync_active_trades(
    current_user: dict = Depends(get_current_active_user),
):
    """
    Sync manually opened multiplier contracts from broker into local tracking.
    """
    user_id = current_user["id"]
    bot = _get_user_bot(user_id)
    logger = logging.getLogger(__name__)
    notification_bridge = (
        getattr(bot, "telegram_bridge", None)
        if bot is not None
        else default_telegram_bridge
    )
    if notification_bridge is None:
        notification_bridge = default_telegram_bridge

    running_strategy = None
    if bot and getattr(bot, "strategy", None) and hasattr(bot.strategy, "get_strategy_name"):
        try:
            running_strategy = _normalize_strategy(bot.strategy.get_strategy_name())
        except Exception:
            running_strategy = None

    trading_context = _load_user_trading_context(user_id)
    strategy_type = running_strategy or trading_context.get("active_strategy") or "Conservative"
    if strategy_type not in {"Conservative", "Scalping"}:
        raise HTTPException(
            status_code=400,
            detail="Contract sync supports Conservative and Scalping multiplier strategies only",
        )

    trade_engine: Optional[TradeEngine] = None
    owns_engine_connection = False

    if bot and getattr(bot, "trade_engine", None):
        trade_engine = bot.trade_engine
        if not getattr(trade_engine, "is_connected", False):
            connected = await trade_engine.connect()
            if not connected:
                raise HTTPException(
                    status_code=502,
                    detail="Failed to connect to broker using active bot trade engine",
                )
    else:
        api_key = trading_context.get("deriv_api_key")
        if not api_key:
            raise HTTPException(
                status_code=400,
                detail="No Deriv API key found. Add one in Settings before syncing trades.",
            )
        trade_engine = TradeEngine(
            api_key=api_key,
            app_id=getattr(config, "DERIV_APP_ID", "1089"),
            risk_mode=str(strategy_type).upper(),
        )
        owns_engine_connection = True
        connected = await trade_engine.connect()
        if not connected:
            raise HTTPException(status_code=502, detail="Failed to connect to broker for trade sync")

    imported_contract_ids: List[str] = []
    skipped_non_multiplier_ids: List[str] = []
    failed_contract_ids: List[str] = []
    runtime_registered_count = 0

    try:
        portfolio_response = await trade_engine.portfolio({"portfolio": 1})
        if not isinstance(portfolio_response, dict) or "error" in portfolio_response:
            detail = "Broker portfolio request failed"
            if isinstance(portfolio_response, dict):
                detail = (
                    portfolio_response.get("error", {}).get("message")
                    or detail
                )
            raise HTTPException(status_code=502, detail=detail)

        contracts = list((portfolio_response.get("portfolio") or {}).get("contracts") or [])
        broker_contracts: Dict[str, Dict[str, Any]] = {}
        for contract in contracts:
            if not isinstance(contract, dict):
                continue
            contract_id = contract.get("contract_id")
            if contract_id in (None, ""):
                continue
            broker_contracts[str(contract_id)] = contract

        checked_contracts = len(broker_contracts)
        if checked_contracts == 0:
            return prepare_response(
                {
                    "checked_contracts": 0,
                    "existing_count": 0,
                    "missing_count": 0,
                    "imported_count": 0,
                    "runtime_registered_count": 0,
                    "imported_contract_ids": [],
                    "skipped_non_multiplier_ids": [],
                    "failed_contract_ids": [],
                    "message": "No open broker contracts found",
                }
            )

        existing_ids = UserTradesService.get_user_trade_contract_ids(user_id)
        missing_ids = [cid for cid in broker_contracts.keys() if cid not in existing_ids]

        for contract_id in missing_ids:
            contract_hint = broker_contracts.get(contract_id, {})
            detail_response = await trade_engine.send_request(
                {"proposal_open_contract": 1, "contract_id": contract_id}
            )
            if not isinstance(detail_response, dict) or "error" in detail_response:
                failed_contract_ids.append(contract_id)
                continue

            contract_detail = detail_response.get("proposal_open_contract")
            if not isinstance(contract_detail, dict):
                failed_contract_ids.append(contract_id)
                continue

            contract_type = str(
                contract_detail.get("contract_type")
                or contract_hint.get("contract_type")
                or ""
            ).upper()
            direction = _normalize_multiplier_direction(contract_type)
            if direction is None:
                skipped_non_multiplier_ids.append(contract_id)
                continue

            symbol = str(
                contract_detail.get("underlying")
                or contract_hint.get("underlying")
                or ""
            ).strip().upper()
            if not symbol:
                failed_contract_ids.append(contract_id)
                continue

            open_time = _to_datetime(
                contract_detail.get("date_start")
                or contract_hint.get("date_start")
            ) or datetime.now()
            stake = _to_float(
                contract_detail.get("buy_price")
                or contract_hint.get("buy_price")
            )
            entry_price = _to_float(
                contract_detail.get("entry_spot")
                or contract_hint.get("entry_spot")
                or contract_detail.get("buy_price")
            )
            multiplier = _to_float(
                contract_detail.get("multiplier")
                or contract_hint.get("multiplier")
            )

            trade_payload: Dict[str, Any] = {
                "contract_id": contract_id,
                "symbol": symbol,
                "direction": direction,
                "signal": direction,
                "stake": stake,
                "entry_price": entry_price,
                "entry_spot": entry_price,
                "open_time": open_time,
                "timestamp": open_time,
                "status": "open",
                "strategy_type": strategy_type,
                "multiplier": multiplier,
                "execution_reason": "Detected open broker contract via sync",
                "entry_source": "manual_imported",
                "manual_tracking": True,
            }

            saved = UserTradesService.track_active_trade(user_id, trade_payload)
            if not saved:
                failed_contract_ids.append(contract_id)
                continue

            saved_status = str(saved.get("status", "")).strip().lower()
            if saved_status != "open":
                continue

            imported_contract_ids.append(contract_id)

            try:
                if _register_runtime_tracking(bot, trade_payload):
                    runtime_registered_count += 1
            except Exception as runtime_error:
                logger.warning(
                    "Contract %s imported but runtime tracking registration failed: %s",
                    contract_id,
                    runtime_error,
                )

            if notification_bridge and hasattr(notification_bridge, "notify_trade_opened"):
                try:
                    notify_payload = dict(trade_payload)
                    notify_payload["user_id"] = user_id
                    await notification_bridge.notify_trade_opened(
                        notify_payload,
                        strategy_type=strategy_type,
                    )
                except Exception as notify_error:
                    logger.warning(
                        "Sync imported contract %s but Telegram notification failed: %s",
                        contract_id,
                        notify_error,
                    )

            try:
                await event_manager.broadcast(
                    {
                        "type": "new_trade",
                        "contract_id": contract_id,
                        "symbol": symbol,
                        "direction": direction,
                        "strategy_type": strategy_type,
                        "stake": stake,
                        "entry_price": entry_price,
                        "status": "open",
                        "multiplier": multiplier,
                        "entry_source": "manual_imported",
                        "timestamp": open_time.isoformat(),
                        "account_id": user_id,
                    }
                )
            except Exception as broadcast_error:
                logger.warning(
                    "Sync imported contract %s but WS broadcast failed: %s",
                    contract_id,
                    broadcast_error,
                )

        existing_count = checked_contracts - len(missing_ids)
        imported_count = len(imported_contract_ids)
        message = (
            f"Sync complete: imported {imported_count} contract(s), "
            f"skipped {len(skipped_non_multiplier_ids)} non-multiplier, "
            f"failed {len(failed_contract_ids)}."
        )

        return prepare_response(
            {
                "checked_contracts": checked_contracts,
                "existing_count": existing_count,
                "missing_count": len(missing_ids),
                "imported_count": imported_count,
                "runtime_registered_count": runtime_registered_count,
                "imported_contract_ids": imported_contract_ids,
                "skipped_non_multiplier_ids": skipped_non_multiplier_ids,
                "failed_contract_ids": failed_contract_ids,
                "message": message,
            }
        )
    finally:
        if owns_engine_connection and trade_engine:
            try:
                await trade_engine.disconnect()
            except Exception:
                pass

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

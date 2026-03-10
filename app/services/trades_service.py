import logging
from typing import Dict, List, Optional
from datetime import datetime
from app.core.supabase import supabase
import config

logger = logging.getLogger(__name__)

from app.core.cache import cache

class UserTradesService:
    """
    Service to handle persistence of user trades to Supabase.
    """

    @staticmethod
    def _invalidate_trade_cache(user_id: str) -> None:
        """Invalidate cached trade list/stat snapshots for a user."""
        cache.delete_pattern(f"trades:{user_id}:*")
        cache.delete(f"stats:{user_id}")
        cache.delete(f"trades:{user_id}:active")

    @staticmethod
    def _normalize_signal(value: Optional[str]) -> Optional[str]:
        """Normalize direction/signal aliases to UP or DOWN."""
        if value is None:
            return None
        raw = str(value).strip().upper()
        if raw in {"UP", "BUY", "CALL", "RISE"}:
            return "UP"
        if raw in {"DOWN", "SELL", "PUT", "FALL"}:
            return "DOWN"
        return raw if raw else None

    @staticmethod
    def _to_float(value: Optional[object]) -> Optional[float]:
        """Safely coerce a value to float when possible."""
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_bool(value: Optional[object]) -> Optional[bool]:
        """Safely coerce common bool-like values."""
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            if value == 1:
                return True
            if value == 0:
                return False
            return None
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off"}:
                return False
        return None

    @staticmethod
    def _to_datetime(value: Optional[object]) -> Optional[datetime]:
        """Safely coerce supported timestamp payloads into datetime."""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, (int, float)):
            try:
                return datetime.fromtimestamp(float(value))
            except (TypeError, ValueError, OSError):
                return None
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return None
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            if raw.isdigit():
                try:
                    number = float(raw)
                    if len(raw) >= 13:
                        number = number / 1000.0
                    return datetime.fromtimestamp(number)
                except (TypeError, ValueError, OSError):
                    return None
            try:
                return datetime.fromisoformat(raw)
            except ValueError:
                return None
        return None

    @staticmethod
    def _resolve_trade_timestamp(trade_data: Dict) -> Optional[str]:
        """Pick the best available persisted timestamp for a trade row."""
        candidates = (
            trade_data.get("timestamp"),
            trade_data.get("closed_at"),
            trade_data.get("sell_time"),
            trade_data.get("open_time"),
            trade_data.get("date_start"),
        )
        for candidate in candidates:
            parsed = UserTradesService._to_datetime(candidate)
            if parsed is not None:
                return parsed.isoformat()
        return None

    @staticmethod
    def _normalize_entry_source(value: Optional[object]) -> str:
        """Persist a stable non-null source marker for trade rows."""
        if value is None:
            return "system"
        raw = str(value).strip()
        return raw if raw else "system"

    @staticmethod
    def _resolve_multiplier(trade_data: Dict) -> Optional[float]:
        """
        Resolve multiplier from the payload first, then fall back to configured
        symbol metadata when the close path omitted it.
        """
        explicit_multiplier = UserTradesService._to_float(
            trade_data.get("multiplier") or trade_data.get("contract_multiplier")
        )
        if explicit_multiplier is not None and explicit_multiplier > 0:
            return explicit_multiplier

        symbol = str(trade_data.get("symbol") or "").strip()
        if not symbol:
            return None

        asset_config = getattr(config, "ASSET_CONFIG", {}).get(symbol, {})
        if not isinstance(asset_config, dict):
            return None

        configured_multiplier = UserTradesService._to_float(asset_config.get("multiplier"))
        if configured_multiplier is not None and configured_multiplier > 0:
            return configured_multiplier
        return None

    @staticmethod
    def _drop_optional_columns_for_compat(record: Dict, error: Exception) -> Dict:
        """
        Remove optional columns if database schema has not been migrated yet.
        """
        error_text = str(error).lower()
        is_missing_column = (
            ("column" in error_text and "does not exist" in error_text)
            or ("pgrst204" in error_text and "schema cache" in error_text)
            or ("could not find the 'entry_source' column" in error_text)
            or ("could not find the 'multiplier' column" in error_text)
            or ("could not find the 'trailing_enabled' column" in error_text)
            or ("could not find the 'stagnation_enabled' column" in error_text)
        )
        if not is_missing_column:
            return record

        compact = dict(record)
        compact.pop("entry_source", None)
        compact.pop("multiplier", None)
        compact.pop("trailing_enabled", None)
        compact.pop("stagnation_enabled", None)
        return compact

    @staticmethod
    def _normalize_trade_status(
        status: Optional[object],
        profit: Optional[object],
        exit_price: Optional[object],
    ) -> str:
        """
        Normalize status to stable values and prevent stale open rows.

        If P/L or exit price exists, trade is realized and should not remain open.
        """
        raw_status = str(status or "").strip().lower()
        profit_value = UserTradesService._to_float(profit)
        has_realized_data = profit_value is not None or exit_price not in (None, "")

        if raw_status in {"open", "active", "pending"} and has_realized_data:
            if profit_value is not None:
                if profit_value > 0:
                    return "win"
                if profit_value < 0:
                    return "loss"
            return "closed"

        if raw_status in {"won", "win", "profit", "take_profit", "tp"}:
            return "win"
        if raw_status in {"lost", "loss", "stop_loss", "sl"}:
            return "loss"
        if raw_status in {"sold", "closed", "settled", "complete", "completed"}:
            if profit_value is not None:
                if profit_value > 0:
                    return "win"
                if profit_value < 0:
                    return "loss"
            return "closed"

        if has_realized_data:
            if profit_value is not None:
                if profit_value > 0:
                    return "win"
                if profit_value < 0:
                    return "loss"
            return "closed"

        return raw_status or "open"

    @staticmethod
    def save_trade(user_id: str, trade_data: Dict) -> Optional[Dict]:
        """
        Save a completed trade to Supabase.
        """
        try:
            # Validate required NOT NULL fields from schema
            required_fields = {
                'contract_id': 'Contract ID',
                'symbol': 'Symbol',
            }
            for field, name in required_fields.items():
                if not trade_data.get(field):
                    logger.error(f"❌ Cannot save trade: Missing required field '{name}'")
                    logger.debug(f"Trade data keys: {list(trade_data.keys())}")
                    return None
            if not (trade_data.get("signal") or trade_data.get("direction")):
                logger.error("❌ Cannot save trade: Missing required field 'Signal (UP/DOWN)'")
                logger.debug(f"Trade data keys: {list(trade_data.keys())}")
                return None
            
            timestamp = UserTradesService._resolve_trade_timestamp(trade_data)

            normalized_status = UserTradesService._normalize_trade_status(
                trade_data.get("status"),
                trade_data.get("profit"),
                trade_data.get("exit_price"),
            )
            entry_source = UserTradesService._normalize_entry_source(
                trade_data.get("entry_source")
            )
            multiplier = UserTradesService._resolve_multiplier(trade_data)
            trailing_enabled = UserTradesService._to_bool(trade_data.get("trailing_enabled"))
            stagnation_enabled = UserTradesService._to_bool(trade_data.get("stagnation_enabled"))
            
            # Prepare record
            record = {
                "user_id": user_id,
                "contract_id": str(trade_data.get("contract_id")),
                "symbol": trade_data.get("symbol"),
                "signal": UserTradesService._normalize_signal(
                    trade_data.get("signal") or trade_data.get("direction")
                ),
                "stake": trade_data.get("stake"),
                "entry_price": trade_data.get("entry_price") or trade_data.get("entry_spot"),  # Fallback
                "exit_price": trade_data.get("exit_price"),
                "profit": trade_data.get("profit"),
                "status": normalized_status,
                "timestamp": timestamp,
                "duration": trade_data.get("duration"),
                "strategy_type": trade_data.get("strategy_type", "Conservative"),
                "entry_source": entry_source,
            }
            if multiplier is not None:
                record["multiplier"] = multiplier
            if trailing_enabled is not None:
                record["trailing_enabled"] = trailing_enabled
            if stagnation_enabled is not None:
                record["stagnation_enabled"] = stagnation_enabled

            # Insert final trade row. If an active/open row already exists for
            # this contract_id, update it in place.
            try:
                response = supabase.table("trades").insert(record).execute()
            except Exception as insert_error:
                compact_record = UserTradesService._drop_optional_columns_for_compat(
                    record,
                    insert_error,
                )
                if compact_record != record:
                    record = compact_record
                    try:
                        response = supabase.table("trades").insert(record).execute()
                        insert_error = None
                    except Exception as insert_retry_error:
                        insert_error = insert_retry_error

                if insert_error is not None:
                    error_text = str(insert_error).lower()
                    duplicate_contract = (
                        "duplicate key" in error_text
                        or "trades_contract_id_key" in error_text
                        or "conflict" in error_text
                    )
                    if not duplicate_contract:
                        raise
                    response = (
                        supabase.table("trades")
                        .update(record)
                        .eq("user_id", user_id)
                        .eq("contract_id", record["contract_id"])
                        .execute()
                    )
            
            if response.data:
                logger.info(f"✅ Trade persisted to DB: {record['contract_id']}")
                UserTradesService._invalidate_trade_cache(user_id)
                
                return response.data[0]
            else:
                logger.error("Failed to persist trade: No data returned")
                return None
                
        except Exception as e:
            logger.error(f"❌ Error saving trade to DB: {e}")
            return None

    @staticmethod
    def track_active_trade(user_id: str, trade_data: Dict) -> Optional[Dict]:
        """
        Persist/refresh an open trade row so active trades survive bot stops.
        """
        try:
            contract_id = trade_data.get("contract_id")
            symbol = trade_data.get("symbol")
            signal = UserTradesService._normalize_signal(
                trade_data.get("signal") or trade_data.get("direction")
            )
            if not contract_id or not symbol or not signal:
                return None

            timestamp = (
                trade_data.get("timestamp")
                or trade_data.get("open_time")
                or datetime.now().isoformat()
            )
            if isinstance(timestamp, datetime):
                timestamp = timestamp.isoformat()
            entry_source = UserTradesService._normalize_entry_source(
                trade_data.get("entry_source")
            )
            multiplier = UserTradesService._resolve_multiplier(trade_data)
            trailing_enabled = UserTradesService._to_bool(trade_data.get("trailing_enabled"))
            stagnation_enabled = UserTradesService._to_bool(trade_data.get("stagnation_enabled"))

            record = {
                "user_id": user_id,
                "contract_id": str(contract_id),
                "symbol": symbol,
                "signal": signal,
                "stake": trade_data.get("stake"),
                "entry_price": trade_data.get("entry_price") or trade_data.get("entry_spot"),
                "exit_price": None,
                "profit": None,
                "status": "open",
                "timestamp": timestamp,
                "duration": None,
                "strategy_type": trade_data.get("strategy_type", "Conservative"),
                "entry_source": entry_source,
            }
            if multiplier is not None:
                record["multiplier"] = multiplier
            if trailing_enabled is not None:
                record["trailing_enabled"] = trailing_enabled
            if stagnation_enabled is not None:
                record["stagnation_enabled"] = stagnation_enabled

            existing_response = (
                supabase.table("trades")
                .select("status,profit,exit_price,contract_id")
                .eq("user_id", user_id)
                .eq("contract_id", str(contract_id))
                .limit(1)
                .execute()
            )
            existing_rows = list(existing_response.data or [])
            if existing_rows:
                existing_row = existing_rows[0]
                existing_status = UserTradesService._normalize_trade_status(
                    existing_row.get("status"),
                    existing_row.get("profit"),
                    existing_row.get("exit_price"),
                )
                if existing_status != "open":
                    return {
                        **existing_row,
                        "status": existing_status,
                        "contract_id": str(existing_row.get("contract_id", contract_id)),
                    }

            try:
                response = (
                    supabase.table("trades")
                    .upsert(record, on_conflict="contract_id")
                    .execute()
                )
            except Exception as upsert_error:
                compact_record = UserTradesService._drop_optional_columns_for_compat(
                    record,
                    upsert_error,
                )
                if compact_record == record:
                    raise
                record = compact_record
                response = (
                    supabase.table("trades")
                    .upsert(record, on_conflict="contract_id")
                    .execute()
                )
            if response.data:
                UserTradesService._invalidate_trade_cache(user_id)
                return response.data[0]
            return None
        except Exception as e:
            logger.error(f"❌ Error tracking active trade: {e}")
            return None

    @staticmethod
    def get_user_trade_contract_ids(user_id: str) -> set[str]:
        """Fetch all persisted contract IDs for broker-vs-local diff checks."""
        try:
            response = (
                supabase.table("trades")
                .select("contract_id")
                .eq("user_id", user_id)
                .execute()
            )
            rows = list(response.data or [])
            return {
                str(row.get("contract_id"))
                for row in rows
                if row.get("contract_id") not in (None, "")
            }
        except Exception as e:
            logger.error(f"❌ Error fetching user contract IDs: {e}")
            return set()

    @staticmethod
    def update_active_trade_exit_controls(
        user_id: str,
        contract_id: str,
        trailing_enabled: Optional[bool] = None,
        stagnation_enabled: Optional[bool] = None,
    ) -> Optional[Dict]:
        """Persist active-trade exit control toggles for refresh/restart continuity."""
        payload: Dict[str, object] = {}
        trailing = UserTradesService._to_bool(trailing_enabled)
        stagnation = UserTradesService._to_bool(stagnation_enabled)
        if trailing is not None:
            payload["trailing_enabled"] = trailing
        if stagnation is not None:
            payload["stagnation_enabled"] = stagnation

        if not user_id or not contract_id or not payload:
            return None

        try:
            try:
                response = (
                    supabase.table("trades")
                    .update(payload)
                    .eq("user_id", user_id)
                    .eq("contract_id", str(contract_id))
                    .eq("status", "open")
                    .execute()
                )
            except Exception as update_error:
                compact_payload = UserTradesService._drop_optional_columns_for_compat(
                    payload,
                    update_error,
                )
                if compact_payload == payload or not compact_payload:
                    raise
                response = (
                    supabase.table("trades")
                    .update(compact_payload)
                    .eq("user_id", user_id)
                    .eq("contract_id", str(contract_id))
                    .eq("status", "open")
                    .execute()
                )

            if response.data:
                UserTradesService._invalidate_trade_cache(user_id)
                return response.data[0]
            return None
        except Exception as e:
            logger.warning(
                "Could not persist exit controls for user %s contract %s: %s",
                user_id,
                contract_id,
                e,
            )
            return None

    @staticmethod
    def get_user_active_trades(user_id: str, limit: int = 20) -> List[Dict]:
        """
        Fetch currently open trades from persistent storage.
        """
        try:
            cache_key = f"trades:{user_id}:active"
            cached_data = cache.get(cache_key)
            if cached_data:
                return cached_data

            response = (
                supabase.table("trades")
                .select("*")
                .eq("user_id", user_id)
                .eq("status", "open")
                .order("timestamp", desc=True)
                .limit(limit)
                .execute()
            )
            data = list(response.data or [])
            active_rows: List[Dict] = []

            for row in data:
                normalized_status = UserTradesService._normalize_trade_status(
                    row.get("status"),
                    row.get("profit"),
                    row.get("exit_price"),
                )

                if normalized_status == "open":
                    active_rows.append(row)
                    continue

                try:
                    (
                        supabase.table("trades")
                        .update({"status": normalized_status})
                        .eq("user_id", user_id)
                        .eq("contract_id", str(row.get("contract_id")))
                        .execute()
                    )
                except Exception as repair_error:
                    logger.warning(
                        "Failed to repair stale active trade %s for user %s: %s",
                        row.get("contract_id"),
                        user_id,
                        repair_error,
                    )

            cache.set(cache_key, active_rows, ttl=20)
            return active_rows
        except Exception as e:
            logger.error(f"❌ Error fetching active trades from DB: {e}")
            return []

    @staticmethod
    def get_user_trades(user_id: str, limit: int = 50) -> List[Dict]:
        """
        Fetch trade history for a user from Supabase.
        """
        try:
            # Check Cache
            cache_key = f"trades:{user_id}:limit:{limit}"
            cached_data = cache.get(cache_key)
            if cached_data:
                return cached_data

            response = supabase.table("trades")\
                .select("*")\
                .eq("user_id", user_id)\
                .order("timestamp", desc=True)\
                .limit(limit)\
                .execute()
            
            data = response.data if response.data else []
            
            # Set Cache (TTL 60s for live history)
            cache.set(cache_key, data, ttl=60)
            
            return data
            
        except Exception as e:
            logger.error(f"❌ Error fetching trade history: {e}")
            return []

    @staticmethod
    def get_user_stats(user_id: str) -> Dict:
        """
        Calculate lifetime statistics for a user from the database.
        """
        try:
            # Check Cache
            cache_key = f"stats:{user_id}"
            cached_stats = cache.get(cache_key)
            if cached_stats:
                return cached_stats

            # Fetch all user trades (or a sufficiently large limit for stats)
            # Efficient way would be specific aggregation query, but Supabase-py might be limited.
            # For now, fetch ALL (assuming < 10k trades) or last 1000.
            response = supabase.table("trades")\
                .select("*")\
                .eq("user_id", user_id)\
                .execute()
            
            trades = response.data if response.data else []
            
            total_trades = len(trades)
            if total_trades == 0:
                return {
                    "total_trades": 0,
                    "winning_trades": 0,
                    "losing_trades": 0,
                    "win_rate": 0.0,
                    "total_pnl": 0.0,
                    "daily_pnl": 0.0, # Not easily calculable without date filter, set 0
                    "avg_win": 0.0,
                    "avg_loss": 0.0,
                    "largest_win": 0.0,
                    "largest_loss": 0.0,
                    "profit_factor": 0.0
                }
            
            # Calculate stats
            # Handle possible None values in profit
            wins = []
            losses = []
            
            for t in trades:
                profit = t.get('profit')
                if profit is None:
                    continue
                    
                if profit > 0:
                    wins.append(profit)
                elif profit < 0:
                    losses.append(abs(profit))
            
            winning_trades = len(wins)
            losing_trades = len(losses)
            # Avoid division by zero
            win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
            
            total_pnl = sum([t.get('profit') or 0 for t in trades])
            
            avg_win = sum(wins) / winning_trades if winning_trades else 0
            avg_loss = sum(losses) / losing_trades if losing_trades else 0
            
            largest_win = max(wins) if wins else 0
            largest_loss = max(losses) if losses else 0
            
            gross_profit = sum(wins)
            gross_loss = sum(losses)
            
            # profit_factor = gross_profit / gross_loss
            # If gross_loss is 0:
            #   - if gross_profit > 0 -> technically infinite, but we return 0.0 or a high number to avoid JSON errors
            #   - if gross_profit == 0 -> 0.0
            if gross_loss > 0:
                profit_factor = gross_profit / gross_loss
            else:
                profit_factor = 0.0  # Return 0.0 instead of infinite for JSON safety

            result = {
                "total_trades": total_trades,
                "winning_trades": winning_trades,
                "losing_trades": losing_trades,
                "win_rate": win_rate,
                "total_pnl": total_pnl,
                "daily_pnl": 0.0, # Placeholder or calc if needed
                "avg_win": avg_win,
                "avg_loss": avg_loss,
                "largest_win": largest_win,
                "largest_loss": largest_loss,
                "profit_factor": profit_factor
            }
            
            # Cache Stats (TTL 5 mins)
            cache.set(cache_key, result, ttl=300)
            
            return result
        
        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            logger.error(f"❌ Error calculating user stats: {e}\n{error_details}")
            # Return empty structure on error to prevent API 500
            return {
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

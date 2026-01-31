import logging
from typing import Dict, List, Optional
from datetime import datetime
from app.core.supabase import supabase

logger = logging.getLogger(__name__)

from app.core.cache import cache

class UserTradesService:
    """
    Service to handle persistence of user trades to Supabase.
    """

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
                'signal': 'Signal (UP/DOWN)'
            }
            
            for field, name in required_fields.items():
                if not trade_data.get(field):
                    logger.error(f"❌ Cannot save trade: Missing required field '{name}'")
                    logger.debug(f"Trade data keys: {list(trade_data.keys())}")
                    return None
            
            # Get timestamp and convert to string if it's a datetime object
            timestamp = trade_data.get("timestamp") or trade_data.get("closed_at")
            if isinstance(timestamp, datetime):
                timestamp = timestamp.isoformat()
            
            # Prepare record
            record = {
                "user_id": user_id,
                "contract_id": str(trade_data.get("contract_id")),
                "symbol": trade_data.get("symbol"),
                "signal": trade_data.get("signal"),
                "stake": trade_data.get("stake"),
                "entry_price": trade_data.get("entry_price") or trade_data.get("entry_spot"),  # Fallback
                "exit_price": trade_data.get("exit_price"),
                "profit": trade_data.get("profit"),
                "status": trade_data.get("status"),
                "timestamp": timestamp,
                "duration": trade_data.get("duration")
            }


            # Insert into Supabase
            response = supabase.table("trades").insert(record).execute()
            
            if response.data:
                logger.info(f"✅ Trade persisted to DB: {record['contract_id']}")
                
                # Invalidate Cache
                cache.delete_pattern(f"trades:{user_id}:*")
                cache.delete(f"stats:{user_id}")
                
                return response.data[0]
            else:
                logger.error("Failed to persist trade: No data returned")
                return None
                
        except Exception as e:
            logger.error(f"❌ Error saving trade to DB: {e}")
            return None

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

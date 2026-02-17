from typing import Dict, Optional, List
from app.bot.runner import BotRunner, BotStatus
import logging
import asyncio

logger = logging.getLogger(__name__)

class BotManager:
    """
    Manages multiple independent BotRunner instances for different users.
    Each user gets their own isolated bot instance with their own API token and state.
    """
    
    def __init__(self, max_concurrent_bots: int = 50):
        # Map user_id -> BotRunner
        self._bots: Dict[str, BotRunner] = {}
        self._lock = asyncio.Lock()
        self._user_locks: Dict[str, asyncio.Lock] = {}  # NEW: Per-user locks
        self.max_concurrent_bots = max_concurrent_bots
        
    def _get_user_lock(self, user_id: str) -> asyncio.Lock:
        """Get or create a lock for a specific user (for concurrent start protection)"""
        if user_id not in self._user_locks:
            self._user_locks[user_id] = asyncio.Lock()
        return self._user_locks[user_id]
    
    def get_bot(self, user_id: str, strategy = None, risk_manager = None) -> BotRunner:
        """
        Get or create a bot instance for the user.
        If strategy/risk_manager are provided, updates existing bot or creates new one.
        """
        if user_id not in self._bots:
            logger.info(f"Initializing new bot instance for user {user_id}")
            self._bots[user_id] = BotRunner(
                account_id=user_id,
                strategy=strategy,
                risk_manager=risk_manager
            )
        else:
            # Update strategy and risk_manager if provided (for strategy switching)
            if strategy is not None:
                logger.info(f"Updating strategy for existing bot instance: {user_id}")
                self._bots[user_id].strategy = strategy
            if risk_manager is not None:
                logger.info(f"Updating risk manager for existing bot instance: {user_id}")
                self._bots[user_id].risk_manager = risk_manager
            
        return self._bots[user_id]

    async def start_bot(self, user_id: str, api_token: Optional[str] = None, stake: Optional[float] = None, strategy_name: Optional[str] = None) -> dict:
        """
        Start a bot for a specific user with strategy selection support.
        If api_token is provided, it updates the bot's token.
        If strategy_name is provided, it overrides the user's profile strategy.
        """
        # Per-user lock to prevent concurrent start requests
        user_lock = self._get_user_lock(user_id)
        async with user_lock:
            # Check if already running with a different strategy
            if user_id in self._bots and self._bots[user_id].is_running:
                current_strategy = self._bots[user_id].strategy.get_strategy_name()
                requested_strategy = strategy_name or await self._get_user_strategy(user_id)
                
                if current_strategy != requested_strategy:
                    logger.info(f"Strategy switch detected for {user_id}: {current_strategy} -> {requested_strategy}")
                    logger.info(f"Stopping old bot to restart with new strategy...")
                    await self._bots[user_id].stop_bot()
                    del self._bots[user_id]
                else:
                    return {
                        "success": False,
                        "message": "Bot is already running",
                        "status": self._bots[user_id].status.value
                    }
            
            # Global concurrent bot limit check
            async with self._lock:
                running_count = sum(1 for bot in self._bots.values() if bot.is_running)
                
                if user_id not in self._bots and running_count >= self.max_concurrent_bots:
                    return {
                        "success": False,
                        "message": f"Maximum concurrent bots reached ({self.max_concurrent_bots}). Please try again later.",
                        "status": "error"
                    }
            
            # Load strategy from user profile or use provided
            active_strategy = strategy_name or await self._get_user_strategy(user_id)
            
            # Load strategy classes from registry
            from strategy_registry import get_strategy
            strategy_class, risk_manager_class = get_strategy(active_strategy)
            
            # Load user-specific overrides
            overrides = await self._load_strategy_overrides(user_id, active_strategy)
            
            # Instantiate strategy and risk manager
            strategy_instance = strategy_class()
            risk_manager_instance = risk_manager_class(user_id=user_id, overrides=overrides)
            
            logger.info(f"✅ Loaded strategy for {user_id}: {active_strategy}")
            logger.info(f"📋 Strategy class: {strategy_class.__name__}, Risk Manager: {risk_manager_class.__name__}")
            
            # Create or get bot with injected instances
            bot = self.get_bot(user_id, strategy=strategy_instance, risk_manager=risk_manager_instance)
            return await bot.start_bot(api_token=api_token, stake=stake, strategy_name=active_strategy)

    async def stop_bot(self, user_id: str) -> dict:
        """
        Stop a specific user's bot.
        """
        if user_id in self._bots:
            return await self._bots[user_id].stop_bot()
        
        return {
            "success": False,
            "message": "Bot is not running",
            "status": "stopped"
        }
    
    async def restart_bot(self, user_id: str) -> dict:
        """
        Restart a specific user's bot.
        """
        if user_id in self._bots:
            return await self._bots[user_id].restart_bot()
            
        # If not in memory, try to start new one (requires ensuring token)
        # For restart, we assume it was running or exists.
        return {
            "success": False,
            "message": "Bot not found/not running",
            "status": "stopped"
        }

    def get_status(self, user_id: str) -> dict:
        """
        Get status for a specific user's bot.
        """
        if user_id in self._bots:
            return self._bots[user_id].get_status()
            
        return {
            "status": "stopped",
            "is_running": False,
            "uptime_seconds": None,
            "message": "Bot not initialized"
        }
    
    def get_all_running_bots(self) -> List[str]:
        """Get list of all active bot user IDs"""
        return [user_id for user_id, bot in self._bots.items() if bot.is_running]
    
    def get_stats(self) -> dict:
        """Get overall bot manager statistics"""
        total_bots = len(self._bots)
        running_bots = sum(1 for bot in self._bots.values() if bot.is_running)
        stopped_bots = sum(1 for bot in self._bots.values() if bot.status == BotStatus.STOPPED)
        error_bots = sum(1 for bot in self._bots.values() if bot.status == BotStatus.ERROR)
        
        return {
            "total_instances": total_bots,
            "running": running_bots,
            "stopped": stopped_bots,
            "error": error_bots,
            "max_concurrent": self.max_concurrent_bots,
            "capacity_used_pct": (running_bots / self.max_concurrent_bots * 100) if self.max_concurrent_bots > 0 else 0
        }
    
    async def cleanup_inactive_bots(self):
        """Remove bot instances that are stopped or errored (cleanup memory)"""
        async with self._lock:
            to_remove = []
            for user_id, bot in self._bots.items():
                if not bot.is_running and bot.status in [BotStatus.STOPPED, BotStatus.ERROR]:
                    to_remove.append(user_id)
            
            for user_id in to_remove:
                logger.info(f"Cleaning up inactive bot instance for user {user_id}")
                del self._bots[user_id]
            
            if to_remove:
                logger.info(f"Cleaned up {len(to_remove)} inactive bot instances")
                # Clean up user locks too
                for user_id in to_remove:
                    if user_id in self._user_locks:
                        del self._user_locks[user_id]
    
    async def _get_user_strategy(self, user_id: str) -> str:
        """
        Get the user's active strategy from their profile.
        
        Args:
            user_id: User identifier
        
        Returns:
            Strategy name (defaults to 'Conservative' if not set)
        """
        try:
            from app.core.supabase import supabase
            
            result = supabase.table('profiles') \
                .select('active_strategy') \
                .eq('id', user_id) \
                .single() \
                .execute()
            
            if result.data:
                return result.data.get('active_strategy', 'Conservative')
            
            return 'Conservative'
        
        except Exception as e:
            logger.warning(f"Failed to load user strategy for {user_id}: {e}")
            return 'Conservative'
    
    async def _load_strategy_overrides(self, user_id: str, strategy_name: str) -> dict:
        """
        Load user-specific strategy parameter overrides from database.
        
        Args:
            user_id: User identifier
            strategy_name: Strategy name
        
        Returns:
            Dict of overrides (empty dict if none found)
        """
        try:
            from app.core.supabase import supabase
            
            result = supabase.table('strategy_configs') \
                .select('*') \
                .eq('user_id', user_id) \
                .eq('strategy_type', strategy_name) \
                .execute()
            
            if result.data:
                return result.data[0]
            
            return {}
        
        except Exception as e:
            logger.warning(f"Failed to load strategy overrides for {user_id}: {e}")
            return {}
        
    async def stop_all(self):
        """
        Stop all running bots (e.g. on server shutdown)
        """
        logger.info(f"Stopping all {len(self._bots)} active bots...")
        tasks = []
        for user_id, bot in self._bots.items():
            if bot.is_running:
                tasks.append(bot.stop_bot())
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        
        logger.info("✅ All bots stopped")

# Global instance
bot_manager = BotManager(max_concurrent_bots=50)


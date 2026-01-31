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
        self.max_concurrent_bots = max_concurrent_bots
        
    def get_bot(self, user_id: str) -> BotRunner:
        """
        Get or create a bot instance for the user.
        """
        if user_id not in self._bots:
            logger.info(f"Initializing new bot instance for user {user_id}")
            self._bots[user_id] = BotRunner(account_id=user_id)
            
        return self._bots[user_id]

    async def start_bot(self, user_id: str, api_token: Optional[str] = None, stake: Optional[float] = None, strategy_name: Optional[str] = None) -> dict:
        """
        Start a bot for a specific user.
        If api_token is provided, it updates the bot's token.
        """
        async with self._lock:
            # Check concurrent bot limit
            running_count = sum(1 for bot in self._bots.values() if bot.is_running)
            
            # Allow if bot already exists for this user or under limit
            if user_id not in self._bots and running_count >= self.max_concurrent_bots:
                return {
                    "success": False,
                    "message": f"Maximum concurrent bots reached ({self.max_concurrent_bots}). Please try again later.",
                    "status": "error"
                }
        
        bot = self.get_bot(user_id)
        return await bot.start_bot(api_token=api_token, stake=stake, strategy_name=strategy_name)

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


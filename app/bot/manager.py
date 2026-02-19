from typing import Dict, Optional, List
from app.bot.runner import BotRunner, BotStatus
import logging
import asyncio
from datetime import datetime

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
        # Rise/Fall tasks: user_id -> asyncio.Task
        self._rf_tasks: Dict[str, asyncio.Task] = {}
        # Rise/Fall metadata for status reporting
        self._rf_start_times: Dict[str, datetime] = {}
        self._rf_stakes: Dict[str, float] = {}
        
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
                    
                    # ─────────────────────────────────────────────────────────────────────
                    # FIX 2: Clean up RF tasks during strategy switch
                    # Root cause: During strategy switch, _bots is cleaned up but _rf_tasks
                    # is never touched, orphaning the old RF task entirely. Next start request
                    # then either sees stale entry or no entry, and launches second rf_run()
                    # while first is still alive and trading.
                    # ─────────────────────────────────────────────────────────────────────
                    if user_id in self._rf_tasks and not self._rf_tasks[user_id].done():
                        logger.warning(
                            f"[BotManager] Cancelling orphaned RF task during strategy switch for {user_id}"
                        )
                        from risefallbot import rf_bot
                        rf_bot.stop()
                        self._rf_tasks[user_id].cancel()
                        try:
                            await self._rf_tasks[user_id]
                        except asyncio.CancelledError:
                            pass
                    self._rf_tasks.pop(user_id, None)
                    self._rf_start_times.pop(user_id, None)
                    self._rf_stakes.pop(user_id, None)
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
            
            # --- Rise/Fall: independent task (not BotRunner) ---
            if active_strategy == "RiseFall":
                return await self._start_risefall_bot(user_id, api_token, stake)
            
            # --- Multiplier strategies: BotRunner ---
            # Load strategy classes from registry
            from strategy_registry import get_strategy
            strategy_class, risk_manager_class = get_strategy(active_strategy)
            
            # Instantiate strategy and risk manager
            strategy_instance = strategy_class()
            risk_manager_instance = risk_manager_class(user_id=user_id)
            
            logger.info(f"✅ Loaded strategy for {user_id}: {active_strategy}")
            logger.info(f"📋 Strategy class: {strategy_class.__name__}, Risk Manager: {risk_manager_class.__name__}")
            
            # Create or get bot with injected instances
            bot = self.get_bot(user_id, strategy=strategy_instance, risk_manager=risk_manager_instance)
            return await bot.start_bot(api_token=api_token, stake=stake, strategy_name=active_strategy)

    async def stop_bot(self, user_id: str) -> dict:
        """
        Stop a specific user's bot.
        """
        # Rise/Fall task?
        if user_id in self._rf_tasks:
            return await self._stop_risefall_bot(user_id)
        
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
        # Rise/Fall task?
        if user_id in self._rf_tasks:
            task = self._rf_tasks[user_id]
            is_running = not task.done()
            
            # Compute uptime from tracked start time
            uptime = None
            start_time = self._rf_start_times.get(user_id)
            if start_time and is_running:
                uptime = int((datetime.now() - start_time).total_seconds())
            
            stake = self._rf_stakes.get(user_id, 0)
            
            return {
                "status": "running" if is_running else "stopped",
                "is_running": is_running,
                "uptime_seconds": uptime,
                "active_strategy": "RiseFall",
                "stake_amount": stake,
                "message": "Rise/Fall bot running" if is_running else "Rise/Fall bot stopped",
                "config": {"strategy": "RiseFall", "stake": stake}
            }
        
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
        
        # ─────────────────────────────────────────────────────────────────────
        # FIX 3: Clean up completed RF tasks
        # Root cause: cleanup_inactive_bots() never removes done RF tasks,
        # causing _rf_tasks to grow indefinitely. Stale entries interfere with
        # the guard check in _start_risefall_bot().
        # ─────────────────────────────────────────────────────────────────────
        rf_to_remove = [uid for uid, task in self._rf_tasks.items() if task.done()]
        for user_id in rf_to_remove:
            del self._rf_tasks[user_id]
            self._rf_start_times.pop(user_id, None)
            self._rf_stakes.pop(user_id, None)
            logger.info(f"[BotManager] Cleaned up completed RF task for {user_id}")
    
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
    
    
    # ------------------------------------------------------------------ #
    #  Rise/Fall helpers                                                   #
    # ------------------------------------------------------------------ #

    async def _start_risefall_bot(self, user_id: str, api_token: str, stake: float) -> dict:
        """Launch rf_bot.run() as a managed asyncio task for this user."""
        # ─────────────────────────────────────────────────────────────────────
        # FIX 1: Hard-cancel any existing RF task before launching a new one
        # Root cause: rf_bot.stop() only sets _running=False; loop exits AFTER
        # asyncio.sleep(RF_SCAN_INTERVAL)—up to 10 seconds. If a new start request
        # arrives within that window, the old task is still not done().
        # ─────────────────────────────────────────────────────────────────────
        if user_id in self._rf_tasks:
            existing_task = self._rf_tasks[user_id]
            if not existing_task.done():
                logger.warning(
                    f"[BotManager] ⚠️ RF task already exists for {user_id} — "
                    f"cancelling before starting new instance"
                )
                from risefallbot import rf_bot
                rf_bot.stop()
                existing_task.cancel()
                try:
                    await existing_task
                except asyncio.CancelledError:
                    pass
                logger.info(f"[BotManager] ✅ Old RF task fully cancelled for {user_id}")
            del self._rf_tasks[user_id]

        # Safe to start fresh instance
        from risefallbot.rf_bot import run as rf_run
        from app.bot.events import event_manager

        task = asyncio.create_task(rf_run(stake=stake, api_token=api_token, user_id=user_id))
        self._rf_tasks[user_id] = task
        self._rf_start_times[user_id] = datetime.now()
        self._rf_stakes[user_id] = stake or 0

        logger.info(f"✅ Rise/Fall bot started for user {user_id} | stake=${stake}")

        # Broadcast start event
        await event_manager.broadcast({
            "type": "bot_status",
            "status": "running",
            "active_strategy": "RiseFall",
            "stake_amount": stake,
            "message": f"Rise/Fall bot started (stake=${stake})",
            "account_id": user_id,
        })

        return {
            "success": True,
            "message": f"Rise/Fall bot started (stake=${stake})",
            "status": "running"
        }

    async def _stop_risefall_bot(self, user_id: str) -> dict:
        """Stop the Rise/Fall asyncio task for this user."""
        task = self._rf_tasks.get(user_id)
        if not task or task.done():
            self._rf_tasks.pop(user_id, None)
            return {
                "success": False,
                "message": "Rise/Fall bot is not running",
                "status": "stopped"
            }

        from risefallbot import rf_bot
        from app.bot.events import event_manager

        rf_bot.stop()       # signal the while-loop to exit
        task.cancel()        # cancel the asyncio task
        try:
            await task
        except asyncio.CancelledError:
            pass

        del self._rf_tasks[user_id]
        self._rf_start_times.pop(user_id, None)
        self._rf_stakes.pop(user_id, None)
        logger.info(f"🛑 Rise/Fall bot stopped for user {user_id}")

        # Broadcast stop event
        await event_manager.broadcast({
            "type": "bot_status",
            "status": "stopped",
            "message": "Rise/Fall bot stopped",
            "account_id": user_id,
        })

        return {
            "success": True,
            "message": "Rise/Fall bot stopped",
            "status": "stopped"
        }
        
    async def stop_all(self):
        """
        Stop all running bots (e.g. on server shutdown)
        """
        logger.info(f"Stopping all {len(self._bots)} active bots...")
        tasks = []
        for user_id, bot in self._bots.items():
            if bot.is_running:
                tasks.append(bot.stop_bot())
        
        # Also stop Rise/Fall tasks
        for user_id, task in list(self._rf_tasks.items()):
            if not task.done():
                from risefallbot import rf_bot
                rf_bot.stop()
                task.cancel()
            del self._rf_tasks[user_id]
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        
        logger.info("✅ All bots stopped")

# Global instance
bot_manager = BotManager(max_concurrent_bots=50)


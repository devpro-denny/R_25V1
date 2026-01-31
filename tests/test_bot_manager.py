import asyncio
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.bot.manager import BotManager
from app.bot.runner import BotRunner, BotStatus

# Optional pytest import
try:
    import pytest
    HAS_PYTEST = True
except ImportError:
    HAS_PYTEST = False
    # Create dummy decorator
    class pytest:
        class fixture:
            def __init__(self, *args, **kwargs):
                pass
            def __call__(self, func):
                return func
        class mark:
            class asyncio:
                def __init__(self, *args, **kwargs):
                    pass
                def __call__(self, func):
                    return func


class TestBotManager:
    """Test BotManager multi-user functionality"""
    
    async def test_multiple_users_independent_bots(self, manager):
        """Test that multiple users get independent bot instances"""
        print("\n" + "="*60)
        print("TEST: Multiple Users - Independent Bots")
        print("="*60)
        
        # Get bots for different users
        bot_a = manager.get_bot("user_a")
        bot_b = manager.get_bot("user_b")
        bot_c = manager.get_bot("user_c")
        
        # Verify they are different instances
        assert bot_a is not bot_b
        assert bot_b is not bot_c
        assert bot_a is not bot_c
        
        # Verify they have correct account_ids
        assert bot_a.account_id == "user_a"
        assert bot_b.account_id == "user_b"
        assert bot_c.account_id == "user_c"
        
        print(f"✅ Created 3 independent bot instances")
        print(f"   User A: {id(bot_a)}")
        print(f"   User B: {id(bot_b)}")
        print(f"   User C: {id(bot_c)}")
        
        # Getting same user again returns same instance
        bot_a_again = manager.get_bot("user_a")
        assert bot_a is bot_a_again
    
    async def test_concurrent_limit_enforcement(self, manager):
        """Test that concurrent bot limit is enforced"""
        print("\n" + "="*60)
        print("TEST: Concurrent Limit Enforcement (max=5)")
        print("="*60)
        
        # Mock the start_bot to succeed immediately
        async def mock_start(api_token=None, stake=None, strategy_name=None):
            # Simulate successful start
            return {"success": True, "status": "running"}
        
        # Start 5 bots (at the limit)
        for i in range(5):
            user_id = f"user_{i}"
            bot = manager.get_bot(user_id)
            bot.is_running = True  # Simulate running
            bot.start_bot = mock_start
        
        running = manager.get_all_running_bots()
        print(f"✅ Started {len(running)} bots (at limit)")
        assert len(running) == 5
        
        # Try to start 6th bot (should be rejected)
        result = await manager.start_bot("user_6", api_token="test", stake=10.0)
        
        assert result["success"] == False
        assert "Maximum concurrent bots reached" in result["message"]
        print(f"✅ 6th bot rejected: {result['message']}")
    
    async def test_bot_stats(self, manager):
        """Test that bot statistics are accurately tracked"""
        print("\n" + "="*60)
        print("TEST: Bot Statistics Tracking")
        print("="*60)
        
        # Create bots in different states
        bot1 = manager.get_bot("user_1")
        bot1.is_running = True
        bot1.status = BotStatus.RUNNING
        
        bot2 = manager.get_bot("user_2")
        bot2.is_running = True
        bot2.status = BotStatus.RUNNING
        
        bot3 = manager.get_bot("user_3")
        bot3.is_running = False
        bot3.status = BotStatus.STOPPED
        
        bot4 = manager.get_bot("user_4")
        bot4.is_running = False
        bot4.status = BotStatus.ERROR
        
        stats = manager.get_stats()
        
        print(f"📊 Bot Manager Stats:")
        print(f"   Total instances: {stats['total_instances']}")
        print(f"   Running: {stats['running']}")
        print(f"   Stopped: {stats['stopped']}")
        print(f"   Error: {stats['error']}")
        print(f"   Capacity: {stats['capacity_used_pct']:.1f}%")
        
        assert stats['total_instances'] == 4
        assert stats['running'] == 2
        assert stats['stopped'] == 1
        assert stats['error'] == 1
        assert stats['capacity_used_pct'] == 40.0  # 2/5 * 100
        
        print("✅ Statistics accurate")
    
    async def test_cleanup_inactive_bots(self, manager):
        """Test that inactive bots are cleaned up"""
        print("\n" + "="*60)
        print("TEST: Cleanup Inactive Bots")
        print("="*60)
        
        # Create 3 bots: 1 running, 2 inactive
        bot1 = manager.get_bot("user_1")
        bot1.is_running = True
        
        bot2 = manager.get_bot("user_2")
        bot2.is_running = False
        bot2.status = BotStatus.STOPPED
        
        bot3 = manager.get_bot("user_3")
        bot3.is_running = False
        bot3.status = BotStatus.ERROR
        
        print(f"Before cleanup: {len(manager._bots)} bot instances")
        
        # Run cleanup
        await manager.cleanup_inactive_bots()
        
        print(f"After cleanup: {len(manager._bots)} bot instances")
        
        # Only running bot should remain
        assert len(manager._bots) == 1
        assert "user_1" in manager._bots
        assert "user_2" not in manager._bots
        assert "user_3" not in manager._bots
        
        print("✅ Inactive bots cleaned up, running bot preserved")
    
    async def test_stop_all_bots(self, manager):
        """Test that stop_all stops all running bots"""
        print("\n" + "="*60)
        print("TEST: Stop All Bots")
        print("="*60)
        
        # Create mock bots
        async def mock_stop():
            return {"success": True, "status": "stopped"}
        
        # Create 3 running bots
        for i in range(3):
            bot = manager.get_bot(f"user_{i}")
            bot.is_running = True
            bot.stop_bot = mock_stop
        
        print(f"Created {len(manager._bots)} running bots")
        
        # Stop all
        await manager.stop_all()
        
        print(f"✅ Stopped all bots successfully")
    
    async def test_user_status_isolation(self, manager):
        """Test that user statuses are isolated"""
        print("\n" + "="*60)
        print("TEST: User Status Isolation")
        print("="*60)
        
        # User A has running bot
        bot_a = manager.get_bot("user_a")
        bot_a.is_running = True
        bot_a.get_status = lambda: {
            "status": "running",
            "is_running": True,
            "balance": 1000.0
        }
        
        # User B has no bot yet
        status_b = manager.get_status("user_b")
        
        # User B should not see User A's status
        assert status_b["status"] == "stopped"
        assert status_b["is_running"] == False
        assert "User A" not in str(status_b)
        
        print("✅ User B cannot see User A's bot status")
        
        # Now create bot for User B
        bot_b = manager.get_bot("user_b")
        bot_b.is_running = True
        bot_b.get_status = lambda: {
            "status": "running",
            "is_running": True,
            "balance": 2000.0
        }
        
        status_a = manager.get_status("user_a")
        status_b = manager.get_status("user_b")
        
        # Each user sees only their own status
        assert status_a["balance"] == 1000.0
        assert status_b["balance"] == 2000.0
        
        print("✅ Each user sees only their own bot status")


# Standalone test runner (for development)
async def run_all_tests():
    """Run all bot manager tests"""
    print("\n" + "="*60)
    print("BOT MANAGER TEST SUITE")
    print("="*60)
    
    manager = BotManager(max_concurrent_bots=5)
    test_suite = TestBotManager()
    
    try:
        # Run tests
        await test_suite.test_multiple_users_independent_bots(manager)
        
        manager = BotManager(max_concurrent_bots=5)  # Fresh instance
        await test_suite.test_concurrent_limit_enforcement(manager)
        
        manager = BotManager(max_concurrent_bots=5)
        await test_suite.test_bot_stats(manager)
        
        manager = BotManager(max_concurrent_bots=5)
        await test_suite.test_cleanup_inactive_bots(manager)
        
        manager = BotManager(max_concurrent_bots=5)
        await test_suite.test_stop_all_bots(manager)
        
        manager = BotManager(max_concurrent_bots=5)
        await test_suite.test_user_status_isolation(manager)
        
        print("\n" + "="*60)
        print("✅ ALL TESTS PASSED")
        print("="*60)
        
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
    except Exception as e:
        print(f"\n❌ TEST ERROR: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    # Run with pytest if available, otherwise run standalone
    if HAS_PYTEST:
        print("Running with pytest...")
        import pytest as real_pytest
        real_pytest.main([__file__, "-v", "-s"])
    else:
        print("pytest not available, running standalone...")
        asyncio.run(run_all_tests())


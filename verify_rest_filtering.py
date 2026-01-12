
import unittest

class TestLogFiltering(unittest.TestCase):
    def setUp(self):
        # Mock log content
        self.mock_logs = [
            "2026-01-12 10:00:00 | INFO | [None] System started",
            "2026-01-12 10:01:00 | INFO | [user_A] User A action",
            "2026-01-12 10:02:00 | INFO | [user_B] User B action",
            "2026-01-12 10:03:00 | INFO | [user_A] Another User A action",
            "2026-01-12 10:04:00 | INFO | [None] System shutdown"
        ]
        
    def filter_logs(self, user_id, lines_requested):
        filtered_logs = []
        # Simulate reading from file in reverse
        for line in reversed(self.mock_logs):
            if len(filtered_logs) >= lines_requested:
                break
                
            if f"[{user_id}]" in line or "[None]" in line:
                filtered_logs.append(line)
                
        filtered_logs.reverse()
        return filtered_logs

    def test_user_a_view(self):
        logs = self.filter_logs("user_A", 100)
        self.assertEqual(len(logs), 4) # 2 System + 2 User A
        self.assertTrue(all("[user_B]" not in l for l in logs))
        self.assertTrue(any("[user_A]" in l for l in logs))
        print("✅ User A sees A's logs + System logs")

    def test_user_b_view(self):
        logs = self.filter_logs("user_B", 100)
        self.assertEqual(len(logs), 3) # 2 System + 1 User B
        self.assertTrue(all("[user_A]" not in l for l in logs))
        print("✅ User B sees B's logs + System logs")
        
    def test_pagination(self):
        logs = self.filter_logs("user_A", 2)
        self.assertEqual(len(logs), 2)
        # Should be the last 2 relevant logs (System shutdown + Another User A action)
        self.assertIn("System shutdown", logs[-1])
        self.assertIn("Another User A action", logs[0])
        print("✅ Pagination works")

if __name__ == '__main__':
    print("🧪 Testing REST Log Filtering Logic...")
    unittest.main()

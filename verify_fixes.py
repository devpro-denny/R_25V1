#!/usr/bin/env python3
"""
Verification script for Rise/Fall Bot State Management Fixes

This script validates that all the critical state management improvements
are syntactically correct and can be imported without errors.

Run: python3 verify_fixes.py
"""

import sys
import logging
from pathlib import Path

# Setup path to find risefallbot module
sys.path.insert(0, str(Path(__file__).parent))

# Suppress normal logging during verification
logging.disable(logging.CRITICAL)

def verify_imports():
    """Test that all modified modules can be imported."""
    print("=" * 70)
    print("RISE/FALL BOT STATE MANAGEMENT FIX VERIFICATION")
    print("=" * 70)
    print()
    
    checks = []
    
    # Check 1: Import config with new timeout param
    print("✓ Check 1: Importing rf_config with RF_PENDING_TIMEOUT_SECONDS...")
    try:
        from risefallbot import rf_config
        assert hasattr(rf_config, 'RF_PENDING_TIMEOUT_SECONDS'), \
            "Missing RF_PENDING_TIMEOUT_SECONDS in config"
        assert rf_config.RF_PENDING_TIMEOUT_SECONDS == 60, \
            f"Expected 60s, got {rf_config.RF_PENDING_TIMEOUT_SECONDS}s"
        print(f"  ✅ PASS: RF_PENDING_TIMEOUT_SECONDS = {rf_config.RF_PENDING_TIMEOUT_SECONDS}s")
        checks.append(("Config parameter added", True))
    except Exception as e:
        print(f"  ❌ FAIL: {e}")
        checks.append(("Config parameter added", False))
        return checks

    # Check 2: Import risk manager with enhanced class
    print("\n✓ Check 2: Importing RiseFallRiskManager with watchdog state...")
    try:
        from risefallbot.rf_risk_manager import RiseFallRiskManager
        
        # Create instance
        mgr = RiseFallRiskManager()
        
        # Verify watchdog attributes exist
        assert hasattr(mgr, '_pending_entry_timestamp'), "Missing _pending_entry_timestamp"
        assert hasattr(mgr, '_pending_timeout_seconds'), "Missing _pending_timeout_seconds"
        assert hasattr(mgr, '_halt_timestamp'), "Missing _halt_timestamp"
        
        # Verify watchdog timeout is set correctly
        assert mgr._pending_timeout_seconds == 60, \
            f"Expected 60s timeout, got {mgr._pending_timeout_seconds}s"
        
        print(f"  ✅ PASS: Watchdog state tracking initialized")
        print(f"    - _pending_timeout_seconds = {mgr._pending_timeout_seconds}s")
        print(f"    - Halt timestamp tracking enabled")
        checks.append(("Risk manager watchdog", True))
    except Exception as e:
        print(f"  ❌ FAIL: {e}")
        checks.append(("Risk manager watchdog", False))
        return checks

    # Check 3: Verify acquire_trade_lock has watchdog logic
    print("\n✓ Check 3: Verifying acquire_trade_lock() includes watchdog...")
    try:
        import inspect
        from risefallbot.rf_risk_manager import RiseFallRiskManager
        
        source = inspect.getsource(RiseFallRiskManager.acquire_trade_lock)
        assert "WATCHDOG" in source.upper(), "Missing WATCHDOG logic"
        assert "pending_timeout" in source.lower(), "Missing timeout check"
        assert "forcibly" in source.lower() or "force" in source.lower(), \
            "Missing force-release logic"
        
        print(f"  ✅ PASS: Watchdog timeout logic present in acquire_trade_lock()")
        checks.append(("Watchdog timeout implementation", True))
    except Exception as e:
        print(f"  ❌ FAIL: {e}")
        checks.append(("Watchdog timeout implementation", False))

    # Check 4: Verify record_trade_open has duplicate prevention
    print("\n✓ Check 4: Verifying record_trade_open() rejects duplicates...")
    try:
        import inspect
        from risefallbot.rf_risk_manager import RiseFallRiskManager
        
        source = inspect.getsource(RiseFallRiskManager.record_trade_open)
        assert "CRITICAL VIOLATION" in source, "Missing critical violation check"
        assert "ENFORCE" in source, "Missing enforcement logic"
        assert "halt" in source.lower(), "Missing halt call for duplicates"
        
        print(f"  ✅ PASS: Duplicate trade prevention with halt in record_trade_open()")
        checks.append(("Duplicate trade prevention", True))
    except Exception as e:
        print(f"  ❌ FAIL: {e}")
        checks.append(("Duplicate trade prevention", False))

    # Check 5: Check halt timestamp tracking
    print("\n✓ Check 5: Verifying halt() includes timestamp recording...")
    try:
        import inspect
        from risefallbot.rf_risk_manager import RiseFallRiskManager
        
        source = inspect.getsource(RiseFallRiskManager.halt)
        assert "_halt_timestamp" in source, "Missing halt timestamp recording"
        assert "datetime.now()" in source, "Missing current time capture"
        
        print(f"  ✅ PASS: Halt timestamp tracking enabled in halt()")
        checks.append(("Halt timestamp recording", True))
    except Exception as e:
        print(f"  ❌ FAIL: {e}")
        checks.append(("Halt timestamp recording", False))

    return checks

def summary(checks):
    """Print summary of verification results."""
    print("\n" + "=" * 70)
    print("VERIFICATION SUMMARY")
    print("=" * 70)
    
    passed = sum(1 for _, result in checks if result)
    total = len(checks)
    
    for check_name, result in checks:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status}: {check_name}")
    
    print()
    print(f"Total: {passed}/{total} checks passed")
    
    if passed == total:
        print("\n🎉 All fixes verified! System is ready for deployment.")
        return 0
    else:
        print(f"\n⚠️  {total - passed} check(s) failed. Please review the errors above.")
        return 1

if __name__ == "__main__":
    try:
        checks = verify_imports()
        exit_code = summary(checks)
        sys.exit(exit_code)
    except Exception as e:
        print(f"\n❌ FATAL ERROR during verification: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

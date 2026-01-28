"""Test if settings can be loaded with various CORS_ORIGINS formats"""
import os
import sys

# Test with empty string
os.environ['CORS_ORIGINS'] = ''
try:
    from app.core.settings import Settings
    s = Settings()
    print(f"✓ Empty string test passed: {s.CORS_ORIGINS}")
except Exception as e:
    print(f"✗ Empty string test failed: {e}")
    sys.exit(1)

# Test with comma-separated
os.environ['CORS_ORIGINS'] = 'https://malibot.vercel.app,https://r25bot.vercel.app'
try:
    # Reload settings
    import importlib
    import app.core.settings as settings_module
    importlib.reload(settings_module)
    s = settings_module.Settings()
    print(f"✓ Comma-separated test passed: {s.CORS_ORIGINS}")
    assert len(s.CORS_ORIGINS) == 2
except Exception as e:
    print(f"✗ Comma-separated test failed: {e}")
    sys.exit(1)

print("\n✅ All tests passed! Ready to deploy.")

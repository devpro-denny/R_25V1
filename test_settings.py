"""
Test script to verify FastAPI settings configuration
Run this to ensure your API environment is properly configured
"""
from app.core.settings import settings
import sys


def test_settings():
    """Test and display FastAPI settings configuration"""
    
    print("=" * 80)
    print("FastAPI Application - Settings Configuration Test")
    print("=" * 80)
    
    # Display safe configuration
    print("\n✅ Settings loaded successfully!")
    print("\n📋 Configuration Summary:")
    print("-" * 80)
    
    safe_config = settings.display_config()
    
    # Group and display settings
    sections = {
        "API Configuration": [
            "PROJECT_NAME", "VERSION", "HOST", "PORT", 
            "API_V1_PREFIX", "ENVIRONMENT"
        ],
        "Security": [
            "SECRET_KEY", "ALGORITHM", "ACCESS_TOKEN_EXPIRE_MINUTES",
            "API_KEYS"
        ],
        "CORS": [
            "CORS_ALLOW_CREDENTIALS", "CORS_ALLOW_METHODS"
        ],
        "Bot Integration": [
            "BOT_MODULE_PATH", "BOT_CONFIG_PATH", "ALLOW_BOT_CONTROL"
        ],
        "Logging": [
            "LOG_LEVEL", "API_LOG_FILE"
        ],
        "Rate Limiting": [
            "RATE_LIMIT_ENABLED", "RATE_LIMIT_PER_MINUTE", "RATE_LIMIT_PER_HOUR"
        ],
        "WebSocket": [
            "WS_HEARTBEAT_INTERVAL", "WS_MAX_CONNECTIONS", "WS_PING_INTERVAL"
        ],
        "Development": [
            "DEBUG", "TESTING", "DOCS_URL", "REDOC_URL"
        ]
    }
    
    for section, keys in sections.items():
        print(f"\n🔹 {section}:")
        for key in keys:
            if key in safe_config:
                value = safe_config[key]
                print(f"   {key}: {value}")
    
    # Display computed properties
    print(f"\n🔹 Computed Values:")
    print(f"   Is Production: {settings.is_production}")
    print(f"   Is Development: {settings.is_development}")
    print(f"   Docs Enabled: {settings.docs_enabled}")
    print(f"   Effective Docs URL: {settings.effective_docs_url}")
    print(f"   CORS Origins: {len(settings.get_cors_origins())} configured")
    
    # Validation checks
    print(f"\n✅ Validation Checks:")
    
    checks = [
        ("Secret Key changed from default", 
         settings.SECRET_KEY != "your-secret-key-change-this-in-production"),
        ("Port valid", 1024 <= settings.PORT <= 65535),
        ("Log level valid", settings.LOG_LEVEL in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]),
        ("Environment valid", settings.ENVIRONMENT in ["development", "staging", "production"]),
        ("API prefix starts with /", settings.API_V1_PREFIX.startswith("/")),
        ("Rate limiting configured", settings.RATE_LIMIT_ENABLED),
        ("Bot integration paths set", bool(settings.BOT_MODULE_PATH and settings.BOT_CONFIG_PATH)),
    ]
    
    warnings = []
    all_passed = True
    
    for check_name, passed in checks:
        status = "✅" if passed else "❌"
        print(f"   {status} {check_name}")
        if not passed:
            all_passed = False
            if "Secret Key" in check_name:
                warnings.append("⚠️  Using default SECRET_KEY - CHANGE THIS in production!")
    
    # Additional warnings for production
    if settings.is_production:
        print(f"\n⚠️  Production Environment Checks:")
        prod_checks = [
            ("Docs disabled in production", not settings.docs_enabled),
            ("Debug mode disabled", not settings.DEBUG),
            ("Rate limiting enabled", settings.RATE_LIMIT_ENABLED),
            ("API keys configured", len(settings.API_KEYS) > 0),
        ]
        
        for check_name, passed in prod_checks:
            status = "✅" if passed else "⚠️ "
            print(f"   {status} {check_name}")
            if not passed:
                warnings.append(f"⚠️  Production: {check_name}")
    
    print("\n" + "=" * 80)
    
    if all_passed and not warnings:
        print("✅ All checks passed! Your API configuration is ready.")
        if not settings.is_production:
            print("💡 Running in DEVELOPMENT mode.")
    else:
        if not all_passed:
            print("❌ Some checks failed. Please review your configuration.")
        if warnings:
            print("\n⚠️  Warnings:")
            for warning in warnings:
                print(f"   {warning}")
    
    print("\n💡 Tips:")
    print("   - The trading bot has its own config.py - this is just for the API wrapper")
    print("   - Use API_ prefix for environment variables (e.g., API_PORT=8000)")
    print("   - Generate a secure SECRET_KEY: openssl rand -hex 32")
    if settings.is_development:
        print(f"   - API docs available at: http://{settings.HOST}:{settings.PORT}{settings.DOCS_URL}")
    
    print("=" * 80 + "\n")
    
    return all_passed and not warnings


if __name__ == "__main__":
    try:
        success = test_settings()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ Error loading settings: {e}")
        print("\nMake sure:")
        print("1. You have environment variables set (or a .env file)")
        print("2. Use API_ prefix for environment variables")
        print("3. The trading bot's config.py exists separately")
        print("\nExample .env:")
        print("API_PORT=8000")
        print("API_SECRET_KEY=your-secret-key")
        print("API_ENVIRONMENT=development")
        sys.exit(1)
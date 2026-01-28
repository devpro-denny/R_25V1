"""
FastAPI Application Settings and Configuration
Updated with proper authentication support
"""
from typing import Optional, List
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
import os


class Settings(BaseSettings):
    """
    FastAPI Application settings loaded from environment variables
    """
    
    # ============================================================================
    # API Configuration
    # ============================================================================
    API_V1_PREFIX: str = "/api/v1"
    PROJECT_NAME: str = "R25 Trading Bot API"
    VERSION: str = "1.0.0"
    DESCRIPTION: str = "REST API wrapper for R25 Trading Bot"
    
    # Server Configuration
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    RELOAD: bool = True
    WORKERS: int = 1
    
    # ============================================================================
    # Security & Authentication - UPDATED FOR AUTH
    # ============================================================================
    # ============================================================================
    # Supabase Configuration
    # ============================================================================
    SUPABASE_URL: str = Field(..., description="Supabase Project URL")
    SUPABASE_SERVICE_ROLE_KEY: str = Field(..., description="Supabase Service Role Key (for Admin actions)")
    SUPABASE_ANON_KEY: Optional[str] = Field(None, description="Supabase Anon Key (optional, for client-side)")

    # Authentication Settings
    ENABLE_AUTHENTICATION: bool = os.getenv("ENABLE_AUTHENTICATION", "true").lower() == "true"
    INITIAL_ADMIN_EMAIL: Optional[str] = os.getenv("INITIAL_ADMIN_EMAIL")
    REQUIRE_AUTH_FOR_BOT_CONTROL: bool = True
    
    # Session Management
    API_KEY_HEADER: str = "X-API-Key"
    API_KEYS: List[str] = []
    
    # ============================================================================
    # CORS Settings - UPDATED FOR PRODUCTION
    # ============================================================================
    CORS_ORIGINS: List[str] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://localhost:3001",
            "http://localhost:8000",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:8000",
            # Add your Vercel deployment URLs
            "https://r25bot.vercel.app",
            "https://malibot.vercel.app",
        ]
    )
    CORS_ALLOW_CREDENTIALS: bool = True
    CORS_ALLOW_METHODS: List[str] = ["*"]
    CORS_ALLOW_HEADERS: List[str] = ["*"]
    
    # ============================================================================
    # Bot Integration
    # ============================================================================
    BOT_MODULE_PATH: str = "main"
    BOT_CONFIG_PATH: str = "config.py"
    ALLOW_BOT_CONTROL: bool = True
    BOT_AUTO_START: bool = False
    
    # ============================================================================
    # Logging Configuration
    # ============================================================================
    LOG_LEVEL: str = "INFO"
    API_LOG_FILE: str = "api.log"
    LOG_MAX_BYTES: int = 10485760  # 10MB
    LOG_BACKUP_COUNT: int = 5
    LOG_FORMAT: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    
    # ============================================================================
    # Rate Limiting
    # ============================================================================
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_PER_MINUTE: int = 60
    RATE_LIMIT_PER_HOUR: int = 1000
    
    # ============================================================================
    # WebSocket Configuration
    # ============================================================================
    WS_HEARTBEAT_INTERVAL: int = 30
    WS_MAX_CONNECTIONS: int = 100
    WS_MESSAGE_QUEUE_SIZE: int = 1000
    WS_PING_INTERVAL: int = 20
    WS_PING_TIMEOUT: int = 10
    WS_REQUIRE_AUTH: bool = False  # Set to True to require auth for WebSocket
    
    # ============================================================================
    # Database Configuration (Optional)
    # ============================================================================
    DATABASE_URL: Optional[str] = None
    DB_ECHO: bool = False
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10
    
    # ============================================================================
    # Redis Configuration (Optional)
    # ============================================================================
    REDIS_HOST: Optional[str] = None
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: Optional[str] = None
    REDIS_ENABLED: bool = False
    
    # ============================================================================
    # Monitoring & Health Checks
    # ============================================================================
    ENABLE_HEALTH_CHECK: bool = True
    HEALTH_CHECK_INTERVAL: int = 60
    ENABLE_METRICS: bool = True
    METRICS_PORT: Optional[int] = None
    
    # ============================================================================
    # Development Settings
    # ============================================================================
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"
    TESTING: bool = False
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development").lower()
    
    # API Documentation
    DOCS_URL: str = "/docs"
    REDOC_URL: str = "/redoc"
    OPENAPI_URL: str = "/openapi.json"
    DISABLE_DOCS_IN_PRODUCTION: bool = True
    
    # ============================================================================
    # Request/Response Configuration
    # ============================================================================
    MAX_REQUEST_SIZE: int = 1048576  # 1MB
    REQUEST_TIMEOUT: int = 30
    RESPONSE_COMPRESSION: bool = True
    
    # ============================================================================
    # File Storage
    # ============================================================================
    USERS_FILE: str = "users.json"  # User storage file
    TRADES_BACKUP_FILE: str = "trades_backup.json"  # Optional trade backup
    
    # ============================================================================
    # Pydantic Settings Configuration
    # ============================================================================
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )
    
    # ============================================================================
    # Validators
    # ============================================================================
    @field_validator("PORT", "METRICS_PORT")
    @classmethod
    def validate_port(cls, v: Optional[int]) -> Optional[int]:
        """Ensure port is valid"""
        if v is not None and not (1024 <= v <= 65535):
            raise ValueError("Port must be between 1024 and 65535")
        return v
    
    @field_validator("LOG_LEVEL")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        """Ensure log level is valid"""
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        v = v.upper()
        if v not in valid_levels:
            raise ValueError(f"LOG_LEVEL must be one of {valid_levels}")
        return v
    
    @field_validator("ENVIRONMENT")
    @classmethod
    def validate_environment(cls, v: str) -> str:
        """Ensure environment is valid"""
        valid_envs = ["development", "staging", "production"]
        v = v.lower()
        if v not in valid_envs:
            raise ValueError(f"ENVIRONMENT must be one of {valid_envs}")
        return v
    
    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def validate_cors_origins(cls, v):
        """Parse CORS_ORIGINS from string or list"""
        # Handle None or empty values
        if v is None or v == "":
            return []
        
        # If already a list, return it
        if isinstance(v, list):
            return v
        
        # If string, parse it
        if isinstance(v, str):
            # Strip whitespace
            v = v.strip()
            
            # If empty after stripping, return empty list
            if not v:
                return []
            
            # Try parsing as JSON first (handles ["url1","url2"] format)
            import json
            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass
            
            # Fall back to comma-separated (handles url1,url2 format)
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        
        # Fallback for any other type
        return v
    
    # JWT validator removed (using Supabase Auth now)
    
    # ============================================================================
    # Computed Properties
    # ============================================================================
    @property
    def is_production(self) -> bool:
        """Check if running in production"""
        return self.ENVIRONMENT == "production"
    
    @property
    def is_development(self) -> bool:
        """Check if running in development"""
        return self.ENVIRONMENT == "development"
    
    @property
    def docs_enabled(self) -> bool:
        """Check if API docs should be enabled"""
        if self.is_production and self.DISABLE_DOCS_IN_PRODUCTION:
            return False
        return True
    
    @property
    def effective_docs_url(self) -> Optional[str]:
        """Get docs URL if enabled"""
        return self.DOCS_URL if self.docs_enabled else None
    
    @property
    def effective_redoc_url(self) -> Optional[str]:
        """Get redoc URL if enabled"""
        return self.REDOC_URL if self.docs_enabled else None
    
    @property
    def effective_openapi_url(self) -> Optional[str]:
        """Get OpenAPI URL if enabled"""
        return self.OPENAPI_URL if self.docs_enabled else None
    
    @property
    def auth_enabled(self) -> bool:
        """Check if authentication is enabled"""
        return self.ENABLE_AUTHENTICATION
    
    # ============================================================================
    # Helper Methods
    # ============================================================================
    def get_cors_origins(self) -> List[str]:
        """Get CORS origins with environment-specific additions"""
        origins = self.CORS_ORIGINS.copy()
        
        # In production, you might want to restrict origins
        if self.is_production:
            # Remove localhost origins in production
            origins = [o for o in origins if not o.startswith("http://localhost")]
        
        return origins
    
    def display_config(self) -> dict:
        """Return safe config for display (hides sensitive data)"""
        safe_config = self.model_dump()
        
        # Hide sensitive fields
        sensitive_fields = [
            "SUPABASE_SERVICE_ROLE_KEY",
            "SUPABASE_ANON_KEY",
            "API_KEYS",
            "DATABASE_URL",
            "REDIS_PASSWORD"
        ]
        
        for field in sensitive_fields:
            if field in safe_config and safe_config[field]:
                if isinstance(safe_config[field], list):
                    safe_config[field] = ["***HIDDEN***"] * len(safe_config[field])
                else:
                    safe_config[field] = "***HIDDEN***"
        
        return safe_config
    
    def is_api_key_valid(self, api_key: str) -> bool:
        """Validate an API key"""
        if not self.API_KEYS:
            return True  # No API keys configured, allow all
        return api_key in self.API_KEYS
    
    # Password validation removed (handled by Google/Supabase)


# ============================================================================
# Create Global Settings Instance
# ============================================================================
settings = Settings()


# ============================================================================
# Helper Functions
# ============================================================================
def get_settings() -> Settings:
    """
    Dependency function to get settings instance
    Usage in FastAPI: settings = Depends(get_settings)
    """
    return settings


def reload_settings():
    """Reload settings from environment"""
    global settings
    settings = Settings()
    return settings
"""
FastAPI Application Entry Point
Wraps existing Deriv trading bot with REST API and WebSocket endpoints
"""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging

# Rate Limiting
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.core.settings import settings
from app.core.logging import setup_api_logger
from app.bot.runner import bot_runner
from app.api import bot, trades, monitor, config as config_api, auth
from app.ws import live

# Setup logging
logger = setup_api_logger()

# Setup Telegram Error Logging
try:
    # Try to import from root (assuming PYTHONPATH set correctly)
    from telegram_notifier import notifier, TelegramLoggingHandler
    
    # Attach handler to root logger
    telegram_handler = TelegramLoggingHandler(notifier)
    logging.getLogger().addHandler(telegram_handler)
    logger.info("✅ Telegram error logging enabled for API")
    
except ImportError:
    logger.warning("⚠️ Telegram notifier not available - error logging disabled")
except Exception as e:
    logger.warning(f"⚠️ Failed to setup Telegram error logging: {e}")

# Initialize Rate Limiter
limiter = Limiter(key_func=get_remote_address)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for startup and shutdown events
    Ensures clean bot lifecycle management
    """
    # Startup
    logger.info("🚀 FastAPI application starting...")
    logger.info(f"Environment: {settings.ENVIRONMENT}")
    logger.info(f"Bot auto-start: {settings.BOT_AUTO_START}")
    
    # Auto-start bot if configured
    if settings.BOT_AUTO_START:
        logger.info("Auto-starting trading bot...")
        await bot_runner.start_bot()
    
    yield
    
    # Shutdown
    logger.info("🛑 FastAPI application shutting down...")
    if bot_runner.is_running:
        logger.info("Stopping trading bot...")
        await bot_runner.stop_bot()
    logger.info("✅ Shutdown complete")

# Create FastAPI app
app = FastAPI(
    title="Deriv R_25 Trading Bot API",
    description="REST API and WebSocket interface for automated trading bot",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=settings.effective_docs_url,
    redoc_url=settings.effective_redoc_url,
    openapi_url=settings.effective_openapi_url
)

# Add Rate Limiter to app state
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# Configure CORS for Vercel frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Health check endpoint (for Render)
@app.get("/health")
@limiter.limit("5/minute")
async def health_check(request: Request):
    """Health check endpoint for monitoring and load balancers"""
    return {"status": "ok"}

# Root endpoint
@app.get("/")
@limiter.limit("5/minute")
async def root(request: Request):
    """API information endpoint"""
    return {
        "name": "Deriv R_25 Trading Bot API",
        "version": "1.0.0",
        "status": "operational",
        "docs": settings.effective_docs_url,
        "health": "/health",
    }

# Include API routers
app.include_router(auth.router, prefix="/api/v1/auth", tags=["Authentication"])
app.include_router(bot.router, prefix="/api/v1/bot", tags=["Bot Control"])
app.include_router(trades.router, prefix="/api/v1/trades", tags=["Trades"])
app.include_router(monitor.router, prefix="/api/v1/monitor", tags=["Monitoring"])
app.include_router(config_api.router, prefix="/api/v1/config", tags=["Configuration"])

# Include WebSocket router
app.include_router(live.router, prefix="/ws", tags=["WebSocket"])

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.PORT,
        reload=settings.ENVIRONMENT == "development"
    )

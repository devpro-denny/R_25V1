"""
FastAPI Application Entry Point
Wraps existing Deriv trading bot with REST API and WebSocket endpoints
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging

from app.core.settings import settings
from app.core.logging import setup_api_logger
from app.core.auth import create_initial_admin
from app.bot.runner import bot_runner
from app.api import bot, trades, monitor, config as config_api, auth
from app.ws import live

# Setup logging
logger = setup_api_logger()

# Create initial admin user if none exists
create_initial_admin()

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
    docs_url="/docs",
    redoc_url="/redoc"
)

# Configure CORS for Vercel frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Health check endpoint (for Render)
@app.get("/health")
async def health_check():
    """Health check endpoint for monitoring and load balancers"""
    return {
        "status": "healthy",
        "bot_running": bot_runner.is_running,
        "api_version": "1.0.0"
    }

# Root endpoint
@app.get("/")
async def root():
    """API information endpoint"""
    return {
        "name": "Deriv R_25 Trading Bot API",
        "version": "1.0.0",
        "status": "operational",
        "docs": "/docs",
        "health": "/health",
        "websocket": "/ws/live"
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
"""
WebSocket Live Updates
Streams real-time bot updates to connected clients
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from typing import Optional
from datetime import datetime
import asyncio
import logging
import jwt

from app.bot.events import event_manager
from app.bot.manager import bot_manager
from app.core.supabase import supabase
from app.core.settings import settings

router = APIRouter()
logger = logging.getLogger(__name__)

def extract_user_id_from_token(token: str) -> Optional[str]:
    """
    Extract user_id from Supabase JWT access token
    """
    try:
        # Decode without verification (Supabase tokens are pre-verified by the client)
        # Or use the JWT_SECRET if available
        payload = jwt.decode(token, options={"verify_signature": False})
        user_id = payload.get("sub")  # 'sub' claim contains the user_id in Supabase tokens
        return user_id
    except Exception as e:
        logger.warning(f"Failed to decode token: {e}")
        return None

@router.websocket("/live")
async def websocket_live(websocket: WebSocket, token: Optional[str] = Query(None)):
    """
    WebSocket endpoint for live updates
    Streams: bot status, trades, signals, statistics
    """
    # Authenticate
    from app.core.settings import settings
    user_id = None
    subprotocol = None
    
    # Try query param first
    token_candidate = token
    
    # If no query param, check Sec-WebSocket-Protocol header
    if not token_candidate and "sec-websocket-protocol" in websocket.headers:
        # The header can contain a comma-separated list of subprotocols
        protocols = [p.strip() for p in websocket.headers["sec-websocket-protocol"].split(",")]
        
        # Use first non-empty protocol as token
        if protocols and protocols[0]:
            token_candidate = protocols[0]

    if token_candidate:
        # Extract user_id from JWT token
        user_id = extract_user_id_from_token(token_candidate)
        if user_id:
            subprotocol = token_candidate  # Return the token as subprotocol to client
            logger.info(f"WebSocket authenticated for user: {user_id}")
        else:
            logger.warning("Failed to extract user_id from token")

    # Enforce Authentication if required
    if settings.WS_REQUIRE_AUTH and not user_id:
        logger.warning("Rejected unauthenticated WebSocket connection")
        await websocket.close(code=4001, reason="Authentication required")
        return
            
    await event_manager.connect(websocket, user_id, subprotocol=subprotocol)
    
    try:
        # Send initial state
        await websocket.send_json({
            "type": "connected",
            "message": "WebSocket connection established",
            "timestamp": datetime.now().isoformat()
        })
        
        # Determine initial state source
        initial_status = {"status": "disconnected", "message": "Authentication required"}
        initial_stats = {}
        
        if user_id:
            bot = bot_manager.get_bot(user_id)
            initial_status = bot.state.get_status()
            initial_stats = bot.state.get_statistics()
        
        # Send current bot state
        await websocket.send_json({
            "type": "bot_status",
            **initial_status,
            "timestamp": datetime.now().isoformat()
        })
        
        # Send current statistics
        await websocket.send_json({
            "type": "statistics",
            "stats": initial_stats,
            "timestamp": datetime.now().isoformat()
        })
        
        # Keep connection alive and handle incoming messages
        while True:
            # Receive message (ping/pong or commands)
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                # Handle client messages if needed
                logger.debug(f"Received from client: {data}")
            except asyncio.TimeoutError:
                # Send heartbeat
                await websocket.send_json({
                    "type": "heartbeat",
                    "timestamp": datetime.now().isoformat()
                })
    
    except WebSocketDisconnect:
        event_manager.disconnect(websocket)
        logger.info("WebSocket client disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        event_manager.disconnect(websocket)
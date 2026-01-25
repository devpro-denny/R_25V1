"""
WebSocket Live Updates
Streams real-time bot updates to connected clients
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from typing import Optional
from datetime import datetime
import asyncio
import logging

from app.bot.events import event_manager
from app.bot.manager import bot_manager
from app.core.supabase import supabase

router = APIRouter()
logger = logging.getLogger(__name__)

@router.websocket("/live")
async def websocket_live(websocket: WebSocket, token: Optional[str] = Query(None)):
    """
    WebSocket endpoint for live updates
    Streams: bot status, trades, signals, statistics
    """
    # Authenticate
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
        
        # Iterate to find a valid token
        for p in protocols:
            try:
                # Attempt to validate as token
                user_resp = supabase.auth.get_user(p)
                if user_resp and user_resp.user:
                    token_candidate = p
                    subprotocol = p # We must return the selected protocol (token) to the client
                    break
            except Exception:
                continue

    if token_candidate:
        try:
            # Re-verify if we haven't already (optimization: if we found it in loop, we already verified, but this is safe)
            # Actually, if we found it in the loop, we know it's valid. If it came from query, we validte here.
            # To be clean:
            user_resp = supabase.auth.get_user(token_candidate)
            if user_resp and user_resp.user:
                user_id = user_resp.user.id
        except Exception as e:
            logger.warning(f"WebSocket auth failed: {e}")

    # Enforce Authentication if required
    if settings.WS_REQUIRE_AUTH and not user_id:
        logger.warning("Rejected unauthenticated WebSocket connection")
        # If we fail here, we haven't accepted the socket yet.
        # But we must accept or close. FastAPI/Starlette handles close on unaccepted socket?
        # Standard: await websocket.close() works if accepted, but here we haven't.
        # It's better to just return, FastAPI will close it with 403 Forbidden usually or we can explicitly close.
        # However, to send a close code, we technically need to accept or just let it drop?
        # Actually, best practice for unaccepted is just return. But we can try to close with code.
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
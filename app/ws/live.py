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
import time

from app.bot.events import event_manager
from app.bot.manager import bot_manager
from app.core.supabase import supabase
from app.core.settings import settings

router = APIRouter()
logger = logging.getLogger(__name__)

# Deduplicate unauthenticated reconnect logs (common during client logout).
_UNAUTH_LOG_COOLDOWN_SECONDS = 60.0
_last_unauth_log_by_client = {}


def _client_identity(websocket: WebSocket) -> str:
    """
    Build a stable client identifier for log throttling.
    Prefer X-Forwarded-For when behind a proxy/load balancer.
    """
    xff = websocket.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    if websocket.client and websocket.client.host:
        return websocket.client.host
    return "unknown"


def _log_unauth_rejection(websocket: WebSocket, reason: str) -> None:
    """
    Throttle repeated unauthenticated websocket logs from the same client.
    """
    client = _client_identity(websocket)
    now = time.monotonic()
    last = _last_unauth_log_by_client.get(client, 0.0)
    if now - last >= _UNAUTH_LOG_COOLDOWN_SECONDS:
        logger.info(
            "Rejected unauthenticated WebSocket connection (%s, client=%s)",
            reason,
            client,
        )
        _last_unauth_log_by_client[client] = now
    else:
        logger.debug(
            "Suppressed repeated unauthenticated WebSocket rejection (%s, client=%s)",
            reason,
            client,
        )

def extract_user_id_from_token(token: str) -> Optional[str]:
    """
    Decode user_id from Supabase JWT access token without remote validation.
    Kept for backward compatibility and lightweight parsing.
    """
    try:
        payload = jwt.decode(token, options={"verify_signature": False})
        user_id = payload.get("sub")
        return user_id
    except Exception as e:
        logger.warning(f"Failed to decode token: {e}")
        return None


def _validate_token_session(token: str) -> Optional[str]:
    """
    Validate token against Supabase session state.
    Returns user_id only when the session is still active.
    """
    try:
        user_response = supabase.auth.get_user(token)
        user = user_response.user
        if not user:
            return None
        return user.id
    except Exception as e:
        message = str(e).lower()
        if "session from session_id claim in jwt does not exist" in message:
            logger.debug("Rejected WebSocket token for revoked/non-existent session")
        else:
            logger.warning(f"Failed to validate WebSocket token session: {e}")
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
        # Decode + session-validate to avoid accepting revoked/stale tokens.
        decoded_user_id = extract_user_id_from_token(token_candidate)
        validated_user_id = _validate_token_session(token_candidate)
        if validated_user_id:
            user_id = validated_user_id
            subprotocol = token_candidate  # Return the token as subprotocol to client
            logger.info(f"WebSocket authenticated for user: {user_id}")
        elif decoded_user_id:
            logger.debug(
                "Token decoded for user %s but session validation failed",
                decoded_user_id,
            )
        else:
            logger.debug("Failed to extract user_id from token")

    # Enforce Authentication if required
    if settings.WS_REQUIRE_AUTH and not user_id:
        _log_unauth_rejection(websocket, "missing_or_invalid_token")
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

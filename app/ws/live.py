"""
WebSocket Live Updates
Streams real-time bot updates to connected clients
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from datetime import datetime
import asyncio
import logging

from app.bot.events import event_manager
from app.bot.state import bot_state

router = APIRouter()
logger = logging.getLogger(__name__)

@router.websocket("/live")
async def websocket_live(websocket: WebSocket):
    """
    WebSocket endpoint for live updates
    Streams: bot status, trades, signals, statistics
    """
    await event_manager.connect(websocket)
    
    try:
        # Send initial state
        await websocket.send_json({
            "type": "connected",
            "message": "WebSocket connection established",
            "timestamp": datetime.now().isoformat()
        })
        
        # Send current bot state
        await websocket.send_json({
            "type": "bot_status",
            **bot_state.get_status(),
            "timestamp": datetime.now().isoformat()
        })
        
        # Send current statistics
        await websocket.send_json({
            "type": "statistics",
            "stats": bot_state.get_statistics(),
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
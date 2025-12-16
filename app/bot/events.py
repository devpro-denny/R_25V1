"""
WebSocket Event Manager
Broadcasts events to all connected clients AND registered handlers
"""

import asyncio
import logging
from typing import Set, Dict, Callable, List
from fastapi import WebSocket

logger = logging.getLogger(__name__)

class EventManager:
    """
    Manages WebSocket connections and broadcasts events
    Also supports registering event handlers (like Telegram bridge)
    """
    
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        # Event handlers: {event_type: [handler_functions]}
        self.event_handlers: Dict[str, List[Callable]] = {}
    
    def register(self, event_type: str, handler: Callable):
        """
        Register an event handler for a specific event type
        
        Args:
            event_type: Type of event (e.g., "trade_opened", "bot_status")
            handler: Async function to handle the event
        """
        if event_type not in self.event_handlers:
            self.event_handlers[event_type] = []
        
        self.event_handlers[event_type].append(handler)
        logger.info(f"ðŸ“ Registered handler for event type: {event_type}")
    
    def unregister(self, event_type: str, handler: Callable):
        """Remove an event handler"""
        if event_type in self.event_handlers:
            try:
                self.event_handlers[event_type].remove(handler)
                logger.info(f"ðŸ—‘ï¸ Unregistered handler for event type: {event_type}")
            except ValueError:
                pass
    
    async def connect(self, websocket: WebSocket):
        """Add new WebSocket connection"""
        await websocket.accept()
        self.active_connections.add(websocket)
        logger.info(f"WebSocket client connected. Total: {len(self.active_connections)}")
    
    def disconnect(self, websocket: WebSocket):
        """Remove WebSocket connection"""
        self.active_connections.discard(websocket)
        logger.info(f"WebSocket client disconnected. Total: {len(self.active_connections)}")
    
    async def broadcast(self, message: Dict):
        """
        Broadcast message to:
        1. All connected WebSocket clients
        2. All registered event handlers for this event type
        """
        event_type = message.get("type")
        
        # Call registered event handlers first (like Telegram)
        if event_type and event_type in self.event_handlers:
            handler_tasks = []
            for handler in self.event_handlers[event_type]:
                handler_tasks.append(self._call_handler(handler, message))
            
            if handler_tasks:
                await asyncio.gather(*handler_tasks, return_exceptions=True)
        
        # Then broadcast to WebSocket clients
        if self.active_connections:
            ws_tasks = []
            for connection in self.active_connections.copy():
                ws_tasks.append(self._send_message(connection, message))
            
            await asyncio.gather(*ws_tasks, return_exceptions=True)
    
    async def _call_handler(self, handler: Callable, event: Dict):
        """Call an event handler safely"""
        try:
            # Check if handler is async
            if asyncio.iscoroutinefunction(handler):
                await handler(event)
            else:
                handler(event)
        except Exception as e:
            logger.error(f"Error in event handler: {e}", exc_info=True)
    
    async def _send_message(self, websocket: WebSocket, message: Dict):
        """Send message to a single WebSocket client"""
        try:
            await websocket.send_json(message)
        except Exception as e:
            logger.error(f"Error sending WebSocket message: {e}")
            self.disconnect(websocket)

# Global event manager instance
event_manager = EventManager()

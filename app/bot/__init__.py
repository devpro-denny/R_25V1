"""
Bot Module - Multi-User Bot Management
"""

from app.bot.manager import bot_manager
from app.bot.runner import BotRunner, BotStatus
from app.bot.state import BotState
from app.bot.events import event_manager

__all__ = [
    'bot_manager',  # Primary interface for multi-user bot management
    'BotRunner',
    'BotStatus',
    'BotState',
    'event_manager',
]
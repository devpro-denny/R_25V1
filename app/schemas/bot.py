"""Pydantic schemas for bot-related responses"""

from pydantic import BaseModel
from typing import Optional, Dict

class BotStatusResponse(BaseModel):
    status: str
    is_running: bool
    uptime_seconds: Optional[int]
    start_time: Optional[str]
    error_message: Optional[str]
    statistics: Dict

class BotControlResponse(BaseModel):
    success: bool
    message: str
    status: str
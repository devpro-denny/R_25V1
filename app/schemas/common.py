"""
Pydantic schemas for common API responses
"""

from typing import Dict, Any, Optional
from pydantic import BaseModel, Field


class SignalResponse(BaseModel):
    signal: str = Field(..., description="Generated trading signal")
    score: int = Field(..., description="Signal strength score")
    confidence: Optional[float] = Field(
        None, ge=0.0, le=1.0, description="Confidence level (0.0 - 1.0)"
    )
    timestamp: str = Field(..., description="ISO-8601 timestamp")
    details: Dict[str, Any] = Field(default_factory=dict)


class PerformanceResponse(BaseModel):
    uptime_seconds: int = Field(..., ge=0)
    cycles_completed: int = Field(..., ge=0)
    total_trades: int = Field(..., ge=0)
    win_rate: float = Field(..., ge=0.0, le=100.0)
    total_pnl: float


class ConfigResponse(BaseModel):
    trading: Dict[str, Any]
    risk_management: Dict[str, Any]
    strategy: Dict[str, Any]

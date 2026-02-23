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
    cpu_usage: Optional[float] = Field(None, ge=0.0, le=100.0)
    memory_usage: Optional[float] = Field(None, ge=0.0, le=100.0)
    error_rate: Optional[float] = Field(None, ge=0.0, le=100.0)
    active_connections: Optional[int] = Field(None, ge=0)
    scalping_total_symbol_checks: Optional[int] = Field(None, ge=0)
    scalping_signals_generated: Optional[int] = Field(None, ge=0)
    scalping_rejections: Optional[int] = Field(None, ge=0)
    scalping_opportunity_rate_pct: Optional[float] = Field(None, ge=0.0, le=100.0)
    scalping_gate_counters: Optional[Dict[str, int]] = Field(None)


class ConfigResponse(BaseModel):
    trading: Dict[str, Any]
    risk_management: Dict[str, Any]
    strategy: Dict[str, Any]
    deriv_api_key: Optional[str] = Field(None, description="Masked Deriv API Key")
    stake_amount: Optional[float] = Field(None, description="User defined stake amount")
    active_strategy: Optional[str] = Field(None, description="User defined trading strategy")

from pydantic import BaseModel, Field, field_validator
from typing import Optional

class ConfigUpdateRequest(BaseModel):
    """
    Validation model for configuration updates
    """
    deriv_api_key: Optional[str] = Field(None, min_length=10, description="Deriv API Token")
    stake_amount: Optional[float] = Field(None, gt=0, description="Stake amount per trade")
    active_strategy: Optional[str] = Field(None, description="Active strategy name")
    
    # Risk Management
    max_trades_per_day: Optional[int] = Field(None, gt=0, le=1000, description="Max trades per day")
    cooldown_seconds: Optional[int] = Field(None, ge=1, le=3600, description="Cooldown between trades")

    @field_validator('active_strategy')
    @classmethod
    def validate_strategy(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        valid_strategies = ["Conservative", "Aggressive", "Balanced"]
        # Allow checking against user's specific strategies later if needed, 
        # but for now ensure it's not empty text
        if not v.strip():
            raise ValueError("Strategy name cannot be empty")
        return v

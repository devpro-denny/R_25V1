"""
Conservative Strategy Wrapper
Wraps the existing strategy.py logic to implement the BaseStrategy interface
"""

from base_strategy import BaseStrategy
from strategy import TradingStrategy
from typing import Dict, List, Optional
import pandas as pd
import logging

logger = logging.getLogger(__name__)


class ConservativeStrategy(BaseStrategy):
    """
    Wrapper for the existing conservative top-down strategy.
    Delegates to the original TradingStrategy implementation.
    """
    
    def __init__(self):
        """Initialize the conservative strategy wrapper"""
        self.strategy = TradingStrategy()
    
    def analyze(self, **kwargs) -> Optional[Dict]:
        """
        Analyze market data using the existing conservative strategy.
        
        Args:
            **kwargs: Must include data_1m, data_5m, data_1h, data_4h, data_1d, data_1w, symbol
        
        Returns:
            Signal dict if trade should be executed, None otherwise
        """
        # Extract timeframe data from kwargs
        data_1m = kwargs.get('data_1m')
        data_5m = kwargs.get('data_5m')
        data_1h = kwargs.get('data_1h')
        data_4h = kwargs.get('data_4h')
        data_1d = kwargs.get('data_1d')
        data_1w = kwargs.get('data_1w')
        symbol = kwargs.get('symbol')
        
        # Validate required data
        if not all([data_1m is not None, data_5m is not None, data_1h is not None,
                   data_4h is not None, data_1d is not None, data_1w is not None]):
            logger.error("[CONSERVATIVE] ❌ Missing required timeframe data for conservative strategy")
            return None
        
        # Delegate to existing strategy
        return self.strategy.analyze(
            data_1m=data_1m,
            data_5m=data_5m,
            data_1h=data_1h,
            data_4h=data_4h,
            data_1d=data_1d,
            data_1w=data_1w,
            symbol=symbol
        )
    
    def get_required_timeframes(self) -> List[str]:
        """
        Get list of timeframes required by conservative strategy.
        
        Returns:
            ['1w', '1d', '4h', '1h', '5m', '1m']
        """
        return ['1w', '1d', '4h', '1h', '5m', '1m']
    
    def get_strategy_name(self) -> str:
        """
        Get strategy name.
        
        Returns:
            'Conservative'
        """
        return "Conservative"

"""
FastAPI Response Serialization Utilities for R50BOT Trading System

This module provides utilities to convert NumPy types and large integers
to JSON-serializable Python types for FastAPI responses.

Fixes:
1. NumPy type serialization (np.int64, np.float64, etc.)
2. Large integer IDs to strings (contract_id, order_id)
3. Pandas DataFrame conversion
4. Datetime and Decimal handling

Usage:
    from app.core.serializers import prepare_response
    
    @router.get("/signals")
    async def get_signals():
        data = fetch_signals()
        return prepare_response(data)
"""

import logging
from typing import Any, Dict, List, Union, Optional
from datetime import datetime, date
from decimal import Decimal
from enum import Enum

# Third-party imports
import numpy as np
import pandas as pd

# Set up logging
logger = logging.getLogger(__name__)


def ensure_json_serializable(obj: Any) -> Any:
    """
    Recursively convert non-JSON-serializable types to standard Python types.
    
    Handles all common NumPy types, Pandas objects, datetime, Decimal, etc.
    This is the core function that fixes the NumPy serialization error.
    
    Args:
        obj: Any Python object
        
    Returns:
        JSON-serializable version of the input
        
    Example:
        >>> data = {"price": np.float64(123.45), "qty": np.int64(100)}
        >>> ensure_json_serializable(data)
        {"price": 123.45, "qty": 100}
    """
    # Handle None
    if obj is None:
        return None
    
    # Handle dictionaries recursively
    if isinstance(obj, dict):
        return {k: ensure_json_serializable(v) for k, v in obj.items()}
    
    # Handle lists and tuples recursively
    if isinstance(obj, (list, tuple)):
        return [ensure_json_serializable(elem) for elem in obj]
    
    # NumPy integer types
    if isinstance(obj, (np.integer, np.int8, np.int16, np.int32, np.int64,
                        np.uint8, np.uint16, np.uint32, np.uint64)):
        return int(obj)
    
    # NumPy float types
    if isinstance(obj, (np.floating, np.float16, np.float32, np.float64)):
        val = float(obj)
        # Convert NaN and Inf to None for safety
        if np.isnan(val) or np.isinf(val):
            logger.warning(f"Converting NaN/Inf to None in response data")
            return None
        return val
    
    # NumPy boolean
    if isinstance(obj, np.bool_):
        return bool(obj)
    
    # NumPy datetime64
    if isinstance(obj, np.datetime64):
        try:
            return pd.Timestamp(obj).isoformat()
        except Exception as e:
            logger.error(f"Error converting datetime64: {e}")
            return None
    
    # NumPy arrays
    if isinstance(obj, np.ndarray):
        return ensure_json_serializable(obj.tolist())
    
    # Pandas Series
    if isinstance(obj, pd.Series):
        return ensure_json_serializable(obj.to_dict())
    
    # Pandas DataFrame
    if isinstance(obj, pd.DataFrame):
        return ensure_json_serializable(obj.to_dict('records'))
    
    # Python datetime objects
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    
    # Decimal (from database Decimal columns)
    if isinstance(obj, Decimal):
        return float(obj)
    
    # Enums
    if isinstance(obj, Enum):
        return obj.value
    
    # Standard types pass through
    return obj


def convert_large_ints_to_str(
    obj: Any, 
    fields: Optional[List[str]] = None
) -> Any:
    """
    Convert large integer fields to strings for API responses.
    
    Important for IDs like contract_id that exceed JavaScript's safe integer
    limit (2^53). This fixes the "Input should be a valid string" error.
    
    Args:
        obj: The object to convert
        fields: List of field names to convert to strings.
                If None, auto-converts integers > 2^53
                
    Returns:
        Object with specified integer fields converted to strings
        
    Example:
        >>> data = {"contract_id": 301837873208, "price": 123.45}
        >>> convert_large_ints_to_str(data, fields=["contract_id"])
        {"contract_id": "301837873208", "price": 123.45}
    """
    if isinstance(obj, dict):
        result = {}
        for k, v in obj.items():
            if fields and k in fields:
                # Convert specified fields to string
                result[k] = str(v) if v is not None else None
            elif fields is None and isinstance(v, (int, np.integer)):
                # Auto-convert large integers (> JavaScript safe integer)
                if abs(int(v)) > 2**53:
                    logger.debug(f"Auto-converting large int field '{k}': {v}")
                    result[k] = str(v)
                else:
                    result[k] = int(v)
            else:
                # Recurse for nested structures
                result[k] = convert_large_ints_to_str(v, fields)
        return result
    
    if isinstance(obj, list):
        return [convert_large_ints_to_str(elem, fields) for elem in obj]
    
    return obj


def prepare_response(
    data: Any, 
    id_fields: Optional[List[str]] = None
) -> Any:
    """
    Complete response preparation for FastAPI endpoints.
    
    This is the main function you should use in your API routes.
    It combines NumPy type conversion and ID field string conversion.
    
    Args:
        data: Raw data from database/service layer
        id_fields: List of field names that should be strings (e.g., ['contract_id'])
        
    Returns:
        Fully prepared, JSON-serializable response
        
    Example:
        @router.get("/trades/active")
        async def get_active_trades():
            trades = fetch_trades()
            return prepare_response(trades, id_fields=['contract_id', 'order_id'])
    """
    try:
        # Step 1: Convert all NumPy types to Python types
        serialized = ensure_json_serializable(data)
        
        # Step 2: Convert specified ID fields to strings
        if id_fields:
            serialized = convert_large_ints_to_str(serialized, id_fields)
        
        return serialized
        
    except Exception as e:
        logger.error(f"Error preparing response: {e}", exc_info=True)
        raise


def dataframe_to_response(
    df: pd.DataFrame, 
    id_fields: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """
    Convert Pandas DataFrame to list of dicts for API response.
    
    Convenience function for when you're working with DataFrames.
    
    Args:
        df: Pandas DataFrame
        id_fields: Column names that should be strings
        
    Returns:
        List of dictionaries with proper type conversion
        
    Example:
        @router.get("/history")
        async def get_trade_history():
            df = fetch_trade_history_dataframe()
            return dataframe_to_response(df, id_fields=['contract_id'])
    """
    if df.empty:
        return []
    
    try:
        # Convert DataFrame to list of records
        records = df.to_dict('records')
        
        # Apply full serialization
        return prepare_response(records, id_fields)
        
    except Exception as e:
        logger.error(f"Error converting DataFrame: {e}", exc_info=True)
        raise


# Convenience decorator for automatic serialization
def auto_serialize(func):
    """
    Decorator to automatically serialize function return values.
    
    Use this for quick fixes without modifying existing code.
    
    Example:
        @router.get("/signals")
        @auto_serialize
        async def get_signals():
            return data_with_numpy_types  # Automatically fixed
    """
    from functools import wraps
    
    @wraps(func)
    async def wrapper(*args, **kwargs):
        result = await func(*args, **kwargs)
        return ensure_json_serializable(result)
    
    return wrapper


# Export main functions
__all__ = [
    'ensure_json_serializable',
    'convert_large_ints_to_str',
    'prepare_response',
    'dataframe_to_response',
    'auto_serialize'
]


if __name__ == "__main__":
    # Quick self-test
    import json
    
    # Test NumPy types
    test_data = {
        "numpy_int": np.int64(12345),
        "numpy_float": np.float64(123.45),
        "numpy_array": np.array([1, 2, 3]),
        "contract_id": 301837873208,
        "nested": {
            "value": np.float64(99.99)
        }
    }
    
    result = prepare_response(test_data, id_fields=['contract_id'])
    
    # This should work without errors
    json_str = json.dumps(result, indent=2)
    print("✓ Serialization test passed!")
    print(json_str)
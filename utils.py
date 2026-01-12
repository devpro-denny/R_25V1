"""
Utility Functions for Deriv R_25 Trading Bot
Provides logging, formatting, and helper functions
utils.py - COMPLETE FIXED VERSION
"""

import logging
import json
import sys
from datetime import datetime
from typing import Dict, Any, Optional
from app.core.context import user_id_var

class ContextInjectingFilter(logging.Filter):
    """
    Injects user_id from contextvars into the log record.
    """
    def filter(self, record):
        record.user_id = user_id_var.get()
        return True

def setup_logger(log_file: str = "trading_bot.log", level: str = "INFO") -> logging.Logger:
    """
    Set up logging configuration with UTF-8 encoding for Windows compatibility
    
    Args:
        log_file: Path to log file
        level: Logging level (DEBUG, INFO, WARNING, ERROR)
    
    Returns:
        Configured logger instance
    """
    # Create logger
    logger = logging.getLogger("TradingBot")
    logger.setLevel(getattr(logging, level))
    
    # Prevent duplicate handlers
    if logger.handlers:
        return logger
    
    # Create formatters
    # Create formatters with User ID support
    # We include user_id in the message string so it's written to file
    detailed_formatter = logging.Formatter(
        '%(asctime)s | %(levelname)s | [%(user_id)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Console handler with UTF-8 encoding
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, level))
    console_handler.setFormatter(detailed_formatter)
    
    # Set UTF-8 encoding for console on Windows
    if sys.platform == 'win32':
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except AttributeError:
            pass  # Python < 3.7
    
    # File handler with UTF-8 encoding
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)  # File gets everything
    file_handler.setFormatter(detailed_formatter)
    
    # Utilities logger also needs context filter
    context_filter = ContextInjectingFilter()
    logger.addFilter(context_filter)

    # Add handlers
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    
    return logger

def format_price(price: float, decimals: int = 2) -> str:
    """Format price with proper decimal places"""
    return f"{price:.{decimals}f}"

def format_currency(amount: float) -> str:
    """Format amount as currency"""
    return f"${amount:.2f}"

def format_percentage(value: float) -> str:
    """Format value as percentage"""
    return f"{value:.2f}%"

def format_timestamp(timestamp: Optional[int] = None) -> str:
    """
    Format Unix timestamp to readable string
    
    Args:
        timestamp: Unix timestamp (seconds). If None, uses current time
    
    Returns:
        Formatted datetime string
    """
    if timestamp is None:
        dt = datetime.now()
    else:
        dt = datetime.fromtimestamp(timestamp)
    return dt.strftime('%Y-%m-%d %H:%M:%S')

def calculate_pnl(entry_price: float, current_price: float, 
                  stake: float, multiplier: int, direction: str) -> float:
    """
    Calculate profit/loss for a multiplier position
    
    Args:
        entry_price: Entry price of the trade
        current_price: Current market price
        stake: Stake amount
        multiplier: Multiplier value
        direction: 'UP' or 'DOWN'
    
    Returns:
        Current P&L amount
    """
    price_change = current_price - entry_price
    
    if direction.upper() == "DOWN":
        price_change = -price_change
    
    pnl = (price_change / entry_price) * stake * multiplier
    return pnl

def validate_api_response(response: Dict[str, Any], expected_msg_type: str) -> bool:
    """
    Validate API response structure
    
    Args:
        response: API response dictionary
        expected_msg_type: Expected message type
    
    Returns:
        True if valid, False otherwise
    """
    if not isinstance(response, dict):
        return False
    
    if "error" in response:
        return False
    
    if "msg_type" in response and response["msg_type"] != expected_msg_type:
        return False
    
    return True

def parse_candle_data(response: Dict[str, Any]) -> list:
    """
    Parse candle data from API response
    
    Args:
        response: API response containing candle data
    
    Returns:
        List of candles [timestamp, open, high, low, close]
    """
    if "candles" not in response:
        return []
    
    candles = response["candles"]
    parsed = []
    
    for candle in candles:
        parsed.append({
            'timestamp': candle['epoch'],
            'open': float(candle['open']),
            'high': float(candle['high']),
            'low': float(candle['low']),
            'close': float(candle['close'])
        })
    
    return parsed

def print_trade_summary(trade_info: Dict[str, Any]) -> None:
    """
    Print formatted trade summary
    
    Args:
        trade_info: Dictionary containing trade information
    """
    print("\n" + "="*60)
    print("TRADE SUMMARY")
    print("="*60)
    
    for key, value in trade_info.items():
        if isinstance(value, float):
            if key.lower().find('price') != -1:
                print(f"{key}: {format_price(value)}")
            elif key.lower().find('pnl') != -1 or key.lower().find('profit') != -1:
                print(f"{key}: {format_currency(value)}")
            else:
                print(f"{key}: {value}")
        else:
            print(f"{key}: {value}")
    
    print("="*60 + "\n")

def print_statistics(stats: Dict[str, Any]) -> None:
    """
    Print formatted trading statistics
    
    Args:
        stats: Dictionary containing trading statistics
    """
    print("\n" + "="*60)
    print("TRADING STATISTICS")
    print("="*60)
    
    total = stats.get('total_trades', 0)
    wins = stats.get('winning_trades', 0)
    losses = stats.get('losing_trades', 0)
    win_rate = (wins / total * 100) if total > 0 else 0
    total_pnl = stats.get('total_pnl', 0.0)
    
    print(f"Total Trades: {total}")
    print(f"Wins: {wins} | Losses: {losses}")
    print(f"Win Rate: {format_percentage(win_rate)}")
    print(f"Total P&L: {format_currency(total_pnl)}")
    
    if 'max_drawdown' in stats:
        print(f"Max Drawdown: {format_currency(stats['max_drawdown'])}")
    
    if 'largest_win' in stats:
        print(f"Largest Win: {format_currency(stats['largest_win'])}")
    
    if 'largest_loss' in stats:
        print(f"Largest Loss: {format_currency(stats['largest_loss'])}")
    
    print("="*60 + "\n")

def safe_float(value: Any, default: float = 0.0) -> float:
    """
    Safely convert value to float
    
    Args:
        value: Value to convert
        default: Default value if conversion fails
    
    Returns:
        Float value or default
    """
    try:
        return float(value)
    except (ValueError, TypeError):
        return default

def safe_int(value: Any, default: int = 0) -> int:
    """
    Safely convert value to integer
    
    Args:
        value: Value to convert
        default: Default value if conversion fails
    
    Returns:
        Integer value or default
    """
    try:
        return int(value)
    except (ValueError, TypeError):
        return default

def truncate_string(text: str, max_length: int = 50) -> str:
    """
    Truncate string to maximum length
    
    Args:
        text: String to truncate
        max_length: Maximum length
    
    Returns:
        Truncated string
    """
    if len(text) <= max_length:
        return text
    return text[:max_length-3] + "..."

def get_signal_emoji(signal) -> str:
    """
    Get emoji for signal type - FIXED to handle None and any type
    
    Args:
        signal: Signal type ('BUY', 'SELL', 'HOLD', etc.) - can be None
    
    Returns:
        Signal emoji string
    """
    # Handle None or empty signal
    if signal is None or signal == '':
        return '⚪'
    
    # Convert to string and uppercase safely
    try:
        signal_str = str(signal).upper()
    except:
        return '⚪'
    
    # Map signals to emojis
    emojis = {
        'BUY': '🟢',
        'SELL': '🔴',
        'HOLD': '⚪',
        'UP': '⬆️',
        'DOWN': '⬇️'
    }
    
    return emojis.get(signal_str, '⚪')

def get_status_emoji(status) -> str:
    """
    Get emoji for trade status - FIXED to handle None and any type
    
    Args:
        status: Status string (can be None, string, or any type)
    
    Returns:
        Status emoji string
    """
    # Handle None or empty status
    if status is None or status == '':
        return '❓'
    
    # Convert to string and lowercase safely
    try:
        status_str = str(status).lower()
    except:
        return '❓'
    
    # Map status to emojis
    emojis = {
        'open': '📊',
        'won': '✅',
        'lost': '❌',
        'sold': '🔄',
        'closed': '🔒',
        'cancelled': '⛔',
        'unknown': '❓'
    }
    
    return emojis.get(status_str, '❓')

def is_market_open() -> bool:
    """
    Check if market is open (Synthetic indices are 24/7)
    
    Returns:
        True (synthetic indices always open)
    """
    return True

def calculate_lot_size(balance: float, risk_percent: float, 
                       stop_loss_pips: float, pip_value: float) -> float:
    """
    Calculate position size based on risk management
    
    Args:
        balance: Account balance
        risk_percent: Risk percentage per trade
        stop_loss_pips: Stop loss in pips
        pip_value: Value per pip
    
    Returns:
        Calculated lot size
    """
    risk_amount = balance * (risk_percent / 100)
    lot_size = risk_amount / (stop_loss_pips * pip_value)
    return round(lot_size, 2)

# Testing
if __name__ == "__main__":
    print("="*60)
    print("TESTING UTILS.PY")
    print("="*60)
    
    # Test logger
    print("\n1. Testing logger setup...")
    logger = setup_logger()
    logger.info("✅ Logger setup successful")
    logger.debug("Debug message test")
    logger.warning("Warning message test")
    
    # Test formatting functions
    print("\n2. Testing formatting functions...")
    print(f"   Price: {format_price(1234.5678)}")
    print(f"   Currency: {format_currency(1234.56)}")
    print(f"   Percentage: {format_percentage(67.89)}")
    print(f"   Timestamp: {format_timestamp()}")
    
    # Test P&L calculation
    print("\n3. Testing P&L calculation...")
    pnl = calculate_pnl(100.0, 105.0, 10.0, 100, "UP")
    print(f"   P&L for UP trade: {format_currency(pnl)}")
    pnl_down = calculate_pnl(100.0, 95.0, 10.0, 100, "DOWN")
    print(f"   P&L for DOWN trade: {format_currency(pnl_down)}")
    
    # Test emoji functions with None handling
    print("\n4. Testing emoji functions...")
    print(f"   BUY signal: {get_signal_emoji('BUY')}")
    print(f"   SELL signal: {get_signal_emoji('SELL')}")
    print(f"   None signal: {get_signal_emoji(None)}")
    print(f"   Won status: {get_status_emoji('won')}")
    print(f"   Lost status: {get_status_emoji('lost')}")
    print(f"   None status: {get_status_emoji(None)}")
    
    # Test safe conversion functions
    print("\n5. Testing safe conversion functions...")
    print(f"   safe_float('123.45'): {safe_float('123.45')}")
    print(f"   safe_float('invalid'): {safe_float('invalid')}")
    print(f"   safe_float(None): {safe_float(None)}")
    print(f"   safe_int('42'): {safe_int('42')}")
    print(f"   safe_int('invalid'): {safe_int('invalid')}")
    
    # Test statistics
    print("\n6. Testing statistics display...")
    test_stats = {
        'total_trades': 10,
        'winning_trades': 6,
        'losing_trades': 4,
        'total_pnl': 45.50,
        'max_drawdown': -15.00,
        'largest_win': 12.00,
        'largest_loss': -8.50
    }
    print_statistics(test_stats)
    
    print("="*60)
    print("✅ ALL UTILS TESTS COMPLETE!")
    print("="*60)
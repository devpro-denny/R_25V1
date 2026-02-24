"""
Data Fetcher for Deriv Multi-Asset Trading Bot
Enhanced with multi-asset sequential fetching and rate limiting
data_fetcher.py - MULTI-ASSET VERSION
"""

import asyncio
import websockets
import json
import pandas as pd
from typing import Dict, List, Optional, Any
from datetime import datetime
import config
from utils import setup_logger, parse_candle_data, TokenBucket

# Setup logger
logger = setup_logger()

class DataFetcher:
    """Handles all data fetching operations from Deriv API with multi-asset support"""
    
    def __init__(self, api_token: str, app_id: str = "1089"):
        """
        Initialize DataFetcher
        
        Args:
            api_token: Deriv API token
            app_id: Deriv app ID
        """
        self.api_token = api_token
        self.app_id = app_id
        self.ws_url = f"{config.WS_URL}?app_id={app_id}"
        self.ws = None
        self.is_connected = False
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 5
        
        # Rate limiting - TokenBucket allows 10 req/s with burst capacity of 20
        self.rate_limiter = TokenBucket(rate=10.0, capacity=20.0)
        # Protect websocket request/response pairs from concurrent recv() calls.
        self._ws_request_lock = asyncio.Lock()
        # Prevent reconnect storms when many tasks detect a dropped socket.
        self._connection_lock = asyncio.Lock()
        
        # Error tracking
        self.last_error: Optional[str] = None
    
    async def connect(self) -> bool:
        """Connect to Deriv WebSocket API"""
        try:
            logger.info(f"Connecting to Deriv API at {self.ws_url}...")
            self.ws = await websockets.connect(
                self.ws_url,
                ping_interval=30,
                ping_timeout=10
            )
            self.is_connected = True
            self.reconnect_attempts = 0
            logger.info("[OK] Connected to Deriv API")
            
            # Authorize
            if not await self.authorize():
                # last_error is set in authorize()
                logger.error("[ERROR] Authorization failed during connection")
                await self.disconnect()
                return False
                
            return True
            
        except Exception as e:
            self.last_error = f"Connection exception: {str(e)}"
            logger.error(f"[ERROR] Failed to connect to Deriv API: {e}")
            import traceback
            logger.error(traceback.format_exc())
            self.is_connected = False
            return False
    
    async def reconnect(self) -> bool:
        """Attempt to reconnect to the API"""
        self.reconnect_attempts += 1
        
        if self.reconnect_attempts > self.max_reconnect_attempts:
            logger.error(f"[ERROR] Max reconnection attempts ({self.max_reconnect_attempts}) reached")
            return False
        
        logger.warning(f"[RECONNECT] Attempt {self.reconnect_attempts}/{self.max_reconnect_attempts}")
        
        # Close existing connection if any
        if self.ws:
            try:
                await self.ws.close()
            except:
                pass
        
        self.is_connected = False
        
        # Wait before reconnecting (exponential backoff)
        wait_time = min(2 ** self.reconnect_attempts, 30)
        logger.info(f"[RECONNECT] Waiting {wait_time}s before reconnecting...")
        await asyncio.sleep(wait_time)
        
        # Try to connect
        return await self.connect()
    
    async def ensure_connected(self) -> bool:
        """Ensure WebSocket is connected, reconnect if needed"""
        if self.is_connected and self.ws and not self.ws.closed:
            return True

        logger.warning("[WARNING] Connection lost, attempting to reconnect...")
        async with self._connection_lock:
            # Another waiter may have already re-established connection.
            if self.is_connected and self.ws and not self.ws.closed:
                return True
            return await self.reconnect()
    
    async def disconnect(self):
        """Disconnect from WebSocket"""
        if self.ws:
            await self.ws.close()
            self.ws = None
            self.is_connected = False
            logger.info("[DISCONNECTED] From Deriv API")
    
    async def authorize(self) -> bool:
        """Authorize connection with API token"""
        try:
            auth_request = {
                "authorize": self.api_token
            }
            
            # Acquire rate limit token before sending
            await self.rate_limiter.acquire()
            await self.ws.send(json.dumps(auth_request))
            response = await self.ws.recv()
            data = json.loads(response)
            
            if "error" in data:
                error_msg = data['error']['message']
                self.last_error = f"Auth failed: {error_msg}"
                logger.error(f"❌ AUTH_FAILED | Error: {error_msg}")
                return False
            
            if "authorize" in data:
                logger.info("[OK] Authorization successful")
                return True
            
            self.last_error = "Unknown auth response"
            return False
            
        except Exception as e:
            self.last_error = f"Auth exception: {str(e)}"
            logger.error(f"❌ AUTH_EXCEPTION | Error: {type(e).__name__}: {e}", exc_info=True)
            return False
    
    # Removed old _rate_limit method - now using TokenBucket
    
    async def send_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Send request to API with robust retry logic and rate limiting"""
        # Ensure connection is alive before starting
        if not await self.ensure_connected():
            return {"error": {"message": "Failed to establish early connection"}}
        
        # Retry loop
        for attempt in range(1, config.MAX_RETRIES + 1):
            try:
                # Ensure connection for this attempt
                if not self.is_connected or not self.ws or self.ws.closed:
                    logger.warning(f"[RETRY] Connection lost, reconnecting (Attempt {attempt})...")
                    async with self._connection_lock:
                        # Another waiter may have already restored connection.
                        if self.is_connected and self.ws and not self.ws.closed:
                            pass
                        else:
                            reconnected = await self.reconnect()
                            if not reconnected:
                                if attempt < config.MAX_RETRIES:
                                    await asyncio.sleep(config.RETRY_DELAY * attempt)
                                    continue
                                else:
                                    return {"error": {"message": "Connection permanently lost"}}
                    if not self.is_connected or not self.ws or self.ws.closed:
                        if attempt < config.MAX_RETRIES:
                            await asyncio.sleep(config.RETRY_DELAY * attempt)
                            continue
                        else:
                            return {"error": {"message": "Connection permanently lost"}}

                # Acquire token from rate limiter (allows queued parallel producers).
                await self.rate_limiter.acquire()
                # CRITICAL: Deriv websocket uses a single recv stream; concurrent
                # recv() calls raise runtime errors and can mix responses.
                async with self._ws_request_lock:
                    await self.ws.send(json.dumps(request))
                    response_str = await self.ws.recv()
                    response = json.loads(response_str)
                
                # Check for specific transient API errors to retry
                if "error" in response:
                    error_msg = response["error"].get("message", "")
                    # Retry on generic errors or rate limits, but not invalid parameters
                    if "Sorry, an error occurred" in error_msg or "Rate limit" in error_msg:
                         logger.warning(f"[RETRY] Transient API error: {error_msg} (Attempt {attempt}/{config.MAX_RETRIES})")
                         if attempt < config.MAX_RETRIES:
                             await asyncio.sleep(config.RETRY_DELAY * attempt)
                             continue
                
                # If we get here, we have a valid response (success or non-retriable error)
                return response
                
            except (websockets.exceptions.ConnectionClosed, 
                    websockets.exceptions.ConnectionClosedError,
                    websockets.exceptions.ConnectionClosedOK) as e:
                logger.warning(f"[RETRY] Connection closed during request: {e} (Attempt {attempt}/{config.MAX_RETRIES})")
                self.is_connected = False # Mark as disconnected to trigger reconnect next loop
                if attempt < config.MAX_RETRIES:
                     await asyncio.sleep(config.RETRY_DELAY * attempt)
                else:
                     return {"error": {"message": "Connection lost and max retries exceeded"}}
            
            except Exception as e:
                logger.error(f"[ERROR] Request exception: {e} (Attempt {attempt}/{config.MAX_RETRIES})")
                if attempt < config.MAX_RETRIES:
                    await asyncio.sleep(config.RETRY_DELAY * attempt)
                else:
                    return {"error": {"message": f"Request failed after retries: {str(e)}"}}
        
        return {"error": {"message": "Request failed: Max retries exhausted"}}
    
    async def fetch_candles(self, symbol: str, granularity: int, 
                           count: int) -> Optional[pd.DataFrame]:
        """Fetch historical candle data for any symbol"""
        try:
            request = {
                "ticks_history": symbol,
                "adjust_start_time": 1,
                "count": count,
                "end": "latest",
                "start": 1,
                "style": "candles",
                "granularity": granularity
            }
            
            response = await self.send_request(request)
            
            if "error" in response:
                logger.error(f"❌ CANDLE_FETCH_FAILED | Symbol: {symbol} | Granularity: {granularity} | Count: {count} | Reason: {response['error']['message']}")
                return None
            
            if "candles" not in response:
                logger.error(f"❌ CANDLE_RESPONSE_MISSING | Symbol: {symbol} | Expected: candles field | Got keys: {list(response.keys())}")
                return None
            
            # Parse candles
            candles = response["candles"]
            
            df = pd.DataFrame({
                'timestamp': [c['epoch'] for c in candles],
                'open': [float(c['open']) for c in candles],
                'high': [float(c['high']) for c in candles],
                'low': [float(c['low']) for c in candles],
                'close': [float(c['close']) for c in candles]
            })
            
            # Convert timestamp to datetime
            df['datetime'] = pd.to_datetime(df['timestamp'], unit='s')
            
            logger.debug(f"[OK] Fetched {len(df)} {symbol} candles ({granularity}s)")
            return df
            
        except Exception as e:
            logger.error(f"❌ CANDLE_FETCH_EXCEPTION | Symbol: {symbol} | Granularity: {granularity} | Count: {count} | Error: {type(e).__name__}: {e}", exc_info=True)
            return None
    
    async def fetch_tick(self, symbol: str) -> Optional[float]:
        """Fetch current tick price for any symbol"""
        try:
            request = {
                "ticks": symbol
            }
            
            response = await self.send_request(request)
            
            if "error" in response:
                logger.error(f"❌ TICK_FETCH_FAILED | Symbol: {symbol} | Reason: {response['error']['message']}")
                return None
            
            if "tick" in response:
                return float(response["tick"]["quote"])
            
            return None
            
        except Exception as e:
            logger.error(f"❌ TICK_FETCH_EXCEPTION | Symbol: {symbol} | Error: {type(e).__name__}: {e}", exc_info=True)
            return None
    
    async def get_balance(self) -> Optional[float]:
        """Get account balance"""
        try:
            request = {"balance": 1, "subscribe": 0}
            response = await self.send_request(request)
            
            if "error" in response:
                logger.error(f"❌ BALANCE_FETCH_FAILED | Reason: {response['error']['message']}")
                return None
            
            if "balance" in response:
                return float(response["balance"]["balance"])
            
            return None
            
        except Exception as e:
            logger.error(f"❌ BALANCE_FETCH_EXCEPTION | Error: {type(e).__name__}: {e}", exc_info=True)
            return None
    
    async def fetch_timeframe(self, symbol: str, timeframe: str, count: int = 200) -> Optional[pd.DataFrame]:
        """
        Fetch data for any timeframe for any symbol
        
        Args:
            symbol: Trading symbol (e.g., 'R_25', 'R_50')
            timeframe: Timeframe string ('1m', '5m', '15m', '1h', '4h', '1d', '1w')
            count: Number of candles to fetch
        
        Returns:
            DataFrame with OHLC data or None if failed
        """
        # Convert timeframe to Deriv granularity (seconds)
        granularity_map = {
            '1m': 60,
            '5m': 300,
            '15m': 900,
            '1h': 3600,
            '4h': 14400,
            '1d': 86400,
            '1d': 86400,
            '1w': 86400  # Hack: Fetch 1d and resample to 1w
        }
        
        # Handle 1w specially (Deriv doesn't support 1w granularity directly)
        is_weekly = (timeframe == '1w')
        if is_weekly:
            # For 1w, we fetch 1d data (7x the count to get enough days) and resample
            granularity = 86400
            fetch_count = count * 7
        else:
            if timeframe not in granularity_map:
                logger.error(f"[ERROR] Unsupported timeframe: {timeframe}")
                return None
            granularity = granularity_map[timeframe]
            fetch_count = count
        
        try:
            logger.debug(f"Fetching {fetch_count} candles ({granularity}s) for {symbol} to build {timeframe}...")
            df = await self.fetch_candles(symbol, granularity, fetch_count)
            
            if df is not None:
                if is_weekly:
                    # Resample 1d -> 1w
                    df = self._resample_to_weekly(df)
                    # Limit to requested count
                    if df is not None:
                        df = df.tail(count)
                
                logger.debug(f"[OK] Fetched {len(df)} {symbol} {timeframe} candles")
            else:
                logger.warning(f"[WARNING] Failed to fetch {symbol} {timeframe} candles")
            
            return df
            
        except Exception as e:
            logger.error(f"[ERROR] Error fetching {symbol} {timeframe} data: {e}")
            return None
    
    async def fetch_multi_timeframe_data(self, symbol: str) -> Dict[str, pd.DataFrame]:
        """
        Fetch 1m and 5m data for a symbol (legacy scalping mode)
        
        Args:
            symbol: Trading symbol
        
        Returns:
            Dictionary with '1m' and '5m' DataFrames
        """
        try:
            result = {}
            
            # Fetch 1m candles
            candles_1m = await self.fetch_candles(symbol, 60, config.CANDLES_1M)
            if candles_1m is not None:
                result['1m'] = candles_1m
            
            # Then fetch 5m candles
            candles_5m = await self.fetch_candles(symbol, 300, config.CANDLES_5M)
            if candles_5m is not None:
                result['5m'] = candles_5m
            
            return result
            
        except Exception as e:
            logger.error(f"[ERROR] Error fetching multi-timeframe data for {symbol}: {e}")
            return {}
    
    async def fetch_all_timeframes(self, symbol: str) -> Dict[str, pd.DataFrame]:
        """
        Fetch all timeframes needed for Top-Down strategy
        
        Args:
            symbol: Trading symbol (e.g., 'R_25', 'R_50')
        
        Returns:
            Dictionary with keys: '1m', '5m', '1h', '4h', '1d', '1w'
        """
        timeframes = {
            '1m': config.CANDLES_1M,
            '5m': config.CANDLES_5M,
            '1h': config.CANDLES_1H,
            '4h': config.CANDLES_4H,
            '1d': config.CANDLES_1D,
            '1w': config.CANDLES_1W
        }
        
        data = {}
        
        logger.info(f"[INFO] Fetching all timeframes for {symbol}...")
        
        for tf, count in timeframes.items():
            try:
                df = await self.fetch_timeframe(symbol, tf, count)
                if df is not None and not df.empty:
                    data[tf] = df
                else:
                    logger.warning(f"[WARNING] Empty or failed {symbol} {tf} data")
                
                # Rate limiting between requests
                await asyncio.sleep(0.3)
                
            except Exception as e:
                logger.error(f"[ERROR] Failed to fetch {symbol} {tf}: {e}")
        
        logger.info(f"[OK] Fetched {len(data)}/{len(timeframes)} timeframes for {symbol}")
        
        return data

    def _resample_to_weekly(self, df_daily: pd.DataFrame) -> Optional[pd.DataFrame]:
        """
        Resample daily data to weekly data
        Deriv weeks start on Monday (usually)
        """
        if df_daily is None or df_daily.empty:
            return None
            
        try:
            # Ensure datetime index
            df = df_daily.copy()
            df.set_index('datetime', inplace=True)
            
            # Resample to weekly (W-SUN or W-MON depending on pref, Default W-SUN is standard for many)
            # 'W' defaults to Week ending Sunday
            df_weekly = df.resample('W').agg({
                'open': 'first',
                'high': 'max',
                'low': 'min',
                'close': 'last',
                'timestamp': 'last'  # Use last timestamp of week
            })
            
            # Drop incomplete weeks if any (optional, but safer to keep all)
            df_weekly.dropna(inplace=True)
            
            # Reset index to make datetime a column again
            df_weekly.reset_index(inplace=True)
            
            return df_weekly
            
        except Exception as e:
            logger.error(f"[ERROR] Resampling failed: {e}")
            return None


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================

async def get_market_data(symbol: str = "R_25") -> Dict[str, pd.DataFrame]:
    """
    Fetch market data for a single symbol (1m + 5m)
    
    Args:
        symbol: Trading symbol
    
    Returns:
        Dictionary with '1m' and '5m' data
    """
    fetcher = DataFetcher(config.DERIV_API_TOKEN, config.DERIV_APP_ID)
    
    try:
        connected = await fetcher.connect()
        if not connected:
            return {}
        
        data = await fetcher.fetch_multi_timeframe_data(symbol)
        return data
        
    finally:
        await fetcher.disconnect()


async def get_all_timeframes_data(symbol: str = "R_25") -> Dict[str, pd.DataFrame]:
    """
    Fetch all timeframes for Top-Down analysis
    
    Args:
        symbol: Trading symbol
    
    Returns:
        Dictionary with all timeframes (1m, 5m, 1h, 4h, 1d, 1w)
    """
    fetcher = DataFetcher(config.DERIV_API_TOKEN, config.DERIV_APP_ID)
    
    try:
        connected = await fetcher.connect()
        if not connected:
            return {}
        
        data = await fetcher.fetch_all_timeframes(symbol)
        return data
        
    finally:
        await fetcher.disconnect()


async def get_multi_asset_data(symbols: List[str]) -> Dict[str, Dict[str, pd.DataFrame]]:
    """
    Fetch data for multiple assets sequentially (with rate limiting)
    
    Args:
        symbols: List of trading symbols
    
    Returns:
        Dictionary mapping symbol to timeframe data
        e.g., {'R_25': {'1m': df, '5m': df, ...}, 'R_50': {...}}
    """
    fetcher = DataFetcher(config.DERIV_API_TOKEN, config.DERIV_APP_ID)
    
    try:
        # Connect once
        connected = await fetcher.connect()
        if not connected:
            return {}
        
        result = {}
        
        # Fetch each asset sequentially to respect rate limits
        for symbol in symbols:
            logger.info(f"Fetching data for {symbol}...")
            
            if config.USE_TOPDOWN_STRATEGY:
                data = await fetcher.fetch_all_timeframes(symbol)
            else:
                data = await fetcher.fetch_multi_timeframe_data(symbol)
            
            if data:
                result[symbol] = data
            
            # Rate limiting between assets
            await asyncio.sleep(0.5)
        
        logger.info(f"Fetched data for {len(result)}/{len(symbols)} assets")
        return result
        
    finally:
        await fetcher.disconnect()


# Test function
async def test_multi_asset_fetch():
    """Test fetching data for multiple assets"""
    print("="*70)
    print("TESTING MULTI-ASSET DATA FETCHING")
    print("="*70)
    
    symbols = config.get_all_symbols()
    print(f"\nFetching data for {len(symbols)} assets: {', '.join(symbols)}\n")
    
    data = await get_multi_asset_data(symbols)
    
    print(f"\nResults:")
    for symbol, timeframes in data.items():
        print(f"\n{symbol}:")
        for tf, df in timeframes.items():
            print(f"  {tf}: {len(df)} candles")
    
    print("\n" + "="*70)
    print("TEST COMPLETE")
    print("="*70)


if __name__ == "__main__":
    asyncio.run(test_multi_asset_fetch())

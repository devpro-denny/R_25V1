"""
Trade Engine for Deriv Multi-Asset Trading Bot
Handles trade execution with dynamic multiplier selection per asset
trade_engine.py - MULTI-ASSET VERSION
"""

import asyncio
import websockets
import json
from datetime import datetime
from typing import Dict, Optional, Any
import config
from utils import setup_logger, format_currency, get_status_emoji

logger = setup_logger()

try:
    from telegram_notifier import notifier
    logger.info("âœ… Telegram notifier loaded")
except ImportError as e:
    logger.warning(f"âš ï¸ Telegram notifier not available: {e}")
    notifier = None
except Exception as e:
    logger.error(f"âŒ Error loading Telegram notifier: {e}")
    notifier = None


class TradeEngine:
    """Handles trade execution across multiple assets with dynamic multipliers"""
    
    def __init__(self, api_token: str, app_id: str = "1089", risk_mode: Optional[str] = None):
        """Initialize TradeEngine with multi-asset support"""
        self.api_token = api_token
        self.app_id = app_id
        self.ws_url = f"{config.WS_URL}?app_id={app_id}"
        self.ws = None
        self.is_connected = False
        self.active_contract_id = None
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 5
        
        # Risk mode configuration
        self.risk_mode = str(risk_mode).strip().upper() if risk_mode else getattr(config, 'RISK_MODE', 'TOP_DOWN')
        self.use_topdown_strategy = getattr(config, 'USE_TOPDOWN_STRATEGY', True)
        
        # Load asset configurations
        self.asset_configs = config.ASSET_CONFIG
        self.valid_symbols = list(self.asset_configs.keys())
        
        logger.info(f"ðŸŽ¯ Trade Engine initialized")
        logger.info(f"   Risk Mode: {self.risk_mode}")
        logger.info(f"   Exit Strategy: TP/SL Only (No Time-Based Exits)")
        logger.info(f"   Assets Configured: {len(self.valid_symbols)}")
        for symbol in self.valid_symbols:
            mult = self.asset_configs[symbol]['multiplier']
            logger.info(f"     â€¢ {symbol}: {mult}x")
        
        if self.risk_mode == "TOP_DOWN" and self.use_topdown_strategy:
            logger.info(f"   TP/SL: Dynamic (based on market structure)")
        else:
            tp_pct = getattr(config, 'TAKE_PROFIT_PERCENT', None)
            sl_pct = getattr(config, 'STOP_LOSS_PERCENT', None)
            if tp_pct is not None and sl_pct is not None:
                logger.info(f"   TP/SL: Fixed ({tp_pct}% / {sl_pct}%)")
            else:
                logger.info("   TP/SL: Strategy-defined (no global TP/SL percentages)")
    
    async def connect(self) -> bool:
        """Connect to Deriv WebSocket API"""
        try:
            logger.info(f"TradeEngine connecting to {self.ws_url}...")
            self.ws = await websockets.connect(
                self.ws_url,
                ping_interval=30,
                ping_timeout=10
            )
            self.is_connected = True
            self.reconnect_attempts = 0
            logger.info("âœ… Trade Engine connected to Deriv API")
            
            if not await self.authorize():
                logger.error("âŒ Trade Engine authorization failed")
                await self.disconnect()
                return False
                
            return True
        except Exception as e:
            logger.error(f"âŒ Failed to connect Trade Engine: {e}")
            import traceback
            logger.error(traceback.format_exc())
            self.is_connected = False
            return False
    
    async def reconnect(self) -> bool:
        """Attempt to reconnect to the API"""
        self.reconnect_attempts += 1
        if self.reconnect_attempts > self.max_reconnect_attempts:
            logger.error(f"âŒ Max reconnection attempts reached")
            return False
        
        logger.warning(f"âš ï¸ Reconnecting... (attempt {self.reconnect_attempts}/{self.max_reconnect_attempts})")
        if self.ws:
            try:
                await self.ws.close()
            except:
                pass
        
        self.is_connected = False
        await asyncio.sleep(min(2 ** self.reconnect_attempts, 30))
        return await self.connect()
    
    async def ensure_connected(self) -> bool:
        """Ensure WebSocket is connected"""
        if not self.is_connected or not self.ws or self.ws.closed:
            return await self.reconnect()
        return True
    
    async def disconnect(self):
        """Disconnect from WebSocket"""
        if self.ws:
            await self.ws.close()
            self.is_connected = False
            logger.info("ðŸ”Œ Trade Engine disconnected")
    
    async def authorize(self) -> bool:
        """Authorize connection with API token"""
        try:
            auth_request = {"authorize": self.api_token}
            await self.ws.send(json.dumps(auth_request))
            response = await self.ws.recv()
            data = json.loads(response)
            
            if "error" in data:
                logger.error(f"âŒ Authorization failed: {data['error']['message']}")
                return False
            
            if "authorize" in data:
                logger.info("âœ… Trade Engine authorized")
                return True
            return False
        except Exception as e:
            logger.error(f"âŒ Authorization error: {e}")
            return False
    
    async def send_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Send request to API and get response"""
        try:
            if not await self.ensure_connected():
                return {"error": {"message": "Failed to establish connection"}}
            
            await self.ws.send(json.dumps(request))
            response = await self.ws.recv()
            return json.loads(response)
        except (websockets.exceptions.ConnectionClosed, 
                websockets.exceptions.ConnectionClosedError) as e:
            logger.warning(f"âš ï¸ Connection closed: {e}")
            if await self.reconnect():
                try:
                    await self.ws.send(json.dumps(request))
                    response = await self.ws.recv()
                    return json.loads(response)
                except Exception as retry_error:
                    return {"error": {"message": str(retry_error)}}
            return {"error": {"message": "Connection lost"}}
        except Exception as e:
            logger.error(f"âŒ Request error: {e}")
            return {"error": {"message": str(e)}}

    async def portfolio(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Query the Deriv API for portfolio data.
        Required for RiskManager startup checks.
        """
        return await self.send_request(request_data)
    
    def get_asset_multiplier(self, symbol: str) -> int:
        """
        Get multiplier for specific asset from config
        This is the key method for multi-asset support
        
        Args:
            symbol: Trading symbol (e.g., 'R_25', 'R_50')
        
        Returns:
            Multiplier value for that asset
        """
        try:
            if symbol not in self.asset_configs:
                logger.error(f"âŒ Unknown symbol: {symbol}")
                logger.warning(f"   Valid symbols: {', '.join(self.valid_symbols)}")
                # Fallback to default
                return getattr(config, 'MULTIPLIER', 160)
            
            multiplier = self.asset_configs[symbol]['multiplier']
            logger.debug(f"âœ… {symbol} â†’ {multiplier}x multiplier")
            return multiplier
            
        except Exception as e:
            logger.error(f"âŒ Failed to get multiplier for {symbol}: {e}")
            return getattr(config, 'MULTIPLIER', 160)
    
    def validate_symbol(self, symbol: str) -> bool:
        """
        Validate that a symbol is configured and supported
        
        Args:
            symbol: Trading symbol
        
        Returns:
            True if valid, False otherwise
        """
        if symbol not in self.valid_symbols:
            logger.error(f"âŒ Invalid symbol: {symbol}")
            logger.info(f"   Valid symbols: {', '.join(self.valid_symbols)}")
            return False
        return True
    
    async def get_proposal(self, direction: str, stake: float, symbol: str) -> Optional[Dict]:
        """
        Get a trade proposal for any configured asset
        
        Args:
            direction: 'UP' or 'DOWN'
            stake: Stake amount
            symbol: Trading symbol (must be in ASSET_CONFIG)
        
        Returns:
            Proposal dict with id, price, and multiplier, or None if failed
        """
        try:
            # Validate symbol
            if not self.validate_symbol(symbol):
                return None
            
            if direction.upper() in ['UP', 'BUY']:
                contract_type = config.CONTRACT_TYPE
            else:
                contract_type = config.CONTRACT_TYPE_DOWN
            
            # Get asset-specific multiplier
            multiplier = self.get_asset_multiplier(symbol)
            
            # Build proposal request
            proposal_request = {
                "proposal": 1,
                "amount": stake,
                "basis": "stake",
                "contract_type": contract_type,
                "currency": "USD",
                "multiplier": multiplier,
                "symbol": symbol
            }
            
            logger.debug(f"ðŸ“‹ Requesting proposal for {symbol} ({multiplier}x multiplier)...")
            response = await self.send_request(proposal_request)
            
            if "error" in response:
                logger.error(f"âŒ PROPOSAL_REQUEST_FAILED | Symbol: {symbol} | Multiplier: {multiplier}x | Stake: ${stake:.2f} | Reason: {response['error']['message']}")
                return None
            
            if "proposal" not in response:
                logger.error(f"âŒ PROPOSAL_FIELD_MISSING | Symbol: {symbol} | Expected: proposal field | Got keys: {list(response.keys())}")
                return None
            
            proposal = response["proposal"]
            
            return {
                "id": proposal.get("id"),
                "ask_price": float(proposal.get("ask_price", stake)),
                "payout": float(proposal.get("payout", 0)),
                "spot": float(proposal.get("spot", 0)),
                "multiplier": multiplier,
                "symbol": symbol
            }
            
        except Exception as e:
            logger.error(f"âŒ PROPOSAL_REQUEST_EXCEPTION | Symbol: {symbol} | Error: {type(e).__name__}: {e}", exc_info=True)
            return None
    
    async def buy_with_proposal(self, proposal_id: str, price: float) -> Optional[Dict]:
        """Buy a contract using a proposal ID"""
        try:
            # Add 10% tolerance, rounded to 2 decimals
            max_price = round(price * 1.10, 2)
            
            buy_request = {
                "buy": proposal_id,
                "price": max_price
            }
            
            logger.debug(f"ðŸ’³ Buying contract (max price: {format_currency(max_price)})...")
            response = await self.send_request(buy_request)
            
            if "error" in response:
                error_msg = response['error'].get('message', 'Unknown error')
                
                if "moved too much" in error_msg.lower() or "payout has changed" in error_msg.lower():
                    logger.warning(f"âš ï¸ BUY_PRICE_CHANGED | Proposal: {proposal_id} | Max Price: ${max_price:.2f} | Reason: {error_msg}")
                    return None  # Signal to retry
                
                logger.error(f"âŒ BUY_PROPOSAL_FAILED | Proposal: {proposal_id} | Max Price: ${max_price:.2f} | Reason: {error_msg}")
                return None
            
            if "buy" not in response:
                logger.error(f"âŒ BUY_RESPONSE_MISSING_FIELD | Proposal: {proposal_id} | Expected: buy field | Got keys: {list(response.keys())}")
                return None
            
            return response["buy"]
            
        except Exception as e:
            logger.error(f"âŒ BUY_PROPOSAL_EXCEPTION | Proposal: {proposal_id} | Max Price: ${max_price:.2f} | Error: {type(e).__name__}: {e}", exc_info=True)
            return None
    
    async def apply_tp_sl_limits(self, contract_id: str, tp_price: float, sl_price: float, 
                                  entry_spot: float, multiplier: int, stake: float) -> bool:
        """
        Apply Take Profit and Stop Loss limits to an open multiplier contract
        
        Args:
            contract_id: Contract ID
            tp_price: Take Profit price level
            sl_price: Stop Loss price level
            entry_spot: Entry spot price
            multiplier: Contract multiplier
            stake: Stake amount
        
        Note: For multiplier contracts, we can use stop_loss and take_profit as barrier price levels
              OR as profit/loss amounts. Deriv API accepts both formats.
        """
        try:
            if entry_spot <= 0:
                logger.error(f"âŒ INVALID_ENTRY_SPOT | Contract: {contract_id} | Entry Spot: {entry_spot} | TP: {tp_price:.4f} | SL: {sl_price:.4f} | Multiplier: {multiplier}x | Stake: ${stake:.2f} | Root cause: Entry spot must be > 0")
                return False

            # For multiplier contracts, calculate profit/loss amounts
            # Formula based on Deriv's multiplier calculation:
            # Profit = (Price_Change / Entry_Price) * Stake * Multiplier
            
            # Calculate TP amount (profit when price hits TP level)
            price_change_tp = tp_price - entry_spot
            tp_amount = abs((price_change_tp / entry_spot) * stake * multiplier)
            
            # Calculate SL amount (loss when price hits SL level)  
            price_change_sl = sl_price - entry_spot
            sl_amount = abs((price_change_sl / entry_spot) * stake * multiplier)
            
            # Validation for infinite values
            import math
            if math.isinf(tp_amount) or math.isnan(tp_amount) or math.isinf(sl_amount) or math.isnan(sl_amount):
                 logger.error(f"âŒ TP/SL_CALCULATION_ERROR | Contract: {contract_id} | Entry: {entry_spot:.4f} | TP Price: {tp_price:.4f} | SL Price: {sl_price:.4f} | Multiplier: {multiplier}x | Stake: ${stake:.2f} | TP Amount: {tp_amount} | SL Amount: {sl_amount}")
                 return False

            # CRITICAL: SL cannot exceed stake amount on Deriv multipliers
            # If SL exceeds stake, recalculate SL price to fit within constraints
            if sl_amount > stake:
                logger.warning(f"âš ï¸ Stop Loss exceeds stake amount (SL: ${sl_amount:.2f} > Stake: ${stake:.2f})")
                logger.warning(f"   Adjusting SL to maximum allowable loss of ${stake:.2f}")
                
                # Recalculate SL price based on max loss = stake
                # Formula: SL_price = Entry Â± (MaxLoss / (Stake * Multiplier)) * Entry
                max_loss_pct = stake / (stake * multiplier)
                if sl_price < entry_spot:  # DOWN trade
                    sl_price = entry_spot * (1 - max_loss_pct)
                else:  # UP trade
                    sl_price = entry_spot * (1 + max_loss_pct)
                
                # Recalculate SL amount with new price
                price_change_sl = sl_price - entry_spot
                sl_amount = abs((price_change_sl / entry_spot) * stake * multiplier)
                
                logger.info(f"âœ… SL adjusted: {sl_price:.4f} â†’ ${sl_amount:.2f} loss (was exceeding ${stake:.2f} limit)")

            logger.info(f"ðŸŽ¯ Applying TP/SL: TP ${tp_amount:.2f} @ {tp_price:.4f} | SL ${sl_amount:.2f} @ {sl_price:.4f} (Max: ${stake:.2f})")
            
            # Build limit order request
            # Use contract_update instead of limit_order for open contracts
            # Ensure values are strictly Python floats (not numpy types)
            limit_request = {
                "contract_update": 1,
                "contract_id": int(contract_id),  # Ensure int
                "limit_order": {
                    "take_profit": float(round(tp_amount, 2)),
                    "stop_loss": float(round(sl_amount, 2))  # Positive amount for contract_update
                }
            }
            
            logger.debug(f"ðŸ“¤ Sending limit order to Deriv...")
            response = await self.send_request(limit_request)
            
            if "error" in response:
                logger.error(f"âŒ TP/SL_APPLY_FAILED | Contract: {contract_id} | TP Amount: ${tp_amount:.2f} @ {tp_price:.4f} | SL Amount: ${sl_amount:.2f} @ {sl_price:.4f} | Error: {response['error']['message']} | Code: {response['error'].get('code', 'N/A')}")
                return False
            
            if "contract_update" in response:
                logger.info(f"âœ… TP/SL applied: TP ${tp_amount:.2f} @ {tp_price:.4f} | SL ${sl_amount:.2f} @ {sl_price:.4f}")
                return True
            else:
                logger.warning(f"âš ï¸ Unexpected response format: {response}")
                return False
            
        except Exception as e:
            logger.error(f"âŒ TP/SL_APPLY_EXCEPTION | Contract: {contract_id} | TP: ${tp_amount:.2f} | SL: ${sl_amount:.2f} | Error: {type(e).__name__}: {e}", exc_info=True)
            return False
    
    async def remove_take_profit(self, contract_id: str) -> bool:
        """
        Remove the server-side Take Profit from an open contract.
        
        Used by the trailing profit feature: once trailing activates,
        we cancel Deriv's fixed TP so the monitoring loop handles exit instead.
        The Stop Loss remains intact.
        """
        try:
            # Deriv API: pass null/0 to cancel take_profit
            cancel_tp_request = {
                "contract_update": 1,
                "contract_id": int(contract_id),
                "limit_order": {
                    "take_profit": None  # Cancel TP
                }
            }
            
            logger.info(f"ðŸ“ˆ Removing server-side TP for contract {contract_id} (trailing profit takeover)")
            response = await self.send_request(cancel_tp_request)
            
            if "error" in response:
                logger.error(f"âŒ Failed to remove TP: {response['error']['message']}")
                return False
            
            if "contract_update" in response:
                logger.info(f"âœ… Server-side TP removed for {contract_id} â€” trailing profit now controls exit")
                return True
            else:
                logger.warning(f"âš ï¸ Unexpected response when removing TP: {response}")
                return False
        except Exception as e:
            logger.error(f"âŒ Error removing TP: {e}")
            return False
    
    async def open_trade(self, direction: str, stake: float, symbol: str,
                        tp_price: Optional[float] = None,
                        sl_price: Optional[float] = None,
                        max_retries: int = 3) -> Optional[Dict]:
        """
        Open a multiplier trade on any configured asset
        
        Args:
            direction: 'UP' or 'DOWN'
            stake: Stake amount
            symbol: Trading symbol (from ASSET_CONFIG)
            tp_price: Take Profit price level
            sl_price: Stop Loss price level
            max_retries: Max retries on price changes
        
        Returns:
            Trade info dict or None if failed
        """
        # Validate symbol
        if not self.validate_symbol(symbol):
            logger.error(f"âŒ Cannot open trade: Invalid symbol {symbol}")
            print("FINAL DECISION: âŒ EXECUTION FAILED")
            print("Blocked By: TRADE ENGINE (Invalid Symbol)")
            return None
            
        print("\n[EXECUTION] ðŸš€ Connecting to Deriv for Execution...")
        
        # 1. Connection Check
        if not (self.is_connected and self.ws and not self.ws.closed):
             print("[EXECUTION] âŒ DISCONNECTED - Attempting Reconnect...")
             if not await self.reconnect():
                 print("[EXECUTION] âŒ Reconnect Failed")
                 print("FINAL DECISION: âŒ EXECUTION FAILED")
                 print("Blocked By: TRADE ENGINE (Connection Lost)")
                 return None
        
        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    logger.info(f"ðŸ”„ Retry attempt {attempt + 1}/{max_retries}")
                    await asyncio.sleep(0.5)
                
                # Get proposal for this specific asset
                # print(f"[EXECUTION] ðŸ“‹ Fetching Proposal ({attempt+1}/{max_retries})...")
                proposal = await self.get_proposal(direction, stake, symbol)
                if not proposal:
                    print(f"[EXECUTION] âŒ Proposal Failed ({attempt+1}/{max_retries})")
                    logger.error(f"âŒ Failed to get proposal for {symbol}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(1) # Backoff
                        continue
                    print("FINAL DECISION: âŒ EXECUTION FAILED")
                    print("Blocked By: TRADE ENGINE (Proposal Failed)")
                    return None
                 
                print(f"[EXECUTION] âœ… Proposal Received: {proposal.get('multiplier')}x Multiplier")
                
                proposal_id = proposal["id"]
                ask_price = proposal["ask_price"]
                multiplier = proposal["multiplier"]
                
                logger.info(f"âœ… Got proposal for {symbol}: ID={proposal_id}, Multiplier={multiplier}x, Price={format_currency(ask_price)}")
                
                # Buy using proposal ID
                buy_info = await self.buy_with_proposal(proposal_id, ask_price)
                
                if not buy_info:
                    if attempt < max_retries - 1:
                        logger.warning("âš ï¸ Price moved, retrying...")
                        print(f"[EXECUTION] âš ï¸ Price Unstable, Retrying ({attempt+1})...")
                        await asyncio.sleep(0.5)
                        continue
                    logger.error(f"âŒ Failed to buy {symbol} after all retries")
                    print("FINAL DECISION: âŒ EXECUTION FAILED")
                    return None
                
                print("[EXECUTION] âœ… Price Stable - Buy Confirmed")
                
                # Success! Extract trade info
                contract_id = buy_info["contract_id"]
                entry_price = float(buy_info.get("buy_price", stake))
                entry_spot = float(buy_info.get("entry_spot", 0))

                # Fallback to proposal spot if entry_spot is invalid (failed to fetch on buy)
                if entry_spot == 0:
                   logger.warning(f"âš ï¸ Zero entry_spot in buy response, falling back to proposal spot: {proposal.get('spot', 0)}")
                   entry_spot = float(proposal.get('spot', 0))
                
                longcode = buy_info.get("longcode", "")
                
                self.active_contract_id = contract_id
                
                # Build trade info
                trade_info = {
                    'contract_id': contract_id,
                    'direction': direction,
                    'symbol': symbol,
                    'stake': stake,
                    'entry_price': entry_price,
                    'entry_spot': entry_spot,
                    'take_profit': tp_price,
                    'stop_loss': sl_price,
                    'multiplier': multiplier,
                    'contract_type': config.CONTRACT_TYPE if direction.upper() in ['UP', 'BUY'] else config.CONTRACT_TYPE_DOWN,
                    'open_time': datetime.now(),
                    'status': 'open',
                    'longcode': longcode,
                    'risk_mode': self.risk_mode
                }
                
                logger.info(f"âœ… Trade opened: {symbol} {direction} @ {entry_spot:.4f} | Contract: {contract_id}")
                
                print("\n" + "="*50)
                print("FINAL DECISION: âœ… TRADE EXECUTED")
                print(f"Contract ID: {contract_id}")
                print(f"Entry Price: {entry_spot}")
                print("="*50 + "\n")
                
                # Apply TP/SL limits if provided
                if tp_price and sl_price:
                    logger.debug(f"TP: {tp_price:.4f} | SL: {sl_price:.4f}")
                    
                    # Validate R:R ratio
                    distance_to_tp = abs(tp_price - entry_spot)
                    distance_to_sl = abs(entry_spot - sl_price)
                    
                    if distance_to_sl > 0:
                        rr_ratio = distance_to_tp / distance_to_sl
                        if rr_ratio < config.MIN_RR_RATIO:
                            logger.warning(f"âš ï¸ R:R ratio {rr_ratio:.2f} below minimum {config.MIN_RR_RATIO}")
                    
                    # Apply the limits with proper parameter conversion
                    await self.apply_tp_sl_limits(
                        contract_id, 
                        tp_price, 
                        sl_price,
                        entry_spot,
                        multiplier,
                        stake
                    )
                else:
                    logger.warning("âš ï¸ No TP/SL provided - trade will run without limits!")
                
                if notifier is not None:
                    try:
                        await notifier.notify_trade_opened(trade_info)
                    except Exception as e:
                        logger.error(f"âŒ Telegram notification failed: {e}")
                
                return trade_info
                
            except Exception as e:
                logger.error(f"âŒ Error in open_trade for {symbol} (attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    continue
                import traceback
                logger.error(traceback.format_exc())
                return None
        
        return None
    
    async def get_trade_status(self, contract_id: str) -> Optional[Dict]:
        """Get current status of a trade"""
        try:
            proposal_request = {
                "proposal_open_contract": 1,
                "contract_id": contract_id
            }
            
            response = await self.send_request(proposal_request)
            
            if "error" in response:
                logger.error(f"âŒ Failed to get trade status: {response['error']['message']}")
                return None
            
            if "proposal_open_contract" not in response:
                return None
            
            contract = response["proposal_open_contract"]
            trade_status = contract.get('status', None)
            is_sold = contract.get('is_sold', 0) == 1
            profit = float(contract.get('profit', 0))
            
            # Determine trade status
            if trade_status is None or trade_status == '' or trade_status == 'unknown':
                if is_sold:
                    if profit > 0:
                        trade_status = 'won'
                    elif profit < 0:
                        trade_status = 'lost'
                    else:
                        trade_status = 'sold'
                else:
                    trade_status = 'open'
            
            status_info = {
                'contract_id': contract_id,
                'status': trade_status,
                'current_spot': float(contract.get('current_spot', 0)),
                'entry_spot': float(contract.get('entry_spot', 0)),
                'profit': profit,
                'bid_price': float(contract.get('bid_price', 0)),
                'buy_price': float(contract.get('buy_price', 0)),
                'is_sold': is_sold,
                'date_start': contract.get('date_start'),
                'sell_time': contract.get('sell_time')
            }
            
            return status_info
        except Exception as e:
            logger.error(f"âŒ Error getting trade status: {e}")
            return None
    
    async def monitor_trade(self, contract_id: str, trade_info: Dict,
                          risk_manager=None) -> Optional[Dict]:
        """Monitor trade until TP/SL hit (no time-based exits)"""
        try:
            start_time = datetime.now()
            symbol = trade_info.get('symbol', 'Unknown')
            
            logger.info(f"ðŸ‘ï¸ Monitoring trade on {symbol}")
            logger.info(f"   Exit Strategy: TP/SL Only")
            logger.info(f"   No time-based exits - waiting for price levels")
            
            monitor_interval = config.MONITOR_INTERVAL
            last_status_log = datetime.now()
            status_log_interval = 30
            previous_spot = trade_info.get('entry_spot', 0.0)
            
            while True:
                elapsed = (datetime.now() - start_time).total_seconds()
                
                # Get current trade status
                status = await self.get_trade_status(contract_id)
                if not status:
                    await asyncio.sleep(monitor_interval)
                    continue
                
                # Check if risk manager wants to close
                if risk_manager:
                    # Strategy-specific exits are capability-driven to avoid hard
                    # coupling to any specific strategy module.
                    if (
                        hasattr(risk_manager, "check_trailing_profit")
                        and hasattr(risk_manager, "check_stagnation_exit")
                    ):
                        trade_info = risk_manager.get_active_trade_info()
                        if trade_info and trade_info.get('contract_id') == contract_id:
                            current_pnl = status['profit']
                            # CHECK 1: Trailing profit exit
                            should_trail_exit, trail_reason, just_activated = risk_manager.check_trailing_profit(trade_info, current_pnl)
                            if just_activated:
                                try:
                                    await self.remove_take_profit(contract_id)
                                except Exception as e:
                                    logger.error(f"âŒ Failed to remove server-side TP for trailing: {e}")
                            if should_trail_exit:
                                exit_check = {'should_close': True, 'reason': trail_reason, 'message': f'Trailing profit exit: {trail_reason}'}
                            else:
                                # CHECK 2: Stagnation exit
                                should_exit, exit_reason = risk_manager.check_stagnation_exit(trade_info, current_pnl)
                                if should_exit:
                                    exit_check = {'should_close': True, 'reason': exit_reason, 'message': f'Stagnation exit: {exit_reason}'}
                                else:
                                    exit_check = risk_manager.should_close_trade(
                                        contract_id, status['profit'], status['current_spot'], previous_spot
                                    )
                        else:
                            exit_check = risk_manager.should_close_trade(
                                contract_id, status['profit'], status['current_spot'], previous_spot
                            )
                    else:
                            exit_check = risk_manager.should_close_trade(
                                contract_id,  # Pass contract_id to identify which trade
                                status['profit'],
                                status['current_spot'],
                                previous_spot
                        )
                    
                    if exit_check.get('should_close'):
                        logger.info(f"ðŸŽ¯ Risk Manager: {exit_check['message']}")
                        await self.close_trade(contract_id)
                        await asyncio.sleep(2)
                        final_status = await self.get_trade_status(contract_id)
                        if not final_status:
                            final_status = {
                                "contract_id": contract_id,
                                "status": "sold",
                                "profit": status.get("profit", 0.0),
                            }

                        final_status['exit_reason'] = exit_check['reason']
                        final_status['symbol'] = symbol  # Ensure symbol is returned
                        # Removed redundant notification here - Runner handles it
                        # Calculate duration for risk manager exit
                        duration = 0
                        if final_status.get('date_start') and final_status.get('sell_time'):
                             try:
                                 duration = int(final_status['sell_time']) - int(final_status['date_start'])
                             except:
                                 duration = int(elapsed)
                        else:
                            duration = int(elapsed)
                        
                        final_status['duration'] = duration
                        return final_status
                    
                    previous_spot = status['current_spot']
                
                # Check if trade has closed (TP/SL hit)
                if status['is_sold'] or status['status'] in ['sold', 'won', 'lost']:
                    trade_status = status.get('status', 'closed')
                    final_pnl = status.get('profit', 0)
                    
                    # Normalize status
                    if trade_status in [None, '', 'unknown', 'closed']:
                        if final_pnl > 0:
                            trade_status = 'won'
                        elif final_pnl < 0:
                            trade_status = 'lost'
                        else:
                            trade_status = 'sold'
                    
                    duration = 0
                    if status.get('date_start') and status.get('sell_time'):
                         try:
                             duration = int(status['sell_time']) - int(status['date_start'])
                         except:
                             duration = int(elapsed)
                    else:
                        duration = int(elapsed)

                    emoji = get_status_emoji(trade_status)
                    logger.info(f"{emoji} Trade closed | Status: {trade_status.upper()}")
                    logger.info(f"   Symbol: {symbol}")
                    logger.info(f"   Final P&L: {format_currency(status['profit'])}")
                    logger.info(f"   Duration: {duration}s")
                    
                    # Removed redundant notification here - Runner handles it
                    
                    status['symbol'] = symbol  # Ensure symbol is returned
                    status['duration'] = duration
                    return status
                
                # Periodic status logging
                time_since_last_log = (datetime.now() - last_status_log).total_seconds()
                if time_since_last_log >= status_log_interval:
                    pnl_emoji = "ðŸ“ˆ" if status['profit'] >= 0 else "ðŸ“‰"
                    logger.info(f"{pnl_emoji} {symbol}: {format_currency(status['profit'])} | "
                              f"Spot: {status['current_spot']:.4f} | {int(elapsed)}s")
                    last_status_log = datetime.now()
                
                await asyncio.sleep(monitor_interval)
                
        except Exception as e:
            logger.error(f"âŒ Error monitoring trade: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    async def close_trade(self, contract_id: str) -> Optional[Dict]:
        """Manually close an active trade"""
        try:
            sell_request = {"sell": contract_id, "price": 0}
            
            logger.info(f"ðŸ“¤ Manually closing trade {contract_id}...")
            response = await self.send_request(sell_request)
            
            if "error" in response:
                logger.error(f"âŒ Failed to close: {response['error']['message']}")
                return None
            
            if "sell" not in response:
                logger.error("âŒ Invalid close response")
                return None
            
            sell_info = response["sell"]
            sold_for = float(sell_info.get("sold_for", 0))
            
            close_info = {
                'contract_id': contract_id,
                'sold_for': sold_for,
                'close_time': datetime.now()
            }
            
            logger.info(f"âœ… Trade closed | Sold for: {format_currency(sold_for)}")
            self.active_contract_id = None
            return close_info
        except Exception as e:
            logger.error(f"âŒ Error closing trade: {e}")
            return None

    def _unlock_trade_slot_on_failure(self, risk_manager, contract_id: Optional[str]) -> None:
        """
        Best-effort cleanup for partially failed lifecycle states.
        Supports both conservative (dict-tracked) and scalping (id-tracked)
        active trade structures without cross-strategy assumptions.
        """
        if not risk_manager or not hasattr(risk_manager, "active_trades") or not contract_id:
            return

        active_trades = getattr(risk_manager, "active_trades", [])
        if not isinstance(active_trades, list):
            return

        if active_trades and isinstance(active_trades[0], dict):
            filtered = [t for t in active_trades if t.get("contract_id") != contract_id]
        else:
            filtered = [t for t in active_trades if t != contract_id]

        try:
            risk_manager.active_trades = filtered
        except Exception:
            # Conservative wrapper exposes read-only property; update wrapped manager.
            if hasattr(risk_manager, "risk_manager") and hasattr(risk_manager.risk_manager, "active_trades"):
                risk_manager.risk_manager.active_trades = filtered
    
    async def execute_trade(self, signal: Dict, risk_manager) -> Optional[Dict]:
        """
        Execute complete trade cycle with TP/SL only exits
        
        Args:
            signal: Trading signal dict with:
                - 'signal': 'UP' or 'DOWN'
                - 'symbol': Trading symbol (e.g., 'R_25', 'R_50')
                - 'take_profit': TP price level
                - 'stop_loss': SL price level
            risk_manager: Risk manager instance
        
        Returns:
            Final trade result or None if failed
        """
        contract_id = None
        try:
            direction = signal['signal']
            symbol = signal.get('symbol', config.SYMBOLS[0])
            
            # Validate symbol
            if not self.validate_symbol(symbol):
                logger.error(f"âŒ Invalid symbol in signal: {symbol}")
                return None
            
            # Validate TP/SL
            tp_price = signal.get('take_profit')
            sl_price = signal.get('stop_loss')
            
            if not tp_price or not sl_price:
                logger.error("âŒ Missing TP/SL - trades require both")
                return None
            
            # Open trade on specified asset
            # CRITICAL: Use stake from signal if available (passed from BotRunner)
            # Fallback to config.FIXED_STAKE only if signal doesn't provide it
            trade_stake = signal.get('stake', config.FIXED_STAKE)
            
            if not trade_stake:
                logger.error(f"âŒ Missing stake amount for {symbol}")
                return None
                
            trade_info = await self.open_trade(
                direction=direction,
                stake=trade_stake,
                symbol=symbol,
                tp_price=tp_price,
                sl_price=sl_price
            )
            
            if not trade_info:
                logger.error(f"âŒ Failed to open trade on {symbol}")
                return None

            contract_id = trade_info.get("contract_id")
            
            # Record with risk manager
            risk_manager.record_trade_open(trade_info)
            
            # Monitor trade until TP/SL hit
            final_status = await self.monitor_trade(
                trade_info['contract_id'],
                trade_info,
                risk_manager=risk_manager
            )
            
            if final_status:
                # Enrich with initial trade info for DB persistence
                final_status['stake'] = trade_info['stake']
                final_status['entry_price'] = trade_info['entry_spot']
                final_status['exit_price'] = final_status.get('current_spot')
                final_status['direction'] = direction
                final_status['symbol'] = symbol
                # Duration is already in final_status from monitor_trade
                # Ensure alias 'signal' is present (DB uses 'signal')
                final_status['signal'] = direction
                
                # Ensure timestamp is present for DB (use sell_time if available, else now)
                if final_status.get('sell_time'):
                    final_status['timestamp'] = datetime.fromtimestamp(int(final_status['sell_time']))
                else:
                    final_status['timestamp'] = datetime.now()
            
            if final_status is None:
                logger.error("âŒ Monitoring failed - unlocking trade slot")
                self._unlock_trade_slot_on_failure(risk_manager, contract_id)
            
            return final_status
            
        except Exception as e:
            logger.error(f"âŒ Error executing trade: {e}")
            import traceback
            logger.error(traceback.format_exc())
            
            try:
                self._unlock_trade_slot_on_failure(risk_manager, contract_id)
                logger.info("ðŸ”“ Trade slot unlocked after error")
            except:
                pass
            
            return None

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
    logger.info("✅ Telegram notifier loaded")
except ImportError as e:
    logger.warning(f"⚠️ Telegram notifier not available: {e}")
    notifier = None
except Exception as e:
    logger.error(f"❌ Error loading Telegram notifier: {e}")
    notifier = None


class TradeEngine:
    """Handles trade execution across multiple assets with dynamic multipliers"""
    
    def __init__(self, api_token: str, app_id: str = "1089"):
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
        self.risk_mode = getattr(config, 'RISK_MODE', 'TOP_DOWN')
        self.use_topdown_strategy = getattr(config, 'USE_TOPDOWN_STRATEGY', True)
        
        # Load asset configurations
        self.asset_configs = config.ASSET_CONFIG
        self.valid_symbols = list(self.asset_configs.keys())
        
        logger.info(f"🎯 Trade Engine initialized")
        logger.info(f"   Risk Mode: {self.risk_mode}")
        logger.info(f"   Exit Strategy: TP/SL Only (No Time-Based Exits)")
        logger.info(f"   Assets Configured: {len(self.valid_symbols)}")
        for symbol in self.valid_symbols:
            mult = self.asset_configs[symbol]['multiplier']
            logger.info(f"     • {symbol}: {mult}x")
        
        if self.risk_mode == "TOP_DOWN" and self.use_topdown_strategy:
            logger.info(f"   TP/SL: Dynamic (based on market structure)")
        else:
            logger.info(f"   TP/SL: Fixed ({config.TAKE_PROFIT_PERCENT}% / {config.STOP_LOSS_PERCENT}%)")
    
    async def connect(self) -> bool:
        """Connect to Deriv WebSocket API"""
        try:
            self.ws = await websockets.connect(
                self.ws_url,
                ping_interval=30,
                ping_timeout=10
            )
            self.is_connected = True
            self.reconnect_attempts = 0
            logger.info("✅ Trade Engine connected to Deriv API")
            await self.authorize()
            return True
        except Exception as e:
            logger.error(f"❌ Failed to connect Trade Engine: {e}")
            self.is_connected = False
            return False
    
    async def reconnect(self) -> bool:
        """Attempt to reconnect to the API"""
        self.reconnect_attempts += 1
        if self.reconnect_attempts > self.max_reconnect_attempts:
            logger.error(f"❌ Max reconnection attempts reached")
            return False
        
        logger.warning(f"⚠️ Reconnecting... (attempt {self.reconnect_attempts}/{self.max_reconnect_attempts})")
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
            logger.info("🔌 Trade Engine disconnected")
    
    async def authorize(self) -> bool:
        """Authorize connection with API token"""
        try:
            auth_request = {"authorize": self.api_token}
            await self.ws.send(json.dumps(auth_request))
            response = await self.ws.recv()
            data = json.loads(response)
            
            if "error" in data:
                logger.error(f"❌ Authorization failed: {data['error']['message']}")
                return False
            
            if "authorize" in data:
                logger.info("✅ Trade Engine authorized")
                return True
            return False
        except Exception as e:
            logger.error(f"❌ Authorization error: {e}")
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
            logger.warning(f"⚠️ Connection closed: {e}")
            if await self.reconnect():
                try:
                    await self.ws.send(json.dumps(request))
                    response = await self.ws.recv()
                    return json.loads(response)
                except Exception as retry_error:
                    return {"error": {"message": str(retry_error)}}
            return {"error": {"message": "Connection lost"}}
        except Exception as e:
            logger.error(f"❌ Request error: {e}")
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
                logger.error(f"❌ Unknown symbol: {symbol}")
                logger.warning(f"   Valid symbols: {', '.join(self.valid_symbols)}")
                # Fallback to default
                return getattr(config, 'MULTIPLIER', 160)
            
            multiplier = self.asset_configs[symbol]['multiplier']
            logger.debug(f"✅ {symbol} → {multiplier}x multiplier")
            return multiplier
            
        except Exception as e:
            logger.error(f"❌ Failed to get multiplier for {symbol}: {e}")
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
            logger.error(f"❌ Invalid symbol: {symbol}")
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
            
            logger.debug(f"📋 Requesting proposal for {symbol} ({multiplier}x multiplier)...")
            response = await self.send_request(proposal_request)
            
            if "error" in response:
                logger.error(f"❌ Proposal failed for {symbol}: {response['error']['message']}")
                return None
            
            if "proposal" not in response:
                logger.error(f"❌ Invalid proposal response for {symbol}")
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
            logger.error(f"❌ Error getting proposal for {symbol}: {e}")
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
            
            logger.debug(f"💳 Buying contract (max price: {format_currency(max_price)})...")
            response = await self.send_request(buy_request)
            
            if "error" in response:
                error_msg = response['error'].get('message', 'Unknown error')
                
                if "moved too much" in error_msg.lower() or "payout has changed" in error_msg.lower():
                    logger.warning(f"⚠️ Price changed: {error_msg}")
                    return None  # Signal to retry
                
                logger.error(f"❌ Buy failed: {error_msg}")
                return None
            
            if "buy" not in response:
                logger.error("❌ Invalid buy response")
                return None
            
            return response["buy"]
            
        except Exception as e:
            logger.error(f"❌ Error buying contract: {e}")
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
                logger.error(f"❌ Cannot apply limits: Invalid entry spot {entry_spot}")
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
                 logger.error(f"❌ invalid TP/SL calculation result: TP={tp_amount}, SL={sl_amount}")
                 return False

            # CRITICAL: SL cannot exceed stake amount on Deriv multipliers
            # If SL exceeds stake, recalculate SL price to fit within constraints
            if sl_amount > stake:
                logger.warning(f"⚠️ Stop Loss exceeds stake amount (SL: ${sl_amount:.2f} > Stake: ${stake:.2f})")
                logger.warning(f"   Adjusting SL to maximum allowable loss of ${stake:.2f}")
                
                # Recalculate SL price based on max loss = stake
                # Formula: SL_price = Entry ± (MaxLoss / (Stake * Multiplier)) * Entry
                max_loss_pct = stake / (stake * multiplier)
                if sl_price < entry_spot:  # DOWN trade
                    sl_price = entry_spot * (1 - max_loss_pct)
                else:  # UP trade
                    sl_price = entry_spot * (1 + max_loss_pct)
                
                # Recalculate SL amount with new price
                price_change_sl = sl_price - entry_spot
                sl_amount = abs((price_change_sl / entry_spot) * stake * multiplier)
                
                logger.info(f"✅ SL adjusted to fit constraints")
                logger.info(f"   New SL Price: {sl_price:.4f}")
                logger.info(f"   New SL Loss: ${sl_amount:.2f}")

            logger.info(f"🎯 Applying TP/SL for multiplier contract...")
            logger.info(f"   Entry Spot: {entry_spot:.4f}")
            logger.info(f"   Multiplier: {multiplier}x")
            logger.info(f"   Stake: ${stake:.2f}")
            logger.info(f"   TP Level: {tp_price:.4f} → Profit: ${tp_amount:.2f}")
            logger.info(f"   SL Level: {sl_price:.4f} → Loss: ${sl_amount:.2f} (Max Allowed: ${stake:.2f})")
            
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
            
            logger.info(f"📤 Sending limit order (contract_update) to Deriv...")
            logger.debug(f"   Request: {limit_request}")
            response = await self.send_request(limit_request)
            
            if "error" in response:
                logger.error(f"❌ Failed to apply limits: {response['error']['message']}")
                logger.error(f"   Error code: {response['error'].get('code', 'N/A')}")
                logger.error(f"   Request sent: {limit_request}")
                return False
            
            if "contract_update" in response:
                logger.info(f"✅ TP/SL limits applied successfully!")
                logger.info(f"   Take Profit: ${tp_amount:.2f} at {tp_price:.4f}")
                logger.info(f"   Stop Loss: ${sl_amount:.2f} at {sl_price:.4f}")
                return True
            else:
                logger.warning(f"⚠️ Unexpected response format: {response}")
                return False
            
        except Exception as e:
            logger.error(f"❌ Error applying limits: {e}")
            import traceback
            logger.error(traceback.format_exc())
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
            logger.error(f"❌ Cannot open trade: Invalid symbol {symbol}")
            return None
        
        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    logger.info(f"🔄 Retry attempt {attempt + 1}/{max_retries}")
                    await asyncio.sleep(0.5)
                
                # Get proposal for this specific asset
                proposal = await self.get_proposal(direction, stake, symbol)
                if not proposal:
                    logger.error(f"❌ Failed to get proposal for {symbol}")
                    if attempt < max_retries - 1:
                        continue
                    return None
                
                proposal_id = proposal["id"]
                ask_price = proposal["ask_price"]
                multiplier = proposal["multiplier"]
                
                logger.info(f"✅ Got proposal for {symbol}: ID={proposal_id}, Multiplier={multiplier}x, Price={format_currency(ask_price)}")
                
                # Buy using proposal ID
                buy_info = await self.buy_with_proposal(proposal_id, ask_price)
                
                if not buy_info:
                    if attempt < max_retries - 1:
                        logger.warning("⚠️ Price moved, retrying with fresh proposal...")
                        continue
                    logger.error(f"❌ Failed to buy {symbol} after all retries")
                    return None
                
                # Success! Extract trade info
                contract_id = buy_info["contract_id"]
                entry_price = float(buy_info.get("buy_price", stake))
                entry_spot = float(buy_info.get("entry_spot", 0))

                # Fallback to proposal spot if entry_spot is invalid (failed to fetch on buy)
                if entry_spot == 0:
                   logger.warning(f"⚠️ Zero entry_spot in buy response, falling back to proposal spot: {proposal.get('spot', 0)}")
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
                
                logger.info(f"✅ Trade opened successfully!")
                logger.info(f"   Symbol: {symbol}")
                logger.info(f"   Contract ID: {contract_id}")
                logger.info(f"   Entry Spot: {entry_spot:.4f}")
                logger.info(f"   Direction: {direction}")
                logger.info(f"   Multiplier: {multiplier}x")
                
                # Apply TP/SL limits if provided
                if tp_price and sl_price:
                    logger.info(f"   🎯 Take Profit: {tp_price:.4f}")
                    logger.info(f"   🛡️ Stop Loss: {sl_price:.4f}")
                    
                    # Validate R:R ratio
                    distance_to_tp = abs(tp_price - entry_spot)
                    distance_to_sl = abs(entry_spot - sl_price)
                    
                    if distance_to_sl > 0:
                        rr_ratio = distance_to_tp / distance_to_sl
                        logger.info(f"   📊 R:R Ratio: 1:{rr_ratio:.2f}")
                        
                        if rr_ratio < config.MIN_RR_RATIO:
                            logger.warning(f"⚠️ R:R ratio {rr_ratio:.2f} below minimum {config.MIN_RR_RATIO}")
                    
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
                    logger.warning("⚠️ No TP/SL provided - trade will run without limits!")
                
                if notifier is not None:
                    try:
                        await notifier.notify_trade_opened(trade_info)
                    except Exception as e:
                        logger.error(f"❌ Telegram notification failed: {e}")
                
                return trade_info
                
            except Exception as e:
                logger.error(f"❌ Error in open_trade for {symbol} (attempt {attempt + 1}): {e}")
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
                logger.error(f"❌ Failed to get trade status: {response['error']['message']}")
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
                'is_sold': is_sold
            }
            
            return status_info
        except Exception as e:
            logger.error(f"❌ Error getting trade status: {e}")
            return None
    
    async def monitor_trade(self, contract_id: str, trade_info: Dict,
                          risk_manager=None) -> Optional[Dict]:
        """Monitor trade until TP/SL hit (no time-based exits)"""
        try:
            start_time = datetime.now()
            symbol = trade_info.get('symbol', 'Unknown')
            
            logger.info(f"👁️ Monitoring trade on {symbol}")
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
                    exit_check = risk_manager.should_close_trade(
                        status['profit'],
                        status['current_spot'],
                        previous_spot
                    )
                    
                    if exit_check['should_close']:
                        logger.info(f"🎯 Risk Manager: {exit_check['message']}")
                        await self.close_trade(contract_id)
                        await asyncio.sleep(2)
                        final_status = await self.get_trade_status(contract_id)
                        if final_status:
                            final_status['exit_reason'] = exit_check['reason']
                        # Removed redundant notification here - Runner handles it
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
                    
                    emoji = get_status_emoji(trade_status)
                    logger.info(f"{emoji} Trade closed | Status: {trade_status.upper()}")
                    logger.info(f"   Symbol: {symbol}")
                    logger.info(f"   Final P&L: {format_currency(status['profit'])}")
                    logger.info(f"   Duration: {int(elapsed)}s")
                    
                    # Removed redundant notification here - Runner handles it
                    
                    return status
                
                # Periodic status logging
                time_since_last_log = (datetime.now() - last_status_log).total_seconds()
                if time_since_last_log >= status_log_interval:
                    pnl_emoji = "📈" if status['profit'] >= 0 else "📉"
                    logger.info(f"{pnl_emoji} {symbol}: {format_currency(status['profit'])} | "
                              f"Spot: {status['current_spot']:.4f} | {int(elapsed)}s")
                    last_status_log = datetime.now()
                
                await asyncio.sleep(monitor_interval)
                
        except Exception as e:
            logger.error(f"❌ Error monitoring trade: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    async def close_trade(self, contract_id: str) -> Optional[Dict]:
        """Manually close an active trade"""
        try:
            sell_request = {"sell": contract_id, "price": 0}
            
            logger.info(f"📤 Manually closing trade {contract_id}...")
            response = await self.send_request(sell_request)
            
            if "error" in response:
                logger.error(f"❌ Failed to close: {response['error']['message']}")
                return None
            
            if "sell" not in response:
                logger.error("❌ Invalid close response")
                return None
            
            sell_info = response["sell"]
            sold_for = float(sell_info.get("sold_for", 0))
            
            close_info = {
                'contract_id': contract_id,
                'sold_for': sold_for,
                'close_time': datetime.now()
            }
            
            logger.info(f"✅ Trade closed | Sold for: {format_currency(sold_for)}")
            self.active_contract_id = None
            return close_info
        except Exception as e:
            logger.error(f"❌ Error closing trade: {e}")
            return None
    
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
        try:
            direction = signal['signal']
            symbol = signal.get('symbol', config.SYMBOL)
            
            # Validate symbol
            if not self.validate_symbol(symbol):
                logger.error(f"❌ Invalid symbol in signal: {symbol}")
                return None
            
            # Validate TP/SL
            tp_price = signal.get('take_profit')
            sl_price = signal.get('stop_loss')
            
            if not tp_price or not sl_price:
                logger.error("❌ Missing TP/SL - trades require both")
                return None
            
            # Open trade on specified asset
            # CRITICAL: Use stake from signal if available (passed from BotRunner)
            # Fallback to config.FIXED_STAKE only if signal doesn't provide it
            trade_stake = signal.get('stake', config.FIXED_STAKE)
            
            if not trade_stake:
                logger.error(f"❌ Missing stake amount for {symbol}")
                return None
                
            trade_info = await self.open_trade(
                direction=direction,
                stake=trade_stake,
                symbol=symbol,
                tp_price=tp_price,
                sl_price=sl_price
            )
            
            if not trade_info:
                logger.error(f"❌ Failed to open trade on {symbol}")
                return None
            
            # Record with risk manager
            risk_manager.record_trade_open(trade_info)
            
            # Monitor trade until TP/SL hit
            final_status = await self.monitor_trade(
                trade_info['contract_id'],
                trade_info,
                risk_manager=risk_manager
            )
            
            if final_status is None:
                logger.error("❌ Monitoring failed - unlocking trade slot")
                risk_manager.has_active_trade = False
                risk_manager.active_trade = None
            
            return final_status
            
        except Exception as e:
            logger.error(f"❌ Error executing trade: {e}")
            import traceback
            logger.error(traceback.format_exc())
            
            try:
                risk_manager.has_active_trade = False
                risk_manager.active_trade = None
                logger.info("🔓 Trade slot unlocked after error")
            except:
                pass
            
            return None
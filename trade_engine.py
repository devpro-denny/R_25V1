"""
Trade Engine for Deriv R_25 Trading Bot
ENHANCED VERSION - With Dynamic Cancellation Fee Fetching
Handles trade execution, cancellation monitoring, and adaptive risk management
trade_engine.py - PRODUCTION READY
"""

import asyncio
import websockets
import json
from datetime import datetime, timedelta
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
    """Handles all trade execution operations with dynamic cancellation management"""
    
    def __init__(self, api_token: str, app_id: str = "1089"):
        """Initialize TradeEngine with dynamic cancellation support"""
        self.api_token = api_token
        self.app_id = app_id
        self.ws_url = f"{config.WS_URL}?app_id={app_id}"
        self.ws = None
        self.is_connected = False
        self.active_contract_id = None
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 5
        
        # Cancellation tracking
        self.cancellation_enabled = config.ENABLE_CANCELLATION
        self.cancellation_fee_fallback = getattr(config, 'CANCELLATION_FEE', 0.45)  # Fallback only
        self.actual_cancellation_fee = None  # Will be fetched dynamically
        self.in_cancellation_phase = False
        self.cancellation_start_time = None
        self.cancellation_expiry_time = None
        self.reference_entry_price = None
        
        # Calculate TP/SL amounts based on mode
        if self.cancellation_enabled:
            # Phase 2 TP/SL (applied after cancellation expires)
            self.take_profit_amount = self._calculate_post_cancel_tp()
            self.stop_loss_amount = self._calculate_post_cancel_sl()
            logger.info("🛡️ Cancellation mode ENABLED (Dynamic Fee)")
            logger.info(f"   Phase 1: 5-min cancellation filter")
            logger.info(f"   Phase 2 TP: {format_currency(self.take_profit_amount)}")
            logger.info(f"   Phase 2 SL: {format_currency(self.stop_loss_amount)}")
        else:
            # Legacy TP/SL (immediate application)
            self.take_profit_amount = self._calculate_tp_amount()
            self.stop_loss_amount = self._calculate_sl_amount()
            logger.info("⚠️ Cancellation mode DISABLED - Using legacy TP/SL")
            logger.info(f"   TP: {format_currency(self.take_profit_amount)}")
            logger.info(f"   SL: {format_currency(self.stop_loss_amount)}")
    
    def _calculate_tp_amount(self) -> float:
        """Calculate legacy Take Profit amount"""
        tp_amount = (config.TAKE_PROFIT_PERCENT / 100) * config.FIXED_STAKE * config.MULTIPLIER
        return round(tp_amount, 2)
    
    def _calculate_sl_amount(self) -> float:
        """Calculate legacy Stop Loss amount"""
        sl_amount = (config.STOP_LOSS_PERCENT / 100) * config.FIXED_STAKE * config.MULTIPLIER
        return round(sl_amount, 2)
    
    def _calculate_post_cancel_tp(self) -> float:
        """Calculate Phase 2 Take Profit (15% favorable move)"""
        tp_amount = (config.POST_CANCEL_TAKE_PROFIT_PERCENT / 100) * config.FIXED_STAKE * config.MULTIPLIER
        return round(tp_amount, 2)
    
    def _calculate_post_cancel_sl(self) -> float:
        """Calculate Phase 2 Stop Loss (5% of stake max loss)"""
        sl_amount = (config.POST_CANCEL_STOP_LOSS_PERCENT / 100) * config.FIXED_STAKE * config.MULTIPLIER
        return round(sl_amount, 2)
    
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
    
    async def get_cancellation_cost(self, direction: str, stake: float) -> Optional[float]:
        """
        Get the actual cancellation cost from Deriv API
        
        Args:
            direction: 'UP' or 'DOWN'
            stake: Stake amount
        
        Returns:
            Actual cancellation fee or None if failed
        """
        try:
            if direction.upper() in ['UP', 'BUY']:
                contract_type = config.CONTRACT_TYPE
            else:
                contract_type = config.CONTRACT_TYPE_DOWN
            
            # Request proposal with cancellation to get actual fee
            proposal_request = {
                "proposal": 1,
                "amount": stake,
                "basis": "stake",
                "contract_type": contract_type,
                "currency": "USD",
                "multiplier": config.MULTIPLIER,
                "symbol": config.SYMBOL,
                "cancellation": str(config.CANCELLATION_DURATION)
            }
            
            logger.info("🔍 Querying actual cancellation fee from Deriv...")
            response = await self.send_request(proposal_request)
            
            if "error" in response:
                logger.warning(f"⚠️ Failed to get cancellation cost: {response['error']['message']}")
                logger.warning(f"   Using fallback: ${self.cancellation_fee_fallback:.2f}")
                return self.cancellation_fee_fallback
            
            if "proposal" not in response:
                logger.warning("⚠️ Invalid proposal response")
                return self.cancellation_fee_fallback
            
            proposal = response["proposal"]
            
            # Extract cancellation cost
            cancellation_cost = None
            if "cancellation" in proposal:
                # Some APIs return it directly in cancellation object
                cancellation_cost = float(proposal["cancellation"].get("ask_price", 0))
            
            # Alternative: check limit_order or other fields
            if not cancellation_cost or cancellation_cost == 0:
                if "limit_order" in proposal:
                    cancellation_cost = float(proposal["limit_order"].get("cancellation", {}).get("cost", 0))
            
            # Another alternative: commission field
            if not cancellation_cost or cancellation_cost == 0:
                cancellation_cost = float(proposal.get("commission", 0))
            
            if cancellation_cost and cancellation_cost > 0:
                logger.info(f"✅ Actual cancellation fee: {format_currency(cancellation_cost)}")
                return cancellation_cost
            else:
                logger.warning(f"⚠️ Could not extract cancellation fee from proposal")
                logger.warning(f"   Using fallback: ${self.cancellation_fee_fallback:.2f}")
                return self.cancellation_fee_fallback
                
        except Exception as e:
            logger.error(f"❌ Error getting cancellation cost: {e}")
            logger.warning(f"   Using fallback: ${self.cancellation_fee_fallback:.2f}")
            return self.cancellation_fee_fallback
    
    async def open_trade(self, direction: str, stake: float) -> Optional[Dict]:
        """
        Open a multiplier trade with optional cancellation
        
        Args:
            direction: 'UP' or 'DOWN' or 'BUY' or 'SELL'
            stake: Stake amount
        
        Returns:
            Trade information dictionary or None if failed
        """
        try:
            if direction.upper() in ['UP', 'BUY']:
                contract_type = config.CONTRACT_TYPE
            else:
                contract_type = config.CONTRACT_TYPE_DOWN
            
            # Get actual cancellation fee if enabled
            if self.cancellation_enabled:
                self.actual_cancellation_fee = await self.get_cancellation_cost(direction, stake)
                logger.info(f"💰 Using dynamic cancellation fee: {format_currency(self.actual_cancellation_fee)}")
            
            # Build buy request
            buy_request = {
                "buy": 1,
                "price": stake,
                "parameters": {
                    "amount": stake,
                    "basis": "stake",
                    "contract_type": contract_type,
                    "currency": "USD",
                    "multiplier": config.MULTIPLIER,
                    "symbol": config.SYMBOL
                }
            }
            
            # Add cancellation if enabled
            if self.cancellation_enabled:
                buy_request["parameters"]["cancellation"] = str(config.CANCELLATION_DURATION)
                logger.info(f"🛡️ Opening trade WITH {config.CANCELLATION_DURATION}s cancellation")
            else:
                # Add immediate TP/SL if cancellation disabled
                buy_request["parameters"]["limit_order"] = {
                    "take_profit": self.take_profit_amount,
                    "stop_loss": self.stop_loss_amount
                }
                logger.info(f"📤 Opening trade WITHOUT cancellation (legacy mode)")
            
            logger.info(f"📤 Sending {direction} trade request...")
            logger.info(f"   Contract Type: {contract_type}")
            logger.info(f"   Stake: {format_currency(stake)}")
            logger.info(f"   Multiplier: {config.MULTIPLIER}x")
            
            response = await self.send_request(buy_request)
            
            if "error" in response:
                error_msg = response['error'].get('message', 'Unknown error')
                error_details = response['error'].get('details', {})
                logger.error(f"❌ Trade failed: {error_msg}")
                if error_details:
                    logger.error(f"   Details: {error_details}")
                return None
            
            if "buy" not in response:
                logger.error("❌ Invalid trade response")
                return None
            
            buy_info = response["buy"]
            contract_id = buy_info["contract_id"]
            entry_price = float(buy_info.get("buy_price", stake))
            longcode = buy_info.get("longcode", "")
            
            # Extract actual cancellation cost from buy response if available
            if self.cancellation_enabled and "cancellation" in buy_info:
                actual_fee = float(buy_info["cancellation"].get("ask_price", 0))
                if actual_fee > 0:
                    self.actual_cancellation_fee = actual_fee
                    logger.info(f"✅ Confirmed cancellation fee: {format_currency(actual_fee)}")
            
            self.active_contract_id = contract_id
            self.reference_entry_price = entry_price
            
            # Set cancellation tracking
            if self.cancellation_enabled:
                self.in_cancellation_phase = True
                self.cancellation_start_time = datetime.now()
                self.cancellation_expiry_time = self.cancellation_start_time + timedelta(
                    seconds=config.CANCELLATION_DURATION
                )
            
            trade_info = {
                'contract_id': contract_id,
                'direction': direction,
                'stake': stake,
                'entry_price': entry_price,
                'take_profit': self.take_profit_amount if not self.cancellation_enabled else None,
                'stop_loss': self.stop_loss_amount if not self.cancellation_enabled else None,
                'multiplier': config.MULTIPLIER,
                'contract_type': contract_type,
                'open_time': datetime.now(),
                'status': 'open',
                'longcode': longcode,
                'cancellation_enabled': self.cancellation_enabled,
                'cancellation_fee': self.actual_cancellation_fee if self.cancellation_enabled else None,
                'cancellation_expiry': self.cancellation_expiry_time if self.cancellation_enabled else None
            }
            
            logger.info(f"✅ Trade opened successfully!")
            logger.info(f"   Contract ID: {contract_id}")
            logger.info(f"   Entry Price: {entry_price:.2f}")
            logger.info(f"   Direction: {direction} ({contract_type})")
            
            if self.cancellation_enabled:
                cancel_threshold = self.actual_cancellation_fee * config.CANCELLATION_THRESHOLD
                logger.info(f"🛡️ PHASE 1: Cancellation active for {config.CANCELLATION_DURATION}s")
                logger.info(f"   Cancellation Fee: {format_currency(self.actual_cancellation_fee)}")
                logger.info(f"   Cancel Threshold: {format_currency(cancel_threshold)} ({config.CANCELLATION_THRESHOLD*100:.0f}% of fee)")
                logger.info(f"   Will auto-cancel if loss >= {format_currency(cancel_threshold)}")
                logger.info(f"   Expires at: {self.cancellation_expiry_time.strftime('%H:%M:%S')}")
            else:
                logger.info(f"   TP: {format_currency(self.take_profit_amount)}")
                logger.info(f"   SL: {format_currency(self.stop_loss_amount)}")
            
            if notifier is not None:
                try:
                    await notifier.notify_trade_opened(trade_info)
                except Exception as e:
                    logger.error(f"❌ Telegram notification failed: {e}")
            
            return trade_info
        except Exception as e:
            logger.error(f"❌ Error opening trade: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    async def cancel_trade(self, contract_id: str) -> Optional[Dict]:
        """
        Cancel a trade during cancellation period
        
        Args:
            contract_id: Contract ID to cancel
        
        Returns:
            Cancel information dictionary or None if failed
        """
        try:
            cancel_request = {
                "cancel": contract_id
            }
            
            logger.info(f"🛑 Canceling trade {contract_id}...")
            response = await self.send_request(cancel_request)
            
            if "error" in response:
                logger.error(f"❌ Failed to cancel: {response['error']['message']}")
                return None
            
            if "cancel" not in response:
                logger.error("❌ Invalid cancel response")
                return None
            
            cancel_info = response["cancel"]
            
            result = {
                'contract_id': contract_id,
                'cancelled': True,
                'cancel_time': datetime.now(),
                'refund': float(cancel_info.get('balance_after', 0)) - float(cancel_info.get('balance_before', 0))
            }
            
            self.in_cancellation_phase = False
            self.active_contract_id = None
            
            logger.info(f"✅ Trade cancelled successfully")
            logger.info(f"   Refund: {format_currency(result['refund'])}")
            logger.info(f"   Cancellation Fee Paid: {format_currency(self.actual_cancellation_fee or 0)}")
            
            return result
        except Exception as e:
            logger.error(f"❌ Error canceling trade: {e}")
            return None
    
    async def apply_post_cancellation_limits(self, contract_id: str) -> bool:
        """
        Apply TP/SL after cancellation period expires
        
        Args:
            contract_id: Contract ID
        
        Returns:
            True if limits applied successfully
        """
        try:
            limit_request = {
                "limit_order": {
                    "add": {
                        "take_profit": self.take_profit_amount,
                        "stop_loss": self.stop_loss_amount
                    }
                },
                "contract_id": contract_id
            }
            
            logger.info(f"🎯 Applying Phase 2 TP/SL limits...")
            response = await self.send_request(limit_request)
            
            if "error" in response:
                logger.error(f"❌ Failed to apply limits: {response['error']['message']}")
                return False
            
            logger.info(f"✅ Phase 2 limits applied!")
            logger.info(f"   TP: {format_currency(self.take_profit_amount)} (15% target)")
            logger.info(f"   SL: {format_currency(self.stop_loss_amount)} (5% max loss)")
            
            return True
        except Exception as e:
            logger.error(f"❌ Error applying limits: {e}")
            return False
    
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
            
            # Extract cancellation info if available
            cancellation_price = 0
            if "cancellation" in contract:
                cancellation_price = float(contract["cancellation"].get("ask_price", 0))
            
            status_info = {
                'contract_id': contract_id,
                'status': trade_status,
                'current_price': float(contract.get('current_spot', 0)),
                'entry_price': float(contract.get('entry_spot', 0)),
                'profit': profit,
                'bid_price': float(contract.get('bid_price', 0)),
                'buy_price': float(contract.get('buy_price', 0)),
                'is_sold': is_sold,
                'cancellation_price': cancellation_price
            }
            
            return status_info
        except Exception as e:
            logger.error(f"❌ Error getting trade status: {e}")
            return None
    
    def should_cancel_trade(self, status: Dict, direction: str) -> tuple[bool, str]:
        """
        Determine if trade should be cancelled based on price movement
        Uses actual dynamic cancellation fee from Deriv API
        
        Args:
            status: Current trade status
            direction: Trade direction ('UP' or 'DOWN')
        
        Returns:
            Tuple of (should_cancel: bool, reason: str)
        """
        if not self.in_cancellation_phase:
            return False, "Not in cancellation phase"
        
        # Use actual fee or fallback
        cancellation_fee = self.actual_cancellation_fee or self.cancellation_fee_fallback
        
        current_price = status['current_price']
        entry_price = status['entry_price']
        current_pnl = status['profit']
        
        # Calculate price movement
        if direction.upper() in ['UP', 'BUY']:
            # For UP trades, check if price moved DOWN
            price_change = current_price - entry_price
            moving_against_us = price_change < 0
        else:
            # For DOWN trades, check if price moved UP
            price_change = entry_price - current_price
            moving_against_us = price_change < 0
        
        if not moving_against_us:
            return False, "Price moving favorably"
        
        # Check if current loss justifies paying cancellation fee
        # Cancel if: current_loss >= (cancellation_fee × threshold)
        
        current_loss = abs(current_pnl)
        cancel_threshold = cancellation_fee * config.CANCELLATION_THRESHOLD
        
        if current_loss >= cancel_threshold:
            total_cost = cancellation_fee  # Only pay the fee
            return True, (f"Loss {format_currency(current_loss)} >= threshold {format_currency(cancel_threshold)} "
                         f"(will pay {format_currency(total_cost)} to cancel)")
        
        return False, f"Loss {format_currency(current_loss)} < threshold {format_currency(cancel_threshold)}"
    
    async def monitor_cancellation_phase(self, contract_id: str, trade_info: Dict) -> Optional[str]:
        """
        Monitor trade during cancellation phase
        
        Args:
            contract_id: Contract ID
            trade_info: Trade information
        
        Returns:
            'cancelled' if trade was cancelled, 'expired' if cancellation expired, None on error
        """
        direction = trade_info['direction']
        check_interval = config.CANCELLATION_CHECK_INTERVAL
        
        cancellation_fee = self.actual_cancellation_fee or self.cancellation_fee_fallback
        cancel_threshold = cancellation_fee * config.CANCELLATION_THRESHOLD
        
        logger.info(f"🛡️ Monitoring PHASE 1: Cancellation period ({config.CANCELLATION_DURATION}s)")
        logger.info(f"   Fee: {format_currency(cancellation_fee)} | Threshold: {format_currency(cancel_threshold)}")
        
        while self.in_cancellation_phase:
            # Check if cancellation period expired
            time_remaining = (self.cancellation_expiry_time - datetime.now()).total_seconds()
            
            if time_remaining <= 0:
                logger.info(f"⏰ Cancellation period expired - trade now COMMITTED")
                self.in_cancellation_phase = False
                
                # Apply Phase 2 TP/SL
                await self.apply_post_cancellation_limits(contract_id)
                
                return 'expired'
            
            # Get current status
            status = await self.get_trade_status(contract_id)
            if not status:
                await asyncio.sleep(check_interval)
                continue
            
            # Check if trade already closed
            if status['is_sold']:
                self.in_cancellation_phase = False
                return 'closed'
            
            # Check if we should cancel
            should_cancel, reason = self.should_cancel_trade(status, direction)
            
            if should_cancel:
                logger.warning(f"🛑 CANCELLING TRADE: {reason}")
                cancel_result = await self.cancel_trade(contract_id)
                
                if cancel_result:
                    return 'cancelled'
                else:
                    logger.error("❌ Cancellation failed, continuing monitoring")
            else:
                # Log status periodically
                current_pnl = status['profit']
                pnl_emoji = "📈" if current_pnl >= 0 else "📉"
                logger.info(f"{pnl_emoji} Phase 1: {format_currency(current_pnl)} | {time_remaining:.0f}s to commitment | {reason}")
            
            await asyncio.sleep(check_interval)
        
        return 'expired'
    
    async def monitor_trade(self, contract_id: str, trade_info: Dict,
                          max_duration: int = 3600, risk_manager=None) -> Optional[Dict]:
        """
        Monitor trade through both phases
        
        Args:
            contract_id: Contract ID
            trade_info: Trade information
            max_duration: Maximum duration
            risk_manager: Risk manager instance
        
        Returns:
            Final trade result
        """
        try:
            start_time = datetime.now()
            
            # PHASE 1: Cancellation monitoring
            if self.in_cancellation_phase:
                phase1_result = await self.monitor_cancellation_phase(contract_id, trade_info)
                
                if phase1_result == 'cancelled':
                    # Trade was cancelled
                    final_status = await self.get_trade_status(contract_id)
                    if final_status and notifier:
                        try:
                            await notifier.notify_trade_closed(final_status, trade_info)
                        except:
                            pass
                    return final_status
                elif phase1_result == 'closed':
                    # Trade closed during cancellation
                    final_status = await self.get_trade_status(contract_id)
                    return final_status
            
            # PHASE 2: Normal monitoring with TP/SL
            logger.info(f"🎯 Monitoring PHASE 2: Committed trade with TP/SL")
            
            monitor_interval = config.MONITOR_INTERVAL
            last_status_log = datetime.now()
            status_log_interval = 30
            previous_price = trade_info.get('entry_price', 0.0)
            
            while True:
                elapsed = (datetime.now() - start_time).total_seconds()
                if elapsed > max_duration:
                    logger.warning(f"⏰ Max duration reached")
                    await self.close_trade(contract_id)
                    status = await self.get_trade_status(contract_id)
                    if status and notifier:
                        try:
                            await notifier.notify_trade_closed(status, trade_info)
                        except:
                            pass
                    return status
                
                status = await self.get_trade_status(contract_id)
                if not status:
                    await asyncio.sleep(monitor_interval)
                    continue
                
                # Check risk manager conditions
                if risk_manager:
                    exit_check = risk_manager.should_close_trade(
                        status['profit'],
                        status['current_price'],
                        previous_price
                    )
                    
                    if exit_check['should_close']:
                        logger.info(f"🎯 {exit_check['message']}")
                        await self.close_trade(contract_id)
                        await asyncio.sleep(2)
                        final_status = await self.get_trade_status(contract_id)
                        if final_status and notifier:
                            try:
                                await notifier.notify_trade_closed(final_status, trade_info)
                            except:
                                pass
                        return final_status
                    
                    previous_price = status['current_price']
                
                # Check if closed
                if status['is_sold'] or status['status'] in ['sold', 'won', 'lost']:
                    trade_status = status.get('status', 'closed')
                    final_pnl = status.get('profit', 0)
                    
                    if trade_status in [None, '', 'unknown', 'closed']:
                        if final_pnl > 0:
                            trade_status = 'won'
                        elif final_pnl < 0:
                            trade_status = 'lost'
                        else:
                            trade_status = 'sold'
                    
                    emoji = get_status_emoji(trade_status)
                    logger.info(f"{emoji} Trade closed | Status: {trade_status.upper()}")
                    logger.info(f"   Final P&L: {format_currency(status['profit'])}")
                    
                    if notifier:
                        try:
                            await notifier.notify_trade_closed(status, trade_info)
                        except:
                            pass
                    
                    return status
                
                # Log periodically
                time_since_last_log = (datetime.now() - last_status_log).total_seconds()
                if time_since_last_log >= status_log_interval:
                    pnl_emoji = "📈" if status['profit'] >= 0 else "📉"
                    logger.info(f"{pnl_emoji} Phase 2: {format_currency(status['profit'])} | {int(elapsed)}s")
                    last_status_log = datetime.now()
                
                await asyncio.sleep(monitor_interval)
        except Exception as e:
            logger.error(f"❌ Error monitoring trade: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    async def close_trade(self, contract_id: str) -> Optional[Dict]:
        """Close an active trade"""
        try:
            sell_request = {
                "sell": contract_id,
                "price": 0
            }
            
            logger.info(f"📤 Closing trade {contract_id}...")
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
        """Execute complete trade cycle with dynamic cancellation management"""
        try:
            direction = signal['signal']
            
            trade_info = await self.open_trade(
                direction=direction,
                stake=config.FIXED_STAKE
            )
            
            if not trade_info:
                return None
            
            risk_manager.record_trade_open(trade_info)
            
            final_status = await self.monitor_trade(
                trade_info['contract_id'],
                trade_info,
                max_duration=config.MAX_TRADE_DURATION,
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
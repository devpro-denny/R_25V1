"""
Rise/Fall Trade Engine
Independent WebSocket client for buying Rise/Fall contracts on Deriv
rf_trade_engine.py
"""

import asyncio
import websockets
import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional

from risefallbot import rf_config

# Dedicated logger for Rise/Fall trade execution
logger = logging.getLogger("risefallbot.engine")


class RFTradeEngine:
    """
    Standalone Deriv WebSocket client for Rise/Fall contract execution.
    
    This engine owns its own WebSocket connection, completely independent
    from the multiplier TradeEngine used by Conservative/Scalping strategies.
    """

    def __init__(self, api_token: str, app_id: str = None):
        """
        Initialize Rise/Fall trade engine.
        
        Args:
            api_token: Deriv API token
            app_id: Deriv app ID (defaults to rf_config.RF_APP_ID)
        """
        self.api_token = api_token
        self.app_id = app_id or rf_config.RF_APP_ID
        self.ws = None
        self.ws_url = f"{rf_config.RF_WS_URL}?app_id={self.app_id}"
        self.authorized = False
        self._req_id = 0

    # ------------------------------------------------------------------ #
    #  Connection management                                              #
    # ------------------------------------------------------------------ #

    async def connect(self) -> bool:
        """Connect to Deriv WebSocket API."""
        try:
            logger.info("[RF-Engine] 🔌 Connecting to Deriv API...")
            self.ws = await websockets.connect(
                self.ws_url,
                ping_interval=30,
                ping_timeout=10,
                close_timeout=5,
            )
            logger.info("[RF-Engine] ✅ WebSocket connected")
            return await self._authorize()
        except Exception as e:
            logger.error(f"[RF-Engine] ❌ Connection failed: {e}")
            return False

    async def _authorize(self) -> bool:
        """Authorize the WebSocket connection."""
        try:
            resp = await self._send({"authorize": self.api_token})
            if resp and "authorize" in resp:
                self.authorized = True
                balance = resp["authorize"].get("balance", "?")
                logger.info(f"[RF-Engine] ✅ Authorized | balance=${balance}")
                return True
            else:
                error = resp.get("error", {}).get("message", "Unknown")
                logger.error(f"[RF-Engine] ❌ Authorization failed: {error}")
                return False
        except Exception as e:
            logger.error(f"[RF-Engine] ❌ Authorization error: {e}")
            return False

    async def reconnect(self) -> bool:
        """Attempt reconnection."""
        logger.info("[RF-Engine] 🔄 Reconnecting...")
        await self.disconnect()
        await asyncio.sleep(2)
        return await self.connect()

    async def ensure_connected(self) -> bool:
        """Ensure WebSocket is connected, reconnect if needed."""
        if self.ws and self.ws.open:
            return True
        return await self.reconnect()

    async def disconnect(self) -> None:
        """Disconnect from WebSocket."""
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None
            self.authorized = False
        logger.info("[RF-Engine] 🔌 Disconnected")

    # ------------------------------------------------------------------ #
    #  Low-level send/receive                                             #
    # ------------------------------------------------------------------ #

    async def _send(self, request: Dict[str, Any]) -> Optional[Dict]:
        """
        Send a request and wait for the matching response.
        
        Args:
            request: API request payload
        
        Returns:
            Response dict or None on failure
        """
        if not self.ws or not self.ws.open:
            logger.error("[RF-Engine] WebSocket not connected")
            return None

        self._req_id += 1
        request["req_id"] = self._req_id

        try:
            await self.ws.send(json.dumps(request))
            raw = await asyncio.wait_for(
                self.ws.recv(), timeout=rf_config.RF_WS_TIMEOUT
            )
            resp = json.loads(raw)

            if "error" in resp:
                logger.error(
                    f"[RF-Engine] API error: {resp['error'].get('message', resp['error'])}"
                )
            return resp
        except asyncio.TimeoutError:
            logger.error("[RF-Engine] ⏱️ Request timed out")
            return None
        except Exception as e:
            logger.error(f"[RF-Engine] ❌ Send/recv error: {e}")
            return None

    # ------------------------------------------------------------------ #
    #  Rise/Fall contract execution                                       #
    # ------------------------------------------------------------------ #

    async def buy_rise_fall(
        self,
        symbol: str,
        direction: str,
        stake: float,
        duration: int = None,
        duration_unit: str = None,
    ) -> Optional[Dict]:
        """
        Buy a Rise/Fall contract.
        
        Args:
            symbol: Trading symbol (e.g., 'R_10')
            direction: 'CALL' (Rise) or 'PUT' (Fall)
            stake: Stake amount in USD
            duration: Contract duration (default from config)
            duration_unit: Duration unit (default from config)
            
        Returns:
            Dict with contract details on success, None on failure:
            {
                'contract_id': str,
                'buy_price': float,
                'payout': float,
                'symbol': str,
                'direction': str,
            }
        """
        if not await self.ensure_connected():
            return None

        duration = duration or rf_config.RF_CONTRACT_DURATION
        duration_unit = duration_unit or rf_config.RF_DURATION_UNIT

        contract_type = direction.upper()  # CALL or PUT
        if contract_type not in ("CALL", "PUT"):
            logger.error(f"[RF-Engine] Invalid direction: {direction}")
            return None

        buy_request = {
            "buy": 1,
            "price": stake,
            "parameters": {
                "contract_type": contract_type,
                "symbol": symbol,
                "duration": duration,
                "duration_unit": duration_unit,
                "basis": "stake",
                "amount": stake,
                "currency": "USD",
            },
        }

        logger.info(
            f"[RF-Engine] 🛒 Buying {contract_type} on {symbol} | "
            f"stake=${stake} duration={duration}{duration_unit}"
        )

        resp = await self._send(buy_request)
        if not resp or "buy" not in resp:
            error_msg = "Unknown error"
            if resp and "error" in resp:
                error_msg = resp["error"].get("message", str(resp["error"]))
            logger.error(f"[RF-Engine] ❌ Buy failed: {error_msg}")
            return None

        buy_data = resp["buy"]
        contract_id = str(buy_data.get("contract_id", ""))
        buy_price = float(buy_data.get("buy_price", stake))
        payout = float(buy_data.get("payout", 0))

        logger.info(
            f"[RF-Engine] ✅ Contract bought: #{contract_id} | "
            f"buy_price=${buy_price:.2f} payout=${payout:.2f}"
        )

        return {
            "contract_id": contract_id,
            "buy_price": buy_price,
            "payout": payout,
            "symbol": symbol,
            "direction": contract_type,
        }

    # ------------------------------------------------------------------ #
    #  Contract outcome tracking                                          #
    # ------------------------------------------------------------------ #

    async def wait_for_result(
        self, contract_id: str, stake: float = 0.0
    ) -> Optional[Dict]:
        """
        Subscribe to an open contract and monitor until settlement OR
        early take-profit.

        If the unrealised profit reaches RF_TAKE_PROFIT_PCT × stake,
        the contract is sold early to lock in gains.

        Args:
            contract_id: The contract ID to track
            stake: Original stake (used to compute TP threshold)

        Returns:
            Dict with settlement result:
            {
                'contract_id': str,
                'profit': float,
                'status': 'win' | 'loss',
                'sell_price': float,
            }
        """
        if not await self.ensure_connected():
            return None

        tp_threshold = stake * rf_config.RF_TAKE_PROFIT_PCT if stake > 0 else 0
        sl_threshold = stake * rf_config.RF_STOP_LOSS_PCT if stake > 0 else 0
        already_sold = False
        sell_reason = None  # "tp" or "sl"

        # Subscribe to the contract
        sub_request = {
            "proposal_open_contract": 1,
            "contract_id": contract_id,
            "subscribe": 1,
        }

        logger.info(
            f"[RF-Engine] 👁️ Watching contract #{contract_id} "
            f"| TP: +${tp_threshold:.2f} ({rf_config.RF_TAKE_PROFIT_PCT*100:.0f}%) "
            f"| SL: -${sl_threshold:.2f} ({rf_config.RF_STOP_LOSS_PCT*100:.0f}%)"
        )

        try:
            await self.ws.send(json.dumps(sub_request))

            while True:
                raw = await asyncio.wait_for(
                    self.ws.recv(), timeout=600  # 10-min max wait
                )
                data = json.loads(raw)

                if "error" in data:
                    logger.error(
                        f"[RF-Engine] Contract watch error: "
                        f"{data['error'].get('message', data['error'])}"
                    )
                    return None

                poc = data.get("proposal_open_contract")
                if not poc:
                    continue

                is_sold = poc.get("is_sold", 0)
                is_expired = poc.get("is_expired", 0)

                # --- Contract has settled (naturally or after our sell) ---
                if is_sold or is_expired:
                    sell_price = float(poc.get("sell_price", 0))
                    buy_price = float(poc.get("buy_price", 0))
                    profit = sell_price - buy_price
                    status = "win" if profit > 0 else "loss"

                    # Determine closure tag and type
                    if sell_reason == "tp":
                        tag = "🎯 TP-SOLD"
                        closure_type = "take_profit"
                    elif sell_reason == "sl":
                        tag = "🛑 SL-SOLD"
                        closure_type = "stop_loss"
                    elif is_expired:
                        tag = "🏁 EXPIRED"
                        closure_type = "expiry"
                    elif is_sold and not already_sold:
                        # Sold externally — manual close on Deriv platform
                        tag = "🖐️ MANUAL-CLOSE"
                        closure_type = "manual"
                        logger.warning(
                            f"[RF-Engine] 🖐️ MANUAL CLOSE DETECTED for #{contract_id} — "
                            f"trade was closed outside the bot. pnl={profit:+.2f} | "
                            f"Lifecycle will complete and trade will be recorded in DB."
                        )
                    else:
                        tag = "🏁 SETTLED"
                        closure_type = "expiry"

                    logger.info(
                        f"[RF-Engine] {tag} Contract #{contract_id}: "
                        f"{status.upper()} pnl={profit:+.2f}"
                    )

                    # Unsubscribe
                    try:
                        unsub_id = data.get("subscription", {}).get("id")
                        if unsub_id:
                            await self.ws.send(
                                json.dumps({"forget": unsub_id})
                            )
                    except Exception:
                        pass

                    return {
                        "contract_id": contract_id,
                        "profit": profit,
                        "status": status,
                        "sell_price": sell_price,
                        "closure_type": closure_type,
                    }

                # --- Still open: check for take-profit / stop-loss ---
                if not already_sold:
                    bid_price = float(poc.get("bid_price", 0))
                    buy_price = float(poc.get("buy_price", 0))
                    unrealised_pnl = bid_price - buy_price

                    # Take-profit check
                    if tp_threshold > 0 and unrealised_pnl >= tp_threshold:
                        logger.info(
                            f"[RF-Engine] 💰 TP hit! Unrealised +${unrealised_pnl:.2f} "
                            f">= threshold ${tp_threshold:.2f} — selling early"
                        )
                        sold = await self._sell_with_retry(
                            contract_id, bid_price, "TP",
                            max_attempts=rf_config.RF_TP_SL_MAX_RETRIES,
                            retry_delay=rf_config.RF_TP_SL_RETRY_DELAY,
                        )
                        if sold:
                            already_sold = True
                            sell_reason = "tp"
                        else:
                            logger.warning(
                                f"[RF-Engine] ⚠️ TP sell failed all retries for #{contract_id} — "
                                f"continuing to monitor. Will retry on next tick."
                            )

                    # Stop-loss check
                    elif sl_threshold > 0 and unrealised_pnl <= -sl_threshold:
                        logger.info(
                            f"[RF-Engine] 🛑 SL hit! Unrealised ${unrealised_pnl:.2f} "
                            f"<= threshold -${sl_threshold:.2f} — cutting loss"
                        )
                        sold = await self._sell_with_retry(
                            contract_id, bid_price, "SL",
                            max_attempts=rf_config.RF_TP_SL_MAX_RETRIES,
                            retry_delay=rf_config.RF_TP_SL_RETRY_DELAY,
                        )
                        if sold:
                            already_sold = True
                            sell_reason = "sl"
                        else:
                            logger.warning(
                                f"[RF-Engine] ⚠️ SL sell failed all retries for #{contract_id} — "
                                f"continuing to monitor. Will retry on next tick."
                            )

        except asyncio.TimeoutError:
            logger.critical(
                f"[RF-Engine] 🚨 Contract #{contract_id} watch TIMED OUT after 600s — "
                f"returning None. Bot will record as settlement_unknown and release lock."
            )
            return None
        except Exception as e:
            logger.error(f"[RF-Engine] ❌ Contract watch error: {e}")
            return None

    async def _sell_with_retry(
        self,
        contract_id: str,
        min_price: float,
        reason: str,
        max_attempts: int = 3,
        retry_delay: float = 1.0,
    ) -> bool:
        """
        Attempt to sell a contract with retries.
        
        TP/SL enforcement MUST NOT be silently skipped. If the first sell
        attempt fails, retry up to max_attempts times before giving up.
        On retries, uses market price (0) instead of the original bid to
        avoid stale-price rejections from Deriv.

        Args:
            contract_id: Contract to sell
            min_price: Minimum acceptable sell price (used on first attempt)
            reason: 'TP' or 'SL' (for logging)
            max_attempts: Number of sell attempts
            retry_delay: Seconds between retry attempts

        Returns:
            True if sold successfully, False if all attempts failed
        """
        for attempt in range(1, max_attempts + 1):
            # First attempt: use exact bid price
            # Subsequent attempts: accept market price (0) to avoid stale price rejection
            price_to_use = min_price if attempt == 1 else 0.0
            if attempt > 1:
                logger.info(
                    f"[RF-Engine] 🔄 {reason} retry {attempt}/{max_attempts} — "
                    f"using market price (original bid ${min_price:.2f} may be stale)"
                )
            sold = await self._sell_contract(contract_id, price_to_use)
            if sold:
                logger.info(
                    f"[RF-Engine] ✅ {reason} sell confirmed for #{contract_id} "
                    f"(attempt {attempt}/{max_attempts})"
                )
                return True
            if attempt < max_attempts:
                logger.warning(
                    f"[RF-Engine] ⚠️ {reason} sell attempt {attempt}/{max_attempts} failed "
                    f"for #{contract_id} — retrying in {retry_delay}s..."
                )
                await asyncio.sleep(retry_delay)
        
        logger.critical(
            f"[RF-Engine] 🚨 {reason} SELL FAILED after {max_attempts} attempts "
            f"for #{contract_id} — contract remains open!"
        )
        return False

    async def _sell_contract(
        self, contract_id: str, min_price: float = 0
    ) -> bool:
        """
        Sell (close) an open contract early.

        Args:
            contract_id: Contract to sell
            min_price: Minimum acceptable sell price (0 = market)

        Returns:
            True if the sell request was accepted
        """
        sell_request = {
            "sell": contract_id,
            "price": min_price,
        }

        logger.info(f"[RF-Engine] 🏷️ Selling contract #{contract_id} @ min ${min_price:.2f}")

        resp = await self._send(sell_request)
        if not resp:
            logger.error(f"[RF-Engine] ❌ Sell request failed (no response)")
            return False

        if "error" in resp:
            err = resp["error"].get("message", str(resp["error"]))
            logger.error(f"[RF-Engine] ❌ Sell rejected: {err}")
            return False

        if "sell" in resp:
            sold_price = resp["sell"].get("sold_for", 0)
            logger.info(f"[RF-Engine] ✅ Contract sold for ${sold_price}")
            return True

        return False


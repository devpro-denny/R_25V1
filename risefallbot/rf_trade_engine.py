"""
Rise/Fall Trade Engine
Independent WebSocket client for buying Rise/Fall contracts on Deriv
rf_trade_engine.py

FIX A (2026-02-20): Ghost contract detection added to buy_rise_fall().
Root cause of duplicate trades:
  - Deriv processes the BUY server-side before returning a response.
  - A WebSocket timeout / parse error causes the engine to report failure
    and return None — but the contract already exists on Deriv.
  - The risk manager then releases the halt (transient error path) and the
    next cycle opens a SECOND trade, resulting in 2 simultaneous positions.

Fix A strategy:
  1. After any buy failure, call _check_for_ghost_contract() to query the
     Deriv portfolio API for a recently-opened contract matching the symbol
     and direction.
  2. If a ghost is found, return it as if the buy succeeded — the lifecycle
     continues normally and records the real contract.
  3. A module-level _last_buy_attempt registry prevents the ghost check from
     matching a contract from a previous cycle.

FIX B (2026-02-20): Stale WebSocket message consumed as settlement result.
Root cause of wrong P&L / premature lifecycle completion:
  - wait_for_result() sends a proposal_open_contract subscription for the
    new contract, then reads the next message from the WebSocket.
  - If a buffered message from the PREVIOUS contract's subscription is still
    in the receive queue (late-arriving update or echo), the loop consumes
    it, sees is_sold/is_expired=1 with the OLD contract's profit, and
    immediately returns — declaring a WIN/LOSS for the wrong contract.
  - The real contract then runs to expiry unmonitored on Deriv.

Fix B strategy:
  1. Every proposal_open_contract message is validated: the contract_id
     inside poc["contract_id"] must match the expected contract_id.
  2. Messages for a different contract_id are logged as warnings and
     discarded; the loop continues waiting for the correct contract.
  3. A _flush_stale_messages() helper drains any buffered messages before
     sending the subscription, preventing the stale-read window entirely.
  4. The subscription req_id is tracked and every response is validated
     against it so unrelated API responses are also discarded safely.
"""

import asyncio
import websockets
import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional

from risefallbot import rf_config

logger = logging.getLogger("risefallbot.engine")

# Registry of the most recent buy attempt per symbol, used by ghost detection
# to avoid matching an old contract from a previous cycle.
# Format: { symbol: { "direction": str, "timestamp": datetime } }
_last_buy_attempt: Dict[str, Dict] = {}

# Maximum age (seconds) of a Deriv contract that can be considered a ghost.
# Must be longer than the worst-case WS timeout but shorter than the contract
# duration so we never confuse a previous cycle's settled contract.
_GHOST_MAX_AGE_SECONDS = 90


class RFTradeEngine:
    """
    Standalone Deriv WebSocket client for Rise/Fall contract execution.

    This engine owns its own WebSocket connection, completely independent
    from the multiplier TradeEngine used by Conservative/Scalping strategies.
    """

    def __init__(self, api_token: str, app_id: str = None):
        self.api_token = api_token
        self.app_id = app_id or rf_config.RF_APP_ID
        self.ws = None
        self.ws_url = f"{rf_config.RF_WS_URL}?app_id={self.app_id}"
        self.authorized = False
        self._req_id = 0

    # ------------------------------------------------------------------ #
    #  Connection management                                               #
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
    #  Low-level send/receive                                              #
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
            expected_req_id = request["req_id"]
            timeout_seconds = rf_config.RF_WS_TIMEOUT
            deadline = asyncio.get_running_loop().time() + timeout_seconds

            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    logger.error(
                        f"[RF-Engine] ⏱️ Request timed out waiting for matching "
                        f"response (req_id={expected_req_id})"
                    )
                    return None

                raw = await asyncio.wait_for(self.ws.recv(), timeout=remaining)
                try:
                    resp = json.loads(raw)
                except Exception:
                    logger.warning("[RF-Engine] Ignoring non-JSON websocket frame")
                    continue

                resp_req_id = resp.get("req_id")
                if resp_req_id is None:
                    resp_req_id = resp.get("echo_req", {}).get("req_id")

                # CRITICAL: Only a frame with the matching req_id is a valid
                # response for this request. Subscription updates usually have
                # no req_id and must be ignored, otherwise BUY can be treated
                # as failed while Deriv already opened the contract.
                if resp_req_id is None:
                    logger.debug(
                        "[RF-Engine] Ignoring websocket frame without req_id "
                        f"while waiting for req_id={expected_req_id}"
                    )
                    continue

                try:
                    if int(resp_req_id) != int(expected_req_id):
                        logger.debug(
                            f"[RF-Engine] Ignoring unrelated response req_id={resp_req_id} "
                            f"(expected={expected_req_id})"
                        )
                        continue
                except (TypeError, ValueError):
                    logger.debug(
                        f"[RF-Engine] Ignoring frame with invalid req_id={resp_req_id!r} "
                        f"(expected={expected_req_id})"
                    )
                    continue

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
    #  Ghost contract detection                                            #
    # ------------------------------------------------------------------ #

    async def _check_for_ghost_contract(
        self, symbol: str, direction: str
    ) -> Optional[Dict]:
        """
        Query the Deriv portfolio for a recently-opened contract that matches
        the attempted buy.  Called after a buy response failure to detect the
        case where Deriv processed the order but the WS response was lost.

        Args:
            symbol:    Trading symbol (e.g. 'R_25')
            direction: 'CALL' or 'PUT'

        Returns:
            Contract dict (same shape as buy_rise_fall success return) if a
            ghost is found, None otherwise.
        """
        logger.info(
            f"[RF-Engine] 🔍 Ghost-contract check: querying portfolio for "
            f"{symbol} {direction} opened in last {_GHOST_MAX_AGE_SECONDS}s..."
        )

        attempt_info = _last_buy_attempt.get(symbol)
        if not attempt_info:
            logger.warning(
                "[RF-Engine] Ghost check: no buy-attempt timestamp found for "
                f"{symbol} — skipping (safe)"
            )
            return None

        attempt_ts: datetime = attempt_info["timestamp"]
        attempt_dir: str = attempt_info["direction"]

        # Direction mismatch guard (shouldn't happen but be defensive)
        if attempt_dir != direction:
            logger.warning(
                f"[RF-Engine] Ghost check: direction mismatch "
                f"(attempt={attempt_dir} vs check={direction}) — skipping"
            )
            return None

        # Re-ensure connection before portfolio query
        if not await self.ensure_connected():
            logger.error("[RF-Engine] Ghost check: cannot connect — skipping")
            return None

        try:
            resp = await self._send({"portfolio": 1})
        except Exception as e:
            logger.error(f"[RF-Engine] Ghost check: portfolio query failed: {e}")
            return None

        if not resp or "portfolio" not in resp:
            logger.warning(
                "[RF-Engine] Ghost check: empty/error portfolio response — "
                "cannot confirm ghost, treating buy as failed"
            )
            return None

        contracts = resp["portfolio"].get("contracts", [])
        now = datetime.now()

        for c in contracts:
            if c.get("symbol") != symbol:
                continue

            # Deriv returns purchase_time as a Unix timestamp (int/float)
            purchase_time_unix = c.get("purchase_time")
            if purchase_time_unix is None:
                continue

            try:
                purchase_dt = datetime.fromtimestamp(float(purchase_time_unix))
            except (ValueError, OSError, OverflowError):
                continue

            # Must have been purchased AFTER our buy attempt (with 2 s slack)
            if purchase_dt < attempt_ts.replace(microsecond=0) - __import__("datetime").timedelta(seconds=2):
                continue

            # Must be recent enough (within ghost window)
            age = (now - purchase_dt).total_seconds()
            if age > _GHOST_MAX_AGE_SECONDS:
                continue

            # contract_type is 'CALL' or 'PUT' on Deriv
            if c.get("contract_type", "").upper() != direction.upper():
                continue

            # Match found
            contract_id = str(c.get("contract_id", ""))
            buy_price   = float(c.get("buy_price",   0.0))
            payout      = float(c.get("payout",       0.0))

            logger.critical(
                f"[RF-Engine] 🚨 GHOST CONTRACT DETECTED: #{contract_id} "
                f"| symbol={symbol} direction={direction} "
                f"| purchase_time={purchase_dt} age={age:.1f}s "
                f"| buy_price=${buy_price:.2f} payout=${payout:.2f} "
                f"| Returning ghost to caller — lifecycle will track it normally"
            )

            return {
                "contract_id": contract_id,
                "buy_price":   buy_price,
                "payout":      payout,
                "symbol":      symbol,
                "direction":   direction,
                "ghost":       True,   # Flag for upstream logging
            }

        logger.info(
            f"[RF-Engine] ✅ Ghost check complete — no ghost contract found for "
            f"{symbol} {direction}. Buy genuinely failed."
        )
        return None

    # ------------------------------------------------------------------ #
    #  Rise/Fall contract execution                                        #
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
            symbol:        Trading symbol (e.g., 'R_10')
            direction:     'CALL' (Rise) or 'PUT' (Fall)
            stake:         Stake amount in USD
            duration:      Contract duration (default from config)
            duration_unit: Duration unit (default from config)

        Returns:
            Dict with contract details on success, None on genuine failure:
            {
                'contract_id': str,
                'buy_price':   float,
                'payout':      float,
                'symbol':      str,
                'direction':   str,
                'ghost':       bool  # True only when recovered from ghost
            }
        """
        if not await self.ensure_connected():
            return None

        duration      = duration      or rf_config.RF_CONTRACT_DURATION
        duration_unit = duration_unit or rf_config.RF_DURATION_UNIT

        contract_type = direction.upper()
        if contract_type not in ("CALL", "PUT"):
            logger.error(f"[RF-Engine] Invalid direction: {direction}")
            return None

        buy_request = {
            "buy": 1,
            "price": stake,
            "parameters": {
                "contract_type": contract_type,
                "symbol":        symbol,
                "duration":      duration,
                "duration_unit": duration_unit,
                "basis":         "stake",
                "amount":        stake,
                "currency":      "USD",
            },
        }

        # Record the attempt BEFORE sending so the ghost check can use it
        _last_buy_attempt[symbol] = {
            "direction": contract_type,
            "timestamp": datetime.now(),
        }

        logger.info(
            f"[RF-Engine] 🛒 Buying {contract_type} on {symbol} | "
            f"stake=${stake} duration={duration}{duration_unit}"
        )

        resp = await self._send(buy_request)

        # ── Primary success path ──────────────────────────────────────────
        if resp and "buy" in resp:
            buy_data    = resp["buy"]
            contract_id = str(buy_data.get("contract_id", ""))
            buy_price   = float(buy_data.get("buy_price", stake))
            payout      = float(buy_data.get("payout", 0))

            logger.info(
                f"[RF-Engine] ✅ Contract bought: #{contract_id} | "
                f"buy_price=${buy_price:.2f} payout=${payout:.2f}"
            )

            return {
                "contract_id": contract_id,
                "buy_price":   buy_price,
                "payout":      payout,
                "symbol":      symbol,
                "direction":   contract_type,
                "ghost":       False,
            }

        # ── Buy failed — extract error message ────────────────────────────
        error_msg = "Unknown error"
        if resp and "error" in resp:
            error_msg = resp["error"].get("message", str(resp["error"]))
        logger.error(f"[RF-Engine] ❌ Buy failed: {error_msg}")

        # ── GHOST CONTRACT CHECK ──────────────────────────────────────────
        # Deriv may have processed the order even though we got an error.
        # Query portfolio before returning None so the caller can track the
        # real contract instead of opening a second one.
        ghost = await self._check_for_ghost_contract(symbol, contract_type)
        if ghost:
            return ghost  # Caller handles ghost flag for extra logging

        # Genuine failure — return None so caller can halt/recover
        return None

    # ------------------------------------------------------------------ #
    #  Contract outcome tracking                                           #
    # ------------------------------------------------------------------ #

    async def _flush_stale_messages(self, timeout: float = 0.3) -> int:
        """
        Drain any messages already buffered in the WebSocket receive queue
        before sending a new subscription.  This prevents a late-arriving
        update from a PREVIOUS contract being consumed as the result for
        the NEW contract.

        Args:
            timeout: How long to wait for each buffered message (seconds).
                     Short enough not to delay trading; long enough to catch
                     any messages already in the TCP buffer.

        Returns:
            Number of stale messages drained.
        """
        drained = 0
        while True:
            try:
                raw = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
                msg = json.loads(raw)
                # Log what we're discarding so it's visible in the audit trail
                msg_type = list(msg.keys() - {"req_id", "echo_req", "msg_type", "subscription"})
                logger.warning(
                    f"[RF-Engine] 🗑️ FLUSHED stale WS message before new subscription: "
                    f"type={msg.get('msg_type', msg_type)} "
                    f"contract={msg.get('proposal_open_contract', {}).get('contract_id', '?')}"
                )
                drained += 1
            except asyncio.TimeoutError:
                # No more buffered messages
                break
            except Exception as e:
                logger.warning(f"[RF-Engine] Flush error (non-fatal): {e}")
                break
        if drained:
            logger.info(f"[RF-Engine] ✅ Flushed {drained} stale message(s) from WS buffer")
        return drained

    async def wait_for_result(
        self, contract_id: str, stake: float = 0.0
    ) -> Optional[Dict]:
        """
        Subscribe to an open contract and monitor until settlement.
        Contracts expire naturally without early exit.

        FIX B: Every received proposal_open_contract message is validated
        against the expected contract_id before acting on it.  Messages for
        a different contract_id (stale buffered updates from a previous
        subscription) are logged and discarded — the loop continues waiting
        for the correct contract.

        Args:
            contract_id: The contract ID to track
            stake:       Original stake (for logging only)

        Returns:
            Dict with settlement result:
            {
                'contract_id': str,
                'profit':       float,
                'status':       'win' | 'loss' | 'breakeven',
                'sell_price':   float,
                'closure_type': str,
            }
        """
        if not await self.ensure_connected():
            return None

        # ── FIX B-1: Drain any stale buffered messages BEFORE subscribing ──
        # This closes the window where a late-arriving update from the
        # previous contract is the very first message we read.
        await self._flush_stale_messages(timeout=0.3)

        self._req_id += 1
        sub_req_id = self._req_id

        sub_request = {
            "proposal_open_contract": 1,
            "contract_id": contract_id,
            "subscribe": 1,
            "req_id": sub_req_id,
        }

        logger.info(
            f"[RF-Engine] 👁️ Watching contract #{contract_id} "
            f"(req_id={sub_req_id}, will expire naturally)"
        )

        subscription_id: Optional[str] = None  # filled on first matching message

        try:
            await self.ws.send(json.dumps(sub_request))

            while True:
                raw = await asyncio.wait_for(
                    self.ws.recv(), timeout=600  # 10-min max wait
                )
                data = json.loads(raw)

                # ── FIX B-2: Discard error responses ──────────────────────
                if "error" in data:
                    logger.error(
                        f"[RF-Engine] Contract watch error: "
                        f"{data['error'].get('message', data['error'])}"
                    )
                    return None

                poc = data.get("proposal_open_contract")
                if not poc:
                    # Not a contract update — could be a ping/pong or other
                    # API response.  Log at DEBUG and skip.
                    logger.debug(
                        f"[RF-Engine] Non-contract message discarded: "
                        f"keys={list(data.keys())}"
                    )
                    continue

                # ── FIX B-3: CONTRACT ID VALIDATION (core fix) ────────────
                # Every proposal_open_contract message carries the contract_id
                # of the contract it refers to.  Reject any message that does
                # not match the contract we are monitoring.
                received_cid = str(poc.get("contract_id", ""))
                if received_cid != str(contract_id):
                    logger.warning(
                        f"[RF-Engine] ⚠️ STALE MESSAGE DISCARDED: received update for "
                        f"contract #{received_cid} but monitoring #{contract_id}. "
                        f"is_sold={poc.get('is_sold', 0)} "
                        f"is_expired={poc.get('is_expired', 0)} "
                        f"profit={poc.get('profit', '?')} — ignoring."
                    )
                    continue  # ← THIS is what was missing before the fix

                # Track subscription id for clean unsubscribe later
                if subscription_id is None:
                    subscription_id = data.get("subscription", {}).get("id")

                is_sold    = poc.get("is_sold",    0)
                is_expired = poc.get("is_expired", 0)

                if not (is_sold or is_expired):
                    # Contract still open — log current P&L and keep waiting
                    current_pnl = poc.get("profit", "?")
                    logger.debug(
                        f"[RF-Engine] ⏳ #{contract_id} still open | "
                        f"current_pnl={current_pnl}"
                    )
                    continue

                # ── Contract has settled ───────────────────────────────────
                profit     = float(poc.get("profit",     0))
                sell_price = float(poc.get("sell_price", 0))
                status     = "win" if profit > 0 else "loss" if profit < 0 else "breakeven"

                if is_expired:
                    tag          = "🏁 EXPIRED"
                    closure_type = "expiry"
                elif is_sold:
                    tag          = "🖐️ MANUAL-CLOSE"
                    closure_type = "manual"
                    logger.warning(
                        f"[RF-Engine] 🖐️ MANUAL CLOSE DETECTED for #{contract_id} — "
                        f"trade was closed outside the bot. pnl={profit:+.2f} | "
                        f"Lifecycle will complete and trade will be recorded in DB."
                    )
                else:
                    tag          = "🏁 SETTLED"
                    closure_type = "expiry"

                logger.info(
                    f"[RF-Engine] {tag} Contract #{contract_id}: "
                    f"{status.upper()} pnl={profit:+.2f}"
                )

                # Unsubscribe cleanly using tracked subscription_id
                try:
                    unsub_id = subscription_id or data.get("subscription", {}).get("id")
                    if unsub_id:
                        await self.ws.send(json.dumps({"forget": unsub_id}))
                        logger.debug(f"[RF-Engine] Unsubscribed from {unsub_id}")
                except Exception:
                    pass

                return {
                    "contract_id": contract_id,
                    "profit":       profit,
                    "status":       status,
                    "sell_price":   sell_price,
                    "closure_type": closure_type,
                }

        except asyncio.TimeoutError:
            logger.critical(
                f"[RF-Engine] 🚨 Contract #{contract_id} watch TIMED OUT after 600s — "
                f"returning None. Bot will record as settlement_unknown and release lock."
            )
            return None
        except Exception as e:
            logger.error(f"[RF-Engine] ❌ Contract watch error: {e}")
            return None

import asyncio
import json
import pytest
import logging
from datetime import datetime, timedelta
from unittest.mock import patch, AsyncMock, MagicMock
from risefallbot.rf_trade_engine import RFTradeEngine

# Configure logging for tests
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tests.rf_engine_fixes")

@pytest.fixture
def engine():
    return RFTradeEngine(api_token="TEST_TOKEN", app_id="1089")

@pytest.mark.asyncio
async def test_ghost_contract_recovery(engine):
    """
    BUG 1 VERIFICATION:
    Simulate a buy failure where the contract was actually opened on Deriv.
    The engine should query the portfolio and recover the 'ghost' contract.
    """
    symbol = "R_100"
    direction = "CALL"
    stake = 1.0
    contract_id = "ghost_999"

    # 1. Mock _send to return an error for the 'buy' request
    # 2. Mock _send to return a matching contract for the 'portfolio' request
    
    async def mock_send_side_effect(request):
        if "buy" in request:
            # Simulate a network error / timeout response
            return {"error": {"message": "Request timeout simulation"}}
        if "portfolio" in request:
            # Simulate portfolio containing the 'ghost' contract
            return {
                "portfolio": {
                    "contracts": [{
                        "symbol": symbol,
                        "contract_id": contract_id,
                        "purchase_time": datetime.now().timestamp(),
                        "contract_type": direction,
                        "buy_price": stake,
                        "payout": 1.95,
                    }]
                }
            }
        return {}

    with patch.object(engine, 'ensure_connected', return_value=True), \
         patch.object(engine, '_send', side_effect=mock_send_side_effect):
        
        result = await engine.buy_rise_fall(symbol, direction, stake)
        
        assert result is not None
        assert result["contract_id"] == contract_id
        assert result["ghost"] is True
        assert result["symbol"] == symbol
        assert result["direction"] == direction
        logger.info(f"✅ Recovered ghost contract #{contract_id}")

@pytest.mark.asyncio
async def test_stale_message_flushing(engine):
    """
    BUG 2 VERIFICATION (Part 1 - Flushing):
    Verify that _flush_stale_messages drains the WebSocket receive queue.
    """
    mock_ws = AsyncMock()
    mock_ws.open = True
    
    # Queue up 3 messages
    messages = [
        json.dumps({"msg_type": "proposal_open_contract", "proposal_open_contract": {"contract_id": "stale_1"}}),
        json.dumps({"msg_type": "proposal_open_contract", "proposal_open_contract": {"contract_id": "stale_2"}}),
        json.dumps({"msg_type": "heartbeat"})
    ]
    
    # Mock recv to return messages then timeout
    async def mock_recv():
        if messages:
            return messages.pop(0)
        raise asyncio.TimeoutError()
        
    mock_ws.recv = mock_recv
    engine.ws = mock_ws
    
    drained = await engine._flush_stale_messages(timeout=0.01)
    
    assert drained == 3
    logger.info("✅ Successfully flushed 3 stale messages")

@pytest.mark.asyncio
async def test_contract_id_validation(engine):
    """
    BUG 2 VERIFICATION (Part 2 - Validation):
    Verify that wait_for_result ignores messages for different contract IDs.
    """
    target_cid = "target_123"
    wrong_cid = "stale_456"
    
    mock_ws = AsyncMock()
    mock_ws.open = True
    
    # Stream of messages:
    # 1. Unrelated message
    # 2. Message for wrong contract ID
    # 3. Final settlement for target contract ID
    updates = [
        json.dumps({"msg_type": "tick", "tick": {"quote": 100.1}}),
        json.dumps({
            "msg_type": "proposal_open_contract",
            "proposal_open_contract": {
                "contract_id": wrong_cid,
                "is_sold": 1,
                "profit": 10.0
            }
        }),
        json.dumps({
            "msg_type": "proposal_open_contract",
            "subscription": {"id": "sub_target"},
            "proposal_open_contract": {
                "contract_id": target_cid,
                "is_sold": 1,
                "is_expired": 1,
                "profit": 0.95,
                "sell_price": 1.95
            }
        })
    ]
    
    async def mock_recv():
        if updates:
            return updates.pop(0)
        await asyncio.sleep(1) # Block if empty
        
    mock_ws.recv = mock_recv
    mock_ws.send = AsyncMock()
    engine.ws = mock_ws
    
    # Mock flush to not delay
    with patch.object(engine, '_flush_stale_messages', return_value=0):
        result = await engine.wait_for_result(target_cid, stake=1.0)
        
        assert result is not None
        assert result["contract_id"] == target_cid
        assert result["profit"] == 0.95
        assert result["status"] == "win"
        logger.info(f"✅ Successfully ignored message for #{wrong_cid} and waited for #{target_cid}")


@pytest.mark.asyncio
async def test_send_waits_for_matching_req_id(engine):
    """
    Regression guard:
    _send() must ignore unrelated websocket messages and wait for the
    matching req_id response, otherwise buy responses can be missed.
    """
    ws = AsyncMock()
    ws.open = True
    ws.send = AsyncMock()

    frames = [
        json.dumps({"msg_type": "proposal_open_contract", "req_id": 999, "proposal_open_contract": {"contract_id": "old"}}),
        json.dumps({"msg_type": "buy", "req_id": 1, "buy": {"contract_id": "new_1"}}),
    ]

    async def recv_side_effect():
        return frames.pop(0)

    ws.recv = recv_side_effect
    engine.ws = ws

    resp = await engine._send({"buy": 1})
    assert resp is not None
    assert resp.get("buy", {}).get("contract_id") == "new_1"


@pytest.mark.asyncio
async def test_send_ignores_frames_without_req_id(engine):
    """
    Regression guard:
    _send() must ignore subscription/update frames that do not carry req_id.
    Otherwise a stale contract update can be mistaken as BUY response.
    """
    ws = AsyncMock()
    ws.open = True
    ws.send = AsyncMock()

    frames = [
        json.dumps(
            {
                "msg_type": "proposal_open_contract",
                "proposal_open_contract": {"contract_id": "stale_no_req"},
            }
        ),
        json.dumps({"msg_type": "buy", "req_id": 1, "buy": {"contract_id": "new_2"}}),
    ]

    async def recv_side_effect():
        return frames.pop(0)

    ws.recv = recv_side_effect
    engine.ws = ws

    resp = await engine._send({"buy": 1})
    assert resp is not None
    assert resp.get("buy", {}).get("contract_id") == "new_2"

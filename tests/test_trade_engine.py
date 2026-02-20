import asyncio
import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trade_engine import TradeEngine
import config


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

class DummyRiskManager:
    def __init__(self):
        self.opened = None
        self.closed = None
        self._should_close = {"should_close": False, "reason": "", "message": ""}

    def record_trade_open(self, info):
        self.opened = info

    def should_close_trade(self, contract_id, profit, current_spot, previous_spot):
        return self._should_close


@pytest.mark.asyncio
async def test_get_asset_multiplier_valid_and_invalid():
    engine = TradeEngine(api_token="TEST", app_id="1089")
    # Valid symbol
    assert engine.get_asset_multiplier("R_50") == config.ASSET_CONFIG["R_50"]["multiplier"]
    # Invalid symbol falls back to default MULTIPLIER (160 default if missing)
    fallback = getattr(config, "MULTIPLIER", 160)
    assert engine.get_asset_multiplier("BAD_SYMBOL") == fallback


@pytest.mark.asyncio
async def test_validate_symbol_checks_configured():
    engine = TradeEngine(api_token="TEST", app_id="1089")
    assert engine.validate_symbol("R_25") is True
    assert engine.validate_symbol("NOT_EXISTS") is False


@pytest.mark.asyncio
async def test_get_proposal_builds_request_and_parses_response(monkeypatch):
    engine = TradeEngine(api_token="TEST", app_id="1089")

    # Mock connected state and send_request behavior
    engine.is_connected = True
    engine.ws = MagicMock(closed=False)

    async def fake_send(req):
        # Verify constructed request basics
        assert req["proposal"] == 1
        assert req["basis"] == "stake"
        assert req["symbol"] == "R_50"
        assert req["contract_type"] in (config.CONTRACT_TYPE, config.CONTRACT_TYPE_DOWN)
        # Return a well-formed proposal
        return {
            "proposal": {
                "id": "PID123",
                "ask_price": 1.23,
                "payout": 2.0,
                "spot": 100.5,
            }
        }

    engine.send_request = fake_send

    # Direction mapping check (UP -> CONTRACT_TYPE)
    p = await engine.get_proposal(direction="UP", stake=1.0, symbol="R_50")
    assert p is not None
    assert p["id"] == "PID123"
    assert p["ask_price"] == pytest.approx(1.23)
    assert p["payout"] == pytest.approx(2.0)
    assert p["spot"] == pytest.approx(100.5)
    assert p["multiplier"] == config.ASSET_CONFIG["R_50"]["multiplier"]


@pytest.mark.asyncio
async def test_get_proposal_handles_errors_and_missing_fields():
    engine = TradeEngine(api_token="TEST", app_id="1089")
    engine.is_connected = True
    engine.ws = MagicMock(closed=False)

    # 1) Invalid symbol returns None early
    res = await engine.get_proposal(direction="UP", stake=1.0, symbol="BAD")
    assert res is None

    # 2) API error
    async def err_req(req):
        return {"error": {"message": "bad"}}
    engine.send_request = err_req
    res2 = await engine.get_proposal(direction="DOWN", stake=1.0, symbol="R_25")
    assert res2 is None

    # 3) Missing proposal field
    async def missing_req(req):
        return {"msg_type": "proposal"}
    engine.send_request = missing_req
    res3 = await engine.get_proposal(direction="DOWN", stake=1.0, symbol="R_25")
    assert res3 is None


@pytest.mark.asyncio
async def test_buy_with_proposal_success_and_price_change_retry_signal():
    engine = TradeEngine(api_token="TEST", app_id="1089")
    engine.is_connected = True
    engine.ws = MagicMock(closed=False)

    # Successful buy
    async def ok_req(req):
        assert req["buy"] == "PID"
        # price should be 10% tolerance over ask (rounded by caller before)
        return {"buy": {"contract_id": 111, "buy_price": 1.1, "entry_spot": 100.0}}

    engine.send_request = ok_req
    r1 = await engine.buy_with_proposal("PID", price=1.0)
    assert r1["contract_id"] == 111

    # Price moved — should return None to signal retry
    async def moved_req(req):
        return {"error": {"message": "Payout has changed"}}
    engine.send_request = moved_req
    r2 = await engine.buy_with_proposal("PID", price=1.0)
    assert r2 is None


@pytest.mark.asyncio
async def test_apply_tp_sl_limits_amount_calculation_and_clamp():
    engine = TradeEngine(api_token="TEST", app_id="1089")
    engine.is_connected = True
    engine.ws = MagicMock(closed=False)

    # Capture sent request
    captured = {}

    async def cap_req(req):
        captured.update(req)
        return {"contract_update": {"status": 1}}

    engine.send_request = cap_req

    # Entry 100, TP at 101, SL at 99, stake 10, mult 100
    ok = await engine.apply_tp_sl_limits(
        contract_id="123",
        tp_price=101.0,
        sl_price=99.0,
        entry_spot=100.0,
        multiplier=100,
        stake=10.0,
    )
    assert ok is True
    assert captured["contract_update"] == 1
    lo = captured["limit_order"]
    # Profit = (1/100)*10*100 = 10
    assert lo["take_profit"] == pytest.approx(10.0)
    # Loss = (1/100)*10*100 = 10 but positive amount for stop_loss
    assert lo["stop_loss"] == pytest.approx(10.0)

    # SL exceeding stake => clamp by adjusting sl price; using entry 100, mult 100, stake 1
    captured.clear()
    ok2 = await engine.apply_tp_sl_limits(
        contract_id="2",
        tp_price=110.0,
        sl_price=80.0,
        entry_spot=100.0,
        multiplier=100,
        stake=1.0,
    )
    assert ok2 is True
    assert "limit_order" in captured
    assert captured["limit_order"]["stop_loss"] <= 1.0 + 1e-6  # must not exceed stake


@pytest.mark.asyncio
@patch("trade_engine.notifier")  # Prevent real Telegram notifications during tests
async def test_open_trade_happy_path_and_symbol_validation(mock_notifier, monkeypatch, capsys):
    engine = TradeEngine(api_token="TEST", app_id="1089")

    # Force connected
    engine.is_connected = True
    engine.ws = MagicMock(closed=False)

    # Invalid symbol -> None with printed messages
    res_invalid = await engine.open_trade("UP", 1.0, symbol="BAD")
    assert res_invalid is None

    # Mock get_proposal and buy_with_proposal for happy path
    async def fake_prop(direction, stake, symbol):
        return {"id": "PID", "ask_price": 1.0, "spot": 100.0, "multiplier": 80, "symbol": symbol}

    async def fake_buy(pid, price):
        return {"contract_id": 999, "buy_price": 1.0, "entry_spot": 100.0, "longcode": "x"}

    engine.get_proposal = fake_prop
    engine.buy_with_proposal = fake_buy

    trade = await engine.open_trade("UP", 2.5, symbol="R_50", tp_price=101.0, sl_price=99.0)
    assert trade is not None
    assert trade["contract_id"] == 999
    assert trade["symbol"] == "R_50"
    assert trade["stake"] == 2.5
    assert trade["multiplier"] == 80


@pytest.mark.asyncio
async def test_get_trade_status_normalization_open_and_closed():
    engine = TradeEngine(api_token="TEST", app_id="1089")
    engine.is_connected = True
    engine.ws = MagicMock(closed=False)

    # Open, unknown status -> should normalize to 'open'
    async def res_open(req):
        return {"proposal_open_contract": {"status": "", "is_sold": 0, "profit": 0, "current_spot": 101.0, "entry_spot": 100.0}}

    engine.send_request = res_open
    s1 = await engine.get_trade_status("C1")
    assert s1["status"] == "open"

    # Sold with profit -> 'won'
    async def res_won(req):
        return {"proposal_open_contract": {"is_sold": 1, "profit": 1.5, "current_spot": 102.0, "entry_spot": 100.0, "buy_price": 1.0, "bid_price": 2.5}}

    engine.send_request = res_won
    s2 = await engine.get_trade_status("C2")
    assert s2["status"] == "won"
    assert s2["is_sold"] is True


@pytest.mark.asyncio
async def test_monitor_trade_risk_manager_close_path(monkeypatch):
    engine = TradeEngine(api_token="TEST", app_id="1089")
    engine.is_connected = True
    engine.ws = MagicMock(closed=False)

    # get_trade_status returns an open status first then consider closed after close_trade
    statuses = [
        {"proposal_open_contract": {"status": "open", "is_sold": 0, "profit": 0.2, "current_spot": 101.0, "entry_spot": 100.0, "date_start": 1000, "sell_time": 1100}},
    ]

    async def seq_req(req):
        # pop last known status repeatedly
        return statuses[0]

    engine.send_request = seq_req

    # Risk manager that asks to close immediately
    rm = DummyRiskManager()
    rm._should_close = {"should_close": True, "reason": "rule", "message": "exit now"}

    # Close trade mock
    engine.close_trade = AsyncMock(return_value={"sold_for": 1.2})

    result = await engine.monitor_trade("CLOSE_ME", {"symbol": "R_50", "entry_spot": 100.0}, risk_manager=rm)
    assert result is not None
    assert result["exit_reason"] == "rule"
    assert result["symbol"] == "R_50"
    assert "duration" in result


@pytest.mark.asyncio
async def test_send_request_handles_closed_ws_and_failed_reconnect(monkeypatch):
    engine = TradeEngine(api_token="TEST", app_id="1089")
    # Simulate connected flag but closed websocket to force ensure_connected -> reconnect
    engine.is_connected = True
    engine.ws = MagicMock(closed=True)
    engine.reconnect = AsyncMock(return_value=False)

    resp = await engine.send_request({"ping": 1})
    assert isinstance(resp, dict)
    assert "error" in resp
    assert "Failed to establish connection" in resp["error"]["message"]


@pytest.mark.asyncio
async def test_close_trade_returns_none_on_errors(monkeypatch):
    engine = TradeEngine(api_token="TEST", app_id="1089")
    engine.is_connected = True
    engine.ws = MagicMock(closed=False)

    # API error path
    engine.send_request = AsyncMock(return_value={"error": {"message": "You do not own this contract"}})
    res1 = await engine.close_trade("CERR1")
    assert res1 is None

    # Missing 'sell' field path
    engine.send_request = AsyncMock(return_value={})
    res2 = await engine.close_trade("CERR2")
    assert res2 is None


@pytest.mark.asyncio
async def test_get_trade_status_normalizes_lost_case():
    engine = TradeEngine(api_token="TEST", app_id="1089")
    engine.is_connected = True
    engine.ws = MagicMock(closed=False)

    async def lost_resp(_):
        return {
            "proposal_open_contract": {
                "status": "",  # unknown/empty
                "is_sold": 1,
                "profit": -0.5,
                "current_spot": 99.0,
                "entry_spot": 100.0,
                "buy_price": 1.0,
                "bid_price": 0.5,
            }
        }

    engine.send_request = lost_resp
    status = await engine.get_trade_status("CLOSE_LOSS")
    assert status is not None
    assert status["status"] == "lost"
    assert status["is_sold"] is True
    assert status["profit"] == pytest.approx(-0.5)
    assert status["current_spot"] == pytest.approx(99.0)
    assert status["entry_spot"] == pytest.approx(100.0)
    assert status["buy_price"] == pytest.approx(1.0)
    assert status["bid_price"] == pytest.approx(0.5)


@pytest.mark.asyncio
@patch("trade_engine.notifier")  # Prevent real Telegram notifications during tests
async def test_open_trade_fallback_to_proposal_spot_when_entry_zero(mock_notifier):
    engine = TradeEngine(api_token="TEST", app_id="1089")
    engine.is_connected = True
    engine.ws = MagicMock(closed=False)

    async def fake_prop(direction, stake, symbol):
        return {"id": "PIDX", "ask_price": 1.0, "spot": 123.45, "multiplier": 80, "symbol": symbol}

    async def fake_buy(pid, price):
        return {"contract_id": 42, "buy_price": 1.0, "entry_spot": 0.0}

    engine.get_proposal = fake_prop
    engine.buy_with_proposal = fake_buy
    engine.apply_tp_sl_limits = AsyncMock()

    trade = await engine.open_trade("UP", 1.0, symbol="R_50")  # No TP/SL provided
    assert trade is not None
    assert trade["contract_id"] == 42
    # Fallback from 0.0 entry_spot to proposal spot
    assert trade["entry_spot"] == pytest.approx(123.45)
    # No TP/SL -> should not call apply_tp_sl_limits
    engine.apply_tp_sl_limits.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_tp_sl_limits_fails_when_disconnected():
    engine = TradeEngine(api_token="TEST", app_id="1089")
    engine.is_connected = False
    engine.ws = None
    engine.reconnect = AsyncMock(return_value=False)

    ok = await engine.apply_tp_sl_limits(
        contract_id="1", tp_price=101.0, sl_price=99.0, entry_spot=100.0, multiplier=100, stake=1.0
    )
    assert ok is False

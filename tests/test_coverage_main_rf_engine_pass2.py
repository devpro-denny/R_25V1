import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import main as main_mod
import risefallbot.rf_trade_engine as rf_mod
from risefallbot.rf_trade_engine import RFTradeEngine


@pytest.mark.asyncio
async def test_main_initialize_validate_config_exception(monkeypatch):
    bot = main_mod.TradingBot()
    monkeypatch.setattr(main_mod.config, "validate_config", MagicMock(side_effect=RuntimeError("bad cfg")))
    assert await bot.initialize() is False


@pytest.mark.asyncio
async def test_main_shutdown_exception_path():
    bot = main_mod.TradingBot()
    bot.data_fetcher = SimpleNamespace(disconnect=AsyncMock(side_effect=RuntimeError("x")))
    bot.trade_engine = SimpleNamespace(disconnect=AsyncMock())
    bot.risk_manager = SimpleNamespace(get_statistics=lambda: {})
    await bot.shutdown()


@pytest.mark.asyncio
async def test_main_analyze_asset_topdown_fetch_fail(monkeypatch):
    bot = main_mod.TradingBot()
    bot.data_fetcher = SimpleNamespace(fetch_all_timeframes=AsyncMock(return_value={}))
    bot.strategy = SimpleNamespace(analyze=MagicMock())
    monkeypatch.setattr(main_mod.config, "USE_TOPDOWN_STRATEGY", True)
    assert await bot.analyze_asset("R_25") is None


@pytest.mark.asyncio
async def test_main_trading_cycle_legacy_validation_and_execute_fail(monkeypatch):
    bot = main_mod.TradingBot()
    bot.risk_manager = SimpleNamespace(
        can_trade=lambda: (True, "ok"),
        validate_trade_parameters=lambda **_k: (False, "bad params"),
    )
    bot.scan_all_assets = AsyncMock(return_value=[{"symbol": "R_25", "signal": "UP", "can_trade": True}])
    monkeypatch.setattr(main_mod.config, "USE_TOPDOWN_STRATEGY", False)
    await bot.trading_cycle()

    bot.risk_manager = SimpleNamespace(
        can_trade=lambda: (True, "ok"),
        validate_trade_parameters=lambda **_k: (True, "ok"),
        record_trade_close=MagicMock(),
        get_statistics=lambda: {"win_rate": 0, "total_pnl": 0, "trades_today": 0},
        trades_today=[],
    )
    bot.trade_engine = SimpleNamespace(execute_trade=AsyncMock(return_value=None))
    await bot.trading_cycle()


@pytest.mark.asyncio
async def test_main_run_initialize_false_and_loop_error(monkeypatch):
    bot = main_mod.TradingBot()
    bot.initialize = AsyncMock(return_value=False)
    bot.shutdown = AsyncMock()
    await bot.run()
    bot.shutdown.assert_awaited()

    bot2 = main_mod.TradingBot()
    bot2.initialize = AsyncMock(return_value=True)
    bot2.shutdown = AsyncMock()
    bot2.risk_manager = SimpleNamespace(get_cooldown_remaining=lambda: 0)
    calls = {"n": 0}

    async def bad_cycle():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("loop err")
        bot2.running = False

    bot2.trading_cycle = bad_cycle
    monkeypatch.setattr(main_mod.asyncio, "sleep", AsyncMock())
    await bot2.run()
    assert calls["n"] >= 2


def test_main_entrypoint_paths(monkeypatch):
    fake_bot = SimpleNamespace(run=AsyncMock())
    monkeypatch.setattr(main_mod, "TradingBot", lambda: fake_bot)
    monkeypatch.setattr(main_mod.asyncio, "run", lambda coro: coro.close())
    main_mod.main()

    monkeypatch.setattr(main_mod, "TradingBot", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(main_mod.sys, "exit", MagicMock(side_effect=SystemExit(1)))
    with pytest.raises(SystemExit):
        main_mod.main()


@pytest.mark.asyncio
async def test_rf_engine_connect_reconnect_disconnect_and_authorize_exception(monkeypatch):
    eng = RFTradeEngine("T", "1089")

    # connect success path
    ws = AsyncMock()
    monkeypatch.setattr(rf_mod.websockets, "connect", AsyncMock(return_value=ws))
    eng._authorize = AsyncMock(return_value=True)
    assert await eng.connect() is True

    # connect exception path
    monkeypatch.setattr(rf_mod.websockets, "connect", AsyncMock(side_effect=RuntimeError("x")))
    assert await eng.connect() is False

    # _authorize exception path
    eng_auth = RFTradeEngine("T", "1089")
    eng_auth._send = AsyncMock(side_effect=RuntimeError("auth"))
    assert await eng_auth._authorize() is False

    # ensure_connected uses reconnect when ws not open
    eng.ws = SimpleNamespace(open=False)
    eng.reconnect = AsyncMock(return_value=True)
    assert await eng.ensure_connected() is True

    # reconnect/disconnect path
    eng2 = RFTradeEngine("T", "1089")
    eng2.disconnect = AsyncMock()
    eng2.connect = AsyncMock(return_value=True)
    monkeypatch.setattr(rf_mod.asyncio, "sleep", AsyncMock())
    assert await eng2.reconnect() is True
    eng2.disconnect.assert_awaited()

    eng3 = RFTradeEngine("T", "1089")
    ws3 = AsyncMock()
    eng3.ws = ws3
    await eng3.disconnect()
    assert eng3.ws is None
    assert eng3.authorized is False


@pytest.mark.asyncio
async def test_rf_engine_ghost_and_buy_branches(monkeypatch):
    eng = RFTradeEngine("T", "1089")

    # ghost check direction mismatch and empty portfolio
    rf_mod._last_buy_attempt.clear()
    rf_mod._last_buy_attempt["stpRNG1"] = {"direction": "PUT", "timestamp": rf_mod.datetime.now()}
    assert await eng._check_for_ghost_contract("stpRNG1", "CALL") is None

    rf_mod._last_buy_attempt["stpRNG1"] = {"direction": "CALL", "timestamp": rf_mod.datetime.now()}
    eng.ensure_connected = AsyncMock(return_value=True)
    eng._send = AsyncMock(return_value={"portfolio": {"contracts": []}})
    assert await eng._check_for_ghost_contract("stpRNG1", "CALL") is None

    # invalid direction in buy
    eng.ensure_connected = AsyncMock(return_value=True)
    assert await eng.buy_rise_fall("stpRNG1", "SIDEWAYS", 1.0) is None

    # no connection branch in buy
    eng.ensure_connected = AsyncMock(return_value=False)
    assert await eng.buy_rise_fall("stpRNG1", "CALL", 1.0) is None

    # primary buy success path
    eng.ensure_connected = AsyncMock(return_value=True)
    eng._send = AsyncMock(
        return_value={"buy": {"contract_id": "c1", "buy_price": 1.0, "payout": 1.9}}
    )
    ok = await eng.buy_rise_fall("stpRNG1", "CALL", 1.0)
    assert ok["contract_id"] == "c1"
    assert ok["ghost"] is False


@pytest.mark.asyncio
async def test_rf_engine_wait_for_result_error_timeout_and_breakeven(monkeypatch):
    eng = RFTradeEngine("T", "1089")
    eng.ensure_connected = AsyncMock(return_value=True)

    # error response path
    ws = AsyncMock()
    ws.open = True
    ws.send = AsyncMock()
    ws.recv = AsyncMock(return_value=json.dumps({"error": {"message": "bad"}}))
    eng.ws = ws
    eng._flush_stale_messages = AsyncMock(return_value=0)
    assert await eng.wait_for_result("c1", 1.0) is None

    # timeout path
    ws2 = AsyncMock()
    ws2.open = True
    ws2.send = AsyncMock()

    async def _timeout():
        raise asyncio.TimeoutError()

    ws2.recv = _timeout
    eng.ws = ws2
    eng._flush_stale_messages = AsyncMock(return_value=0)
    assert await eng.wait_for_result("c1", 1.0) is None

    # breakeven path + stale contract discard + unsubscribe
    ws3 = AsyncMock()
    ws3.open = True
    ws3.send = AsyncMock()
    msgs = [
        json.dumps({"proposal_open_contract": {"contract_id": "other", "is_sold": 1, "profit": 5}}),
        json.dumps(
            {
                "proposal_open_contract": {
                    "contract_id": "c2",
                    "is_sold": 1,
                    "is_expired": 1,
                    "profit": 0.0,
                    "sell_price": 1.0,
                },
                "subscription": {"id": "sub2"},
            }
        ),
    ]

    async def _recv():
        return msgs.pop(0)

    ws3.recv = _recv
    eng.ws = ws3
    eng._flush_stale_messages = AsyncMock(return_value=0)
    res = await eng.wait_for_result("c2", 1.0)
    assert res["status"] == "breakeven"
    assert res["closure_type"] == "expiry"

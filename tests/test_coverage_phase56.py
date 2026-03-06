import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

import data_fetcher as df_mod
import trade_engine as te_mod
import risefallbot.rf_bot as rf_bot
from app.bot.runner import BotRunner


def _all_tfs():
    return {
        "1m": pd.DataFrame([{"x": 1}]),
        "5m": pd.DataFrame([{"x": 1}]),
        "1h": pd.DataFrame([{"x": 1}]),
        "4h": pd.DataFrame([{"x": 1}]),
        "1d": pd.DataFrame([{"x": 1}]),
        "1w": pd.DataFrame([{"x": 1}]),
    }


@pytest.mark.asyncio
async def test_phase5_data_fetcher_additional_branches(monkeypatch):
    f = df_mod.DataFetcher("T")

    # connect -> authorize false path
    ws = AsyncMock()
    ws.closed = False
    monkeypatch.setattr(df_mod.websockets, "connect", AsyncMock(return_value=ws))
    f.authorize = AsyncMock(return_value=False)
    f.disconnect = AsyncMock()
    assert await f.connect() is False
    f.disconnect.assert_awaited()

    # send_request -> reconnect fail final message
    f.ensure_connected = AsyncMock(return_value=True)
    f.is_connected = False
    f.ws = SimpleNamespace(closed=True, send=AsyncMock(), recv=AsyncMock())
    f.reconnect = AsyncMock(return_value=False)
    monkeypatch.setattr(df_mod.config, "MAX_RETRIES", 1)
    monkeypatch.setattr(df_mod.config, "RETRY_DELAY", 0)
    out = await f.send_request({"a": 1})
    assert "Connection permanently lost" in out["error"]["message"]

    # send_request -> exception retries exhausted
    f.is_connected = True
    f.ws = SimpleNamespace(closed=False, send=AsyncMock(side_effect=RuntimeError("boom")), recv=AsyncMock())
    out = await f.send_request({"a": 1})
    assert "failed after retries" in out["error"]["message"]

    # fetch_candles: error and missing candles
    f.send_request = AsyncMock(return_value={"error": {"message": "no"}})
    assert await f.fetch_candles("R_25", 60, 10) is None
    f.send_request = AsyncMock(return_value={"ok": 1})
    assert await f.fetch_candles("R_25", 60, 10) is None

    # fetch_tick/get_balance missing payload paths
    f.send_request = AsyncMock(return_value={"foo": 1})
    assert await f.fetch_tick("R_25") is None
    assert await f.get_balance() is None

    # multi-timeframe exception path
    f.fetch_candles = AsyncMock(side_effect=RuntimeError("x"))
    assert await f.fetch_multi_timeframe_data("R_25") == {}

    # fetch_all_timeframes partial success path
    seq = [pd.DataFrame([{"x": 1}]), None, RuntimeError("x"), None, None, None]

    async def _ft(*_a, **_k):
        nxt = seq.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    f.fetch_timeframe = _ft
    data = await f.fetch_all_timeframes("R_25")
    assert "1m" in data


@pytest.mark.asyncio
async def test_phase5_trade_engine_additional_branches(monkeypatch):
    e = te_mod.TradeEngine("T")

    # authorize unknown response
    e.ws = AsyncMock()
    e.ws.recv = AsyncMock(return_value="{}")
    assert await e.authorize() is False

    # connect exception path
    monkeypatch.setattr(te_mod.websockets, "connect", AsyncMock(side_effect=RuntimeError("dial")))
    assert await e.connect() is False

    # open_trade disconnected + reconnect fails
    e.is_connected = False
    e.ws = None
    e.reconnect = AsyncMock(return_value=False)
    assert await e.open_trade("UP", 1.0, "R_25") is None

    # open_trade proposal fail one-shot
    e.is_connected = True
    e.ws = SimpleNamespace(closed=False)
    e.get_proposal = AsyncMock(return_value=None)
    assert await e.open_trade("UP", 1.0, "R_25", max_retries=1) is None

    # open_trade buy fail one-shot
    e.get_proposal = AsyncMock(return_value={"id": "p", "ask_price": 1.0, "multiplier": 50, "spot": 100.0})
    e.buy_with_proposal = AsyncMock(return_value=None)
    assert await e.open_trade("UP", 1.0, "R_25", max_retries=1) is None

    # get_trade_status error/missing field
    e.send_request = AsyncMock(return_value={"error": {"message": "no"}})
    assert await e.get_trade_status("c") is None
    e.send_request = AsyncMock(return_value={"ok": 1})
    assert await e.get_trade_status("c") is None

    # close_trade error path
    e.send_request = AsyncMock(return_value={"error": {"message": "x"}})
    assert await e.close_trade("c") is None


@pytest.mark.asyncio
async def test_phase6_rf_bot_run_early_failure_paths(monkeypatch):
    # shared config
    monkeypatch.setattr(rf_bot, "_fetch_user_config", AsyncMock(return_value={"api_token": "tok", "stake": 1.0}))
    monkeypatch.setattr(rf_bot, "RiseFallStrategy", lambda: MagicMock())
    monkeypatch.setattr(rf_bot, "RiseFallRiskManager", lambda: MagicMock())

    # case 1: data fetcher connect fails
    df1 = SimpleNamespace(connect=AsyncMock(return_value=False), disconnect=AsyncMock(), get_balance=AsyncMock(return_value=0))
    te1 = SimpleNamespace(connect=AsyncMock(return_value=True), disconnect=AsyncMock())
    monkeypatch.setattr(rf_bot, "DataFetcher", lambda *_a, **_k: df1)
    monkeypatch.setattr(rf_bot, "RFTradeEngine", lambda *_a, **_k: te1)

    rf_bot._bot_task = None
    await rf_bot.run(user_id="u")
    assert df1.connect.await_count == 1

    # case 2: trade engine connect fails and data_fetcher disconnect called
    df2 = SimpleNamespace(connect=AsyncMock(return_value=True), disconnect=AsyncMock(), get_balance=AsyncMock(return_value=0))
    te2 = SimpleNamespace(connect=AsyncMock(return_value=False), disconnect=AsyncMock())
    monkeypatch.setattr(rf_bot, "DataFetcher", lambda *_a, **_k: df2)
    monkeypatch.setattr(rf_bot, "RFTradeEngine", lambda *_a, **_k: te2)

    rf_bot._bot_task = None
    await rf_bot.run(user_id="u")
    df2.disconnect.assert_awaited()

    rf_bot._running = False
    rf_bot._bot_task = None


@pytest.mark.asyncio
async def test_phase6_runner_analyze_symbol_additional_branches(monkeypatch):
    r = BotRunner(account_id="u")
    r.auto_execute_signals = True
    r.symbols = ["R_25"]
    r.asset_config = {"R_25": {"multiplier": 10}}

    # patch globals used inside method
    mock_em = SimpleNamespace(broadcast=AsyncMock())
    monkeypatch.setattr("app.bot.runner.event_manager", mock_em)

    class _UTS:
        save_trade = MagicMock(return_value=False)

    monkeypatch.setattr("app.bot.runner.UserTradesService", _UTS)

    # missing required tfs
    r.data_fetcher = SimpleNamespace(fetch_all_timeframes=AsyncMock(return_value={"1m": pd.DataFrame()}))
    r.strategy = SimpleNamespace(get_required_timeframes=lambda: ["1m"], analyze=lambda **_k: {"can_trade": False, "details": {"reason": "No edge", "passed_checks": []}}, get_strategy_name=lambda: "Conservative")
    r.risk_manager = SimpleNamespace(can_open_trade=lambda **_k: (True, "ok"))
    r.telegram_bridge = SimpleNamespace(notify_signal=AsyncMock(), notify_error=AsyncMock(), notify_trade_closed=AsyncMock())
    r.state = SimpleNamespace(add_signal=MagicMock(), update_trade=MagicMock(), update_statistics=MagicMock(), update_signal_result=MagicMock())
    r.trade_engine = SimpleNamespace(execute_trade=AsyncMock(return_value=None))
    r.user_stake = 1.0
    assert await r._analyze_symbol("R_25") is False

    # can_trade true but user stake missing
    r.data_fetcher = SimpleNamespace(fetch_all_timeframes=AsyncMock(return_value=_all_tfs()))
    r.strategy = SimpleNamespace(
        get_required_timeframes=lambda: ["1m"],
        analyze=lambda **_k: {"can_trade": True, "signal": "UP", "score": 7, "confidence": 80, "take_profit": 110, "stop_loss": 99, "details": {"passed_checks": []}},
        get_strategy_name=lambda: "Conservative",
    )
    r.user_stake = None
    assert await r._analyze_symbol("R_25") is False

    # full success path with DB save false
    r.user_stake = 1.0
    r.risk_manager = SimpleNamespace(
        can_open_trade=lambda **_k: (True, "ok"),
        record_trade_close=MagicMock(),
        get_statistics=lambda: {"total": 1},
    )
    r.trade_engine = SimpleNamespace(execute_trade=AsyncMock(return_value={"contract_id": "c1", "profit": 0.5, "status": "won", "sell_time": 1, "current_spot": 101.0}))
    assert await r._analyze_symbol("R_25") is True
    assert r.telegram_bridge.notify_error.await_count >= 1

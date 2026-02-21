import asyncio
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

import data_fetcher as df_mod
import risefallbot.rf_bot as rf_bot
import strategy as st_mod
import trade_engine as te_mod


def _df(n=120, start=100.0, step=0.1):
    return pd.DataFrame(
        {
            "open": [start + i * step for i in range(n)],
            "high": [start + i * step + 0.2 for i in range(n)],
            "low": [start + i * step - 0.2 for i in range(n)],
            "close": [start + i * step + 0.1 for i in range(n)],
        }
    )


@pytest.mark.asyncio
async def test_trade_engine_additional_retry_and_monitor_paths(monkeypatch):
    e = te_mod.TradeEngine("T")

    # reconnect max attempts branch
    e.reconnect_attempts = e.max_reconnect_attempts
    assert await e.reconnect() is False

    # authorize unknown response branch
    ws = AsyncMock()
    ws.recv = AsyncMock(return_value=json.dumps({"msg_type": "authorize"}))
    e.ws = ws
    assert await e.authorize() is False

    # open_trade proposal fail after retries
    e.validate_symbol = MagicMock(return_value=True)
    e.is_connected = True
    e.ws = SimpleNamespace(closed=False)
    e.get_proposal = AsyncMock(return_value=None)
    out = await e.open_trade("UP", 1.0, "R_25", max_retries=2)
    assert out is None

    # monitor path: first None status, then closed status
    e.get_trade_status = AsyncMock(
        side_effect=[
            None,
            {
                "is_sold": True,
                "status": "",
                "profit": 0.0,
                "current_spot": 100.0,
                "date_start": 10,
                "sell_time": 20,
            },
        ]
    )
    monkeypatch.setattr(te_mod.asyncio, "sleep", AsyncMock())
    res = await e.monitor_trade("c1", {"symbol": "R_25", "entry_spot": 100.0})
    assert res is not None
    assert res["is_sold"] is True

    # execute_trade exception branch (missing signal key)
    rm = SimpleNamespace(active_trades=[{"contract_id": "x"}])
    assert await e.execute_trade({"symbol": "R_25"}, rm) is None


@pytest.mark.asyncio
async def test_data_fetcher_additional_retry_paths(monkeypatch):
    f = df_mod.DataFetcher("T")

    # reconnect max attempts branch
    f.reconnect_attempts = f.max_reconnect_attempts
    assert await f.reconnect() is False

    # authorize unknown auth response branch
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.recv = AsyncMock(return_value=json.dumps({"msg_type": "authorize"}))
    f.ws = ws
    assert await f.authorize() is False
    assert f.last_error == "Unknown auth response"

    # send_request branch: reconnect fails, permanent loss
    f.ensure_connected = AsyncMock(return_value=True)
    f.is_connected = False
    f.ws = SimpleNamespace(closed=True)
    f.reconnect = AsyncMock(return_value=False)
    monkeypatch.setattr(df_mod.config, "MAX_RETRIES", 1)
    monkeypatch.setattr(df_mod.config, "RETRY_DELAY", 0)
    r = await f.send_request({"x": 1})
    assert "Connection permanently lost" in r["error"]["message"]

    # fetch_all_timeframes catches per-timeframe exceptions
    async def _tf(symbol, tf, count):
        if tf == "1h":
            raise RuntimeError("boom")
        return pd.DataFrame()

    f.fetch_timeframe = _tf
    monkeypatch.setattr(df_mod.asyncio, "sleep", AsyncMock())
    out = await f.fetch_all_timeframes("R_25")
    assert out == {}


def test_strategy_additional_rsi_and_entry_branches(monkeypatch):
    s = st_mod.TradingStrategy()
    d = _df(150)

    import indicators

    monkeypatch.setattr(st_mod, "calculate_adx", lambda _x: pd.Series([40] * len(d)))
    monkeypatch.setattr(indicators, "detect_price_movement", lambda *_a, **_k: (0.1, 0.1, False))
    monkeypatch.setattr(indicators, "detect_consolidation", lambda *_a, **_k: (True, 1.0, 0.5))
    monkeypatch.setattr(s, "_determine_trend", lambda *_a, **_k: "UP")

    # RSI weak UP
    monkeypatch.setattr(st_mod, "calculate_rsi", lambda _x: pd.Series([10] * len(d)))
    r1 = s.analyze(d, d, d, d, d, d, symbol="R_25")
    assert r1["can_trade"] is False
    assert "RSI too weak for UP" in r1["details"]["reason"]

    # RSI overbought UP
    monkeypatch.setattr(st_mod, "calculate_rsi", lambda _x: pd.Series([99] * len(d)))
    monkeypatch.setattr(st_mod.config, "RSI_BUY_THRESHOLD", 50, raising=False)
    monkeypatch.setattr(st_mod.config, "RSI_MAX_THRESHOLD", 75, raising=False)
    r2 = s.analyze(d, d, d, d, d, d, symbol="R_25")
    assert r2["can_trade"] is False
    assert "RSI Overbought" in r2["details"]["reason"]

    # Entry trigger "Fresh Momentum Breakout"
    s._calculate_atr = MagicMock(return_value=1.0)
    one = _df(40)
    one.iloc[-1] = [99.0, 102.0, 98.5, 101.0]
    ok, reason = s._check_entry_trigger(one, 100.0, "UP")
    assert ok is True
    assert "Fresh Momentum Breakout" in reason


class _FakeLock:
    def __init__(self, initial=False):
        self._locked = initial

    def locked(self):
        return self._locked

    def release(self):
        self._locked = False


class _FakeRFManager:
    def __init__(self, *, locked=False, halted=False, active=False):
        self.trade_mutex = _FakeLock(locked)
        self._trade_mutex = self.trade_mutex
        self._trade_lock_active = locked
        self._locked_symbol = None
        self._locked_trade_info = {}
        self._pending_entry_timestamp = datetime.now() - timedelta(seconds=999)
        self._halted = halted
        self._halt_reason = "test halt"
        self._halt_timestamp = datetime.now() - timedelta(seconds=50)
        self.active_trades = {"c1": {"contract_id": "c1", "symbol": "R_25"}} if active else {}
        self._active = active

    def ensure_daily_reset_if_needed(self):
        return None

    def is_halted(self):
        return self._halted

    def clear_halt(self):
        self._halted = False

    def is_trade_active(self):
        return self._active

    def get_active_trade_info(self):
        return {"symbol": "R_25", "contract_id": "c1"}

    def get_statistics(self):
        return {
            "trades_today": 0,
            "wins": 0,
            "losses": 0,
            "total_pnl": 0.0,
            "mutex_locked": self.trade_mutex.locked(),
            "halted": self._halted,
            "active_positions": 1 if self._active else 0,
            "win_rate": 0.0,
        }

    def release_trade_lock(self, reason=""):
        self.trade_mutex.release()
        self._active = False


@pytest.mark.asyncio
async def test_rf_bot_run_additional_state_machine_paths(monkeypatch):
    rf_bot._running = False
    if hasattr(rf_bot, "_running_by_user"):
        rf_bot._running_by_user.clear()
    if hasattr(rf_bot, "_bot_task_by_user"):
        rf_bot._bot_task_by_user.clear()

    # shared fake infra
    em = SimpleNamespace(broadcast=AsyncMock())
    monkeypatch.setattr("app.bot.events.event_manager", em)
    monkeypatch.setattr("app.services.trades_service.UserTradesService", MagicMock())
    monkeypatch.setattr(rf_bot, "_fetch_user_config", AsyncMock(return_value={"api_token": "tok", "stake": 1.0}))
    monkeypatch.setattr(rf_bot, "_release_session_lock", AsyncMock())
    monkeypatch.setattr(rf_bot, "RiseFallStrategy", lambda: MagicMock())
    monkeypatch.setattr(rf_bot.rf_config, "RF_SYMBOLS", ["R_25"], raising=False)
    monkeypatch.setattr(rf_bot.rf_config, "RF_SCAN_INTERVAL", 0, raising=False)
    monkeypatch.setattr(rf_bot.rf_config, "RF_PENDING_TIMEOUT_SECONDS", 1, raising=False)

    df = SimpleNamespace(connect=AsyncMock(return_value=True), get_balance=AsyncMock(return_value=0.0), disconnect=AsyncMock())
    te = SimpleNamespace(connect=AsyncMock(return_value=True), disconnect=AsyncMock())
    monkeypatch.setattr(rf_bot, "DataFetcher", lambda *_a, **_k: df)
    monkeypatch.setattr(rf_bot, "RFTradeEngine", lambda *_a, **_k: te)

    # case 1: startup locked + halted + no active trades => watchdog + auto-recovery + scan branch
    rm1 = _FakeRFManager(locked=True, halted=True, active=False)
    monkeypatch.setattr(rf_bot, "RiseFallRiskManager", lambda: rm1)

    async def _proc(*_a, **_k):
        rf_bot.stop("u1")

    monkeypatch.setattr(rf_bot, "_process_symbol", _proc)
    rf_bot._bot_task = None
    await rf_bot.run(user_id="u1")
    assert rm1.trade_mutex.locked() is False
    assert rm1.is_halted() is False

    # case 2: active trade branch, then shutdown emergency record path
    rm2 = _FakeRFManager(locked=True, halted=False, active=True)
    monkeypatch.setattr(rf_bot, "RiseFallRiskManager", lambda: rm2)
    monkeypatch.setattr(rf_bot.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError()))
    rf_bot._bot_task = None
    await rf_bot.run(user_id="u2")
    assert rm2.trade_mutex.locked() is False

import asyncio
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

import data_fetcher as df_mod
import risefallbot.rf_risk_manager as rfr_mod


@pytest.mark.asyncio
async def test_data_fetcher_connect_reconnect_and_send_retry_branches(monkeypatch):
    f = df_mod.DataFetcher("T")

    # connect -> authorize fail path
    ws = AsyncMock()
    monkeypatch.setattr(df_mod.websockets, "connect", AsyncMock(return_value=ws))
    f.authorize = AsyncMock(return_value=False)
    ok = await f.connect()
    assert ok is False

    # reconnect max attempts branch
    f.reconnect_attempts = f.max_reconnect_attempts
    assert await f.reconnect() is False

    # send_request transient API error retries then success
    f.ensure_connected = AsyncMock(return_value=True)
    f.is_connected = True
    f.ws = AsyncMock()
    f.ws.closed = False
    msgs = [
        {"error": {"message": "Sorry, an error occurred"}},
        {"error": {"message": "Rate limit exceeded"}},
        {"ok": 1},
    ]
    f.ws.send = AsyncMock()
    f.ws.recv = AsyncMock(side_effect=[str(m).replace("'", '"') for m in msgs])
    monkeypatch.setattr(df_mod.config, "MAX_RETRIES", 3)
    monkeypatch.setattr(df_mod.config, "RETRY_DELAY", 0)
    out = await f.send_request({"x": 1})
    assert out.get("ok") == 1


@pytest.mark.asyncio
async def test_data_fetcher_connection_closed_and_weekly_resample_paths(monkeypatch):
    f = df_mod.DataFetcher("T")
    f.ensure_connected = AsyncMock(return_value=True)
    f.is_connected = True
    f.ws = AsyncMock()
    f.ws.closed = False

    class DummyClosed(Exception):
        pass

    if not hasattr(df_mod.websockets, "exceptions"):
        monkeypatch.setattr(
            df_mod.websockets,
            "exceptions",
            SimpleNamespace(
                ConnectionClosed=DummyClosed,
                ConnectionClosedError=DummyClosed,
                ConnectionClosedOK=DummyClosed,
            ),
            raising=False,
        )
    else:
        monkeypatch.setattr(df_mod.websockets.exceptions, "ConnectionClosed", DummyClosed)
        monkeypatch.setattr(df_mod.websockets.exceptions, "ConnectionClosedError", DummyClosed)
        monkeypatch.setattr(df_mod.websockets.exceptions, "ConnectionClosedOK", DummyClosed)

    f.ws.send = AsyncMock(side_effect=DummyClosed("closed"))
    monkeypatch.setattr(df_mod.config, "MAX_RETRIES", 1)
    monkeypatch.setattr(df_mod.config, "RETRY_DELAY", 0)
    out = await f.send_request({"x": 1})
    assert "max retries exceeded" in out["error"]["message"]

    # weekly timeframe path with resample success
    daily = pd.DataFrame(
        {
            "timestamp": [1700000000 + i * 86400 for i in range(14)],
            "open": [100 + i for i in range(14)],
            "high": [101 + i for i in range(14)],
            "low": [99 + i for i in range(14)],
            "close": [100.5 + i for i in range(14)],
            "datetime": pd.date_range("2025-01-01", periods=14, freq="D"),
        }
    )
    f.fetch_candles = AsyncMock(return_value=daily)
    got = await f.fetch_timeframe("R_25", "1w", count=2)
    assert got is not None
    assert len(got) <= 2


@pytest.mark.asyncio
async def test_data_fetcher_send_request_serializes_parallel_recv(monkeypatch):
    f = df_mod.DataFetcher("T")
    f.ensure_connected = AsyncMock(return_value=True)
    f.is_connected = True
    f.rate_limiter.acquire = AsyncMock()

    class ProbeWS:
        def __init__(self):
            self.closed = False
            self._recv_seq = 0
            self.active_recv = 0
            self.max_active_recv = 0

        async def send(self, _payload):
            return None

        async def recv(self):
            self.active_recv += 1
            self.max_active_recv = max(self.max_active_recv, self.active_recv)
            await asyncio.sleep(0.01)
            self._recv_seq += 1
            self.active_recv -= 1
            return json.dumps({"ok": self._recv_seq})

    ws = ProbeWS()
    f.ws = ws

    monkeypatch.setattr(df_mod.config, "MAX_RETRIES", 1)
    monkeypatch.setattr(df_mod.config, "RETRY_DELAY", 0)

    r1, r2 = await asyncio.gather(
        f.send_request({"a": 1}),
        f.send_request({"b": 2}),
    )

    assert r1.get("ok") in {1, 2}
    assert r2.get("ok") in {1, 2}
    assert ws.max_active_recv == 1


@pytest.mark.asyncio
async def test_rf_risk_manager_watchdog_and_acquire_failure_branches():
    rm = rfr_mod.RiseFallRiskManager()

    # Prepare stale pending lock + halted, watchdog should clear everything
    await rm.trade_mutex.acquire()
    rm._pending_entry_timestamp = datetime.now() - timedelta(seconds=rm._pending_timeout_seconds + 5)
    rm._halted = True
    rm._halt_reason = "stale"
    assert await rm.acquire_trade_lock("R_25", "pending") is True
    rm.release_trade_lock("done")

    # halted branch
    rm.halt("manual halt")
    assert await rm.acquire_trade_lock("R_25", "c1") is False
    rm.clear_halt()

    # post-acquire risk check fail branch (non-mutex reason)
    original_can_trade = rm.can_trade

    def blocked(*_a, **_k):
        return False, "daily cap reached"

    rm.can_trade = blocked
    assert await rm.acquire_trade_lock("R_25", "c2") is False
    rm.can_trade = original_can_trade

    # race window branch: active_trades already populated when lock acquired
    rm.active_trades["x"] = {"contract_id": "x", "symbol": "R_50"}
    assert await rm.acquire_trade_lock("R_25", "c3") is False
    rm.active_trades.clear()


@pytest.mark.asyncio
async def test_rf_risk_manager_record_open_close_and_can_trade_edges():
    rm = rfr_mod.RiseFallRiskManager()

    # record_trade_open without mutex held
    rm.record_trade_open({"contract_id": "c0", "symbol": "R_25"})
    assert "c0" not in rm.active_trades

    # normal open/close lifecycle with loss cooldown trigger
    await rm.acquire_trade_lock("R_25", "c1")
    rm.record_trade_open({"contract_id": "c1", "symbol": "R_25"})
    rm.record_trade_closed({"contract_id": "c1", "profit": -1.0, "status": "loss", "symbol": "R_25"})
    rm.release_trade_lock("closed")

    await rm.acquire_trade_lock("R_25", "c2")
    rm.record_trade_open({"contract_id": "c2", "symbol": "R_25"})
    rm.record_trade_closed({"contract_id": "c2", "profit": -1.0, "status": "loss", "symbol": "R_25"})
    rm.release_trade_lock("closed")

    can, reason = rm.can_trade(symbol="R_25")
    assert can is False
    assert "cooldown" in reason.lower()

    # stats helpers
    snap = rm.get_current_limits()
    assert "mutex_locked" in snap
    stats = rm.get_statistics()
    assert "win_rate" in stats

    # force daily reset path
    rm._last_daily_reset_date = datetime.now().date() - timedelta(days=1)
    rm.ensure_daily_reset_if_needed()
    assert rm.daily_trade_count == 0


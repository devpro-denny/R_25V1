import asyncio
import types
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from app.bot.manager import BotManager
from app.bot.runner import BotRunner, BotStatus


def _md(n=80):
    return pd.DataFrame(
        {
            "open": [100 + i * 0.1 for i in range(n)],
            "high": [100 + i * 0.1 + 0.2 for i in range(n)],
            "low": [100 + i * 0.1 - 0.2 for i in range(n)],
            "close": [100 + i * 0.1 + 0.05 for i in range(n)],
        }
    )


def _tf_pack():
    d = _md()
    return {"1m": d, "5m": d, "1h": d, "4h": d, "1d": d, "1w": d}


@pytest.mark.asyncio
async def test_runner_analyze_symbol_spam_suppression_and_trade_block(monkeypatch):
    r = BotRunner(account_id="u1")
    r.data_fetcher = SimpleNamespace(fetch_all_timeframes=AsyncMock(return_value=_tf_pack()))
    r.strategy = SimpleNamespace(
        get_required_timeframes=lambda: ["1m", "5m", "1h", "4h", "1d", "1w"],
        analyze=lambda **_k: {"can_trade": False, "details": {"reason": "No setup", "passed_checks": ["A", "B"]}},
    )
    r.last_status_log["R_25"] = {"msg": "No setup (Checks Passed: A, B)", "time": datetime.now()}

    out = await r._analyze_symbol("R_25")
    assert out is False

    # same path but with signal, blocked by risk manager can_open_trade
    r.strategy = SimpleNamespace(
        get_required_timeframes=lambda: ["1m", "5m", "1h", "4h", "1d", "1w"],
        analyze=lambda **_k: {
            "can_trade": True,
            "signal": "UP",
            "score": 1.0,
            "confidence": 90,
            "take_profit": 101.0,
            "stop_loss": 99.0,
            "details": {"passed_checks": ["x"]},
        },
        get_strategy_name=lambda: "Conservative",
    )
    r.risk_manager = SimpleNamespace(
        can_open_trade=lambda **_k: (False, "blocked"),
        record_trade_close=MagicMock(),
        get_statistics=lambda: {"trades_today": 0},
    )
    r.trade_engine = SimpleNamespace(execute_trade=AsyncMock(return_value=None))
    r.user_stake = 1.0
    r.asset_config = {"R_25": {"multiplier": 100}}
    monkeypatch.setattr("app.bot.runner.event_manager", SimpleNamespace(broadcast=AsyncMock()))
    r.telegram_bridge = SimpleNamespace(notify_signal=AsyncMock(), notify_error=AsyncMock())
    assert await r._analyze_symbol("R_25") is False


@pytest.mark.asyncio
async def test_runner_analyze_symbol_db_save_false_and_db_exception(monkeypatch):
    r = BotRunner(account_id="u1")
    r.auto_execute_signals = True
    r.data_fetcher = SimpleNamespace(fetch_all_timeframes=AsyncMock(return_value=_tf_pack()))
    r.strategy = SimpleNamespace(
        get_required_timeframes=lambda: ["1m", "5m", "1h", "4h", "1d", "1w"],
        analyze=lambda **_k: {
            "can_trade": True,
            "signal": "UP",
            "score": 1.0,
            "confidence": 90,
            "take_profit": 101.0,
            "stop_loss": 99.0,
            "details": {"passed_checks": ["x"]},
        },
        get_strategy_name=lambda: "Conservative",
    )
    r.risk_manager = SimpleNamespace(
        can_open_trade=lambda **_k: (True, "ok"),
        record_trade_close=MagicMock(),
        get_statistics=lambda: {"trades_today": 1},
    )
    r.trade_engine = SimpleNamespace(
        execute_trade=AsyncMock(return_value={"contract_id": "c1", "profit": 0.5, "status": "won"})
    )
    r.user_stake = 1.0
    r.asset_config = {"R_25": {"multiplier": 100}}
    ev = SimpleNamespace(broadcast=AsyncMock())
    monkeypatch.setattr("app.bot.runner.event_manager", ev)
    r.telegram_bridge = SimpleNamespace(
        notify_signal=AsyncMock(),
        notify_error=AsyncMock(),
        notify_trade_closed=AsyncMock(side_effect=RuntimeError("notify fail")),
    )

    with patch("app.bot.runner.UserTradesService.save_trade", return_value=False):
        assert await r._analyze_symbol("R_25") is True
        assert r.telegram_bridge.notify_error.await_count >= 1

    with patch("app.bot.runner.UserTradesService.save_trade", side_effect=RuntimeError("db err")):
        assert await r._analyze_symbol("R_25") is True
        assert r.telegram_bridge.notify_error.await_count >= 2


@pytest.mark.asyncio
async def test_runner_monitor_active_trade_additional_error_paths(monkeypatch):
    r = BotRunner(account_id="u1")
    r.trade_engine = SimpleNamespace(
        get_trade_status=AsyncMock(return_value={"is_sold": False, "profit": 1.0}),
        remove_take_profit=AsyncMock(side_effect=RuntimeError("rm tp fail")),
        close_trade=AsyncMock(side_effect=RuntimeError("close fail")),
    )
    r.strategy = SimpleNamespace(get_strategy_name=lambda: "Scalping")

    # branch: has_active_trade true but no active_info
    r.risk_manager = SimpleNamespace(has_active_trade=True, get_active_trade_info=lambda: None)
    await r._monitor_active_trade()

    # scalping isinstance + remove TP exception + trailing close exception
    import scalping_risk_manager as srm_mod

    class DummyScalp:
        has_active_trade = True

        def get_active_trade_info(self):
            return {
                "symbol": "R_25",
                "contract_id": "c1",
                "open_time": datetime.now(),
                "stake": 1.0,
            }

        def check_trailing_profit(self, *_a, **_k):
            return True, "trail", True

        def check_stagnation_exit(self, *_a, **_k):
            return True, "stag"

        def record_trade_close(self, *_a, **_k):
            return None

    monkeypatch.setattr(srm_mod, "ScalpingRiskManager", DummyScalp)
    r.risk_manager = DummyScalp()
    await r._monitor_active_trade()


@pytest.mark.asyncio
async def test_manager_get_bot_start_restart_status_and_strategy_fallback(monkeypatch):
    bm = BotManager(max_concurrent_bots=1)

    # get_bot update branch
    b1 = bm.get_bot("u1", strategy="s1", risk_manager="r1")
    b2 = bm.get_bot("u1", strategy="s2", risk_manager="r2")
    assert b1 is b2
    assert bm._bots["u1"].strategy == "s2"
    assert bm._bots["u1"].risk_manager == "r2"

    # start_bot already running with same strategy
    running_bot = SimpleNamespace(
        is_running=True,
        strategy=SimpleNamespace(get_strategy_name=lambda: "Conservative"),
        status=BotStatus.RUNNING,
        stop_bot=AsyncMock(),
        start_bot=AsyncMock(return_value={"success": True}),
    )
    bm._bots["u2"] = running_bot
    same = await bm.start_bot("u2", strategy_name="Conservative")
    assert same["success"] is False
    assert "already running" in same["message"]

    # restart missing bot branch
    r = await bm.restart_bot("nobody")
    assert r["success"] is False

    # get_status for done RF task path
    bm._rf_tasks["u3"] = SimpleNamespace(done=lambda: True)
    st = bm.get_status("u3")
    assert st["is_running"] is False

    # _get_user_strategy exception -> conservative fallback
    fake_supabase = types.SimpleNamespace(table=MagicMock(side_effect=RuntimeError("supabase down")))
    with patch("app.core.supabase.supabase", fake_supabase):
        s = await bm._get_user_strategy("u4")
        assert s == "Conservative"


@pytest.mark.asyncio
async def test_manager_risefall_lock_denied_stop_timeout_and_stop_all(monkeypatch):
    bm = BotManager(max_concurrent_bots=5)
    ev = SimpleNamespace(broadcast=AsyncMock())
    monkeypatch.setattr("app.bot.events.event_manager", ev)

    # lock denied path
    with patch("risefallbot.rf_bot._acquire_session_lock", new=AsyncMock(return_value=False)):
        denied = await bm._start_risefall_bot("u1", "tok", 1.0)
        assert denied["success"] is False

    # stop rf timeout + cancel path
    long_task = asyncio.create_task(asyncio.sleep(3600))
    bm._rf_tasks["u2"] = long_task
    bm._rf_start_times["u2"] = datetime.now()
    bm._rf_stakes["u2"] = 1.0

    with patch("app.bot.manager.asyncio.wait_for", side_effect=asyncio.TimeoutError()), \
         patch("risefallbot.rf_bot._release_session_lock", new=AsyncMock()), \
         patch("risefallbot.rf_bot.stop", new=MagicMock()):
        out = await bm._stop_risefall_bot("u2")
        assert out["success"] is True

    # stop_all with running normal bot + rf task
    normal = SimpleNamespace(is_running=True, stop_bot=AsyncMock(return_value={"success": True}))
    bm._bots["u3"] = normal
    rf_task = asyncio.create_task(asyncio.sleep(3600))
    bm._rf_tasks["u4"] = rf_task
    with patch("risefallbot.rf_bot.stop", new=MagicMock()):
        await bm.stop_all()
    assert "u4" not in bm._rf_tasks

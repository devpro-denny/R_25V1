import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime
from app.bot.runner import BotRunner, BotStatus
import config

@pytest.fixture
def runner():
    return BotRunner(account_id="test_user")

@pytest.fixture
def mock_deps():
    with patch("app.bot.runner.DataFetcher", new_callable=MagicMock) as mock_df, \
         patch("app.bot.runner.TradeEngine", new_callable=MagicMock) as mock_te, \
         patch("app.bot.runner.UserTradesService", new_callable=MagicMock) as mock_uts, \
         patch("app.bot.runner.event_manager", new_callable=AsyncMock) as mock_em, \
         patch("app.bot.runner.telegram_bridge", new_callable=MagicMock) as mock_tb:
        
        yield {
            "df": mock_df,
            "te": mock_te,
            "uts": mock_uts,
            "em": mock_em,
            "tb": mock_tb
        }

@pytest.mark.asyncio
async def test_start_bot_already_running(runner):
    runner.is_running = True
    runner.status = BotStatus.RUNNING
    res = await runner.start_bot(stake=10.0)
    assert res["success"] is False
    assert "already running" in res["message"]

@pytest.mark.asyncio
async def test_stop_bot_not_running(runner):
    runner.is_running = False
    res = await runner.stop_bot()
    assert res["success"] is False
    assert "not running" in res["message"]

@pytest.mark.asyncio
async def test_restart_bot(runner):
    runner.start_bot = AsyncMock(return_value={"success": True})
    runner.stop_bot = AsyncMock(return_value={"success": True})
    
    # Test restart when running
    runner.is_running = True
    with patch("asyncio.sleep", return_value=None):
        res = await runner.restart_bot()
        assert runner.stop_bot.called
        assert runner.start_bot.called

@pytest.mark.asyncio
async def test_run_bot_component_init_failure(runner, mock_deps):
    runner.api_token = "valid_token"
    mock_deps["df"].side_effect = Exception("Init failed")
    
    await runner._run_bot()
    assert runner.status == BotStatus.ERROR
    assert "Component initialization failed" in runner.error_message

@pytest.mark.asyncio
async def test_run_bot_connection_failure(runner, mock_deps):
    runner.api_token = "valid_token"
    df_instance = mock_deps["df"].return_value
    df_instance.connect = AsyncMock(return_value=False)
    df_instance.last_error = "Connection timeout"
    
    await runner._run_bot()
    assert runner.status == BotStatus.ERROR
    assert "DataFetcher failed to connect" in runner.error_message

@pytest.mark.asyncio
async def test_analyze_symbol_missing_multiplier(runner, mock_deps):
    runner.data_fetcher = mock_deps["df"].return_value
    runner.data_fetcher.fetch_all_timeframes = AsyncMock(return_value={
        '1m': [{}], '5m': [{}], '1h': [{}], '4h': [{}], '1d': [{}], '1w': [{}]
    })
    runner.strategy = MagicMock()
    runner.strategy.get_required_timeframes.return_value = ['1m']
    runner.strategy.analyze.return_value = {"can_trade": True, "signal": "BUY", "score": 1.0}
    runner.user_stake = 10.0
    
    # Empty asset_config to trigger missing multiplier
    runner.asset_config = {}
    
    res = await runner._analyze_symbol("UNKNOWN_SYMBOL")
    assert res is False

@pytest.mark.asyncio
async def test_analyze_symbol_strategy_error(runner, mock_deps):
    runner.data_fetcher = mock_deps["df"].return_value
    runner.data_fetcher.fetch_all_timeframes = AsyncMock(return_value={
        '1m': [{}], '5m': [{}], '1h': [{}], '4h': [{}], '1d': [{}], '1w': [{}]
    })
    runner.strategy = MagicMock()
    runner.strategy.get_required_timeframes.return_value = ['1m']
    runner.strategy.analyze.side_effect = Exception("Strategy crash")
    
    with pytest.raises(Exception, match="Strategy crash"):
        await runner._analyze_symbol("R_25")

@pytest.mark.asyncio
async def test_multi_asset_scan_cycle_global_pause(runner):
    runner.risk_manager = MagicMock()
    runner.risk_manager.active_trades = []
    runner.risk_manager.can_trade.return_value = (False, "Daily limit reached")
    
    with patch.object(runner, "_analyze_symbol") as mock_analyze:
        await runner._multi_asset_scan_cycle()
        assert not mock_analyze.called


@pytest.mark.asyncio
async def test_analyze_symbol_emits_structured_decision_events(runner, monkeypatch):
    ev = MagicMock()
    ev.broadcast = AsyncMock()
    monkeypatch.setattr("app.bot.runner.event_manager", ev)

    runner.data_fetcher = MagicMock()
    runner.data_fetcher.fetch_all_timeframes = AsyncMock(return_value={
        "1m": [{}], "5m": [{}], "1h": [{}], "4h": [{}], "1d": [{}], "1w": [{}]
    })
    runner.user_stake = 1.0
    runner.asset_config = {"R_25": {"multiplier": 10}}
    runner.telegram_bridge = MagicMock()
    runner.telegram_bridge.notify_signal = AsyncMock()
    runner.telegram_bridge.notify_error = AsyncMock()

    # no-trade branch
    runner.strategy = MagicMock()
    runner.strategy.get_required_timeframes.return_value = ["1m"]
    runner.strategy.analyze.return_value = {
        "can_trade": False,
        "details": {"reason": "No setup", "passed_checks": ["Trend"]},
    }
    assert await runner._analyze_symbol("R_25") is False

    # opportunity branch
    runner.strategy.analyze.return_value = {
        "can_trade": True,
        "signal": "UP",
        "score": 9.5,
        "confidence": 88,
        "take_profit": 101.0,
        "stop_loss": 99.0,
        "details": {"passed_checks": ["Trend", "Momentum"]},
    }
    runner.risk_manager = MagicMock()
    runner.risk_manager.can_open_trade.return_value = (True, "OK")
    runner.trade_engine = MagicMock()
    runner.trade_engine.execute_trade = AsyncMock(return_value=None)
    assert await runner._analyze_symbol("R_25") is False

    payloads = [c.args[0] for c in ev.broadcast.await_args_list if c.args]
    assert any(
        p.get("type") == "bot_decision"
        and p.get("decision") == "no_trade"
        and p.get("phase") == "signal"
        for p in payloads
    )
    assert any(
        p.get("type") == "bot_decision"
        and p.get("decision") == "opportunity_detected"
        and p.get("phase") == "signal"
        for p in payloads
    )
    assert any(
        p.get("type") == "bot_decision"
        and p.get("decision") == "opportunity_taken"
        and p.get("phase") == "execution"
        for p in payloads
    )

@pytest.mark.asyncio
async def test_run_bot_no_token(runner):
    runner.account_id = "user_no_token"
    runner.api_token = None
    
    # Mock bot_state to avoid NameError in runner.py line 535
    with patch("app.bot.runner.bot_state", MagicMock(), create=True):
        await runner._run_bot()
    assert runner.is_running is False
    assert runner.status == BotStatus.ERROR


@pytest.mark.asyncio
async def test_scalping_gate_counters_track_rejections_and_opportunity_rate(runner, mock_deps):
    runner.data_fetcher = mock_deps["df"].return_value
    runner.data_fetcher.fetch_all_timeframes = AsyncMock(return_value={
        "1m": [{}], "5m": [{}], "1h": [{}], "4h": [{}], "1d": [{}], "1w": [{}]
    })

    runner.strategy = MagicMock()
    runner.strategy.get_strategy_name.return_value = "Scalping"
    runner.strategy.get_required_timeframes.return_value = ["1m", "5m", "1h"]

    # First outcome: rejection at crossover gate.
    runner.strategy.analyze.return_value = {
        "can_trade": False,
        "details": {"reason": "No fresh crossover on 1h/5m"},
    }
    assert await runner._analyze_symbol("R_25") is False

    # Second outcome: signal generated by strategy, but blocked by risk gate.
    runner.user_stake = 10.0
    runner.risk_manager = MagicMock()
    runner.risk_manager.can_open_trade.return_value = (False, "Risk blocked")
    runner.strategy.analyze.return_value = {
        "can_trade": True,
        "signal": "UP",
        "score": 8.0,
        "confidence": 80,
        "take_profit": 101.0,
        "stop_loss": 99.0,
        "details": {},
    }
    assert await runner._analyze_symbol("R_25") is False

    metrics = runner.get_scalping_gate_metrics()
    assert metrics["scalping_total_symbol_checks"] == 2
    assert metrics["scalping_signals_generated"] == 1
    assert metrics["scalping_rejections"] == 1
    assert metrics["scalping_opportunity_rate_pct"] == 50.0
    assert metrics["scalping_gate_counters"]["gate_2_trend:no_fresh_crossover"] == 1


def test_recover_runtime_active_trades_throttles_and_recovers(runner):
    runner.risk_manager = MagicMock()
    runner._restore_trade_for_monitoring = MagicMock(side_effect=[True])

    with patch("app.bot.runner.UserTradesService.get_user_active_trades", return_value=[{"contract_id": "c1"}]) as mock_get:
        recovered = runner._recover_runtime_active_trades(min_interval_seconds=1)
        assert recovered == 1
        assert mock_get.call_count == 1

        # Second immediate call should be throttled.
        recovered_again = runner._recover_runtime_active_trades(min_interval_seconds=60)
        assert recovered_again == 0
        assert mock_get.call_count == 1


@pytest.mark.asyncio
async def test_multi_asset_scan_cycle_monitors_active_trades_even_when_global_gate_blocked(runner):
    runner.risk_manager = MagicMock()
    runner.risk_manager.active_trades = ["c1"]
    runner.risk_manager.can_trade.return_value = (False, "Circuit breaker cooldown active")
    runner._recover_runtime_active_trades = MagicMock(return_value=0)
    runner._broadcast_decision = AsyncMock()
    runner._monitor_active_trade = AsyncMock()

    await runner._multi_asset_scan_cycle()

    runner._recover_runtime_active_trades.assert_called_once()
    runner._monitor_active_trade.assert_awaited_once()


@pytest.mark.asyncio
async def test_monitor_active_trade_fallback_closes_when_status_missing_and_not_in_portfolio(runner):
    runner.account_id = "u-test"
    runner.state = MagicMock()

    risk_manager = MagicMock()
    risk_manager.active_trades = ["c1"]
    risk_manager.get_active_trade_info.return_value = {
        "contract_id": "c1",
        "symbol": "R_25",
        "stake": 10.0,
        "entry_source": "manual_imported",
        "manual_tracking": True,
    }
    runner.risk_manager = risk_manager

    trade_engine = MagicMock()
    trade_engine.get_trade_status = AsyncMock(return_value=None)
    trade_engine.portfolio = AsyncMock(return_value={"portfolio": {"contracts": []}})
    runner.trade_engine = trade_engine

    # Prime miss counter so fallback branch executes in this call.
    runner._active_status_miss_counts["c1"] = 2

    with patch("app.bot.runner.UserTradesService.save_trade", return_value={"contract_id": "c1"}) as mock_save:
        await runner._monitor_active_trade()

    risk_manager.record_trade_close.assert_called_once_with("c1", 0.0, "closed")
    runner.state.update_trade.assert_called_once()
    assert "c1" not in runner._active_status_miss_counts
    assert mock_save.called

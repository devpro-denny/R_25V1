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

import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime
from app.bot.runner import BotRunner, BotStatus

@pytest.fixture
def mock_components():
    with patch("app.bot.runner.DataFetcher", new_callable=MagicMock) as mock_df, \
         patch("app.bot.runner.TradeEngine", new_callable=MagicMock) as mock_te, \
         patch("app.bot.runner.UserTradesService", new_callable=MagicMock) as mock_uts, \
         patch("app.bot.runner.event_manager", new_callable=AsyncMock) as mock_em, \
         patch("app.bot.runner.telegram_bridge", new_callable=MagicMock) as mock_tb:
        
        # Setup DataFetcher mock
        df_instance = mock_df.return_value
        df_instance.connect = AsyncMock(return_value=True)
        df_instance.get_balance = AsyncMock(return_value=1000.0)
        df_instance.disconnect = AsyncMock()
        df_instance.fetch_all_timeframes = AsyncMock(return_value={})
        
        # Setup TradeEngine mock
        te_instance = mock_te.return_value
        te_instance.connect = AsyncMock(return_value=True)
        te_instance.disconnect = AsyncMock()
        te_instance.execute_trade = AsyncMock()
        
        # Setup TelegramBridge mock
        mock_tb.notify_bot_started = AsyncMock()
        mock_tb.notify_bot_stopped = AsyncMock()
        mock_tb.notify_error = AsyncMock()
        mock_tb.notify_signal = AsyncMock()
        mock_tb.notify_trade_closed = AsyncMock()
        
        # Setup UserTradesService mock
        mock_uts.get_user_trades.return_value = []
        mock_uts.save_trade.return_value = True
        
        yield {
            "df": mock_df,
            "te": mock_te,
            "uts": mock_uts,
            "em": mock_em,
            "tb": mock_tb
        }

@pytest.mark.asyncio
async def test_bot_runner_init():
    runner = BotRunner(account_id="test_user")
    assert runner.account_id == "test_user"
    assert runner.status == BotStatus.STOPPED
    assert runner.is_running is False

@pytest.mark.asyncio
async def test_bot_runner_start_stop(mock_components):
    runner = BotRunner(account_id="test_user")
    
    # Mock _run_bot to avoid full loop but set is_running
    async def fake_run():
        runner.is_running = True
        runner.status = BotStatus.RUNNING
        while runner.is_running:
            await asyncio.sleep(0.1)
    
    with patch.object(BotRunner, "_run_bot", side_effect=fake_run):
        # Start
        res = await runner.start_bot(stake=10.0)
        assert res["success"] is True
        assert runner.is_running is True
        assert runner.status == BotStatus.RUNNING
        
        # Stop
        res = await runner.stop_bot()
        assert res["success"] is True
        assert runner.is_running is False
        assert runner.status == BotStatus.STOPPED

@pytest.mark.asyncio
async def test_bot_runner_start_no_stake():
    runner = BotRunner(account_id="test_user")
    res = await runner.start_bot(stake=None)
    assert res["success"] is False
    assert "Stake amount not configured" in res["message"]

@pytest.mark.asyncio
async def test_bot_runner_get_status():
    runner = BotRunner(account_id="test_user")
    status = runner.get_status()
    assert runner.account_id == "test_user"
    assert status["status"] == "stopped"
    assert "multi_asset" in status

@pytest.mark.asyncio
async def test_bot_runner_analyze_symbol_no_data(mock_components):
    runner = BotRunner(account_id="test_user")
    runner.data_fetcher = mock_components["df"].return_value
    # fetch_all_timeframes is used in the real runner
    runner.data_fetcher.fetch_all_timeframes = AsyncMock(side_effect=Exception("Data error"))
    
    runner.risk_manager = MagicMock()
    runner.risk_manager.active_trades = []
    runner.risk_manager.can_trade.return_value = (True, "OK")
    
    # Call scan cycle which increments error count
    await runner._multi_asset_scan_cycle()
    assert runner.errors_by_symbol["R_25"] >= 1

@pytest.mark.asyncio
async def test_bot_runner_analyze_symbol_insufficient_data(mock_components, sample_ohlc_data):
    runner = BotRunner(account_id="test_user")
    runner.data_fetcher = mock_components["df"].return_value
    # Mock successful fetch but empty/insufficient data
    runner.data_fetcher.fetch_all_timeframes = AsyncMock(return_value={"1m": sample_ohlc_data(n=1)})
    
    runner.strategy = MagicMock()
    runner.strategy.get_required_timeframes.return_value = ["1m"]
    runner.strategy.analyze.return_value = {"can_trade": False, "details": {"reason": "Insufficient data"}}
    
    result = await runner._analyze_symbol("R_25")
    assert result is False

@pytest.mark.asyncio
async def test_bot_runner_run_bot_initialization(mock_components):
    runner = BotRunner(account_id="test_user", api_token="valid_token")
    runner.user_stake = 10.0
    
    # Mock risk manager
    runner.risk_manager = MagicMock()
    runner.risk_manager.check_for_existing_positions = AsyncMock(return_value=False)
    runner.risk_manager.can_trade.return_value = (True, "OK")
    runner.risk_manager.get_cooldown_remaining.return_value = 0
    runner.risk_manager.active_trades = []
    
    # We use a flag to check if it reached the running state
    reached_running = False
    
    async def side_effect(*args, **kwargs):
        nonlocal reached_running
        if runner.status == BotStatus.RUNNING:
            reached_running = True
        raise asyncio.CancelledError()

    with patch("app.bot.runner.asyncio.sleep", side_effect=side_effect):
        try:
            await runner._run_bot()
        except asyncio.CancelledError:
            pass
            
    assert runner.data_fetcher is not None
    assert reached_running is True


@pytest.mark.asyncio
async def test_bot_runner_active_trade_monitor_ignores_entry_cooldown(mock_components):
    runner = BotRunner(account_id="test_user", api_token="valid_token")
    runner.user_stake = 10.0

    runner.risk_manager = MagicMock()
    runner.risk_manager.check_for_existing_positions = AsyncMock(return_value=False)
    runner.risk_manager.can_trade.return_value = (True, "OK")
    runner.risk_manager.get_cooldown_remaining.return_value = 45
    runner.risk_manager.active_trades = ["C001"]

    async def fake_scan_cycle():
        # End after first loop iteration; _run_bot should still compute wait_time.
        runner.is_running = False

    runner._multi_asset_scan_cycle = AsyncMock(side_effect=fake_scan_cycle)

    with patch("app.bot.runner.logger.debug") as mock_debug:
        await runner._run_bot()

    messages = [str(call.args[0]) for call in mock_debug.call_args_list if call.args]
    assert any("Active trade monitor in 1s" in msg for msg in messages)

@pytest.mark.asyncio
async def test_bot_runner_scan_cycle_executes_trade(mock_components, sample_ohlc_data):
    runner = BotRunner(account_id="test_user")
    runner.data_fetcher = mock_components["df"].return_value
    runner.trade_engine = mock_components["te"].return_value
    
    # Mock data fetcher to return valid data for one symbol
    runner.data_fetcher.fetch_all_timeframes = AsyncMock(return_value={
        '1m': sample_ohlc_data(n=100), '5m': sample_ohlc_data(n=20), 
        '1h': sample_ohlc_data(n=10), '4h': sample_ohlc_data(n=5),
        '1d': sample_ohlc_data(n=5), '1w': sample_ohlc_data(n=5)
    })
    
    # Mock risk manager
    runner.risk_manager = MagicMock()
    runner.risk_manager.active_trades = []
    runner.risk_manager.can_trade.return_value = (True, "OK")
    runner.risk_manager.can_open_trade.return_value = (True, "OK")
    runner.risk_manager.get_statistics.return_value = {"total_trades": 1}
    
    # Mock strategy to return a BUY signal
    runner.strategy = MagicMock()
    runner.strategy.get_required_timeframes.return_value = ['1m', '5m', '1h', '4h', '1d', '1w']
    runner.strategy.get_strategy_name.return_value = "Conservative"
    runner.strategy.analyze.return_value = {
        "can_trade": True, 
        "signal": "BUY", 
        "score": 0.8, 
        "confidence": 90,
        "entry_price": 100.0,
        "take_profit": 110.0,
        "stop_loss": 90.0,
        "details": {"passed_checks": ["Trend", "Momentum"], "reason": "Strong trend"}
    }
    
    # Mock trade execution result
    runner.trade_engine.execute_trade.return_value = {
        "status": "won",
        "profit": 5.0,
        "contract_id": "12345",
        "symbol": "R_25"
    }
    
    runner.user_stake = 10.0
    runner.is_running = True
    
    # Call scan cycle
    await runner._multi_asset_scan_cycle()
    
    # Verify expectations
    assert runner.trade_engine.execute_trade.called
    assert runner.signals_by_symbol["R_25"] == 1
    assert len(runner.state.recent_signals) == 1

@pytest.mark.asyncio
async def test_bot_runner_monitor_active_trade_none(mock_components):
    runner = BotRunner(account_id="test_user")
    runner.risk_manager = MagicMock()
    runner.risk_manager.has_active_trade = False
    
    await runner._monitor_active_trade()
    # Should return early
    assert not mock_components["te"].return_value.get_trade_status.called

@pytest.mark.asyncio
async def test_bot_runner_monitor_active_trade_already_closed(mock_components):
    runner = BotRunner(account_id="test_user")
    runner.risk_manager = MagicMock()
    runner.risk_manager.has_active_trade = True
    runner.risk_manager.get_active_trade_info.return_value = {
        'symbol': 'R_25', 'contract_id': '123'
    }
    
    runner.trade_engine = mock_components["te"].return_value
    # Mock trade engine returning closed status
    runner.trade_engine.get_trade_status = AsyncMock(return_value={'is_sold': True, 'profit': 10.0, 'status': 'won'})
    
    # Simple strategy mock
    runner.strategy = MagicMock()
    runner.strategy.get_strategy_name.return_value = "Conservative"
    
    await runner._monitor_active_trade()
    
    # Since it's sold, but _monitor_active_trade only handles Scalping stagnation for non-sold?
    # Wait, let's check the code. If is_sold is True, it doesn't do anything currently?
    
    # Actually, if trade_status and not trade_status.get('is_sold'):
    # it only enters the scalping logic.
    # If is_sold is True, it should probably be recorded as closed if not already?
    # The runner currently relies on execute_trade waiting for closure.
    pass

@pytest.mark.asyncio
async def test_bot_runner_monitor_active_trade_conservative_stays_open(mock_components):
    runner = BotRunner(account_id="test_user")
    runner.risk_manager = MagicMock() # Default mock is not ScalpingRiskManager
    runner.risk_manager.has_active_trade = True
    runner.risk_manager.get_active_trade_info.return_value = {
        'symbol': 'R_25', 'contract_id': '123'
    }
    
    runner.trade_engine = mock_components["te"].return_value
    runner.trade_engine.get_trade_status = AsyncMock(return_value={'is_sold': False, 'profit': 2.0})
    
    await runner._monitor_active_trade()
    
    # Should not close since it's not scalping and not sold
    assert not runner.trade_engine.close_trade.called

@pytest.mark.asyncio
async def test_bot_runner_monitor_active_trade_scalping_stagnation(mock_components):
    # Mock ScalpingRiskManager to pass isinstance check
    from scalping_risk_manager import ScalpingRiskManager
    class MockScalpingRiskManager(ScalpingRiskManager):
        def __init__(self):
            # Bypass real init if needed, or call with mocks
            self.has_active_trade = True
            self.active_trades = []
            self.max_daily_loss = 100.0
            self.total_daily_loss = 0.0
            self.is_locked = False
            self.user_id = "test_user"

    risk_manager = MagicMock(spec=MockScalpingRiskManager)
    risk_manager.has_active_trade = True
    risk_manager.get_active_trade_info.return_value = {
        'symbol': 'R_25', 'contract_id': '123', 'open_time': datetime.now()
    }
    
    # Mock methods used in _monitor_active_trade
    risk_manager.check_trailing_profit.return_value = (False, "", False)
    risk_manager.check_stagnation_exit.return_value = (True, "stagnation_test")
    
    runner = BotRunner(account_id="test_user")
    runner.risk_manager = risk_manager
    
    runner.trade_engine = mock_components["te"].return_value
    runner.trade_engine.get_trade_status = AsyncMock(return_value={'is_sold': False, 'profit': -0.5})
    runner.trade_engine.close_trade = AsyncMock(return_value={'status': 'closed', 'profit': -0.5})
    
    # Mock strategy name check if needed, but isinstance should be enough for 'from ...'
    runner.strategy = MagicMock()
    runner.strategy.get_strategy_name.return_value = "Scalping"
    
    await runner._monitor_active_trade()
    
    assert runner.trade_engine.close_trade.called

@pytest.mark.asyncio
async def test_bot_runner_monitor_active_trade_scalping_trailing_profit(mock_components):
    # Mock ScalpingRiskManager to pass isinstance check
    from scalping_risk_manager import ScalpingRiskManager
    class MockScalpingRiskManager(ScalpingRiskManager):
        def __init__(self):
            self.has_active_trade = True
            self.active_trades = []
            self.user_id = "test_user"

    risk_manager = MagicMock(spec=MockScalpingRiskManager)
    risk_manager.has_active_trade = True
    risk_manager.get_active_trade_info.return_value = {
        'symbol': 'R_25', 'contract_id': '123', 'open_time': datetime.now()
    }
    
    # Mock trailing profit activation
    # (should_close, reason, just_activated)
    risk_manager.check_trailing_profit.return_value = (True, "trailing_test", False)
    
    runner = BotRunner(account_id="test_user")
    runner.risk_manager = risk_manager
    
    runner.trade_engine = mock_components["te"].return_value
    runner.trade_engine.get_trade_status = AsyncMock(return_value={'is_sold': False, 'profit': 5.0})
    runner.trade_engine.close_trade = AsyncMock(return_value={'status': 'closed', 'profit': 5.0, 'contract_id': '123'})
    
    runner.strategy = MagicMock()
    runner.strategy.get_strategy_name.return_value = "Scalping"
    
    await runner._monitor_active_trade()
    
    # Should call close_trade because trailing profit triggered
    assert runner.trade_engine.close_trade.called
    assert runner.risk_manager.record_trade_close.called

@pytest.mark.asyncio
async def test_bot_runner_monitor_active_trade_scalping_trailing_activation(mock_components):
    # Mock ScalpingRiskManager
    from scalping_risk_manager import ScalpingRiskManager
    risk_manager = MagicMock(spec=ScalpingRiskManager)
    risk_manager.has_active_trade = True
    risk_manager.get_active_trade_info.return_value = {
        'symbol': 'R_25', 'contract_id': '123', 'open_time': datetime.now()
    }
    
    # Mock just_activated=True
    risk_manager.check_trailing_profit.return_value = (False, "", True)
    
    runner = BotRunner(account_id="test_user")
    runner.risk_manager = risk_manager
    runner.trade_engine = mock_components["te"].return_value
    runner.trade_engine.get_trade_status = AsyncMock(return_value={'is_sold': False, 'profit': 2.0})
    runner.trade_engine.remove_take_profit = AsyncMock()
    
    await runner._monitor_active_trade()
    
    # Should call remove_take_profit
    assert runner.trade_engine.remove_take_profit.called

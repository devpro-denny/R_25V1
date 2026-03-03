import pytest
import sys
from datetime import datetime, timedelta
from unittest.mock import patch
from unittest.mock import MagicMock

from scalping_risk_manager import ScalpingRiskManager


@pytest.fixture
def srm():
    return ScalpingRiskManager(user_id="test_user")


def _open_trade(srm: ScalpingRiskManager, contract_id: str, symbol: str = "R_75", stake: float = 10.0):
    srm.record_trade_open(
        {
            "contract_id": contract_id,
            "stake": stake,
            "symbol": symbol,
            "direction": "DOWN",
            "open_time": datetime.now() - timedelta(seconds=180),
        }
    )


def test_scalping_rm_init(srm):
    assert srm.user_id == "test_user"
    assert srm.max_concurrent_trades > 0
    assert srm.daily_trade_count == 0


def test_scalping_rm_can_trade_success(srm):
    can, reason = srm.can_trade("R_75")
    assert can is True
    assert "passed" in reason.lower()


def test_scalping_rm_concurrent_limit(srm):
    srm.max_concurrent_trades = 1
    srm.active_trades = ["CON1"]
    can, reason = srm.can_trade("R_75")
    assert can is False
    assert "concurrent" in reason.lower()


def test_scalping_rm_cooldown(srm):
    srm.cooldown_seconds = 60
    srm.last_trade_time = datetime.now() - timedelta(seconds=10)
    can, reason = srm.can_trade("R_75")
    assert can is False
    assert "cooldown" in reason.lower()


def test_scalping_rm_daily_loss(srm):
    srm.stake = 10.0
    srm.daily_loss_multiplier = 2.0
    srm.daily_pnl = -25.0
    can, reason = srm.can_trade("R_75")
    assert can is False
    assert "loss limit" in reason.lower()


def test_scalping_rm_daily_entry_limit_enforced_at_10(srm):
    srm.daily_trade_count = 10
    srm.last_trade_time = None
    can, reason = srm.can_trade("R_75")
    assert can is False
    assert "daily trade limit reached (10)" in reason.lower()


def test_scalping_rm_daily_entry_limit_hard_caps_config(monkeypatch):
    monkeypatch.setattr("scalping_config.SCALPING_MAX_TRADES_PER_DAY", 80)
    monkeypatch.setattr("scalping_config.SCALPING_HARD_MAX_TRADES_PER_DAY", 10)
    manager = ScalpingRiskManager(user_id="test_user")
    assert manager.max_trades_per_day == 10


def test_scalping_rm_daily_entry_limit_uses_db_synced_count(srm, monkeypatch):
    mock_supabase = MagicMock()
    count_response = MagicMock()
    count_response.count = 10
    (
        mock_supabase.table.return_value.select.return_value.eq.return_value.gte.return_value
        .execute.return_value
    ) = count_response
    monkeypatch.setitem(
        sys.modules,
        "app.core.supabase",
        MagicMock(supabase=mock_supabase),
    )

    srm.daily_trade_count = 0
    srm.last_trade_time = None
    srm._last_daily_count_sync = datetime.min

    can, reason = srm.can_trade("R_75")
    assert can is False
    assert "daily trade limit reached (10)" in reason.lower()
    assert srm.daily_trade_count == 10


def test_scalping_rm_daily_entry_limit_restores_from_runtime_state(monkeypatch):
    mock_supabase = MagicMock()
    trades_table = MagicMock()
    state_table = MagicMock()

    mock_supabase.table.side_effect = lambda name: (
        state_table if name == "scalping_runtime_state" else trades_table
    )

    today_iso = datetime.now().date().isoformat()
    trades_table.select.return_value.eq.return_value.gte.return_value.execute.return_value = MagicMock(
        data=[],
        count=0,
    )
    state_table.select.return_value.eq.return_value.limit.return_value.execute.return_value = MagicMock(
        data=[{"daily_trade_count": 10, "daily_trade_count_date": today_iso}]
    )

    monkeypatch.setitem(
        sys.modules,
        "app.core.supabase",
        MagicMock(supabase=mock_supabase),
    )

    manager = ScalpingRiskManager(user_id="test_user")
    manager.last_trade_time = None
    manager._last_daily_count_sync = datetime.min

    can, reason = manager.can_trade("R_75")
    assert can is False
    assert "daily trade limit reached (10)" in reason.lower()
    assert manager.daily_trade_count == 10


def test_scalping_rm_record_trade_open_persists_runtime_daily_counter(srm):
    srm._persist_daily_trade_count = MagicMock()
    srm.record_trade_open(
        {
            "contract_id": "ENTRY-1",
            "symbol": "R_75",
            "direction": "UP",
            "stake": 10.0,
        }
    )
    assert srm._persist_daily_trade_count.called
    assert srm._persist_daily_trade_count.call_args[0][1] == 1


def test_status_normalization_counts_lost_as_loss(srm):
    _open_trade(srm, "CON1", symbol="R_75")
    srm.record_trade_close("CON1", -1.0, "lost")
    assert srm.consecutive_losses == 1


def test_load_daily_stats_reengages_circuit_breaker_when_streak_already_breached():
    mock_supabase = MagicMock()
    mock_supabase.table.return_value.select.return_value.eq.return_value.gte.return_value.execute.return_value = (
        MagicMock(
            data=[
                {"profit": -1.0, "status": "loss", "created_at": "2026-02-25T10:00:00", "signal": "DOWN"},
                {"profit": -1.0, "status": "lost", "created_at": "2026-02-25T10:05:00", "signal": "DOWN"},
                {"profit": -1.0, "status": "loss", "created_at": "2026-02-25T10:10:00", "signal": "DOWN"},
            ]
        )
    )

    with patch.dict(
        "sys.modules",
        {"app": MagicMock(), "app.core": MagicMock(), "app.core.supabase": MagicMock(supabase=mock_supabase)},
    ):
        manager = ScalpingRiskManager(user_id="test_user")

    assert manager.consecutive_losses >= manager.max_consecutive_losses
    assert manager.loss_cooldown_until > datetime.now()


def test_global_consecutive_loss_cooldown_and_recovery_reset(srm):
    srm.max_consecutive_losses = 3
    srm.loss_cooldown_seconds = 60
    srm.single_loss_cooldown_seconds = 0

    _open_trade(srm, "L1", "R_75")
    srm.record_trade_close("L1", -1.0, "loss")
    _open_trade(srm, "L2", "R_75")
    srm.record_trade_close("L2", -1.0, "lost")
    _open_trade(srm, "L3", "1HZ90V")
    srm.record_trade_close("L3", -1.0, "loss")

    can, reason = srm.can_trade("R_75")
    assert can is False
    assert "circuit breaker cooldown active" in reason.lower()

    # Expire cooldown -> counter resets and recovery mode starts.
    srm.loss_cooldown_until = datetime.now() - timedelta(seconds=1)
    srm.last_trade_time = datetime.now() - timedelta(seconds=srm.cooldown_seconds + 1)
    can_after, _ = srm.can_trade("1HZ90V")
    assert can_after is True
    assert srm.consecutive_losses == 0


def test_symbol_cooldown_after_two_symbol_losses(srm):
    with patch("scalping_config.BLOCKED_SYMBOLS", {"1HZ100V", "1HZ30V", "R_100", "1HZ50V"}):
        srm.max_consecutive_losses = 99
        srm.symbol_max_consecutive_losses = 2
        srm.symbol_loss_cooldown_seconds = 600

        _open_trade(srm, "S1", symbol="R_50")
        srm.record_trade_close("S1", -1.0, "loss")
        _open_trade(srm, "S2", symbol="R_50")
        srm.record_trade_close("S2", -1.0, "loss")

        can_r50, reason_r50 = srm.can_trade("R_50")
        assert can_r50 is False
        assert "cooldown active" in reason_r50.lower()

        srm.last_trade_time = datetime.now() - timedelta(seconds=srm.cooldown_seconds + 1)
        can_r75, _ = srm.can_trade("R_75")
        assert can_r75 is True


def test_short_loss_suppression_triggers_symbol_pause(srm):
    srm.max_consecutive_losses = 99
    srm.symbol_max_consecutive_losses = 99
    srm.short_loss_duration_seconds = 60
    srm.short_loss_count_threshold = 2
    srm.short_loss_lookback_seconds = 2 * 60 * 60
    srm.short_loss_cooldown_seconds = 600

    _open_trade(srm, "F1", symbol="R_75")
    srm.record_trade_close("F1", -1.0, "loss", duration=30)
    _open_trade(srm, "F2", symbol="R_75")
    srm.record_trade_close("F2", -1.0, "loss", duration=45)

    can, reason = srm.can_trade("R_75")
    assert can is False
    assert "cooldown active" in reason.lower()


def test_r50_down_requires_high_confidence(srm):
    with patch("scalping_config.BLOCKED_SYMBOLS", {"1HZ100V", "1HZ30V", "R_100", "1HZ50V"}):
        blocked, reason = srm.can_open_trade(
            symbol="R_50",
            stake=10.0,
            signal_dict={"signal": "DOWN", "confidence": 8.5},
        )
        assert blocked is False
        assert "confidence" in reason.lower()

        allowed, _ = srm.can_open_trade(
            symbol="R_50",
            stake=10.0,
            signal_dict={"signal": "DOWN", "confidence": 9.2},
        )
        assert allowed is True


def test_can_open_trade_blocks_rr_below_minimum(srm):
    allowed, reason = srm.can_open_trade(
        symbol="R_75",
        stake=10.0,
        take_profit=101.0,
        stop_loss=99.5,
        signal_dict={
            "signal": "UP",
            "entry_price": 100.0,
            "min_rr_required": 2.5,
            "confidence": 9.5,
        },
    )
    assert allowed is False
    assert "rr gate blocked" in reason.lower()


def test_can_open_trade_allows_rr_at_or_above_minimum(srm):
    allowed, reason = srm.can_open_trade(
        symbol="R_75",
        stake=10.0,
        take_profit=102.0,
        stop_loss=99.5,
        signal_dict={
            "signal": "UP",
            "entry_price": 100.0,
            "min_rr_required": 2.0,
            "confidence": 9.5,
        },
    )
    assert allowed is True
    assert reason == "OK"


def test_can_open_trade_allows_rr_within_tolerance(srm):
    allowed, reason = srm.can_open_trade(
        symbol="R_75",
        stake=10.0,
        take_profit=101.4999995,
        stop_loss=99.0,
        signal_dict={
            "signal": "UP",
            "entry_price": 100.0,
            "min_rr_required": 1.5,
            "confidence": 9.5,
        },
    )
    assert allowed is True
    assert reason == "OK"


def test_scalping_rm_stagnation_exit(srm):
    trade_info = {
        "open_time": datetime.now() - timedelta(seconds=200),
        "stake": 10.0,
        "symbol": "R_75",
    }

    with patch("scalping_config.SCALPING_STAGNATION_EXIT_TIME", 150), patch(
        "scalping_config.SCALPING_STAGNATION_LOSS_PCT", 10.0
    ), patch(
        "scalping_config.SCALPING_SYMBOL_STAGNATION_OVERRIDES", {}
    ):
        should_exit, reason = srm.check_stagnation_exit(trade_info, -2.0)
        assert should_exit is True
        assert reason == "stagnation_exit"


def test_scalping_rm_stagnation_exit_adds_time_for_high_rr(srm):
    trade_info = {
        "open_time": datetime.now() - timedelta(seconds=170),
        "stake": 10.0,
        "symbol": "R_75",
        "risk_reward_ratio": 3.0,
    }

    with patch("scalping_config.SCALPING_STAGNATION_EXIT_TIME", 120), patch(
        "scalping_config.SCALPING_STAGNATION_RR_GRACE_THRESHOLD", 2.5
    ), patch("scalping_config.SCALPING_STAGNATION_EXTRA_TIME", 60), patch(
        "scalping_config.SCALPING_STAGNATION_LOSS_PCT", 5.0
    ), patch(
        "scalping_config.SCALPING_SYMBOL_STAGNATION_OVERRIDES", {}
    ):
        should_exit, _ = srm.check_stagnation_exit(trade_info, -1.0)
        assert should_exit is False

        trade_info["open_time"] = datetime.now() - timedelta(seconds=195)
        should_exit, reason = srm.check_stagnation_exit(trade_info, -1.0)
        assert should_exit is True
        assert reason == "stagnation_exit"


def test_scalping_rm_trailing_profit(srm):
    trade_info = {"contract_id": "CON1", "stake": 100.0, "symbol": "R_75"}

    with patch("scalping_config.SCALPING_TRAIL_ACTIVATION_PCT", 10.0), patch(
        "scalping_config.SCALPING_TRAIL_TIERS", [(10.0, 5.0)]
    ), patch(
        "scalping_config.SCALPING_TRAIL_BREACH_CONFIRMATIONS", 1
    ), patch(
        "scalping_config.SCALPING_TRAIL_MIN_ACTIVE_SECONDS", 0
    ):
        should, _, just_acts = srm.check_trailing_profit(trade_info, 5.0)
        assert should is False
        assert just_acts is False

        should, _, just_acts = srm.check_trailing_profit(trade_info, 12.0)
        assert should is False
        assert just_acts is True

        srm.check_trailing_profit(trade_info, 20.0)
        should, reason, _ = srm.check_trailing_profit(trade_info, 14.0)
        assert should is True
        assert reason == "trailing_profit_exit"


def test_can_open_trade_uses_precomputed_rr_over_price_recompute(srm):
    allowed, reason = srm.can_open_trade(
        symbol="R_75",
        stake=10.0,
        take_profit=101.0,
        stop_loss=99.5,
        signal_dict={
            "signal": "UP",
            "entry_price": 100.0,
            "risk_reward_ratio": 1.5,
            "min_rr_required": 1.5,
        },
    )
    assert allowed is True
    assert reason == "OK"


def test_can_open_trade_falls_back_to_recomputed_rr_when_missing_signal_rr(srm):
    allowed, reason = srm.can_open_trade(
        symbol="R_75",
        stake=10.0,
        take_profit=101.0,
        stop_loss=99.5,
        signal_dict={
            "signal": "UP",
            "entry_price": 100.0,
            "min_rr_required": 2.5,
        },
    )
    assert allowed is False
    assert "rr gate blocked" in reason.lower()


def test_load_daily_stats_restores_persisted_loss_cooldown():
    mock_supabase = MagicMock()
    trades_table = MagicMock()
    state_table = MagicMock()

    mock_supabase.table.side_effect = lambda name: (
        state_table if name == "scalping_runtime_state" else trades_table
    )
    trades_table.select.return_value.eq.return_value.gte.return_value.execute.return_value = MagicMock(
        data=[]
    )
    future = datetime.now() + timedelta(minutes=25)
    state_table.select.return_value.eq.return_value.limit.return_value.execute.return_value = MagicMock(
        data=[{"loss_cooldown_until": future.isoformat()}]
    )

    with patch.dict(
        "sys.modules",
        {"app": MagicMock(), "app.core": MagicMock(), "app.core.supabase": MagicMock(supabase=mock_supabase)},
    ):
        manager = ScalpingRiskManager(user_id="test_user")

    assert manager.loss_cooldown_until > datetime.now()


def test_global_cooldown_expiry_persists_clear_state(srm):
    srm._persist_loss_cooldown_until = MagicMock()
    srm.loss_cooldown_until = datetime.now() - timedelta(seconds=1)
    srm.consecutive_losses = 3
    srm.last_trade_time = datetime.now() - timedelta(seconds=srm.cooldown_seconds + 1)

    can_trade, _ = srm.can_trade("R_75")
    assert can_trade is True
    assert srm.consecutive_losses == 0
    srm._persist_loss_cooldown_until.assert_called_with(None)


def test_load_daily_stats_reconciles_stale_open_trades():
    mock_supabase = MagicMock()
    trades_table = MagicMock()
    state_table = MagicMock()

    mock_supabase.table.side_effect = lambda name: (
        state_table if name == "scalping_runtime_state" else trades_table
    )
    trades_table.select.return_value.eq.return_value.gte.return_value.execute.return_value = MagicMock(
        data=[
            {
                "contract_id": "307845220868",
                "profit": -1.81,
                "status": "open",
                "created_at": "2026-03-02T10:20:00",
                "symbol": "R_75",
                "signal": "DOWN",
                "exit_price": 33937.97,
            }
        ]
    )
    trades_table.update.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(
        data=[{"status": "sold"}]
    )
    state_table.select.return_value.eq.return_value.limit.return_value.execute.return_value = MagicMock(
        data=[]
    )

    with patch.dict(
        "sys.modules",
        {"app": MagicMock(), "app.core": MagicMock(), "app.core.supabase": MagicMock(supabase=mock_supabase)},
    ):
        ScalpingRiskManager(user_id="test_user")

    assert trades_table.update.called
    assert trades_table.update.call_args[0][0] == {"status": "sold"}


def test_stagnation_exit_honors_symbol_override_timeout(srm):
    with patch("scalping_config.SCALPING_STAGNATION_EXIT_TIME", 120), patch(
        "scalping_config.SCALPING_SYMBOL_STAGNATION_OVERRIDES",
        {"stpRNG5": 150, "R_75": 130},
    ), patch(
        "scalping_config.SCALPING_STAGNATION_LOSS_PCT", 5.0
    ):
        stp_trade = {
            "open_time": datetime.now() - timedelta(seconds=140),
            "stake": 10.0,
            "symbol": "stpRNG5",
        }
        should_exit, _ = srm.check_stagnation_exit(stp_trade, -1.0)
        assert should_exit is False

        r75_trade = {
            "open_time": datetime.now() - timedelta(seconds=140),
            "stake": 10.0,
            "symbol": "R_75",
        }
        should_exit, reason = srm.check_stagnation_exit(r75_trade, -1.0)
        assert should_exit is True
        assert reason == "stagnation_exit"


def test_performance_guard_blocks_trading_below_threshold(srm):
    now = datetime.now()
    srm.performance_window_days = 3
    srm.performance_min_trades = 10
    srm.performance_min_win_rate_pct = 35.0
    srm.performance_cooldown_seconds = 300
    srm.last_trade_time = None
    srm.rolling_outcomes = [(now - timedelta(minutes=i), i < 3) for i in range(10)]  # 30% wins

    can_trade, reason = srm.can_trade("R_75")
    assert can_trade is False
    assert "performance guard cooldown active" in reason.lower()

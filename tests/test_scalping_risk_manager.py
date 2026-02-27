import pytest
from datetime import datetime, timedelta
from unittest.mock import patch
from unittest.mock import MagicMock

from scalping_risk_manager import ScalpingRiskManager


@pytest.fixture
def srm():
    return ScalpingRiskManager(user_id="test_user")


def _open_trade(srm: ScalpingRiskManager, contract_id: str, symbol: str = "R_25", stake: float = 10.0):
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
    can, reason = srm.can_trade("R_25")
    assert can is True
    assert "passed" in reason.lower()


def test_scalping_rm_concurrent_limit(srm):
    srm.max_concurrent_trades = 1
    srm.active_trades = ["CON1"]
    can, reason = srm.can_trade("R_25")
    assert can is False
    assert "concurrent" in reason.lower()


def test_scalping_rm_cooldown(srm):
    srm.cooldown_seconds = 60
    srm.last_trade_time = datetime.now() - timedelta(seconds=10)
    can, reason = srm.can_trade("R_25")
    assert can is False
    assert "cooldown" in reason.lower()


def test_scalping_rm_daily_loss(srm):
    srm.stake = 10.0
    srm.daily_loss_multiplier = 2.0
    srm.daily_pnl = -25.0
    can, reason = srm.can_trade("R_25")
    assert can is False
    assert "loss limit" in reason.lower()


def test_status_normalization_counts_lost_as_loss(srm):
    _open_trade(srm, "CON1", symbol="R_25")
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

    _open_trade(srm, "L1", "R_25")
    srm.record_trade_close("L1", -1.0, "loss")
    _open_trade(srm, "L2", "R_75")
    srm.record_trade_close("L2", -1.0, "lost")
    _open_trade(srm, "L3", "R_100")
    srm.record_trade_close("L3", -1.0, "loss")

    can, reason = srm.can_trade("R_25")
    assert can is False
    assert "circuit breaker cooldown active" in reason.lower()

    # Expire cooldown -> counter resets and recovery mode starts.
    srm.loss_cooldown_until = datetime.now() - timedelta(seconds=1)
    srm.last_trade_time = datetime.now() - timedelta(seconds=srm.cooldown_seconds + 1)
    can_after, _ = srm.can_trade("R_25")
    assert can_after is True
    assert srm.consecutive_losses == 0


def test_symbol_cooldown_after_two_symbol_losses(srm):
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
    can_r25, _ = srm.can_trade("R_25")
    assert can_r25 is True


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
        symbol="R_25",
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
        symbol="R_25",
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


def test_scalping_rm_stagnation_exit(srm):
    trade_info = {
        "open_time": datetime.now() - timedelta(seconds=200),
        "stake": 10.0,
        "symbol": "R_25",
    }

    with patch("scalping_config.SCALPING_STAGNATION_EXIT_TIME", 150), patch(
        "scalping_config.SCALPING_STAGNATION_LOSS_PCT", 10.0
    ):
        should_exit, reason = srm.check_stagnation_exit(trade_info, -2.0)
        assert should_exit is True
        assert reason == "stagnation_exit"


def test_scalping_rm_stagnation_exit_adds_time_for_high_rr(srm):
    trade_info = {
        "open_time": datetime.now() - timedelta(seconds=170),
        "stake": 10.0,
        "symbol": "R_25",
        "risk_reward_ratio": 3.0,
    }

    with patch("scalping_config.SCALPING_STAGNATION_EXIT_TIME", 120), patch(
        "scalping_config.SCALPING_STAGNATION_RR_GRACE_THRESHOLD", 2.5
    ), patch("scalping_config.SCALPING_STAGNATION_EXTRA_TIME", 60), patch(
        "scalping_config.SCALPING_STAGNATION_LOSS_PCT", 5.0
    ):
        should_exit, _ = srm.check_stagnation_exit(trade_info, -1.0)
        assert should_exit is False

        trade_info["open_time"] = datetime.now() - timedelta(seconds=190)
        should_exit, reason = srm.check_stagnation_exit(trade_info, -1.0)
        assert should_exit is True
        assert reason == "stagnation_exit"


def test_scalping_rm_trailing_profit(srm):
    trade_info = {"contract_id": "CON1", "stake": 100.0, "symbol": "R_25"}

    with patch("scalping_config.SCALPING_TRAIL_ACTIVATION_PCT", 10.0), patch(
        "scalping_config.SCALPING_TRAIL_TIERS", [(10.0, 5.0)]
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

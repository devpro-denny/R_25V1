import pandas as pd
import pytest
from unittest.mock import patch

from risefallbot.rf_strategy import RiseFallStrategy


@pytest.fixture
def strategy():
    with patch("risefallbot.rf_strategy.rf_config") as mock_config:
        mock_config.RF_SYMBOLS = ["stpRNG1", "stpRNG2"]
        mock_config.RF_TICK_SEQUENCE_LENGTH = 3
        mock_config.RF_CONFIRMATION_TICKS = 2
        mock_config.RF_TICK_HISTORY_COUNT = 6
        mock_config.RF_REQUIRE_CONSECUTIVE_DIRECTION = True
        mock_config.RF_REQUIRE_FRESH_SIGNAL_AFTER_COOLDOWN = True
        mock_config.RF_DEFAULT_STAKE = 10.0
        mock_config.RF_CONTRACT_DURATION = 3
        mock_config.RF_DURATION_UNIT = "t"
        yield RiseFallStrategy()


def _ticks(prices):
    return pd.DataFrame(
        {
            "quote": prices,
            "timestamp": [1700000000 + idx for idx in range(len(prices))],
            "datetime": pd.date_range("2026-01-01", periods=len(prices), freq="s"),
        }
    )


def test_analyze_insufficient_tick_history(strategy):
    assert strategy.analyze(
        data_ticks=_ticks([100.0, 100.1, 100.2, 100.3, 100.4]),
        symbol="stpRNG1",
    ) is None
    meta = strategy.get_last_analysis("stpRNG1")
    assert meta["code"] == "insufficient_tick_history"


def test_analyze_empty_data(strategy):
    assert strategy.analyze(data_ticks=None, symbol="stpRNG1") is None
    assert strategy.analyze(data_ticks=pd.DataFrame(), symbol="stpRNG1") is None


def test_analyze_upward_sequence_generates_fall(strategy):
    result = strategy.analyze(
        data_ticks=_ticks([100.0, 100.2, 100.4, 100.8, 100.8, 100.7]),
        symbol="stpRNG1",
    )

    assert result is not None
    assert result["direction"] == "PUT"
    assert result["trade_label"] == "FALL"
    assert result["sequence_direction"] == "up"
    assert result["burst_movements"] == [0.2, 0.2, 0.4]
    assert result["confirmation_movements"] == [0.0, -0.1]
    assert result["stake"] == 10.0
    assert result["duration"] == 3
    assert result["duration_unit"] == "t"


def test_analyze_downward_sequence_generates_rise(strategy):
    result = strategy.analyze(
        data_ticks=_ticks([100.8, 100.6, 100.4, 100.1, 100.2, 100.2]),
        symbol="stpRNG2",
    )

    assert result is not None
    assert result["direction"] == "CALL"
    assert result["trade_label"] == "RISE"
    assert result["sequence_direction"] == "down"
    assert result["confirmation_movements"] == [0.1, 0.0]


def test_analyze_mixed_or_flat_sequence_rejected(strategy):
    assert strategy.analyze(
        data_ticks=_ticks([100.0, 100.2, 100.1, 100.3, 100.2, 100.1]),
        symbol="stpRNG1",
    ) is None
    assert strategy.get_last_analysis("stpRNG1")["code"] == "mixed_tick_sequence"

    assert strategy.analyze(
        data_ticks=_ticks([100.0, 100.2, 100.2, 100.4, 100.3, 100.2]),
        symbol="stpRNG1",
    ) is None
    assert strategy.get_last_analysis("stpRNG1")["code"] == "mixed_tick_sequence"


def test_confirmation_that_continues_burst_is_rejected(strategy):
    assert strategy.analyze(
        data_ticks=_ticks([100.0, 100.2, 100.4, 100.6, 100.8, 101.0]),
        symbol="stpRNG1",
    ) is None
    assert strategy.get_last_analysis("stpRNG1")["code"] == "confirmation_rejected"

    assert strategy.analyze(
        data_ticks=_ticks([100.8, 100.6, 100.4, 100.2, 100.0, 99.8]),
        symbol="stpRNG2",
    ) is None
    assert strategy.get_last_analysis("stpRNG2")["code"] == "confirmation_rejected"


def test_reuses_of_same_sequence_are_rejected_as_not_fresh(strategy):
    ticks = _ticks([100.0, 100.2, 100.4, 100.8, 100.8, 100.7])
    first = strategy.analyze(data_ticks=ticks, symbol="stpRNG1")
    second = strategy.analyze(data_ticks=ticks, symbol="stpRNG1")

    assert first is not None
    assert second is None
    assert strategy.get_last_analysis("stpRNG1")["code"] == "signal_not_fresh"


def test_symbol_not_allowed_rejected(strategy):
    assert strategy.analyze(
        data_ticks=_ticks([100.0, 100.2, 100.4, 100.8, 100.8, 100.7]),
        symbol="R_25",
    ) is None
    assert strategy.get_last_analysis("R_25")["code"] == "symbol_not_allowed"


def test_metadata(strategy):
    assert strategy.get_strategy_name() == "RiseFall"
    assert strategy.get_required_timeframes() == ["ticks"]

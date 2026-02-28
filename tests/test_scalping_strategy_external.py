import pandas as pd
from unittest.mock import patch

import scalping_strategy as scalping_pkg
from scalping_strategy.strategy_external import ScalpingStrategy


def _mock_ohlc(rows: int = 60, base: float = 100.0) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=rows, freq="1min")
    df = pd.DataFrame(
        {
            "open": [base] * rows,
            "high": [base + 1.0] * rows,
            "low": [base - 1.0] * rows,
            "close": [base] * rows,
        },
        index=idx,
    )
    return df


def test_external_strategy_metadata():
    strategy = ScalpingStrategy()
    assert strategy.get_strategy_name() == "Scalping"
    assert strategy.get_required_timeframes() == ["1h", "5m", "1m"]
    assert "R_50" in strategy.get_symbols()


def test_external_analyze_missing_data():
    strategy = ScalpingStrategy()
    result = strategy.analyze(data_1h=None, data_5m=pd.DataFrame(), data_1m=pd.DataFrame())
    assert result["can_trade"] is False
    assert "Missing required timeframe data" in result["details"]["reason"]


def test_external_determine_trend_up():
    strategy = ScalpingStrategy()
    df = pd.DataFrame({"close": [float(i) for i in range(1, 80)]})
    assert strategy._determine_trend(df, "1h") == "UP"


def test_external_is_parabolic_spike_true():
    strategy = ScalpingStrategy()
    df = _mock_ohlc(rows=10, base=100.0)
    df.iloc[-3:, df.columns.get_loc("open")] = 100.0
    df.iloc[-3:, df.columns.get_loc("close")] = 103.0
    assert strategy._is_parabolic_spike(df, atr=1.0) is True


def test_external_analyze_rejects_weak_adx(monkeypatch):
    strategy = ScalpingStrategy()
    data = _mock_ohlc()

    monkeypatch.setattr(
        scalping_pkg,
        "calculate_rsi",
        lambda _df, period=14: pd.Series([60.0] * len(data), index=data.index),
    )
    monkeypatch.setattr(
        scalping_pkg,
        "calculate_adx",
        lambda _df, period=14: pd.Series([10.0] * len(data), index=data.index),
    )

    with patch.object(ScalpingStrategy, "_determine_trend", side_effect=["UP", "UP"]):
        result = strategy.analyze(data_1h=data, data_5m=data, data_1m=data, symbol="R_50")

    assert result["can_trade"] is False
    assert "Weak trend" in result["details"]["reason"]


def test_external_analyze_success_up(monkeypatch):
    strategy = ScalpingStrategy()
    data = _mock_ohlc()

    # Create a momentum candle on the live bar used by strategy_external.
    data.iloc[-1, data.columns.get_loc("open")] = 100.0
    data.iloc[-1, data.columns.get_loc("close")] = 100.8
    data.iloc[-1, data.columns.get_loc("high")] = 101.0
    data.iloc[-1, data.columns.get_loc("low")] = 99.6

    monkeypatch.setattr(
        scalping_pkg,
        "calculate_rsi",
        lambda _df, period=14: pd.Series([60.0] * len(data), index=data.index),
    )
    monkeypatch.setattr(
        scalping_pkg,
        "calculate_adx",
        lambda _df, period=14: pd.Series([25.0] * len(data), index=data.index),
    )

    with patch.object(ScalpingStrategy, "_determine_trend", side_effect=["UP", "UP"]), patch.object(
        ScalpingStrategy, "_calculate_atr", return_value=0.5
    ), patch.object(ScalpingStrategy, "_is_parabolic_spike", return_value=False):
        result = strategy.analyze(data_1h=data, data_5m=data, data_1m=data, symbol="R_50")

    assert result["can_trade"] is True
    assert result["signal"] == "UP"
    assert "take_profit" in result
    assert "stop_loss" in result
    assert result["risk_reward_ratio"] >= 1.5
    assert result["min_rr_required"] == scalping_pkg.config.SCALPING_MIN_RR_RATIO


def test_external_analyze_allows_rr_within_tolerance(monkeypatch):
    strategy = ScalpingStrategy()
    data = _mock_ohlc()

    data.iloc[-1, data.columns.get_loc("open")] = 100.0
    data.iloc[-1, data.columns.get_loc("close")] = 100.8
    data.iloc[-1, data.columns.get_loc("high")] = 101.0
    data.iloc[-1, data.columns.get_loc("low")] = 99.6

    monkeypatch.setattr(
        scalping_pkg,
        "calculate_rsi",
        lambda _df, period=14: pd.Series([60.0] * len(data), index=data.index),
    )
    monkeypatch.setattr(
        scalping_pkg,
        "calculate_adx",
        lambda _df, period=14: pd.Series([25.0] * len(data), index=data.index),
    )
    monkeypatch.setattr(scalping_pkg.config, "SCALPING_SL_ATR_MULTIPLIER", 2.0)
    monkeypatch.setattr(scalping_pkg.config, "SCALPING_TP_ATR_MULTIPLIER", 2.999999)
    monkeypatch.setattr(scalping_pkg.config, "SCALPING_MIN_RR_RATIO", 1.5)
    monkeypatch.setattr(scalping_pkg.config, "SCALPING_RR_TOLERANCE", 1e-6)

    with patch.object(ScalpingStrategy, "_determine_trend", side_effect=["UP", "UP"]), patch.object(
        ScalpingStrategy, "_calculate_atr", return_value=0.5
    ), patch.object(ScalpingStrategy, "_is_parabolic_spike", return_value=False):
        result = strategy.analyze(data_1h=data, data_5m=data, data_1m=data, symbol="R_50")

    assert result["can_trade"] is True
    assert result["risk_reward_ratio"] < 1.5
    assert result["risk_reward_ratio"] + scalping_pkg.config.SCALPING_RR_TOLERANCE >= 1.5

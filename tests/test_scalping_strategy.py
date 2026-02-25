import pandas as pd
import pytest
from unittest.mock import patch

from scalping_strategy import ScalpingStrategy
from scalping_strategy import config as scalping_config


@pytest.fixture
def strategy():
    return ScalpingStrategy()


@pytest.fixture
def mock_ohlc():
    """Create stable 60-candle OHLC data for deterministic scalping tests."""
    dates = pd.date_range("2024-01-01", periods=60, freq="1min")
    return pd.DataFrame(
        {
            "open": [100.0] * 60,
            "high": [101.0] * 60,
            "low": [99.0] * 60,
            "close": [100.0] * 60,
        },
        index=dates,
    )


class TestScalpingStrategyInit:
    def test_init_success(self, strategy):
        assert strategy.get_strategy_name() == "Scalping"
        assert "1h" in strategy.get_required_timeframes()
        assert "5m" in strategy.get_required_timeframes()
        assert "1m" in strategy.get_required_timeframes()


class TestScalpingStrategyTrend:
    def test_determine_trend_up_on_fresh_crossover(self, strategy):
        df = pd.DataFrame({"close": [100.0] * 60})
        fast = pd.Series([100.0] * 60)
        slow = pd.Series([100.0] * 60)
        fast.iloc[-3] = 99.0
        slow.iloc[-3] = 100.0
        fast.iloc[-2] = 101.0
        slow.iloc[-2] = 100.0

        with patch.object(ScalpingStrategy, "_calculate_ema") as mock_ema:
            mock_ema.side_effect = [fast, slow]
            trend = strategy._determine_trend(df, "1h", fast_period=20, slow_period=50)
            assert trend == "UP"

    def test_determine_trend_down_on_fresh_crossover(self, strategy):
        df = pd.DataFrame({"close": [100.0] * 60})
        fast = pd.Series([100.0] * 60)
        slow = pd.Series([100.0] * 60)
        fast.iloc[-3] = 101.0
        slow.iloc[-3] = 100.0
        fast.iloc[-2] = 99.0
        slow.iloc[-2] = 100.0

        with patch.object(ScalpingStrategy, "_calculate_ema") as mock_ema:
            mock_ema.side_effect = [fast, slow]
            trend = strategy._determine_trend(df, "1h", fast_period=20, slow_period=50)
            assert trend == "DOWN"

    def test_determine_trend_none_without_fresh_cross(self, strategy):
        df = pd.DataFrame({"close": [100.0] * 60})
        fast = pd.Series([101.0] * 60)
        slow = pd.Series([100.0] * 60)

        with patch.object(ScalpingStrategy, "_calculate_ema") as mock_ema:
            mock_ema.side_effect = [fast, slow]
            trend = strategy._determine_trend(df, "1h", fast_period=20, slow_period=50)
            assert trend is None

    def test_determine_trend_uses_recent_crossover_lookback(self, strategy):
        df = pd.DataFrame({"close": [100.0] * 60})
        fast = pd.Series([100.0] * 60)
        slow = pd.Series([100.0] * 60)
        # Crossover happened one closed candle earlier (between -4 and -3),
        # not on the latest closed pair (-3 and -2).
        fast.iloc[-4] = 99.0
        slow.iloc[-4] = 100.0
        fast.iloc[-3] = 101.0
        slow.iloc[-3] = 100.0
        fast.iloc[-2] = 101.0
        slow.iloc[-2] = 100.0

        with patch.object(ScalpingStrategy, "_calculate_ema") as mock_ema:
            mock_ema.side_effect = [fast, slow, fast, slow]
            trend_default = strategy._determine_trend(df, "5m", fast_period=9, slow_period=21)
            trend_lookback = strategy._determine_trend(
                df,
                "5m",
                fast_period=9,
                slow_period=21,
                crossover_lookback=3,
            )
            assert trend_default is None
            assert trend_lookback == "UP"

    def test_determine_trend_fallback_alignment_with_slope(self, strategy):
        df = pd.DataFrame({"close": [100.0] * 60})
        fast = pd.Series([100.0] * 60)
        slow = pd.Series([100.0] * 60)
        # No crossover on latest closed pair, but aligned with positive slope.
        fast.iloc[-3] = 100.10
        slow.iloc[-3] = 100.00
        fast.iloc[-2] = 100.20
        slow.iloc[-2] = 100.10

        with patch.object(ScalpingStrategy, "_calculate_ema") as mock_ema:
            mock_ema.side_effect = [fast, slow]
            trend = strategy._determine_trend(
                df,
                "5m",
                fast_period=9,
                slow_period=21,
                crossover_lookback=1,
                allow_alignment_fallback=True,
                min_slope_pct=0.005,
            )
            assert trend == "UP"

    def test_determine_trend_fallback_rejects_flat_slope(self, strategy):
        df = pd.DataFrame({"close": [100.0] * 60})
        fast = pd.Series([100.0] * 60)
        slow = pd.Series([100.0] * 60)
        # Aligned but nearly flat slope below threshold.
        fast.iloc[-3] = 100.1000
        slow.iloc[-3] = 100.0000
        fast.iloc[-2] = 100.1001
        slow.iloc[-2] = 100.0001

        with patch.object(ScalpingStrategy, "_calculate_ema") as mock_ema:
            mock_ema.side_effect = [fast, slow]
            trend = strategy._determine_trend(
                df,
                "5m",
                fast_period=9,
                slow_period=21,
                crossover_lookback=1,
                allow_alignment_fallback=True,
                min_slope_pct=0.005,
            )
            assert trend is None


class TestScalpingStrategyAnalyze:
    def test_analyze_missing_data(self, strategy):
        result = strategy.analyze(data_1h=None, data_5m=pd.DataFrame(), data_1m=pd.DataFrame())
        assert result["can_trade"] is False
        assert "Missing" in result["details"]["reason"]

    def test_analyze_insufficient_data_length(self, strategy):
        short_df = pd.DataFrame({"close": [100] * 10})
        result = strategy.analyze(data_1h=short_df, data_5m=short_df, data_1m=short_df)
        assert result["can_trade"] is False
        assert "Insufficient data" in result["details"]["reason"]

    def test_analyze_uses_extended_5m_crossover_lookback(self, strategy, mock_ohlc):
        with patch.object(ScalpingStrategy, "_determine_bias", return_value="UP"), patch.object(
            ScalpingStrategy, "_determine_trend", return_value=None
        ) as mock_trend:
            result = strategy.analyze(data_1h=mock_ohlc, data_5m=mock_ohlc, data_1m=mock_ohlc)
            assert result["can_trade"] is False
            assert "No fresh crossover on 5m" in result["details"]["reason"]
            mock_trend.assert_called_once()
            args, kwargs = mock_trend.call_args
            assert args == (mock_ohlc, "5m")
            assert kwargs["fast_period"] == 9
            assert kwargs["slow_period"] == 21
            assert kwargs["crossover_lookback"] == 5
            assert kwargs["allow_alignment_fallback"] is True
            assert kwargs["min_slope_pct"] == scalping_config.SCALPING_5M_EMA_SLOPE_MIN_PCT

    @patch("scalping_strategy.calculate_rsi")
    @patch("scalping_strategy.calculate_adx")
    def test_analyze_trend_mismatch(self, mock_adx, mock_rsi, strategy, mock_ohlc):
        with patch.object(ScalpingStrategy, "_determine_bias", return_value="UP"), patch.object(
            ScalpingStrategy, "_determine_trend", return_value="DOWN"
        ):
            result = strategy.analyze(data_1h=mock_ohlc, data_5m=mock_ohlc, data_1m=mock_ohlc)
            assert result["can_trade"] is False
            assert "Trend mismatch" in result["details"]["reason"]

    @patch("scalping_strategy.calculate_rsi")
    @patch("scalping_strategy.calculate_adx")
    def test_analyze_weak_adx(self, mock_adx, mock_rsi, strategy, mock_ohlc):
        with patch.object(ScalpingStrategy, "_determine_bias", return_value="UP"), patch.object(
            ScalpingStrategy, "_determine_trend", return_value="UP"
        ):
            mock_adx.return_value = pd.Series([10.0] * 60)
            mock_rsi.return_value = pd.Series([60.0] * 60)
            result = strategy.analyze(data_1h=mock_ohlc, data_5m=mock_ohlc, data_1m=mock_ohlc)
            assert result["can_trade"] is False
            assert "Weak trend" in result["details"]["reason"]

    @patch("scalping_strategy.calculate_rsi")
    @patch("scalping_strategy.calculate_adx")
    def test_analyze_adx_declining(self, mock_adx, mock_rsi, strategy, mock_ohlc):
        with patch.object(ScalpingStrategy, "_determine_bias", return_value="UP"), patch.object(
            ScalpingStrategy, "_determine_trend", return_value="UP"
        ):
            mock_rsi.return_value = pd.Series([60.0] * 60)
            mock_adx.return_value = pd.Series([30.0] * 57 + [30.0, 20.0, 20.0])
            result = strategy.analyze(data_1h=mock_ohlc, data_5m=mock_ohlc, data_1m=mock_ohlc)
            assert result["can_trade"] is False
            assert "ADX declining" in result["details"]["reason"]

    @patch("scalping_strategy.calculate_rsi")
    @patch("scalping_strategy.calculate_adx")
    def test_analyze_rsi_out_of_range_up(self, mock_adx, mock_rsi, strategy, mock_ohlc):
        with patch.object(ScalpingStrategy, "_determine_bias", return_value="UP"), patch.object(
            ScalpingStrategy, "_determine_trend", return_value="UP"
        ):
            mock_adx.return_value = pd.Series([25.0] * 60)
            mock_rsi.return_value = pd.Series([40.0] * 60)
            result = strategy.analyze(data_1h=mock_ohlc, data_5m=mock_ohlc, data_1m=mock_ohlc)
            assert result["can_trade"] is False
            assert "RSI" in result["details"]["reason"]

    @patch("scalping_strategy.calculate_rsi")
    @patch("scalping_strategy.calculate_adx")
    def test_analyze_no_momentum_breakout(self, mock_adx, mock_rsi, strategy, mock_ohlc):
        with patch.object(ScalpingStrategy, "_determine_bias", return_value="UP"), patch.object(
            ScalpingStrategy, "_determine_trend", return_value="UP"
        ), patch.object(ScalpingStrategy, "_calculate_atr", return_value=10.0):
            mock_adx.return_value = pd.Series([25.0] * 60)
            mock_rsi.return_value = pd.Series([60.0] * 60)

            # Signal candle is tiny (uses iloc[-2], not iloc[-1]).
            mock_ohlc.iloc[-2, mock_ohlc.columns.get_loc("open")] = 100.0
            mock_ohlc.iloc[-2, mock_ohlc.columns.get_loc("close")] = 100.1

            result = strategy.analyze(data_1h=mock_ohlc, data_5m=mock_ohlc, data_1m=mock_ohlc)
            assert result["can_trade"] is False
            assert "momentum" in result["details"]["reason"].lower()

    @patch("scalping_strategy.calculate_rsi")
    @patch("scalping_strategy.calculate_adx")
    def test_analyze_parabolic_spike_fails(self, mock_adx, mock_rsi, strategy, mock_ohlc):
        with patch.object(ScalpingStrategy, "_determine_bias", return_value="UP"), patch.object(
            ScalpingStrategy, "_determine_trend", return_value="UP"
        ), patch.object(ScalpingStrategy, "_calculate_atr", return_value=0.5), patch.object(
            ScalpingStrategy, "_is_parabolic_spike", return_value=True
        ):
            mock_adx.return_value = pd.Series([25.0] * 60)
            mock_rsi.return_value = pd.Series([60.0] * 60)

            # Strong bullish signal candle on iloc[-2] to pass momentum/body checks first.
            mock_ohlc.iloc[-2, mock_ohlc.columns.get_loc("open")] = 100.0
            mock_ohlc.iloc[-2, mock_ohlc.columns.get_loc("close")] = 100.8
            mock_ohlc.iloc[-2, mock_ohlc.columns.get_loc("high")] = 100.9
            mock_ohlc.iloc[-2, mock_ohlc.columns.get_loc("low")] = 100.0

            result = strategy.analyze(data_1h=mock_ohlc, data_5m=mock_ohlc, data_1m=mock_ohlc)
            assert result["can_trade"] is False
            assert "Parabolic" in result["details"]["reason"]

    @patch("scalping_strategy.calculate_rsi")
    @patch("scalping_strategy.calculate_adx")
    def test_analyze_zone_proximity_gate(self, mock_adx, mock_rsi, strategy, mock_ohlc):
        with patch.object(ScalpingStrategy, "_determine_bias", return_value="UP"), patch.object(
            ScalpingStrategy, "_determine_trend", return_value="UP"
        ), patch.object(ScalpingStrategy, "_calculate_atr", return_value=0.5), patch.object(
            ScalpingStrategy, "_is_parabolic_spike", return_value=False
        ):
            mock_adx.return_value = pd.Series([25.0] * 60)
            mock_rsi.return_value = pd.Series([60.0] * 60)

            mock_ohlc.iloc[-2, mock_ohlc.columns.get_loc("open")] = 100.0
            mock_ohlc.iloc[-2, mock_ohlc.columns.get_loc("close")] = 100.8
            mock_ohlc.iloc[-2, mock_ohlc.columns.get_loc("high")] = 100.9
            mock_ohlc.iloc[-2, mock_ohlc.columns.get_loc("low")] = 100.0

            result = strategy.analyze(data_1h=mock_ohlc, data_5m=mock_ohlc, data_1m=mock_ohlc)
            assert result["can_trade"] is False
            assert "Price not near any key zone" in result["details"]["reason"]

    @patch("scalping_strategy.calculate_rsi")
    @patch("scalping_strategy.calculate_adx")
    def test_analyze_success_up(self, mock_adx, mock_rsi, strategy, mock_ohlc):
        with patch.object(ScalpingStrategy, "_determine_bias", return_value="UP"), patch.object(
            ScalpingStrategy, "_determine_trend", return_value="UP"
        ), patch.object(ScalpingStrategy, "_calculate_atr", return_value=0.5), patch.object(
            ScalpingStrategy, "_is_parabolic_spike", return_value=False
        ), patch.object(ScalpingStrategy, "_price_near_zone", return_value=(True, {"level": 100.5, "type": "support"})), patch.object(
            ScalpingStrategy, "_confirm_zone_rejection", return_value=True
        ), patch.object(
            ScalpingStrategy, "_detect_1m_pattern", return_value="engulfing"
        ):
            mock_adx.return_value = pd.Series([25.0] * 60)
            mock_rsi.return_value = pd.Series([60.0] * 60)

            mock_ohlc.iloc[-2, mock_ohlc.columns.get_loc("open")] = 100.0
            mock_ohlc.iloc[-2, mock_ohlc.columns.get_loc("close")] = 100.8
            mock_ohlc.iloc[-2, mock_ohlc.columns.get_loc("high")] = 100.9
            mock_ohlc.iloc[-2, mock_ohlc.columns.get_loc("low")] = 100.0
            mock_ohlc.iloc[-5, mock_ohlc.columns.get_loc("close")] = 99.9
            mock_ohlc.iloc[-4, mock_ohlc.columns.get_loc("close")] = 100.1
            mock_ohlc.iloc[-3, mock_ohlc.columns.get_loc("close")] = 100.4
            mock_ohlc.iloc[-1, mock_ohlc.columns.get_loc("close")] = 100.9

            result = strategy.analyze(data_1h=mock_ohlc, data_5m=mock_ohlc, data_1m=mock_ohlc)
            assert result["can_trade"] is True
            assert result["signal"] == "UP"
            assert result["confidence"] == 8.5
            assert result["details"]["zone_type"] == "support"
            assert result["details"]["pa_pattern"] == "engulfing"
            assert "take_profit" in result
            assert "stop_loss" in result


class TestScalpingStrategyEdgeCases:
    def test_confirm_1m_directional_sequence_up(self, strategy, mock_ohlc):
        mock_ohlc.iloc[-5, mock_ohlc.columns.get_loc("close")] = 99.8
        mock_ohlc.iloc[-4, mock_ohlc.columns.get_loc("close")] = 100.0
        mock_ohlc.iloc[-3, mock_ohlc.columns.get_loc("close")] = 100.2
        mock_ohlc.iloc[-2, mock_ohlc.columns.get_loc("close")] = 100.4
        assert strategy._confirm_1m_directional_sequence(mock_ohlc, "UP", 3) is True

    def test_confirm_1m_directional_sequence_rejects_flat(self, strategy, mock_ohlc):
        mock_ohlc.iloc[-5, mock_ohlc.columns.get_loc("close")] = 100.0
        mock_ohlc.iloc[-4, mock_ohlc.columns.get_loc("close")] = 100.0
        mock_ohlc.iloc[-3, mock_ohlc.columns.get_loc("close")] = 100.1
        mock_ohlc.iloc[-2, mock_ohlc.columns.get_loc("close")] = 100.2
        assert strategy._confirm_1m_directional_sequence(mock_ohlc, "UP", 3) is False

    def test_is_parabolic_spike_true_three_large_closed_candles(self, strategy, mock_ohlc):
        atr = 1.0
        mock_ohlc.iloc[-4:-1, mock_ohlc.columns.get_loc("open")] = 100.0
        mock_ohlc.iloc[-4:-1, mock_ohlc.columns.get_loc("close")] = 103.0
        assert strategy._is_parabolic_spike(mock_ohlc, atr) is True

    def test_is_parabolic_spike_true_single_large_candle(self, strategy, mock_ohlc):
        atr = 1.0
        mock_ohlc.iloc[-2, mock_ohlc.columns.get_loc("open")] = 100.0
        mock_ohlc.iloc[-2, mock_ohlc.columns.get_loc("close")] = 103.5
        assert strategy._is_parabolic_spike(mock_ohlc, atr) is True

    def test_calculate_atr_basic(self, strategy, mock_ohlc):
        atr = strategy._calculate_atr(mock_ohlc, period=14)
        assert atr > 0

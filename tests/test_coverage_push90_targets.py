from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

import data_fetcher as df_mod
import risefallbot.rf_bot as rf_bot
import strategy as st_mod
import trade_engine as te_mod


def _ohlc(n=120, start=100.0, step=0.1):
    return pd.DataFrame(
        {
            "open": [start + i * step for i in range(n)],
            "high": [start + i * step + 0.3 for i in range(n)],
            "low": [start + i * step - 0.3 for i in range(n)],
            "close": [start + i * step + 0.1 for i in range(n)],
        }
    )


def test_strategy_down_paths_and_entry_trigger(monkeypatch):
    s = st_mod.TradingStrategy()
    s.max_sl_distance_pct = 0.5

    levels = [{"price": 95.0}, {"price": 90.0}, {"price": 80.0}]
    base = _ohlc()
    s._get_swing_points = MagicMock(return_value=([105.0, 103.0], [96.0, 94.0]))
    tp, sl = s._identify_tp_sl_levels(levels, 100.0, "DOWN", base, base, base, base)
    assert tp == 95.0
    assert sl == pytest.approx(100.5)
    support, resistance = s._find_trading_range(levels, 100.0)
    assert support == 95.0
    assert resistance is None


def test_strategy_analyze_consolidation_and_exec_level_branches(monkeypatch):
    s = st_mod.TradingStrategy()
    df = _ohlc()

    monkeypatch.setattr(st_mod, "calculate_adx", lambda _x: pd.Series([40] * len(df)))
    monkeypatch.setattr(st_mod, "calculate_rsi", lambda _x: pd.Series([60] * len(df)))

    import indicators

    # Strict consolidation base required
    monkeypatch.setattr(indicators, "detect_price_movement", lambda *_a, **_k: (0.1, 0.1, False))
    monkeypatch.setattr(indicators, "detect_consolidation", lambda *_a, **_k: (False, 0.0, 0.0))
    monkeypatch.setattr(st_mod.config, "REQUIRE_CONSOLIDATION_BASE", True, raising=False)
    out = s.analyze(df, df, df, df, df, df, symbol="R_25")
    assert out["can_trade"] is False
    assert "consolidation base" in out["details"]["reason"].lower()

    # Reach "No execution level found"
    monkeypatch.setattr(st_mod.config, "REQUIRE_CONSOLIDATION_BASE", False, raising=False)
    monkeypatch.setattr(indicators, "detect_consolidation", lambda *_a, **_k: (True, 1.0, 0.5))
    monkeypatch.setattr(s, "_determine_trend", lambda *_a, **_k: "UP")
    monkeypatch.setattr(s, "_identify_tp_sl_levels", lambda *_a, **_k: (120.0, 99.0))
    monkeypatch.setattr(s, "_find_levels", lambda *_a, **_k: [])
    monkeypatch.setattr(s, "_find_trading_range", lambda *_a, **_k: (90.0, 110.0))
    monkeypatch.setattr(s, "_is_in_middle_zone", lambda *_a, **_k: False)
    monkeypatch.setattr(s, "_find_nearest_level", lambda *_a, **_k: None)
    out2 = s.analyze(df, df, df, df, df, df, symbol="R_25")
    assert out2["can_trade"] is False
    assert "No execution level found" in out2["details"]["reason"]


@pytest.mark.asyncio
async def test_data_fetcher_error_and_multi_asset_branches(monkeypatch):
    f = df_mod.DataFetcher(api_token="T")

    # fetch_candles error and missing candles field branches
    f.send_request = AsyncMock(return_value={"error": {"message": "x"}})
    assert await f.fetch_candles("R_25", 60, 5) is None
    f.send_request = AsyncMock(return_value={"msg_type": "history"})
    assert await f.fetch_candles("R_25", 60, 5) is None

    # tick/balance branches
    f.send_request = AsyncMock(return_value={"error": {"message": "tick fail"}})
    assert await f.fetch_tick("R_25") is None
    f.send_request = AsyncMock(return_value={})
    assert await f.get_balance() is None

    # multi-timeframe exception path
    f.fetch_candles = AsyncMock(side_effect=RuntimeError("boom"))
    assert await f.fetch_multi_timeframe_data("R_25") == {}

    # get_multi_asset_data branches for topdown on/off
    fake = SimpleNamespace(
        connect=AsyncMock(return_value=True),
        disconnect=AsyncMock(),
        fetch_all_timeframes=AsyncMock(return_value={"1m": _ohlc(5)}),
        fetch_multi_timeframe_data=AsyncMock(return_value={"1m": _ohlc(5)}),
    )
    monkeypatch.setattr(df_mod, "DataFetcher", lambda *_a, **_k: fake)
    monkeypatch.setattr(df_mod.asyncio, "sleep", AsyncMock())

    monkeypatch.setattr(df_mod.config, "USE_TOPDOWN_STRATEGY", True)
    out = await df_mod.get_multi_asset_data(["R_25"])
    assert "R_25" in out

    monkeypatch.setattr(df_mod.config, "USE_TOPDOWN_STRATEGY", False)
    out2 = await df_mod.get_multi_asset_data(["R_50"])
    assert "R_50" in out2


@pytest.mark.asyncio
async def test_trade_engine_additional_error_paths(monkeypatch):
    eng = te_mod.TradeEngine(api_token="T")

    # get_asset_multiplier exception fallback path
    eng.asset_configs = {"R_25": {}}
    fallback = getattr(te_mod.config, "MULTIPLIER", 160)
    assert eng.get_asset_multiplier("R_25") == fallback

    # buy_with_proposal missing buy response
    eng.send_request = AsyncMock(return_value={"msg_type": "buy"})
    assert await eng.buy_with_proposal("P1", 1.0) is None

    # remove_take_profit unexpected response path
    eng.send_request = AsyncMock(return_value={"ok": 1})
    assert await eng.remove_take_profit("1") is False

    # execute_trade: missing stake and exception path
    monkeypatch.setattr(te_mod.config, "FIXED_STAKE", None, raising=False)
    assert await eng.execute_trade(
        {"signal": "UP", "symbol": "R_25", "take_profit": 101.0, "stop_loss": 99.0},
        MagicMock(),
    ) is None
    assert await eng.execute_trade({"symbol": "R_25"}, MagicMock()) is None


@pytest.mark.asyncio
async def test_rf_bot_process_symbol_additional_branches(monkeypatch):
    class DummyRM:
        def __init__(self):
            self.active_trades = []
            self._halted = False
            self._halt_reason = ""
            self._locked_trade_info = {}
            self.acquire_trade_lock = AsyncMock(return_value=True)
            self.record_trade_open = MagicMock()
            self.record_trade_closed = MagicMock()
            self.release_trade_lock = MagicMock()
            self.clear_halt = MagicMock()

        def can_trade(self, **_kwargs):
            return True, "ok"

        def halt(self, reason):
            self._halted = True
            self._halt_reason = reason

        def is_halted(self):
            return self._halted

    rm = DummyRM()
    em = SimpleNamespace(broadcast=AsyncMock())
    df = _ohlc(10)
    fetcher = SimpleNamespace(fetch_tick_history=AsyncMock(return_value=df))
    strategy = SimpleNamespace(
        analyze=MagicMock(
            return_value={
                "direction": "CALL",
                "stake": 1.0,
                "duration": 3,
                "duration_unit": "t",
                "trade_label": "RISE",
                "sequence_direction": "down",
                "tick_sequence": [100.3, 100.2, 100.1, 100.0],
                "sequence_signature": "sig-1",
            }
        )
    )

    # stake exceeds max branch
    monkeypatch.setattr(rf_bot.rf_config, "RF_MAX_STAKE", 0.5, raising=False)
    await rf_bot._process_symbol("stpRNG1", strategy, rm, fetcher, AsyncMock(), 1.0, "u1", em, MagicMock())
    rm.acquire_trade_lock.assert_not_awaited()

    # lock acquisition failure branch
    monkeypatch.setattr(rf_bot.rf_config, "RF_MAX_STAKE", 10.0, raising=False)
    rm.acquire_trade_lock = AsyncMock(return_value=False)
    await rf_bot._process_symbol("stpRNG1", strategy, rm, fetcher, AsyncMock(), 1.0, "u1", em, MagicMock())

    # settlement unknown + no user_id branch (db skip) + unlock broadcast
    rm.acquire_trade_lock = AsyncMock(return_value=True)
    engine = SimpleNamespace(
        buy_rise_fall=AsyncMock(return_value={"contract_id": "c1", "buy_price": 1.0}),
        wait_for_result=AsyncMock(return_value=None),
    )
    await rf_bot._process_symbol("stpRNG1", strategy, rm, fetcher, engine, 1.0, None, em, MagicMock())
    assert rm.record_trade_open.called
    assert rm.record_trade_closed.called
    assert rm.release_trade_lock.called

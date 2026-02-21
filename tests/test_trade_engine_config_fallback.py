import importlib


def test_trade_engine_init_handles_missing_global_tp_sl(monkeypatch):
    trade_engine = importlib.import_module("trade_engine")
    cfg = importlib.import_module("config")

    # Force the init path that previously read config.TAKE_PROFIT_PERCENT directly.
    monkeypatch.setattr(cfg, "RISK_MODE", "FIXED", raising=False)
    monkeypatch.setattr(cfg, "USE_TOPDOWN_STRATEGY", False, raising=False)

    # Simulate strategy configs that do not define global TP/SL percentages.
    if hasattr(cfg, "TAKE_PROFIT_PERCENT"):
        monkeypatch.delattr(cfg, "TAKE_PROFIT_PERCENT", raising=False)
    if hasattr(cfg, "STOP_LOSS_PERCENT"):
        monkeypatch.delattr(cfg, "STOP_LOSS_PERCENT", raising=False)

    engine = trade_engine.TradeEngine(api_token="token")
    assert engine is not None

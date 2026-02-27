# Combined Scalping Report (Feb 25-27, 2026)

## Scope
- Source 1: `trade_analysis.html`
- Source 2: `telegram_signal_analysis.html`
- Window: February 25-27, 2026
- Sample: 21 unique signals, 30 executed trades, 2 accounts

## Shared Findings (Both Reports Agree)
- Net result was negative: total P&L around `-$9.75`, with low win rate (`8W / 22L`, `26.7%`).
- Stagnation exits were the dominant failure mode (`17/30`, `56.7%`) and were consistently losing exits.
- R:R policy was not effectively enforced in execution logs (`20/30` trades below configured `1.5` minimum).
- Signal direction was strongly one-sided (`29 UP / 1 DOWN`), with weak downside participation.
- `1HZ30V` was structurally poor in this window (`0/6`, about `-$7.60`) and should be blocked.
- `stpRNG4` was the only symbol with demonstrated edge in this sample (`5/10`, about `+$7.27`).

## Diagnostic Interpretation
- Primary issue is not only exits; entries frequently happened near exhaustion, then flatlined into stagnation.
- High ADX did not improve outcomes globally in this sample, suggesting late-trend entries were common.
- Strategy-level R:R checks existed, but execution-time pricing drift likely allowed sub-threshold live trades.

## Changes Implemented in Code
- Symbol risk control:
  - Added `1HZ30V` to scalping `BLOCKED_SYMBOLS`.
  - `ScalpingStrategy.get_symbols()` now excludes blocked symbols, so blocked symbols are not scanned.
- Entry quality hardening:
  - Tightened RSI bands:
    - UP: `54-72` (was `55-75`)
    - DOWN: `28-48` (was `22-45`)
  - Added ADX exhaustion cap (`SCALPING_ADX_MAX_THRESHOLD = 34`).
  - Added symbol-specific strength floor for `stpRNG4` (`SCALPING_STPRNG4_MIN_ADX = 25`).
  - Added live-entry drift guard vs signal candle (`SCALPING_MAX_ENTRY_DRIFT_ATR = 0.35`).
- R:R enforcement fix path:
  - Strategy now emits `min_rr_required`.
  - `ScalpingRiskManager.can_open_trade()` now computes and blocks low R:R using entry/TP/SL.
  - `TradeEngine.execute_trade()` validates planned R:R before opening.
  - `TradeEngine.open_trade()` validates projected R:R at proposal spot before buy.
- Re-entry and stagnation tuning:
  - Added single-loss symbol cooldown (`SCALPING_SINGLE_LOSS_COOLDOWN_SECONDS = 600`).
  - Added high-R:R stagnation grace:
    - `SCALPING_STAGNATION_RR_GRACE_THRESHOLD = 2.5`
    - `SCALPING_STAGNATION_EXTRA_TIME = 60`
- Observability:
  - Added new scalping gate mappings in runner for ADX exhaustion/symbol ADX floor/entry drift rejections.

## Validation Notes
- Added/updated scalping risk manager tests for:
  - R:R gate block/allow behavior.
  - High-R:R stagnation grace timing behavior.

## Next Evaluation Pass
- Run a new controlled demo sample after these changes.
- Compare:
  - `% of trades below min R:R` (target near 0%).
  - stagnation rate (target lower than 56.7%).
  - directional balance (more DOWN participation when market supports it).
  - per-symbol P&L after keeping `1HZ30V` blocked.

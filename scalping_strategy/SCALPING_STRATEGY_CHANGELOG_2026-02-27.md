# Scalping Strategy Change Log (February 27, 2026)

## Scope
This document records the scalping-related implementation changes applied during the Feb 27, 2026 improvement cycle, including symbol-universe updates, entry/risk logic updates, execution safeguards, and validation status.

## Objectives
- Enforce configured risk/reward (R:R) at strategy, risk, and execution layers.
- Reduce low-quality/exhaustion entries.
- Improve stagnation-loss handling.
- Remove `1HZ30V` from active trading universes.
- Keep full multi-asset development coverage active (except blocked symbols).

## Changes Implemented

### 1) Symbol Universe and Blocking
- Added `1HZ30V` to blocked sets where applicable.
- Removed `1HZ30V` from active symbol lists and asset config mappings.
- Disabled scalping single-symbol rollout so full symbol set is active again.

Files:
- `scalping_strategy/config.py`
- `conservative_strategy/config.py`
- `risefallbot/rf_config.py`

### 2) Scalping Config Tuning
- Added ADX exhaustion and symbol-specific ADX controls:
  - `SCALPING_ADX_MAX_THRESHOLD = 34`
  - `SCALPING_STPRNG4_MIN_ADX = 25`
- Tightened RSI windows:
  - `SCALPING_RSI_UP_MIN = 54`
  - `SCALPING_RSI_UP_MAX = 72`
  - `SCALPING_RSI_DOWN_MIN = 28`
  - `SCALPING_RSI_DOWN_MAX = 48`
- Updated volatility distances:
  - `SCALPING_SL_ATR_MULTIPLIER = 2.0`
  - `SCALPING_TP_ATR_MULTIPLIER = 3.0`
- Updated stagnation controls:
  - `SCALPING_STAGNATION_EXIT_TIME = 75`
  - `SCALPING_STAGNATION_LOSS_PCT = 3.0`
  - `SCALPING_STAGNATION_EXTRA_TIME = 0` (strict default behavior)
- Updated trailing activation:
  - `SCALPING_TRAIL_ACTIVATION_PCT = 8.0`
- Added additional guards:
  - `SCALPING_MAX_ENTRY_DRIFT_ATR = 0.35`
  - `SCALPING_SINGLE_LOSS_COOLDOWN_SECONDS = 600`

File:
- `scalping_strategy/config.py`

### 3) Strategy Logic Hardening
- Added ADX exhaustion rejection.
- Added `stpRNG4` minimum ADX gate.
- Added ATR-based live entry drift guard.
- Ensured emitted signal includes:
  - `risk_reward_ratio`
  - `min_rr_required`
- Updated `get_symbols()` to respect rollout list and blocked set.

File:
- `scalping_strategy/strategy.py`

### 4) Risk Manager Enforcement
- Added R:R hard-block in `can_open_trade()` using entry/TP/SL.
- Added single-loss symbol cooldown application on losing close.
- Extended trade metadata to persist `risk_reward_ratio` and `min_rr_required`.
- Enhanced stagnation log context with effective time limit and RR.

File:
- `scalping_strategy/risk_manager.py`

### 5) Trade Engine Execution Safeguards
- Added `_compute_rr_ratio()` helper for consistent RR calculations.
- Added pre-buy proposal-spot RR validation (`open_trade`).
- Added pre-execution planned RR validation (`execute_trade`).
- Added RR diagnostics logs:
  - signal RR
  - planned RR
  - open-time RR
- Persisted `risk_reward_ratio` and `min_rr_required` in opened trade metadata.

File:
- `trade_engine.py`

### 6) Runner Metrics Mapping
- Added rejection reason mappings for new scalping gate reasons:
  - ADX exhaustion
  - symbol ADX threshold
  - entry drift too high

File:
- `app/bot/runner.py`

### 7) Tests Updated
- Added scalping risk-manager tests for:
  - RR blocking below threshold
  - RR pass at/above threshold
  - stagnation RR grace behavior
- Adjusted one cooldown test setup to isolate circuit-breaker behavior from new single-loss cooldown.

File:
- `tests/test_scalping_risk_manager.py`

## Validation Summary
- Full test suite: `422 passed`
- Coverage gate: `80.81%` (threshold: `>= 80%`) passed
- Branch push: success (`ci/github-actions-setup-squashed`)

## Notes
- Lint in CI workflow is advisory (non-blocking).
- `1HZ30V` is fully excluded from active strategy universes in this cycle.
- Current dev-mode behavior trades all active symbols except explicitly blocked symbols.

# RiseFall Bot: Single Concurrent Trade Enforcement

## Overview

The RiseFall bot now strictly enforces **only 1 concurrent trade** across all trading symbols. This means:

- When any symbol has an active trade, **NO other symbol can initiate a new trade**
- The system **continuously monitors** the locked trade until completion
- Only after the trade closes (win/loss/timeout) can the next trade be executed
- This applies globally across all 4 symbols: `R_10`, `R_25`, `R_50`, `R_100`

---

## How It Works

### 1. **Trade Lock Activation** 🔒

When a signal is generated and a trade is executed:

```
[RF] Signal detected on R_10 → Triple confirmation met
     ↓
[RF] Executing CALL trade on R_10
     ↓
[RF] ✅ Contract bought: #12345
     ↓
[RF-Risk] 🔒 TRADE LOCKED: R_10 #12345
     ↓
System broadcasts: "trade_lock_active"
```

### 2. **Other Symbols Blocked** 🚫

While R_10's trade is locked:

```
[RF] Cycle #2 - Scanning symbols...
     ↓
[RF] 🔒 LOCKED — R_10#12345 is being monitored | Skipping signal scan
     ↓
[RF] NOT scanning R_25, R_50, R_100 until R_10 trade closes
```

Even if R_25 has a valid signal:

```
[RF][R_25] 🔒 LOCKED — R_10#12345 is currently active
            Waiting for that trade to close before proceeding...
```

### 3. **Continuous Monitoring** ⏳

The bot monitors the locked trade until it settles:

```
[RF][R_10] ⏳ Monitoring contract #12345 until close...
           (system locked for other symbols)
           
           👁️ Watching contract #12345
              • TP: +$0.50 (50%)
              • SL: -$0.40 (40%)
           
           (waiting for settlement...)
```

### 4. **Trade Lock Release** 🔓

When the trade closes:

```
[RF-Engine] 🏁 SETTLED Contract #12345: WIN pnl=+0.50
           ↓
[RF-Risk] 🔓 TRADE UNLOCKED: R_10 #12345
           status=win pnl=+0.50
           (W=5 L=3)
           — System ready for next trade
           ↓
System broadcasts: "trade_lock_released"
```

### 5. **Next Trade Allowed** ✅

After unlock, the next cycle can execute a new trade:

```
[RF] Cycle #3 - Scanning symbols...
     ↓
[RF] ✅ No active trades | Scanning R_10, R_25, R_50, R_100 for signals
     ↓
[RF][R_25] Signal detected → CALL confidence=10
           ↓
[RF] Executing PUT trade on R_25
     ↓
[RF-Risk] 🔒 TRADE LOCKED: R_25 #67890
```

---

## Implementation Details

### Risk Manager Changes

**File:** `risefallbot/rf_risk_manager.py`

#### New State Variables:
```python
self._trade_lock_active: bool = False           # Is a trade currently locked?
self._locked_symbol: str = None                 # Which symbol has the active trade?
self._locked_trade_info: Dict = {}              # Contract ID and symbol info
```

#### New Methods:

```python
def is_trade_active() -> bool:
    """Return True if a trade is currently locked (being monitored)."""
    # Prevents other signals from executing while locked
    
def get_active_trade_info() -> Dict:
    """Get information about the currently active/locked trade."""
    # Returns: {"contract_id": "12345", "symbol": "R_10"}
```

#### Modified Methods:

```python
def record_trade_open(trade_info):
    """
    Record trade opening.
    NOW enforces: Only 1 concurrent trade globally.
    Sets _trade_lock_active = True
    Logs: "🔒 TRADE LOCKED"
    """
    
def record_trade_closed(result):
    """
    Record trade closure.
    NOW releases the lock.
    Sets _trade_lock_active = False
    Logs: "🔓 TRADE UNLOCKED"
    """

def can_trade(symbol) -> (bool, str):
    """
    Check if a new trade is allowed.
    NOW Additionally checks:
      • Total concurrent trades (max 1 globally)
      • Per-symbol concurrent trades (max 1 per symbol)
    """
```

### Bot Changes

**File:** `risefallbot/rf_bot.py`

#### Main Loop Enhancement:

```python
while _running:
    # NEW: Check if a trade is locked
    if risk_manager.is_trade_active():
        logger.warning(
            f"[RF] 🔒 TRADE LOCKED — {symbol}#{contract} is being monitored "
            f"| Skipping signal scan until trade closes"
        )
        # Skip all symbol scanning, wait for next cycle
    else:
        # Safe to scan for signals
        for symbol in RF_SYMBOLS:
            # Additional double-check: stop if trade opens mid-loop
            if risk_manager.is_trade_active():
                break
            
            await _process_symbol(...)
```

#### Symbol Processing Enhancement:

```python
async def _process_symbol(...):
    """
    NOW enforces trade lock before processing signal.
    """
    # 1. Risk gate check
    can_trade, reason = risk_manager.can_trade(symbol)
    if not can_trade:
        return
    
    # 1b. NEW: Verify no other trade is active
    if risk_manager.is_trade_active():
        logger.warning(
            f"[RF][{symbol}] 🔒 LOCKED — other trade is active "
            f"| Waiting for that trade to close..."
        )
        return
    
    # ... continue with signal analysis and execution ...
    
    # When trade opens:
    risk_manager.record_trade_open(...)  # Activates lock
    
    # Broadcast lock status
    await event_manager.broadcast({
        "type": "trade_lock_active",
        "message": "🔒 Trade LOCKED on {symbol} — system will monitor until close"
    })
    
    # Monitor trade
    settlement = await trade_engine.wait_for_result(contract_id)
    
    # When trade closes:
    risk_manager.record_trade_closed(...)  # Deactivates lock
    
    # Broadcast unlock status
    await event_manager.broadcast({
        "type": "trade_lock_released",
        "message": "🔓 Trade UNLOCKED — system ready for next trade"
    })
```

### Configuration

**File:** `risefallbot/rf_config.py`

```python
RF_MAX_CONCURRENT_TOTAL = 1           # Max 1 trade total across ALL symbols
RF_MAX_CONCURRENT_PER_SYMBOL = 1      # Max 1 trade per symbol at a time
```

The config now includes documentation:
```
⚠️ SINGLE CONCURRENT TRADE ENFORCEMENT:
   The bot STRICTLY enforces only 1 concurrent trade across ALL symbols.
```

---

## Event Broadcasts

The system broadcasts events to keep the frontend/dashboard informed:

### When Trade Opens:
```javascript
{
  "type": "trade_lock_active",
  "symbol": "R_10",
  "contract_id": "12345",
  "message": "🔒 Trade LOCKED on R_10 — system will monitor until close",
  "timestamp": "2026-02-18T17:21:20.000Z"
}
```

### When Trade Closes:
```javascript
{
  "type": "trade_lock_released",
  "symbol": "R_10",
  "contract_id": "12345",
  "status": "win",
  "pnl": 0.50,
  "message": "🔓 Trade UNLOCKED — system ready for next trade",
  "timestamp": "2026-02-18T17:21:30.000Z"
}
```

---

## Log Examples

### Normal Execution Sequence:

```
[RF] CYCLE #1 | 17:21:00
✅ No active trades | Scanning 4 symbols for signals...
[RF][R_10] CALL signal detected | EMA5=100.00 EMA13=99.90 RSI(7)=28.0 Stoch%K=15.5
[RF-Engine] 🛒 Buying CALL on R_10 | stake=$1.00 duration=5m
[RF-Engine] ✅ Contract bought: #12345 | buy_price=$1.00 payout=$1.95
[RF-Risk] 🔒 TRADE LOCKED: R_10 #12345 (active=1 daily=1) — No other trades allowed
[RF][R_10] ⏳ Monitoring contract #12345 until close... (system locked for other symbols)
[RF-Engine] 👁️ Watching contract #12345 | TP: +$0.50 | SL: -$0.40

[RF] CYCLE #2 | 17:21:10
🔒 TRADE LOCKED — R_10#12345 is being monitored | Skipping signal scan

[RF] CYCLE #3 | 17:21:20
🔒 TRADE LOCKED — R_10#12345 is being monitored | Skipping signal scan

[RF] CYCLE #4 | 17:21:30
[RF-Engine] 🏁 SETTLED Contract #12345: WIN pnl=+0.50
[RF-Risk] 🔓 TRADE UNLOCKED: R_10 #12345 status=win pnl=+0.50 (W=1 L=0)
           — System ready for next trade

[RF] CYCLE #5 | 17:21:40
✅ No active trades | Scanning 4 symbols for signals...
[RF][R_25] PUT signal detected | EMA5=50.00 EMA13=50.10 RSI(7)=72.0 Stoch%K=85.5
[RF-Engine] 🛒 Buying PUT on R_25 | stake=$1.00 duration=5m
[RF-Engine] ✅ Contract bought: #67890 | buy_price=$1.00 payout=$1.95
[RF-Risk] 🔒 TRADE LOCKED: R_25 #67890 (active=1 daily=2) — No other trades allowed
```

---

## Verification Test

A comprehensive test script is included to verify the enforcement:

```bash
python risefallbot/test_single_trade_enforcement.py
```

**Tests Verify:**
1. ✅ Only 1 trade can be active at a time (globally)
2. ✅ Other symbols are BLOCKED while a trade is locked
3. ✅ Trade lock is RELEASED when trade closes
4. ✅ Next trade can execute after lock released
5. ✅ Statistics are tracked correctly

---

## Key Behaviors

| Scenario | Behavior |
|----------|----------|
| **No trade active** | ✅ Bot scans all symbols for signals |
| **R_10 trade locked** | 🚫 R_25, R_50, R_100 signals IGNORED |
| **Any trade closed** | 🔓 Bot immediately ready for next trade |
| **Trade monitoring** | ⏳ System monitors until WIN/LOSS/TIMEOUT |
| **Daily cap reached** | 🛑 No trades allowed (separate limit) |
| **5 consecutive losses** | ⏸️ 120s cooldown before next trade |

---

## FAQ

### Q: What if a trade doesn't close in time?
**A:** The bot has a 10-minute timeout on contract monitoring. If not closed by then, it's marked as a loss conservatively, and the lock is released.

### Q: Can I modify the single-trade limit?
**A:** Yes, change `RF_MAX_CONCURRENT_TOTAL` in `rf_config.py`, but the current setting enforces ONE trade at a time.

### Q: How does this interact with daily limits?
**A:** The concurrent lock is separate from daily limits. Even if below daily cap, you can't open a new trade while one is already active.

### Q: What if the bot crashes?
**A:** The lock state is stored in memory. On restart, it resets to no active trades and resumes normal operation.

### Q: Can consecutive losses interrupt the lock?
**A:** No. After 5 losses, a 120s cooldown is applied to the entire system before ANY new trade is attempted.

---

## Summary

The RiseFall bot now implements **strict single-concurrent-trade enforcement**:

✅ **Only 1 trade can execute at any time** (globally across all symbols)
✅ **All other symbols are blocked** while a trade is active
✅ **System continuously monitors** the locked trade
✅ **Lock automatically releases** when trade settles
✅ **Next trade can immediately execute** after release
✅ **Full logging and event broadcasts** for monitoring

This ensures disciplined, sequential trade execution without overlapping risk exposure.

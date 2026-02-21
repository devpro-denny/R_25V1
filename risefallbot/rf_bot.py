"""
Rise/Fall Bot Orchestrator
Main async loop — subscribes to 1-min OHLC, generates signals, executes trades

STRICT SINGLE-TRADE ENFORCEMENT:
    The scan loop is BLOCKED at the asyncio level whenever a trade is in
    its lifecycle. Uses asyncio.Lock (trade_mutex) — not a boolean flag.
    A full 6-step lifecycle must complete before the next trade is considered.

rf_bot.py
"""

import asyncio
import os
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

from data_fetcher import DataFetcher
from risefallbot import rf_config
from risefallbot.rf_strategy import RiseFallStrategy
from risefallbot.rf_risk_manager import RiseFallRiskManager
from risefallbot.rf_trade_engine import RFTradeEngine

# Try to import telegram notifier
try:
    from telegram_notifier import notifier
    TELEGRAM_ENABLED = True
except ImportError:
    TELEGRAM_ENABLED = False

# Dedicated logger for Rise/Fall bot orchestration — writes to its own file
logger = logging.getLogger("risefallbot")

# Module-level sentinel for clean stop and duplicate prevention
_running = False
_bot_task: Optional[asyncio.Task] = None
_decision_emit_state: Dict[str, Dict[str, Any]] = {}


def _setup_rf_logger():
    """
    Configure the risefallbot logger hierarchy so all RF modules
    (risefallbot.strategy, risefallbot.risk, risefallbot.engine)
    write ONLY to risefall_bot.log and do NOT propagate to the root
    (multiplier bot) logger.
    """
    rf_root = logging.getLogger("risefallbot")

    # Prevent double-handler on re-import
    if rf_root.handlers:
        return

    rf_root.setLevel(getattr(logging, rf_config.RF_LOG_LEVEL, logging.INFO))
    rf_root.propagate = False  # ← isolate from multiplier bot logs
    
    # Add context filter for user_id injection
    try:
        from app.core.logging import ContextInjectingFilter
        rf_root.addFilter(ContextInjectingFilter())
    except Exception:
        pass

    formatter = logging.Formatter(
        "%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler
    fh = logging.FileHandler(rf_config.RF_LOG_FILE, encoding="utf-8")
    fh.setFormatter(formatter)
    rf_root.addHandler(fh)

    # Console handler (optional — useful during development)
    ch = logging.StreamHandler()
    ch.setFormatter(formatter)
    rf_root.addHandler(ch)
    
    # WebSocket handler (for live dashboard streaming) — added early
    try:
        from app.core.logging import WebSocketLoggingHandler
        ws_handler = WebSocketLoggingHandler()
        ws_handler.setFormatter(formatter)
        rf_root.addHandler(ws_handler)
    except Exception as e:
        # If WebSocket handler is not available, continue without it
        pass


# Initialise logging on module load
_setup_rf_logger()


async def _fetch_user_config() -> dict:
    """
    Fetch deriv_api_key and stake_amount from Supabase profiles table
    for the first user who has active_strategy = 'RiseFall'.
    Falls back to env-var token and config default stake.
    """
    result_config = {
        "api_token": os.getenv("DERIV_API_TOKEN"),
        "stake": rf_config.RF_DEFAULT_STAKE,
    }

    try:
        from app.core.supabase import supabase
        result = (
            supabase.table("profiles")
            .select("deriv_api_key, stake_amount")
            .eq("active_strategy", "RiseFall")
            .limit(1)
            .execute()
        )
        if result.data:
            row = result.data[0]
            if row.get("deriv_api_key"):
                result_config["api_token"] = row["deriv_api_key"]
                logger.info("🔑 API token loaded from user profile")
            if row.get("stake_amount") is not None:
                result_config["stake"] = float(row["stake_amount"])
                logger.info(f"💵 User stake loaded from profile: ${result_config['stake']}")
    except Exception as e:
        logger.warning(f"⚠️ Could not fetch user config from Supabase: {e}")

    return result_config


# ═══════════════════════════════════════════════════════════════════════════════
# Cross-process session lock (Supabase rf_bot_sessions table)
# ═══════════════════════════════════════════════════════════════════════════════

async def _acquire_session_lock(user_id: str) -> bool:
    """
    Attempt to INSERT a row into rf_bot_sessions.
    The table has user_id as PRIMARY KEY, so a second insert for the same
    user will raise a unique-violation and we return False.

    Returns True if the lock was acquired, False otherwise.
    Guarded by RF_ENFORCE_DB_LOCK — returns True immediately when disabled.
    """
    if not rf_config.RF_ENFORCE_DB_LOCK:
        logger.info("[RF] DB session lock disabled (RF_ENFORCE_DB_LOCK=False) — skipping")
        return True

    if not user_id:
        logger.error("[RF] _acquire_session_lock called with no user_id — aborting")
        return False

    try:
        from app.core.supabase import supabase

        ttl_seconds = max(1, int(getattr(rf_config, "RF_DB_LOCK_TTL_SECONDS", 900)))
        existing = (
            supabase.table("rf_bot_sessions")
            .select("user_id, started_at, process_id")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )

        if existing.data:
            row = existing.data[0]
            started_at = row.get("started_at")
            should_reclaim = False

            if not started_at:
                should_reclaim = True
            else:
                try:
                    started_dt = datetime.fromisoformat(
                        str(started_at).replace("Z", "+00:00")
                    )
                    if started_dt.tzinfo is None:
                        started_dt = started_dt.replace(tzinfo=timezone.utc)
                    age = datetime.now(timezone.utc) - started_dt
                    should_reclaim = age > timedelta(seconds=ttl_seconds)
                except Exception:
                    should_reclaim = True

            if should_reclaim:
                supabase.table("rf_bot_sessions").delete().eq(
                    "user_id", user_id
                ).execute()
                logger.warning(
                    f"[RF] ♻️ Reclaimed stale DB session lock for user={user_id} "
                    f"(ttl={ttl_seconds}s)"
                )

        supabase.table("rf_bot_sessions").insert({
            "user_id": user_id,
            "started_at": datetime.now().isoformat(),
            "process_id": os.getpid(),
        }).execute()
        logger.info(
            f"[RF] ✅ DB session lock acquired for user={user_id} pid={os.getpid()}"
        )
        return True
    except Exception as e:
        err_str = str(e).lower()
        if any(x in err_str for x in [
            "duplicate", "unique", "conflict", "23505",  # duplicate key
            "invalid input syntax", "uuid",               # malformed UUID
        ]):
            logger.warning(
                f"[RF] ⛔ DB session lock DENIED for user={user_id} — "
                f"another instance is already running or invalid user_id: {e}"
            )
        else:
            logger.error(
                f"[RF] ❌ DB session lock error for user={user_id}: {e}"
            )
        return False


async def _release_session_lock(user_id: str) -> None:
    """
    Delete the rf_bot_sessions row for this user.
    Safe to call even if no row exists.  Never raises.
    """
    if not rf_config.RF_ENFORCE_DB_LOCK:
        return

    try:
        from app.core.supabase import supabase
        supabase.table("rf_bot_sessions").delete().eq(
            "user_id", user_id
        ).execute()
        logger.info(f"[RF] 🔓 DB session lock released for user={user_id}")
    except Exception as e:
        logger.error(f"[RF] ❌ Failed to release DB session lock for user={user_id}: {e}")


async def _refresh_session_lock(user_id: str) -> None:
    """
    Refresh started_at for an existing rf_bot_sessions row so active bots
    do not appear stale to other processes.
    """
    if not rf_config.RF_ENFORCE_DB_LOCK or not user_id:
        return

    try:
        from app.core.supabase import supabase
        supabase.table("rf_bot_sessions").update({
            "started_at": datetime.now().isoformat(),
            "process_id": os.getpid(),
        }).eq("user_id", user_id).execute()
    except Exception as e:
        logger.warning(f"[RF] ⚠️ Failed to refresh DB session lock for user={user_id}: {e}")


async def run(stake: Optional[float] = None, api_token: Optional[str] = None,
              user_id: Optional[str] = None):
    """
    Main Rise/Fall bot entry point.
    
    Args:
        stake: User stake amount. If None, fetches from Supabase profiles table.
        api_token: Deriv API token. If None, fetches from Supabase profiles table.
        user_id: User ID for event broadcasting and DB persistence.
    
    - Creates its own DataFetcher (reuses the class, own WS connection)
    - Creates its own RFTradeEngine (independent WS connection)
    - Loops: fetch 1m candles → analyse → risk check → execute (strict 6-step lifecycle)
    
    CRITICAL: Prevents duplicate instances via module-level task guard.
    If a bot is already running, returns immediately without starting a second instance.
    """
    # ───────────────────────────────────────────────────────────────────────
    # DUPLICATE INSTANCE PREVENTION (PRIORITY 1)
    # asyncio.Lock is per-instance. Two separate run() calls would have two
    # separate RiseFallRiskManager instances with independent mutexes.
    # This guard ensures only ONE global instance runs at a time.
    # ───────────────────────────────────────────────────────────────────────
    global _bot_task
    
    if _bot_task and not _bot_task.done():
        logger.warning(
            f"[RF] ⚠️ Duplicate start ignored — bot already running. "
            f"Task: {_bot_task} | Current: {asyncio.current_task()}"
        )
        return
    
    _bot_task = asyncio.current_task()
    logger.info(f"[RF] ✅ Registered bot task as singleton: {_bot_task}")
    
    # Set user_id in context for logging handlers to access
    from app.core.context import user_id_var
    user_id_var.set(user_id)
    
    # Lazy import to avoid circular imports at module level
    from app.bot.events import event_manager
    from app.services.trades_service import UserTradesService

    logger.info("[RF] 🚀 Rise/Fall scalping bot starting")
    logger.info("[RF] 🔒 Strict single-trade enforcement enabled (asyncio.Lock mutex)")

    # Resolve user config: explicit params > Supabase profile > env vars
    user_cfg = await _fetch_user_config()
    if stake is None:
        stake = user_cfg["stake"]
    if api_token is None:
        api_token = user_cfg["api_token"]

    if not api_token:
        logger.error("❌ No API token found (profile or DERIV_API_TOKEN env) — cannot start Rise/Fall bot")
        return

    # --- Instantiate components ---
    strategy = RiseFallStrategy()
    risk_manager = RiseFallRiskManager()
    data_fetcher = DataFetcher(api_token, rf_config.RF_APP_ID)
    trade_engine = RFTradeEngine(api_token, rf_config.RF_APP_ID)

    # --- Connect ---
    if not await data_fetcher.connect():
        logger.error("❌ DataFetcher connection failed — aborting")
        return
    if not await trade_engine.connect():
        logger.error("❌ RFTradeEngine connection failed — aborting")
        await data_fetcher.disconnect()
        return

    # Get account balance for notification
    balance = await data_fetcher.get_balance()
    if balance:
        logger.info(f"💰 Account Balance: ${balance:.2f}")
        if TELEGRAM_ENABLED:
            try:
                await notifier.notify_bot_started(
                    balance, 
                    stake, 
                    "Rise/Fall Scalping",
                    symbol_count=len(rf_config.RF_SYMBOLS),
                )
            except Exception as e:
                logger.error(f"❌ Telegram notification failed: {e}")

    logger.info(
        f"[RF] ⚙️ Config | symbols={rf_config.RF_SYMBOLS} "
        f"scan={rf_config.RF_SCAN_INTERVAL}s stake=${stake} "
        f"contract={rf_config.RF_CONTRACT_DURATION}{rf_config.RF_DURATION_UNIT}"
    )

    global _running
    # ────────────────────────────────────────────────────────────────────────
    # FIX 4: Reset _running flag on entry
    # Root cause: _running is module-level. If a previous run exited and
    # set it to False, a new run() call must explicitly reset it before the
    # loop begins — otherwise a stale False value could cause the loop to
    # exit immediately on the first iteration (especially after hard cancel).
    # ────────────────────────────────────────────────────────────────────────
    _running = True  # Explicit reset — clear stale state from any previous run
    cycle = 0
    _start_time = datetime.now()
    _current_balance = balance or 0.0

    # ───────────────────────────────────────────────────────────────────
    # STARTUP CLEANUP: Detect and recover from ghost entries
    # If a 'pending' entry exists at startup with no real active trade,
    # force-release the lock and clear any associated halt.
    # ───────────────────────────────────────────────────────────────────
    if risk_manager.trade_mutex.locked():
        logger.warning(
            "[RF] 🔍 STARTUP LOCK DETECTED: Performing ghost entry cleanup..."
        )
        # Check if there's a real active trade
        if len(risk_manager.active_trades) == 0:
            logger.warning(
                "[RF] ⚠️ GHOST ENTRY FOUND: Mutex held but no active trades! "
                "Force-releasing lock and clearing halt (if set)"
            )
            # Force-release the lock
            if risk_manager.trade_mutex.locked():
                risk_manager._trade_mutex.release()
                risk_manager._trade_lock_active = False
                risk_manager._locked_symbol = None
                risk_manager._locked_trade_info = {}
            # Clear any associated halt
            if risk_manager.is_halted():
                risk_manager.clear_halt()
            logger.info(
                "[RF] ✅ STARTUP CLEANUP COMPLETE: System ready to resume trading"
            )
        else:
            logger.warning(
                f"[RF] ⚠️ STARTUP LOCK is valid: {len(risk_manager.active_trades)} "
                f"active trade(s) found. System will resume with ongoing lifecycle."
            )

    # Broadcast bot_status → running with all fields the frontend expects
    await event_manager.broadcast({
        "type": "bot_status",
        "status": "running",
        "active_strategy": "RiseFall",
        "stake_amount": stake,
        "uptime_seconds": 0,
        "balance": _current_balance,
        "active_positions": 0,
        "win_rate": 0,
        "trades_today": 0,
        "profit": 0,
        "message": f"Rise/Fall bot started – scanning {len(rf_config.RF_SYMBOLS)} symbols",
        "symbols": rf_config.RF_SYMBOLS,
        "account_id": user_id,
    })

    # Broadcast initial statistics
    initial_stats = risk_manager.get_statistics()
    await event_manager.broadcast({
        "type": "statistics",
        "stats": initial_stats,
        "strategy": "RiseFall",
        "timestamp": datetime.now().isoformat(),
        "account_id": user_id,
    })

    try:
        while _running:
            cycle += 1
            await _refresh_session_lock(user_id)
            logger.debug(f"[RF] Cycle #{cycle} | {datetime.now().strftime('%H:%M:%S')}")

            # Daily stats reset at midnight
            risk_manager.ensure_daily_reset_if_needed()

            # ─────────────────────────────────────────────────────────────────────
            # WATCHDOG: Detect ghost mutex — held with no real active trades
            # Runs every cycle so it fires even when no new trade is being acquired
            # PRIORITY 4 FIX: Guard with datetime.min check to prevent false trigger on startup
            # ─────────────────────────────────────────────────────────────────────
            if risk_manager.trade_mutex.locked() and len(risk_manager.active_trades) == 0:
                # _pending_entry_timestamp initializes to datetime.min, which would cause
                # elapsed time to be astronomically large and trigger false watchdog on startup
                if risk_manager._pending_entry_timestamp != datetime.min:
                    elapsed = (datetime.now() - risk_manager._pending_entry_timestamp).total_seconds()
                else:
                    elapsed = 0.0
                
                if elapsed > rf_config.RF_PENDING_TIMEOUT_SECONDS:
                    logger.warning(
                        f"[RF] ⚠️ WATCHDOG: Mutex held for {elapsed:.0f}s with no active trades — "
                        f"force-releasing ghost lock (timeout={rf_config.RF_PENDING_TIMEOUT_SECONDS}s)"
                    )
                    risk_manager._trade_mutex.release()
                    risk_manager._trade_lock_active = False
                    risk_manager._locked_symbol = None
                    risk_manager._locked_trade_info = {}
                    if risk_manager.is_halted():
                        risk_manager.clear_halt()
                    logger.info("[RF] ✅ WATCHDOG RECOVERY COMPLETE: Ghost lock released — resuming scan")

            # ─────────────────────────────────────────────────────────────
            # AUTO-RECOVERY: If halted and no active trades, auto-clear halt
            # This allows the bot to self-recover after a transient error if
            # the triggering condition (DB write, trade execution) has resolved.
            # ─────────────────────────────────────────────────────────────
            if risk_manager.is_halted() and len(risk_manager.active_trades) == 0:
                logger.warning(
                    f"[RF] 🔄 AUTO-RECOVERY: System was halted but no active trades. "
                    f"Clearing halt and resuming. Reason was: {risk_manager._halt_reason}"
                )
                risk_manager.clear_halt()
                await event_manager.broadcast({
                    "type": "bot_status",
                    "status": "running",
                    "message": "🔄 System recovered from halt — resuming normal operation",
                    "timestamp": datetime.now().isoformat(),
                    "account_id": user_id,
                })

            # ═══════════════════════════════════════════════════════════
            # MUTEX-LEVEL CHECK: If the trade lock is held, the scan
            # loop is blocked. This is NOT a conditional skip — the
            # asyncio.Lock prevents any race condition.
            # ═══════════════════════════════════════════════════════════
            if risk_manager.is_trade_active():
                active_info = risk_manager.get_active_trade_info()
                active_symbol = active_info.get("symbol", "unknown")
                active_contract = active_info.get("contract_id", "unknown")
                logger.warning(
                    f"[RF] 🔒 TRADE LOCKED — {active_symbol}#{active_contract} is in lifecycle | "
                    f"Mutex held: {risk_manager.trade_mutex.locked()} | "
                    f"Skipping scan until lifecycle completes"
                )
            elif risk_manager.is_halted():
                elapsed = (datetime.now() - risk_manager._halt_timestamp).total_seconds()
                logger.error(
                    f"[RF] 🚨 SYSTEM HALTED — no scanning until halt is cleared | "
                    f"Reason: {risk_manager._halt_reason} | "
                    f"Duration: {elapsed:.0f}s"
                )
            else:
                # No active trade, system not halted — safe to scan
                logger.debug(f"[RF] ✅ No active trades | Mutex free | Scanning {len(rf_config.RF_SYMBOLS)} symbols...")
                
                for symbol in rf_config.RF_SYMBOLS:
                    # If a trade became active during this loop iteration, stop immediately
                    if risk_manager.is_trade_active():
                        active_info = risk_manager.get_active_trade_info()
                        logger.info(
                            f"[RF][{symbol}] Trade opened during symbol loop "
                            f"({active_info.get('symbol')}#{active_info.get('contract_id')}) — stopping scan"
                        )
                        break

                    # If system halted during loop, stop
                    if risk_manager.is_halted():
                        logger.error(f"[RF][{symbol}] System halted during scan — stopping")
                        break

                    try:
                        await _process_symbol(
                            symbol, strategy, risk_manager, data_fetcher,
                            trade_engine, stake, user_id, event_manager,
                            UserTradesService,
                        )
                    except Exception as e:
                        logger.error(f"[RF][{symbol}] ❌ Error: {e}")

            # Log summary
            stats = risk_manager.get_statistics()
            logger.debug(
                f"[RF] Cycle #{cycle} done | "
                f"trades={stats['trades_today']} "
                f"W={stats['wins']} L={stats['losses']} "
                f"pnl={stats['total_pnl']:+.2f} "
                f"mutex={stats['mutex_locked']} halted={stats['halted']}"
            )

            # Broadcast statistics after each cycle
            await event_manager.broadcast({
                "type": "statistics",
                "stats": stats,
                "timestamp": datetime.now().isoformat(),
                "account_id": user_id,
            })

            # Refresh balance periodically
            try:
                fresh_balance = await data_fetcher.get_balance()
                if fresh_balance is not None:
                    _current_balance = fresh_balance
            except Exception:
                pass  # Keep using last known balance

            # Broadcast periodic bot_status so dashboard updates uptime/balance
            uptime_secs = int((datetime.now() - _start_time).total_seconds())
            await event_manager.broadcast({
                "type": "bot_status",
                "status": "running",
                "active_strategy": "RiseFall",
                "stake_amount": stake,
                "uptime_seconds": uptime_secs,
                "balance": _current_balance,
                "active_positions": stats.get('active_positions', 0),
                "win_rate": stats.get('win_rate', 0),
                "trades_today": stats.get('trades_today', 0),
                "profit": stats.get('total_pnl', 0),
                "account_id": user_id,
            })

            await asyncio.sleep(rf_config.RF_SCAN_INTERVAL)

    except asyncio.CancelledError:
        logger.info("🛑 Rise/Fall bot cancelled")
    except Exception as e:
        logger.error(f"❌ Rise/Fall bot fatal error: {e}")
        await event_manager.broadcast({
            "type": "error",
            "message": f"Rise/Fall fatal error: {e}",
            "timestamp": datetime.now().isoformat(),
            "account_id": user_id,
        })
    finally:
        _running = False

        # ─────────────────────────────────────────────────────────────────
        # EMERGENCY RECORD: If the bot was cancelled while a trade
        # lifecycle was in progress (mutex held + active trades), the
        # buy already executed on Deriv but the DB write never happened.
        # Write a safety record so the trade is never silently lost.
        # ─────────────────────────────────────────────────────────────────
        if risk_manager.trade_mutex.locked() and len(risk_manager.active_trades) > 0:
            # Pull from active_trades (richer: has direction, stake, etc.)
            first_trade = list(risk_manager.active_trades.values())[0]
            emergency_cid = first_trade.get("contract_id", "unknown")
            emergency_sym = first_trade.get("symbol", "unknown")
            logger.critical(
                f"[RF] 🚨 BOT CANCELLED MID-LIFECYCLE — in-flight trade detected! "
                f"contract={emergency_cid} symbol={emergency_sym} | "
                f"Writing emergency DB record to prevent silent loss"
            )
            # Attempt emergency DB write
            if user_id:
                try:
                    from app.services.trades_service import UserTradesService
                    emergency_record = {
                        "contract_id": emergency_cid,
                        "symbol": emergency_sym,
                        "signal": first_trade.get("direction", "unknown"),
                        "stake": first_trade.get("stake", 0),
                        "profit": 0,
                        "status": "unknown",
                        "duration": 0,
                        "strategy_type": "RiseFall",
                        "closure_reason": "bot_cancelled",
                        "timestamp": datetime.now().isoformat(),
                    }
                    UserTradesService.save_trade(user_id, emergency_record)
                    logger.info(
                        f"[RF] ✅ Emergency DB record written for {emergency_cid}"
                    )
                except Exception as db_err:
                    logger.error(
                        f"[RF] ❌ Emergency DB write FAILED for {emergency_cid}: {db_err}"
                    )

        # Release mutex if still held (cleanup on shutdown)
        if risk_manager.trade_mutex.locked():
            risk_manager.release_trade_lock(reason="bot shutdown — forced cleanup")

        await data_fetcher.disconnect()
        await trade_engine.disconnect()

        # Release cross-process session lock
        await _release_session_lock(user_id)

        logger.info("🛑 Rise/Fall bot stopped")

        # Send final statistics via Telegram
        if TELEGRAM_ENABLED:
            try:
                stats = risk_manager.get_statistics()
                await notifier.notify_bot_stopped(stats)
            except Exception as e:
                logger.error(f"❌ Telegram notification failed: {e}")

        # Broadcast bot_status → stopped
        await event_manager.broadcast({
            "type": "bot_status",
            "status": "stopped",
            "message": "Rise/Fall bot stopped",
            "account_id": user_id,
        })


def stop():
    """Signal the Rise/Fall bot loop to stop."""
    global _running
    _running = False
    logger.info("🛑 Rise/Fall bot stop requested")


def _should_emit_rf_decision(
    user_id: Optional[str],
    symbol: str,
    phase: str,
    decision: str,
    reason: str,
    min_interval_seconds: int,
) -> bool:
    """Throttle repeated RF decision events for cleaner frontend timelines."""
    if min_interval_seconds <= 0:
        return True

    now = datetime.now()
    state_key = f"{user_id or 'anon'}:{symbol}:{phase}:{decision}"
    fingerprint = f"{reason or ''}"
    last = _decision_emit_state.get(state_key)
    if not last:
        _decision_emit_state[state_key] = {"fingerprint": fingerprint, "time": now}
        return True

    elapsed = (now - last.get("time", datetime.min)).total_seconds()
    if last.get("fingerprint") != fingerprint or elapsed >= min_interval_seconds:
        _decision_emit_state[state_key] = {"fingerprint": fingerprint, "time": now}
        return True

    return False


async def _broadcast_rf_decision(
    event_manager,
    user_id: Optional[str],
    symbol: str,
    phase: str,
    decision: str,
    reason: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    severity: str = "info",
    min_interval_seconds: int = 20,
) -> None:
    """Emit structured Rise/Fall decision events for frontend compatibility."""
    if not _should_emit_rf_decision(
        user_id=user_id,
        symbol=symbol,
        phase=phase,
        decision=decision,
        reason=reason or "",
        min_interval_seconds=min_interval_seconds,
    ):
        return

    payload = {
        "type": "bot_decision",
        "bot": "risefall",
        "strategy": "RiseFall",
        "symbol": symbol,
        "phase": phase,
        "decision": decision,
        "severity": severity,
        "message": reason or decision.replace("_", " "),
        "timestamp": datetime.now().isoformat(),
        "account_id": user_id,
    }
    if reason:
        payload["reason"] = reason
    if details:
        payload["details"] = details

    try:
        await event_manager.broadcast(payload)
    except Exception as e:
        logger.debug(f"[RF] Decision event broadcast skipped due to error: {e}")


async def _process_symbol(
    symbol: str,
    strategy: RiseFallStrategy,
    risk_manager: RiseFallRiskManager,
    data_fetcher: DataFetcher,
    trade_engine: RFTradeEngine,
    stake: float,
    user_id: Optional[str],
    event_manager,
    UserTradesService,
):
    """
    Process one symbol through the STRICT 6-step lifecycle.
    
    LIFECYCLE:
        Step 1 — Acquire trade lock (asyncio.Lock mutex)
        Step 2 — Execute trade (buy Rise/Fall contract)
        Step 3 — Track trade (record open, begin monitoring)
        Step 4 — Contract monitoring until maturity (expiry or manual close)
        Step 5 — DB write with retry (halt on failure)
        Step 6 — Release lock, resume scanning
    
    At NO POINT can two trades exist simultaneously.
    If any step fails, the system halts rather than proceeding.
    """
    # ── Pre-check: Risk gate (daily cap, cooldown, daily loss, etc.) ──
    can_trade, reason = risk_manager.can_trade(symbol=symbol, stake=stake)
    if not can_trade:
        logger.info(f"[RF][{symbol}] ⏸️ Cannot trade: {reason}")
        await _broadcast_rf_decision(
            event_manager=event_manager,
            user_id=user_id,
            symbol=symbol,
            phase="risk",
            decision="no_trade",
            reason=reason,
            details={"gate": "can_trade"},
            severity="warning",
        )
        return

    # ── Pre-check: Fetch data and check for signal BEFORE acquiring lock ──
    # (avoid holding the lock during data fetching / analysis)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logger.debug(f"[RF][{symbol}] {ts} | Pre-scan: fetching 1m candle data")

    df = await data_fetcher.fetch_timeframe(
        symbol, rf_config.RF_TIMEFRAME, count=rf_config.RF_CANDLE_COUNT
    )
    if df is None or df.empty:
        logger.warning(f"[RF][{symbol}] No data returned")
        await _broadcast_rf_decision(
            event_manager=event_manager,
            user_id=user_id,
            symbol=symbol,
            phase="data",
            decision="no_trade",
            reason="No market data returned",
            details={"timeframe": rf_config.RF_TIMEFRAME},
            severity="warning",
        )
        return

    # Strategy analysis
    signal = strategy.analyze(data_1m=df, symbol=symbol, stake=stake)
    if signal is None:
        await _broadcast_rf_decision(
            event_manager=event_manager,
            user_id=user_id,
            symbol=symbol,
            phase="signal",
            decision="no_trade",
            reason="Strategy conditions not met",
            details={"mode": "triple_confirmation"},
        )
        return  # No triple-confirmation — already logged by strategy

    # ══════════════════════════════════════════════════════════════════════
    # SIGNAL CONFIRMED — Enter strict 6-step lifecycle
    # From this point, the lock is held until DB write completes.
    # ══════════════════════════════════════════════════════════════════════

    direction = signal["direction"]
    stake_val = signal["stake"]
    duration = signal["duration"]
    duration_unit = signal["duration_unit"]

    # ── Pre-check: Stake validation (use actual signal stake) ──
    max_stake = getattr(rf_config, "RF_MAX_STAKE", 100.0)
    if stake_val > max_stake:
        logger.warning(f"[RF][{symbol}] ⏸️ Stake ${stake_val} exceeds max ${max_stake} — rejecting")
        await _broadcast_rf_decision(
            event_manager=event_manager,
            user_id=user_id,
            symbol=symbol,
            phase="risk",
            decision="no_trade",
            reason=f"Stake ${stake_val:.2f} exceeds max ${max_stake:.2f}",
            details={"stake": stake_val, "max_stake": max_stake},
            severity="warning",
        )
        return

    await _broadcast_rf_decision(
        event_manager=event_manager,
        user_id=user_id,
        symbol=symbol,
        phase="signal",
        decision="opportunity_detected",
        reason="All Rise/Fall strategy checks aligned",
        details={
            "direction": direction,
            "stake": stake_val,
            "confidence": signal.get("confidence"),
            "rsi": signal.get("rsi"),
            "stoch": signal.get("stoch"),
        },
        min_interval_seconds=0,
    )

    # ── STEP 1: Acquire trade lock ──
    lock_acquired = await risk_manager.acquire_trade_lock(symbol, "pending", stake=stake_val)
    if not lock_acquired:
        logger.error(f"[RF][{symbol}] ❌ Could not acquire trade lock — system may be halted")
        await _broadcast_rf_decision(
            event_manager=event_manager,
            user_id=user_id,
            symbol=symbol,
            phase="risk",
            decision="no_trade",
            reason="Trade lock not acquired",
            details={"gate": "trade_lock"},
            severity="warning",
        )
        return

    try:
        # Broadcast signal event
        await event_manager.broadcast({
            "type": "signal",
            "symbol": symbol,
            "signal": direction,
            "strategy": "RiseFall",
            "timestamp": datetime.now().isoformat(),
            "account_id": user_id,
        })

        # Notify via Telegram
        if TELEGRAM_ENABLED:
            try:
                signal_info = {
                    "signal": direction,
                    "symbol": symbol,
                    "score": signal.get("confidence", 10),
                    "details": {
                        "rsi": signal.get("rsi", 0),
                        "adx": signal.get("stoch", 0),
                    },
                }
                await notifier.notify_signal(signal_info)
            except Exception as e:
                logger.error(f"❌ Telegram notification failed: {e}")

        # ── STEP 2: Execute trade ──
        # Defensive: never buy if active_trades non-empty (should never happen with mutex)
        if len(risk_manager.active_trades) > 0:
            logger.critical(
                f"[RF][{symbol}] 🚨 DEFENSIVE BLOCK: active_trades={len(risk_manager.active_trades)} "
                f"before buy — rejecting to prevent multiple trades"
            )
            return  # finally block will release lock

        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logger.info(
            f"[RF] STEP 2/6 | {ts} | EXECUTING TRADE {symbol} {direction} ${stake_val}"
        )
        await _broadcast_rf_decision(
            event_manager=event_manager,
            user_id=user_id,
            symbol=symbol,
            phase="execution",
            decision="opportunity_taken",
            reason="Lock acquired and execution started",
            details={"direction": direction, "stake": stake_val},
            min_interval_seconds=0,
        )

        result = await trade_engine.buy_rise_fall(
            symbol=symbol,
            direction=direction,
            stake=stake_val,
            duration=duration,
            duration_unit=duration_unit,
        )

        if not result:
            logger.error(f"[RF][{symbol}] ❌ Trade execution FAILED at Step 2")
            await _broadcast_rf_decision(
                event_manager=event_manager,
                user_id=user_id,
                symbol=symbol,
                phase="execution",
                decision="opportunity_failed",
                reason="Trade engine buy request failed",
                severity="error",
                min_interval_seconds=0,
            )
            # HALT: Trade execution failed — release lock and halt
            risk_manager.halt(f"Trade execution failed for {symbol} {direction}")
            await event_manager.broadcast({
                "type": "error",
                "message": f"🚨 Trade execution failed for {symbol} — system halted",
                "timestamp": datetime.now().isoformat(),
                "account_id": user_id,
            })
            return  # finally block will release lock

        contract_id = result["contract_id"]

        # Update locked trade info with real contract ID
        risk_manager._locked_trade_info = {"contract_id": contract_id, "symbol": symbol}

        # ── STEP 3: Track trade (record open + confirm monitoring) ──
        risk_manager.record_trade_open({
            "contract_id": contract_id,
            "symbol": symbol,
            "direction": direction,
            "stake": stake_val,
        })

        # Broadcast lock status + trade opened events
        await event_manager.broadcast({
            "type": "trade_lock_active",
            "symbol": symbol,
            "contract_id": contract_id,
            "message": f"🔒 Trade LOCKED on {symbol} — full lifecycle active",
            "timestamp": datetime.now().isoformat(),
            "account_id": user_id,
        })

        await event_manager.broadcast({
            "type": "trade_opened",
            "symbol": symbol,
            "direction": direction,
            "stake": stake_val,
            "contract_id": contract_id,
            "strategy": "RiseFall",
            "timestamp": datetime.now().isoformat(),
            "account_id": user_id,
        })

        # Notify via Telegram
        if TELEGRAM_ENABLED:
            try:
                trade_info = {
                    "contract_id": contract_id,
                    "symbol": symbol,
                    "direction": direction,
                    "stake": stake_val,
                    "entry_price": result.get("buy_price", 0),
                    "multiplier": 1,
                }
                await notifier.notify_trade_opened(trade_info, strategy_type="RiseFall")
            except Exception as e:
                logger.error(f"❌ Telegram notification failed: {e}")

        # ── STEP 4: Contract monitoring until maturity ──
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logger.info(
            f"[RF] STEP 4/6 | {ts} | MONITORING contract #{contract_id} "
            f"— system LOCKED for other trades"
        )

        settlement = await trade_engine.wait_for_result(contract_id, stake=stake_val)

        # Record trade closure in risk manager
        if settlement:
            pnl = settlement["profit"]
            status = settlement["status"]
            # Closure type is always returned from wait_for_result
            closure_reason = settlement.get("closure_type", "unknown")
            risk_manager.record_trade_closed({
                "contract_id": contract_id,
                "profit": pnl,
                "status": status,
                "symbol": symbol,
            })
        else:
            # Settlement unknown — conservatively mark as loss
            logger.warning(f"[RF][{symbol}] ⚠️ Settlement unknown for #{contract_id}")
            pnl = -stake_val
            status = "loss"
            closure_reason = "settlement_unknown"
            risk_manager.record_trade_closed({
                "contract_id": contract_id,
                "profit": pnl,
                "status": status,
                "symbol": symbol,
            })

        # ── STEP 5: DB write with retry — lock stays held until confirmed ──
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logger.info(
            f"[RF] STEP 5/6 | {ts} | WRITING TRADE TO DB | contract={contract_id} "
            f"pnl={pnl:+.2f} status={status} closure={closure_reason}"
        )

        db_write_success = False
        if user_id:
            db_write_success = await _write_trade_to_db_with_retry(
                user_id=user_id,
                contract_id=contract_id,
                symbol=symbol,
                direction=direction,
                stake_val=stake_val,
                pnl=pnl,
                status=status,
                closure_reason=closure_reason,
                duration=duration,
                duration_unit=duration_unit,
                result=result,
                settlement=settlement,
                UserTradesService=UserTradesService,
            )
        else:
            logger.warning("[RF] ⚠️ No user_id — skipping DB write (trade lock will release)")
            db_write_success = True  # No user context — allow lock release

        if not db_write_success:
            # DB write failed after all retries — HALT the system
            risk_manager.halt(
                f"DB write failed for contract {contract_id} after "
                f"{rf_config.RF_DB_WRITE_MAX_RETRIES} retries"
            )
            await event_manager.broadcast({
                "type": "error",
                "message": (
                    f"🚨 SYSTEM HALTED: DB write failed for {symbol}#{contract_id}. "
                    f"Trade lock held. Manual intervention required."
                ),
                "timestamp": datetime.now().isoformat(),
                "account_id": user_id,
            })
            # Lock stays held — do NOT release! The finally block handles cleanup.
            return

        # ── DB write succeeded — now broadcast notifications ──
        # Broadcast trade_closed + unlock notification
        await event_manager.broadcast({
            "type": "trade_closed",
            "symbol": symbol,
            "contract_id": contract_id,
            "pnl": pnl,
            "status": status,
            "strategy": "RiseFall",
            "closure_reason": closure_reason,
            "timestamp": datetime.now().isoformat(),
            "account_id": user_id,
        })

        # Notify via Telegram
        if TELEGRAM_ENABLED:
            try:
                result_info = {
                    "status": status,
                    "profit": pnl,
                    "contract_id": contract_id,
                    "current_price": settlement.get("sell_price", 0) if settlement else 0,
                    "duration": signal.get("duration", 0),
                }
                await notifier.notify_trade_closed(result_info, {
                    "symbol": symbol,
                    "direction": direction,
                    "stake": stake_val,
                    "duration": signal.get("duration", 0),
                }, strategy_type="RiseFall")
            except Exception as e:
                logger.error(f"❌ Telegram notification failed: {e}")

        # Broadcast manual close alert so dashboard surfaces it clearly
        if closure_reason == "manual":
            await event_manager.broadcast({
                "type": "notification",
                "level": "warning",
                "title": "Manual Trade Close Detected",
                "message": (
                    f"⚠️ {symbol} contract #{contract_id} was manually closed on Deriv. "
                    f"Trade has been recorded in DB. P&L: ${pnl:.2f}"
                ),
                "timestamp": datetime.now().isoformat(),
                "account_id": user_id,
            })

        notification_type = "success" if pnl > 0 else "error" if pnl < 0 else "info"
        await event_manager.broadcast({
            "type": "notification",
            "level": notification_type,
            "title": f"RF Trade {status.title()}",
            "message": f"{symbol} Rise/Fall trade closed. P&L: ${pnl:.2f}",
            "timestamp": datetime.now().isoformat(),
            "account_id": user_id,
        })

    except Exception as e:
        # Unexpected error during lifecycle — halt
        logger.error(f"[RF][{symbol}] ❌ Lifecycle error: {e}")
        await _broadcast_rf_decision(
            event_manager=event_manager,
            user_id=user_id,
            symbol=symbol,
            phase="execution",
            decision="opportunity_failed",
            reason=f"Lifecycle error: {e}",
            severity="error",
            min_interval_seconds=0,
        )
        risk_manager.halt(f"Unexpected lifecycle error: {e}")
        await event_manager.broadcast({
            "type": "error",
            "message": f"🚨 SYSTEM HALTED: Lifecycle error on {symbol}: {e}",
            "timestamp": datetime.now().isoformat(),
            "account_id": user_id,
        })
        return

    finally:
        # ── STEP 6: Release lock (only if not halted) ──
        if risk_manager.is_halted():
            # Check if this is a transient error that might recover
            halt_reason_lower = risk_manager._halt_reason.lower()
            is_transient = any(x in halt_reason_lower for x in [
                "trade execution failed",
                "lifecycle error",
                "duplicate trade"  # Should never happen with new fixes, but be safe
            ])
            
            if is_transient:
                # Transient errors: release lock so next cycle can retry
                logger.warning(
                    f"[RF] ⚠️ System halted due to transient error. "
                    f"Releasing lock to allow recovery on next cycle. "
                    f"Reason: {risk_manager._halt_reason}"
                )
                risk_manager.release_trade_lock(
                    reason=f"transient error recovery — {halt_reason_lower}"
                )
                # Auto-clear halt so next cycle can proceed
                risk_manager.clear_halt()
            else:
                # Permanent errors (DB write failure, critical violations): hold lock
                logger.error(
                    f"[RF] 🚨 System halted due to critical error. "
                    f"Trade lock HELD — manual intervention may be required. "
                    f"Reason: {risk_manager._halt_reason}"
                )
        else:
            # Broadcast lock released
            await event_manager.broadcast({
                "type": "trade_lock_released",
                "symbol": symbol,
                "message": f"🔓 Trade UNLOCKED on {symbol} — scan resuming",
                "timestamp": datetime.now().isoformat(),
                "account_id": user_id,
            })
            risk_manager.release_trade_lock(
                reason=f"{symbol} lifecycle complete — pnl={pnl:+.2f}"
            )



# ═══════════════════════════════════════════════════════════════════════════════
# Database write retry (Step 5 of 6-step lifecycle)
# ═══════════════════════════════════════════════════════════════════════════════
async def _write_trade_to_db_with_retry(
    user_id: str,
    contract_id: str,
    symbol: str,
    direction: str,
    stake_val: float,
    pnl: float,
    status: str,
    closure_reason: str,
    duration: int,
    duration_unit: str,
    result: dict,
    settlement: dict,
    UserTradesService,
) -> bool:
    """
    Write trade to DB with configurable retries.
    
    Returns:
        True if DB write succeeded, False if all retries exhausted.
    """
    max_retries = rf_config.RF_DB_WRITE_MAX_RETRIES
    retry_delay = rf_config.RF_DB_WRITE_RETRY_DELAY

    # Convert duration to seconds (int) for DB
    duration_sec = 0
    if duration_unit == 'm':
        duration_sec = int(duration * 60)
    elif duration_unit == 'h':
        duration_sec = int(duration * 3600)
    elif duration_unit == 's':
        duration_sec = int(duration)

    trade_record = {
        "contract_id": contract_id,
        "symbol": symbol,
        "signal": direction,
        "stake": stake_val,
        "profit": pnl,
        "status": status,
        "duration": duration_sec,
        "strategy_type": "RiseFall",
        "closure_reason": closure_reason,
        "timestamp": datetime.now().isoformat(),
        "entry_price": result.get("buy_price"),
        "exit_price": settlement.get("sell_price") if settlement else None,
    }

    for attempt in range(1, max_retries + 1):
        try:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            logger.info(
                f"[RF] STEP 5/6 | {ts} | DB write attempt {attempt}/{max_retries} "
                f"for contract {contract_id} | closure={closure_reason}"
            )
            saved = UserTradesService.save_trade(user_id, trade_record)
            if saved:
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                logger.info(
                    f"[RF] STEP 5/6 | {ts} | ✅ Trade persisted to DB: {contract_id} "
                    f"(attempt {attempt}/{max_retries})"
                )
                return True
            else:
                logger.error(
                    f"[RF] STEP 5/6 | DB write returned falsy for {contract_id} "
                    f"(attempt {attempt}/{max_retries})"
                )
        except Exception as e:
            logger.error(
                f"[RF] STEP 5/6 | DB write error for {contract_id} "
                f"(attempt {attempt}/{max_retries}): {e}"
            )

        if attempt < max_retries:
            logger.info(f"[RF] STEP 5/6 | Retrying in {retry_delay}s...")
            await asyncio.sleep(retry_delay)

    # All retries exhausted
    logger.critical(
        f"[RF] STEP 5/6 | 🚨 ALL {max_retries} DB WRITE ATTEMPTS FAILED for {contract_id} "
        f"— trade lock will NOT be released"
    )
    return False

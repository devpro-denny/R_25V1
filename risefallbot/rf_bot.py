"""
Rise/Fall Bot Orchestrator
Main async loop — subscribes to 1-min OHLC, generates signals, executes trades
rf_bot.py
"""

import asyncio
import os
import logging
from datetime import datetime
from typing import Optional

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

# Module-level sentinel for clean stop
_running = False
_current_task: Optional[asyncio.Task] = None


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
    - Loops: fetch 1m candles → analyse → risk check → execute
    """
    # Set user_id in context for logging handlers to access
    from app.core.context import user_id_var
    user_id_var.set(user_id)
    
    # Lazy import to avoid circular imports at module level
    from app.bot.events import event_manager
    from app.services.trades_service import UserTradesService

    logger.info("=" * 60)
    logger.info("🚀 Rise/Fall Scalping Bot Starting")
    logger.info("=" * 60)

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
                # Create Rise/Fall specific risk text
                risk_text = (
                    f"🛡️ <b>Risk Management</b>\n"
                    f"   • TP: {rf_config.RF_TAKE_PROFIT_PCT*100:.0f}%\n"
                    f"   • SL: {rf_config.RF_STOP_LOSS_PCT*100:.0f}%"
                )
                await notifier.notify_bot_started(
                    balance, 
                    stake, 
                    "Rise/Fall Scalping",
                    symbol_count=len(rf_config.RF_SYMBOLS),
                    risk_text=risk_text
                )
            except Exception as e:
                logger.error(f"❌ Telegram notification failed: {e}")

    logger.info(f"📊 Symbols: {rf_config.RF_SYMBOLS}")
    logger.info(f"⏱️ Scan interval: {rf_config.RF_SCAN_INTERVAL}s")
    logger.info(f"💵 Stake: ${stake}")
    logger.info(f"📏 Contract: {rf_config.RF_CONTRACT_DURATION}{rf_config.RF_DURATION_UNIT}")
    logger.info("=" * 60)

    global _running
    _running = True
    cycle = 0
    _start_time = datetime.now()
    _current_balance = balance or 0.0

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
            logger.info(
                f"\n{'='*60}\n"
                f"[RF] CYCLE #{cycle} | {datetime.now().strftime('%H:%M:%S')}\n"
                f"{'='*60}"
            )

            # ⚠️ CHECK: Is a trade currently active?
            if risk_manager.is_trade_active():
                active_info = risk_manager.get_active_trade_info()
                active_symbol = active_info.get("symbol", "unknown")
                active_contract = active_info.get("contract_id", "unknown")
                logger.warning(
                    f"[RF] 🔒 TRADE LOCKED — {active_symbol}#{active_contract} is being monitored "
                    f"| Skipping signal scan until trade closes | "
                    f"Active trades: {len(risk_manager.active_trades)}"
                )
            else:
                # No active trade — safe to scan for new signals
                logger.info(f"[RF] ✅ No active trades | Scanning {len(rf_config.RF_SYMBOLS)} symbols for signals...")
                
                for symbol in rf_config.RF_SYMBOLS:
                    # Double-check: if a trade became active during this loop, stop immediately
                    if risk_manager.is_trade_active():
                        active_info = risk_manager.get_active_trade_info()
                        logger.info(
                            f"[RF][{symbol}] Trade opened during symbol loop ({active_info.get('symbol')}#{active_info.get('contract_id')}) — stopping scan"
                        )
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
            logger.info(
                f"[RF] Cycle #{cycle} done | "
                f"trades={stats['trades_today']} "
                f"W={stats['wins']} L={stats['losses']} "
                f"pnl={stats['total_pnl']:+.2f}"
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
        await data_fetcher.disconnect()
        await trade_engine.disconnect()
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
    Process one symbol: fetch data → analyse → risk check → trade.
    
    ENFORCES: Only 1 concurrent trade globally across all symbols.
    Will not execute a trade if another symbol's trade is currently active.
    """
    # 1. Risk gate (per-symbol) — checks global concurrency + daily limits
    can_trade, reason = risk_manager.can_trade(symbol=symbol)
    if not can_trade:
        logger.info(f"[RF][{symbol}] ⏸️ Cannot trade: {reason}")
        return

    # 1b. Additional enforcement: Check if ANY trade is currently active
    if risk_manager.is_trade_active():
        active_info = risk_manager.get_active_trade_info()
        active_symbol = active_info.get("symbol", "unknown")
        active_contract = active_info.get("contract_id", "unknown")
        logger.warning(
            f"[RF][{symbol}] 🔒 LOCKED — {active_symbol}#{active_contract} is currently active | "
            f"Waiting for that trade to close before proceeding..."
        )
        return

    # 2. Fetch 1-minute candle data (reuse DataFetcher)
    df = await data_fetcher.fetch_timeframe(
        symbol, rf_config.RF_TIMEFRAME, count=rf_config.RF_CANDLE_COUNT
    )
    if df is None or df.empty:
        logger.warning(f"[RF][{symbol}] No data returned")
        return

    # 3. Strategy analysis
    signal = strategy.analyze(data_1m=df, symbol=symbol, stake=stake)
    if signal is None:
        return  # No triple-confirmation — already logged by strategy

    # 4. Broadcast signal event + Telegram notification
    timestamp = datetime.now().isoformat()
    direction = signal["direction"]

    await event_manager.broadcast({
        "type": "signal",
        "symbol": symbol,
        "signal": direction,
        "strategy": "RiseFall",
        "timestamp": timestamp,
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
                    "adx": signal.get("stoch", 0),  # Use stochastic as momentum indicator
                },
            }
            await notifier.notify_signal(signal_info)
        except Exception as e:
            logger.error(f"❌ Telegram notification failed: {e}")

    # 5. Execute trade
    stake_val = signal["stake"]
    duration = signal["duration"]
    duration_unit = signal["duration_unit"]

    result = await trade_engine.buy_rise_fall(
        symbol=symbol,
        direction=direction,
        stake=stake_val,
        duration=duration,
        duration_unit=duration_unit,
    )

    if not result:
        logger.error(f"[RF][{symbol}] Trade execution failed")
        return

    contract_id = result["contract_id"]

    # 6. Record trade open (locks the system for other symbols)
    risk_manager.record_trade_open({
        "contract_id": contract_id,
        "symbol": symbol,
        "direction": direction,
        "stake": stake_val,
    })

    # 6b. Broadcast system-wide lock status
    await event_manager.broadcast({
        "type": "trade_lock_active",
        "symbol": symbol,
        "contract_id": contract_id,
        "message": f"🔒 Trade LOCKED on {symbol} — system will monitor until close",
        "timestamp": datetime.now().isoformat(),
        "account_id": user_id,
    })

    # Broadcast trade_opened event
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
                "multiplier": 1,  # Rise/Fall has fixed multiplier
            }
            await notifier.notify_trade_opened(trade_info, strategy_type="RiseFall")
        except Exception as e:
            logger.error(f"❌ Telegram notification failed: {e}")

    # 7. Wait for contract settlement (async — blocks only this symbol)
    logger.info(
        f"[RF][{symbol}] ⏳ Monitoring contract #{contract_id} until close... "
        f"(system locked for other symbols)"
    )
    settlement = await trade_engine.wait_for_result(contract_id, stake=stake_val)

    if settlement:
        pnl = settlement["profit"]
        status = settlement["status"]
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
        risk_manager.record_trade_closed({
            "contract_id": contract_id,
            "profit": pnl,
            "status": status,
            "symbol": symbol,
        })

    # 8. Broadcast trade_closed + unlock notification
    await event_manager.broadcast({
        "type": "trade_lock_released",
        "symbol": symbol,
        "contract_id": contract_id,
        "status": status,
        "pnl": pnl,
        "message": f"🔓 Trade UNLOCKED on {symbol} — system ready for next trade",
        "timestamp": datetime.now().isoformat(),
        "account_id": user_id,
    })

    # Broadcast trade_closed + notification events
    await event_manager.broadcast({
        "type": "trade_closed",
        "symbol": symbol,
        "contract_id": contract_id,
        "pnl": pnl,
        "status": status,
        "strategy": "RiseFall",
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

    notification_type = "success" if pnl > 0 else "error" if pnl < 0 else "info"
    await event_manager.broadcast({
        "type": "notification",
        "level": notification_type,
        "title": f"RF Trade {status.title()}",
        "message": f"{symbol} Rise/Fall trade closed. P&L: ${pnl:.2f}",
        "timestamp": datetime.now().isoformat(),
        "account_id": user_id,
    })

    # 9. Persist trade to Supabase (same pattern as multiplier bot)
    if user_id:
        try:
            # Convert duration to seconds (int) for DB
            duration_sec = 0
            if duration_unit == 'm':
                duration_sec = int(duration * 60)
            elif duration_unit == 'h':
                duration_sec = int(duration * 3600)
            elif duration_unit == 's':
                duration_sec = int(duration)
            # ticks 't' -> 0 or distinct handling (RF strategy uses minutes)

            trade_record = {
                "contract_id": contract_id,
                "symbol": symbol,
                "signal": direction,          # CALL or PUT
                "stake": stake_val,
                "profit": pnl,
                "status": status,
                "duration": duration_sec,     # Store as integer seconds
                "strategy_type": "RiseFall",
                "timestamp": datetime.now().isoformat(),
                "entry_price": result.get("buy_price"),
                "exit_price": settlement.get("sell_price") if settlement else None,
            }
            saved = UserTradesService.save_trade(user_id, trade_record)
            if saved:
                logger.info(f"[RF] ✅ Trade persisted to DB: {contract_id}")
            else:
                logger.error(f"[RF] ❌ DB persistence failed for contract {contract_id}")
        except Exception as e:
            logger.error(f"[RF] ❌ DB save error for {contract_id}: {e}")

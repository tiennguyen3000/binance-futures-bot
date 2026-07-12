#!/usr/bin/env python3
"""
Main entry point for the Binance Futures trading bot.
Runs independently, scanning for signals and executing trades.

Supports:
  python main.py          # Testnet mode (default)
  python main.py --live   # Live trading
  python main.py --test   # Quick single scan test
"""
import argparse
import logging
import os
import signal
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

# Ensure we can import sibling modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load saved mode from state file (LIVE or TESTNET)
from bot_controller import load_saved_mode, config as bot_cfg
saved_mode = load_saved_mode()
bot_cfg.mode = saved_mode

# Load the correct .env file based on mode
env_file = ".env.live" if saved_mode == "LIVE" else ".env"
env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), env_file)
if os.path.isfile(env_path):
    load_dotenv(env_path)
    print(f"[Startup] Loaded credentials from {env_file} (mode={saved_mode})")
else:
    load_dotenv()
    print(f"[Startup] {env_file} not found, using .env (mode={saved_mode})")

from api_client import BinanceFuturesClient
from settings import BotSettings
from state_manager import StateManager, BotState, Position
from scanner import SmartScanner as SignalScanner, klines_to_df, atr
from executor import OrderExecutor
from telegram_notifier import (
    send_message, signal_msg, entry_msg, exit_msg,
    status_msg, error_msg, bot_start_msg, bot_stop_msg,
)
from bot_controller import config as bot_cfg, start_polling, send_tg, set_fetchers as tg_set_fetchers
from api_server import start_api_thread, set_fetchers

# ---- Configuration ----

# Adjust these for your strategy
SCAN_INTERVAL_SECONDS = 120         # Quét mỗi 120s (2 phút)
MAX_POSITIONS = 1                    # Tối đa 1 lệnh
CAPITAL_USDT = 100.0
RISK_PER_TRADE = 0.015              # 1.5% rủi ro trên mỗi lệnh
SL_DISTANCE = 0.00                  # Không dùng fixed % — dùng ATR từ scanner
TP_DISTANCE = 0.00                  # Không dùng fixed % — dùng ATR từ scanner
LEVERAGE = 10                       # Đòn bẩy 10x
TOP_N_SYMBOLS = 30
INTERVAL = "15m"

LOG_DIR = Path("logs")
LOG_FILE = LOG_DIR / "trade.log"
LOG_MAX_BYTES = 5 * 1024 * 1024     # 5 MB
LOG_BACKUP_COUNT = 3

# ---- Logging Setup ----


def setup_logging(verbose: bool = False):
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    level = logging.DEBUG if verbose else logging.INFO

    # File handler (rotating)
    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
    )
    file_handler.setLevel(level)
    file_format = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_format)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_format = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    console_handler.setFormatter(console_format)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    # Quiet noisy libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)

    return logging.getLogger(__name__)


# ---- Graceful Shutdown ----


class GracefulKiller:
    """Handle Ctrl+C gracefully — finish current cycle then exit."""

    def __init__(self):
        self.kill_now = False
        signal.signal(signal.SIGINT, self._exit_gracefully)
        signal.signal(signal.SIGTERM, self._exit_gracefully)

    def _exit_gracefully(self, signum, frame):
        logger = logging.getLogger(__name__)
        logger.warning("Shutdown signal received. Finishing current cycle...")
        self.kill_now = True


# ---- Position Monitoring & SL/TP (Freqtrade pattern: dedicated monitor thread) ----


def _monitor_sl_tp(client: BinanceFuturesClient, state_mgr: StateManager, executor: OrderExecutor, stop_event: threading.Event):
    """Watchdog only: Binance conditional orders are the exit source of truth.

    This thread never submits a second discretionary close based on a local
    ticker. It detects exchange-side exits and lets the main reconciliation
    report them with fill/income data in a future accounting implementation.
    """
    logger = logging.getLogger(__name__)
    logger.info("Exchange protection watchdog started (interval=5s)")
    while not stop_event.is_set():
        try:
            for pos in state_mgr.get_positions():
                if abs(client.get_position_amt(pos.symbol)) < 1e-8:
                    logger.info("Exchange reports %s closed; awaiting reconciliation", pos.symbol)
        except Exception as exc:
            logger.warning("Exchange protection watchdog error: %s", exc)
        stop_event.wait(5)
    logger.info("Exchange protection watchdog stopped")


def start_sltp_monitor(client: BinanceFuturesClient, state_mgr: StateManager, executor: OrderExecutor) -> threading.Event:
    """Khởi động SL/TP monitor thread. Trả về stop_event."""
    stop_event = threading.Event()
    t = threading.Thread(
        target=_monitor_sl_tp,
        args=(client, state_mgr, executor, stop_event),
        daemon=True,
    )
    t.start()
    return stop_event


def check_and_sync_positions(client: BinanceFuturesClient, state_mgr: StateManager, executor: OrderExecutor):
    """
    Check exchange for closed positions and sync local state.
    Removes positions from local tracking if they no longer exist on exchange.
    """
    for pos in list(state_mgr.get_positions()):
        try:
            amt = client.get_position_amt(pos.symbol)
            # A non-zero exchange amount remains exposure regardless of symbol
            # precision. get_position_amt is the authoritative signed value.
            if abs(amt) == 0:
                logger = logging.getLogger(__name__)
                logger.info(f"Position {pos.symbol} closed on exchange (amt=0), removing from tracker")
                
                # Try to get PnL from the position risk report
                risk = client.get_position_risk(pos.symbol)
                pnl = 0.0
                for r in risk:
                    if r.get("symbol") == pos.symbol:
                        pnl = float(r.get("unRealizedProfit", 0))
                        break
                
                state_mgr.update_position_pnl(pos.symbol, pnl, 0.0)
                state_mgr.remove_position(pos.symbol)

                # Telegram: vị thế tự đóng (SL/TP)
                entry = pos.entry_price
                roi = (pnl / (entry * pos.quantity / LEVERAGE)) * 100 if entry > 0 else 0
                reason = "tp" if pnl > 0 else "sl"
                send_message(exit_msg(
                    pos.symbol, pos.side, entry, entry * (1 + (0.03 if pos.side == "LONG" else -0.03)),
                    pnl, roi, reason,
                ))
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.warning(f"Failed to check position {pos.symbol}: {e}")


def reconcile_exchange_positions(client: BinanceFuturesClient, state_mgr: StateManager) -> dict:
    """Classify every exchange position before new entries are allowed.

    A position without a matching exchange-native stop is never silently
    imported: the bot enters SAFE_HALT and requires operator remediation.
    """
    logger = logging.getLogger(__name__)
    unprotected: list[str] = []
    imported: list[str] = []
    account = client.get_account_info()
    if account.get("_error"):
        reason = f"Cannot reconcile account: {account}"
        state_mgr.safe_halt(reason)
        logger.critical(reason)
        return {"ready": False, "unprotected": ["ACCOUNT_READ_FAILED"], "imported": []}
    for raw in account.get("positions", []):
        amount = float(raw.get("positionAmt", 0))
        if abs(amount) < 1e-8:
            continue
        if raw.get("positionSide") in {"LONG", "SHORT"}:
            reason = f"Hedge-mode exposure is unsupported: {raw.get('symbol')} {raw.get('positionSide')}"
            state_mgr.safe_halt(reason)
            logger.critical(reason)
            return {"ready": False, "unprotected": [raw.get("symbol", "UNKNOWN")], "imported": imported}
        symbol = raw.get("symbol", "")
        side = "LONG" if amount > 0 else "SHORT"
        close_side = "SELL" if side == "LONG" else "BUY"
        orders = client.get_open_orders(symbol)
        if not isinstance(orders, list):
            unprotected.append(symbol)
            logger.critical("Cannot read orders for %s; treating exposure as unsafe", symbol)
            continue
        stop = next((order for order in orders if order.get("type") == "STOP_MARKET" and order.get("side") == close_side), None)
        take_profit = next((order for order in orders if order.get("type") == "TAKE_PROFIT_MARKET" and order.get("side") == close_side), None)
        if not stop:
            unprotected.append(symbol)
            continue
        if state_mgr.has_position(symbol):
            continue
        added = state_mgr.add_position(Position(
            symbol=symbol,
            side=side,
            entry_price=float(raw.get("entryPrice", 0)),
            quantity=abs(amount),
            sl_price=float(stop.get("stopPrice", 0)),
            tp_price=float(take_profit.get("stopPrice", 0)) if take_profit else 0.0,
            order_id_sl=str(stop.get("orderId") or stop.get("clientAlgoId") or "") or None,
            order_id_tp=str(take_profit.get("orderId") or take_profit.get("clientAlgoId") or "") if take_profit else None,
        ))
        if added:
            imported.append(symbol)
        else:
            unprotected.append(symbol)
    if unprotected:
        state_mgr.safe_halt("Unprotected or untracked exchange exposure: " + ", ".join(unprotected))
        logger.critical("Reconciliation failed; new entries disabled: %s", unprotected)
        return {"ready": False, "unprotected": unprotected, "imported": imported}
    logger.info("Exchange reconciliation complete; protected positions=%s", imported)
    return {"ready": True, "unprotected": [], "imported": imported}


# ---- Main Loop ----


def run_bot(testnet: bool = True, use_deepseek: bool = False):
    logger = logging.getLogger(__name__)
    # Runtime settings are the source of truth; legacy constants remain only as CLI defaults.
    runtime_settings = BotSettings.from_env()
    bot_cfg.trading_enabled = runtime_settings.trading_enabled
    bot_cfg.max_positions = runtime_settings.max_positions
    logger.info("=" * 60)
    logger.info(f"Binance Futures Trading Bot Starting...")
    logger.info(f"Mode: {'TESTNET' if testnet else 'LIVE'}")
    logger.info(f"Max positions: {runtime_settings.max_positions}")
    logger.info(f"Capital allocation: {runtime_settings.capital_usdt} USDT, Risk/trade: {runtime_settings.risk_per_trade*100:.2f}%")
    logger.info(f"Leverage: {runtime_settings.leverage}x, SL: ATR-based, TP: ATR-based")
    logger.info(f"Scan interval: {runtime_settings.scan_interval_seconds}s")
    if use_deepseek:
        logger.info("DeepSeek AI filter: ENABLED")
    logger.info("=" * 60)

    # Telegram startup notification
    send_message(bot_start_msg(
        mode="TESTNET" if testnet else "LIVE",
        capital=runtime_settings.capital_usdt,
        leverage=runtime_settings.leverage,
    ))

    # Settings were resolved before startup logging.
    client = BinanceFuturesClient(testnet=testnet)
    state_mgr = StateManager(max_positions=bot_cfg.max_positions)
    executor = OrderExecutor(
        client=client,
        state_mgr=state_mgr,
        capital_usdt=runtime_settings.capital_usdt,
        risk_per_trade=runtime_settings.risk_per_trade,
        leverage=runtime_settings.leverage,
        max_position_notional_pct=runtime_settings.max_position_notional_pct,
    )
    scanner = SignalScanner(
        client=client,
        top_n=bot_cfg.top_n,
        interval_entry=INTERVAL,
        interval_trend="1h",
        max_funding_rate_pct=bot_cfg.max_funding_rate_pct,
    )

    # Synchronize before every signed startup request, including position-mode detection.
    try:
        client.sync_time()
    except Exception as exc:
        state_mgr.safe_halt(f"Cannot synchronize Binance server time: {exc}")
        raise RuntimeError("Startup aborted: Binance time synchronization failed") from exc

    try:
        client.assert_one_way_mode()
    except Exception as exc:
        state_mgr.safe_halt(f"Cannot verify One-way position mode: {exc}")
        raise RuntimeError("Startup aborted: Binance account must use One-way Mode") from exc

    # Reconcile first; never start entries while exchange exposure is unknown.
    reconciliation = reconcile_exchange_positions(client, state_mgr)
    if not reconciliation["ready"]:
        send_tg("🚨 SAFE_HALT: unprotected exchange positions detected; entries disabled.")

    # Legacy sync is intentionally not used: it inferred ATR targets and left stops unset.

    # Khởi động Telegram command listener (polling thread)
    tg_stop = start_polling()
    send_tg("🤖 Bot đã khởi động. Gõ /help để xem lệnh điều khiển.")

    # Khởi động REST API server
    set_fetchers(
        positions_fn=lambda: executor.get_positions_with_pnl(),
        balance_fn=lambda: client.get_balance("USDT"),
        executor=executor,
    )
    api_thread = start_api_thread(host=os.getenv("API_HOST", "127.0.0.1"), port=int(os.getenv("API_PORT", "8765")))

    # Inject callbacks cho Telegram commands (real-time data)
    tg_set_fetchers(
        positions_fn=lambda: executor.get_positions_with_pnl(),
        balance_fn=lambda: client.get_balance("USDT"),
    )

    # Optional DeepSeek integration
    deepseek_filter = None
    if use_deepseek:
        try:
            from deepseek_integration import deepseek_signal_filter
            deepseek_filter = deepseek_signal_filter
        except ImportError:
            logger.warning("DeepSeek module not found, skipping AI filter")

    killer = GracefulKiller()
    cycle_count = 0

    # Khởi động SL/TP monitor thread (Freqtrade pattern — check mỗi 5s)
    sltp_stop = start_sltp_monitor(client, state_mgr, executor)
    logger.info("SL/TP monitor active (5s interval)")

    while not killer.kill_now:
        cycle_count += 1
        logger.info(f"--- Cycle {cycle_count} ---")

        try:
            # 1. Sync positions (check if any were closed externally via SL/TP)
            check_and_sync_positions(client, state_mgr, executor)

            # 2. Report current status
            positions = state_mgr.get_positions()
            if positions:
                logger.info(f"Active positions: {len(positions)}/{MAX_POSITIONS}")
                for p in positions:
                    logger.info(f"  {p.side} {p.symbol} @ {p.entry_price:.2f} | "
                                f"SL={p.sl_price:.2f} TP={p.tp_price:.2f}")

            # Cập nhật động số coin quét từ Telegram command
            if scanner.top_n != bot_cfg.top_n:
                scanner.top_n = bot_cfg.top_n
                logger.info(f"Top N updated to {bot_cfg.top_n} via Telegram")

            # Cập nhật động số vị thế tối đa
            if state_mgr.max_positions != bot_cfg.max_positions:
                state_mgr.max_positions = bot_cfg.max_positions
                logger.info(f"Max positions updated to {bot_cfg.max_positions} via Telegram")

            # Cập nhật động funding rate filter
            if scanner.max_funding_rate_pct != bot_cfg.max_funding_rate_pct:
                scanner.max_funding_rate_pct = bot_cfg.max_funding_rate_pct
                logger.info(f"Funding rate filter updated to {bot_cfg.max_funding_rate_pct}% via Telegram")

            # Kiểm tra restart_needed (khi đổi testnet/live)
            if bot_cfg.restart_needed:
                logger.warning("Restart needed for mode change. Shutting down...")
                send_tg("🔄 Cần restart để đổi mode. Đang tắt bot...")
                break

            # 3. Scan for signals if we have room
            if state_mgr.can_open():

                # Kiểm tra trading_enabled từ Telegram command
                if not bot_cfg.trading_enabled:
                    logger.info("Trading is DISABLED via Telegram. Skipping entry.")
                    time.sleep(runtime_settings.scan_interval_seconds)
                    continue

                state_mgr.set_state(BotState.SCANNING)

                # Sizing and exchange filters decide feasibility per symbol; a fixed
                # account-wide balance threshold would incorrectly reject low-notional contracts.
                balance = client.get_balance("USDT")
                logger.info(f"USDT Balance: {balance:.2f}")
                if balance <= 0:
                    logger.warning("No available USDT balance; skipping scan")
                    time.sleep(runtime_settings.scan_interval_seconds)
                    continue

                symbol, signal = scanner.scan(eligible=lambda candidate, candidate_signal: executor.can_execute_signal(
                    candidate,
                    candidate_signal["side"],
                    candidate_signal["entry_price"],
                    candidate_signal["sl_price"],
                    candidate_signal["tp1_price"],
                ))

                if symbol and signal:
                    # Optional DeepSeek filter
                    if deepseek_filter:
                        logger.info(f"Checking signal with DeepSeek AI...")
                        verdict = deepseek_filter(
                            symbol, signal, signal["entry_price"], signal["rsi"]
                        )
                        logger.info(f"DeepSeek verdict: {verdict}")
                        if verdict and verdict.lower().startswith("no"):
                            logger.info(f"DeepSeek rejected signal for {symbol}, skipping")
                            time.sleep(runtime_settings.scan_interval_seconds)
                            continue

                    # Execute trade with ATR-based SL/TP from scanner
                    state_mgr.set_state(BotState.ENTERING)
                    result = executor.open_position(
                        symbol, signal["side"],
                        sl_price=signal.get("sl_price"),
                        tp1_price=signal.get("tp1_price"),
                        tp2_price=signal.get("tp2_price"),
                    )
                    logger.info(f"Trade result: {result}")

                    if result.get("status") == "success":
                        logger.info(
                            f">>> ENTERED {signal['side']} {symbol} @ {result['entry_price']:.2f} "
                            f"| Qty={result['quantity']:.4f} "
                            f"| SL={result['sl_price']:.2f} "
                            f"TP1={result['tp1_price']:.2f} TP2={result['tp2_price']:.2f}"
                        )
                        # Telegram: tín hiệu + vào lệnh
                        send_message(signal_msg(
                            symbol, signal["side"], signal["entry_price"], signal["rsi"],
                            signal.get("confidence"),
                        ))
                        send_message(entry_msg(
                            symbol, signal["side"],
                            result["entry_price"], result["quantity"],
                            result["sl_price"], result["tp1_price"],
                            balance, result.get("tp2_price"),
                        ))
                else:
                    logger.info("No signal found this cycle")
            else:
                logger.info("Max positions reached — scanning paused")

        except Exception as e:
            logger.exception(f"Error in main loop: {e}")
            send_message(error_msg("Main loop error", str(e)))

        # Sleep between cycles (unless interrupted)
        for _ in range(runtime_settings.scan_interval_seconds):
            if killer.kill_now:
                break
            time.sleep(1)

    tg_stop.set()
    sltp_stop.set()
    logger.info("Bot shutting down...")
    positions = state_mgr.get_positions()
    
    # Telegram: shutdown notification
    pts = [{"side": p.side, "symbol": p.symbol, "entry_price": p.entry_price} for p in positions]
    send_message(bot_stop_msg(pts))
    
    if positions:
        logger.info(f"Active positions on shutdown ({len(positions)}):")
        for p in positions:
            logger.info(f"  {p.side} {p.symbol} @ {p.entry_price:.2f}")
        logger.info("Positions will remain open on exchange. Close them manually or restart bot.")
    else:
        logger.info("No active positions on shutdown.")


def quick_test_run(testnet: bool = True):
    """Single scan test to verify setup works."""
    logger = logging.getLogger(__name__)
    logger.info("=== Quick Test Mode ===")

    client = BinanceFuturesClient(testnet=testnet)
    scanner = SignalScanner(client=client, top_n=TOP_N_SYMBOLS, interval_entry=INTERVAL, interval_trend="1h")

    # Test connection: get account info
    try:
        account = client.get_account_info()
        logger.info(f"Account connected. Can trade: {account.get('canTrade', False)}")
        balance = client.get_balance("USDT")
        logger.info(f"USDT Balance: {balance:.2f}")

        # Test top symbols
        symbols = client.get_top_volume_symbols(limit=5)
        logger.info(f"Top 5 volume symbols: {[s['symbol'] for s in symbols]}")

        # Test single scan
        symbol, signal = scanner.scan()
        if symbol:
            logger.info(f"Signal found: {signal['side']} {symbol} @ {signal['entry_price']} RSI={signal['rsi']:.1f}")
        else:
            logger.info("No signal in current scan")
    except Exception as e:
        logger.exception(f"Test failed: {e}")
        return False

    return True


# ---- Entry Point ----


def main():
    parser = argparse.ArgumentParser(
        description="Binance Futures Trading Bot — EMA+RSI Strategy"
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run in LIVE mode (default: testnet)",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Run a single scan test and exit",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable DEBUG logging",
    )
    parser.add_argument(
        "--deepseek",
        action="store_true",
        help="Enable DeepSeek AI signal filter",
    )
    parser.add_argument(
        "--interval",
        type=str,
        default=INTERVAL,
        help=f"Kline interval (default: {INTERVAL})",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=TOP_N_SYMBOLS,
        help=f"Number of top coins to scan (default: {TOP_N_SYMBOLS})",
    )

    args = parser.parse_args()

    # Setup logging
    logger = setup_logging(verbose=args.verbose)

    # Mode is resolved from the loaded environment. --live is an explicit CLI
    # override; a saved Telegram mode only selects which env file is loaded.
    runtime_settings = BotSettings.from_env()
    testnet = False if args.live else runtime_settings.testnet

    if args.test:
        quick_test_run(testnet=testnet)
    else:
        run_bot(testnet=testnet, use_deepseek=args.deepseek)


if __name__ == "__main__":
    main()

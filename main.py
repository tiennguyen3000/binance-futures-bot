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
from state_manager import StateManager, BotState, Position
from scanner import SmartScanner as SignalScanner
from executor import OrderExecutor
from telegram_notifier import (
    send_message, signal_msg, entry_msg, exit_msg,
    status_msg, error_msg, bot_start_msg, bot_stop_msg,
)
from bot_controller import config as bot_cfg, start_polling, send_tg
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
    """
    Thread riêng kiểm tra SL/TP mỗi 5 giây (Freqtrade pattern).
    Không block main loop, phản hồi nhanh khi chạm SL/TP.
    """
    logger = logging.getLogger(__name__)
    logger.info("SL/TP monitor thread started (interval=5s)")

    while not stop_event.is_set():
        try:
            for pos in list(state_mgr.get_positions()):
                try:
                    price = client.get_symbol_price(pos.symbol)
                    if price <= 0:
                        continue

                    triggered = False
                    reason = ""

                    if pos.side == "LONG":
                        if pos.sl_price > 0 and price <= pos.sl_price:
                            triggered = True
                            reason = "sl"
                            logger.warning(f"🔴 SL TRIGGERED {pos.symbol}: {price:.2f} <= {pos.sl_price:.2f}")
                        elif pos.tp_price > 0 and price >= pos.tp_price:
                            triggered = True
                            reason = "tp"
                            logger.info(f"🟢 TP TRIGGERED {pos.symbol}: {price:.2f} >= {pos.tp_price:.2f}")
                    else:  # SHORT
                        if pos.sl_price > 0 and price >= pos.sl_price:
                            triggered = True
                            reason = "sl"
                            logger.warning(f"🔴 SL TRIGGERED {pos.symbol}: {price:.2f} >= {pos.sl_price:.2f}")
                        elif pos.tp_price > 0 and price <= pos.tp_price:
                            triggered = True
                            reason = "tp"
                            logger.info(f"🟢 TP TRIGGERED {pos.symbol}: {price:.2f} <= {pos.tp_price:.2f}")

                    if triggered:
                        result = executor.close_position(pos.symbol)
                        if result.get("status") == "success":
                            logger.info(f"Closed {pos.symbol} via {reason}: PnL={result.get('pnl',0):+.2f}")
                        else:
                            logger.warning(f"Failed to close {pos.symbol} on {reason}: {result}")

                except Exception as e:
                    logger.debug(f"SL/TP check error for {pos.symbol}: {e}")

        except Exception as e:
            logger.warning(f"SL/TP monitor error: {e}")

        stop_event.wait(5)  # Check mỗi 5 giây

    logger.info("SL/TP monitor thread stopped")


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
            # If position no longer exists on exchange (closed via SL/TP)
            if abs(amt) < 0.0001:
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


def sync_exchange_positions(client: BinanceFuturesClient, state_mgr: StateManager):
    """
    Import ANY open position từ exchange vào StateManager.
    (Xử lý trường hợp restart bot khi còn position trên sàn)
    """
    logger = logging.getLogger(__name__)
    try:
        account = client.get_account_info()
        for p in account.get("positions", []):
            amt = float(p.get("positionAmt", 0))
            if abs(amt) < 0.0001:
                continue
            symbol = p.get("symbol", "")
            if state_mgr.has_position(symbol):
                continue
            side = "LONG" if amt > 0 else "SHORT"
            entry = float(p.get("entryPrice", 0))
            logger.info(f"Synced position from exchange: {side} {symbol} {abs(amt)} @ {entry}")
            state_mgr.add_position(Position(
                symbol=symbol,
                side=side,
                entry_price=entry,
                quantity=abs(amt),
                sl_price=0.0,
                tp_price=0.0,
            ))
    except Exception as e:
        logger.warning(f"Failed to sync exchange positions: {e}")


# ---- Main Loop ----


def run_bot(testnet: bool = True, use_deepseek: bool = False):
    logger = logging.getLogger(__name__)
    logger.info("=" * 60)
    logger.info(f"Binance Futures Trading Bot Starting...")
    logger.info(f"Mode: {'TESTNET' if testnet else 'LIVE'}")
    logger.info(f"Scan interval: {SCAN_INTERVAL_SECONDS}s")
    logger.info(f"Max positions: {MAX_POSITIONS}")
    logger.info(f"Capital: {CAPITAL_USDT} USDT, Risk/trade: {RISK_PER_TRADE*100:.0f}%")
    logger.info(f"Leverage: {LEVERAGE}x, SL: {SL_DISTANCE*100:.0f}%, TP: {TP_DISTANCE*100:.0f}%")
    if use_deepseek:
        logger.info("DeepSeek AI filter: ENABLED")
    logger.info("=" * 60)

    # Telegram startup notification
    send_message(bot_start_msg(
        mode="TESTNET" if testnet else "LIVE",
        capital=CAPITAL_USDT,
        leverage=LEVERAGE,
    ))

    # Initialize components
    client = BinanceFuturesClient(testnet=testnet)
    state_mgr = StateManager(max_positions=bot_cfg.max_positions)
    executor = OrderExecutor(
        client=client,
        state_mgr=state_mgr,
        capital_usdt=CAPITAL_USDT,
        risk_per_trade=RISK_PER_TRADE,
        leverage=LEVERAGE,
    )
    scanner = SignalScanner(
        client=client,
        top_n=bot_cfg.top_n,
        interval_entry=INTERVAL,
        interval_trend="1h",
        max_funding_rate_pct=bot_cfg.max_funding_rate_pct,
    )

    # Sync positions từ exchange (quan trọng khi restart bot)
    sync_exchange_positions(client, state_mgr)

    # Khởi động Telegram command listener (polling thread)
    tg_stop = start_polling()
    send_tg("🤖 Bot đã khởi động. Gõ /help để xem lệnh điều khiển.")

    # Khởi động REST API server
    set_fetchers(
        positions_fn=lambda: executor.get_positions_with_pnl(),
        balance_fn=lambda: client.get_balance("USDT"),
        executor=executor,
    )
    api_thread = start_api_thread(host="0.0.0.0", port=8765)

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
                    time.sleep(SCAN_INTERVAL_SECONDS)
                    continue

                state_mgr.set_state(BotState.SCANNING)

                # Check balance before scanning
                balance = client.get_balance("USDT")
                logger.info(f"USDT Balance: {balance:.2f}")
                if balance < 50:
                    logger.warning(f"Balance too low ({balance:.2f} USDT), skipping scan")
                    time.sleep(SCAN_INTERVAL_SECONDS)
                    continue

                symbol, signal = scanner.scan()

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
                            time.sleep(SCAN_INTERVAL_SECONDS)
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
        for _ in range(SCAN_INTERVAL_SECONDS):
            if killer.kill_now:
                break
            time.sleep(1)

    # Graceful shutdown
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

    # Auto-detect mode: ưu tiên saved_mode từ .bot_state/mode.json
    # (đã được set bởi Telegram /live hoặc /testnet),
    # --live CLI flag ghi đè nếu được truyền.
    testnet = not (args.live or saved_mode == "LIVE")

    if args.test:
        quick_test_run(testnet=testnet)
    else:
        run_bot(testnet=testnet, use_deepseek=args.deepseek)


if __name__ == "__main__":
    main()

"""
Hermes Agent skill integration for Binance Futures trading bot.

This module exposes a `trade_skill()` function that Hermes can call
via function-calling / tool-use. The function supports actions:
  - "scan":    Scan top coins for EMA+RSI signals
  - "open":    Open a new position (requires symbol + side params)
  - "close":   Close an existing position (requires symbol param)
  - "status":  Show current state and active positions

Usage from Hermes config / prompt:
  The agent loads this as a registered tool with JSON schema matching
  the `get_tool_schema()` function below.
"""
from __future__ import annotations

import logging
import os
import sys

# Ensure we can import sibling modules even when called from Hermes
_skill_dir = os.path.dirname(os.path.abspath(__file__))
if _skill_dir not in sys.path:
    sys.path.insert(0, _skill_dir)

from api_client import BinanceFuturesClient
from state_manager import StateManager, BotState
from scanner import SignalScanner
from executor import OrderExecutor

logger = logging.getLogger(__name__)

# ---- Singleton state (lazy-initialized) ----

_client: BinanceFuturesClient | None = None
_state_mgr: StateManager | None = None
_scanner: SignalScanner | None = None
_executor: OrderExecutor | None = None
_initialized = False

# Default parameters
_TESTNET = os.getenv("BINANCE_TESTNET", "true").lower() == "true"
_CAPITAL = float(os.getenv("TRADE_CAPITAL", "100"))
_LEVERAGE = int(os.getenv("TRADE_LEVERAGE", "3"))
_MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "2"))
_TOP_N = int(os.getenv("TOP_N_SYMBOLS", "10"))
_INTERVAL = os.getenv("SCAN_INTERVAL", "15m")


def _ensure_initialized():
    """Lazy-init all components on first call."""
    global _client, _state_mgr, _scanner, _executor, _initialized

    if _initialized:
        return

    logger.info("Initializing trading bot components for Hermes skill...")

    _client = BinanceFuturesClient(testnet=_TESTNET)
    _state_mgr = StateManager(max_positions=_MAX_POSITIONS)
    _executor = OrderExecutor(
        client=_client,
        state_mgr=_state_mgr,
        capital_usdt=_CAPITAL,
        risk_per_trade=0.02,
        sl_distance=0.02,
        tp_distance=0.03,
        leverage=_LEVERAGE,
    )
    _scanner = SignalScanner(
        client=_client,
        top_n=_TOP_N,
        interval=_INTERVAL,
    )

    _initialized = True
    logger.info("Trading bot components initialized")


def get_tool_schema() -> dict:
    """
    Return the JSON schema for registering this skill as a Hermes tool.
    """
    return {
        "name": "trading_bot",
        "description": (
            "Binance Futures trading bot. Scan for EMA+RSI signals, "
            "open/close positions, and check status. "
            "Actions: scan (find signals), open (enter trade), close (exit trade), status (show positions)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["scan", "open", "close", "status"],
                    "description": (
                        "Action to perform: "
                        "'scan' = scan top coins for signals, "
                        "'open' = open a new position (needs symbol+side), "
                        "'close' = close a position (needs symbol), "
                        "'status' = show current bot state and positions"
                    ),
                },
                "params": {
                    "type": "object",
                    "description": "Additional parameters for open/close actions",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "Trading pair e.g. BTCUSDT",
                        },
                        "side": {
                            "type": "string",
                            "enum": ["LONG", "SHORT"],
                            "description": "Trade direction",
                        },
                    },
                },
            },
            "required": ["action"],
        },
    }


def trade_skill(action: str, params: dict = None) -> dict:
    """
    Main skill entry point — called by Hermes agent.

    Args:
        action: One of "scan", "open", "close", "status"
        params: Optional dict with "symbol" and/or "side"

    Returns:
        dict with results, suitable for LLM to format as natural language
    """
    _ensure_initialized()

    params = params or {}

    if action == "scan":
        return _action_scan()
    elif action == "open":
        return _action_open(params)
    elif action == "close":
        return _action_close(params)
    elif action == "status":
        return _action_status()
    else:
        return {"error": f"Unknown action '{action}'. Valid: scan, open, close, status"}


def _action_scan() -> dict:
    """Scan for trading signals."""
    if not _state_mgr.can_open():
        return {
            "status": "info",
            "message": "Cannot scan — max positions reached. Close a position first.",
            "active_positions": len(_state_mgr.get_positions()),
            "max_positions": _state_mgr.max_positions,
        }

    try:
        symbol, signal = _scanner.scan()
        if symbol and signal:
            return {
                "status": "signal_found",
                "symbol": symbol,
                "side": signal["side"],
                "entry_price": signal["entry_price"],
                "rsi": round(signal["rsi"], 1),
                "message": (
                    f"Tín hiệu {signal['side']} trên {symbol} "
                    f"tại giá {signal['price']:.2f} USDT, RSI = {signal['rsi']:.1f}."
                ),
            }
        else:
            return {
                "status": "no_signal",
                "message": "Không có tín hiệu giao dịch nào trong lần quét này.",
            }
    except Exception as e:
        logger.exception("Scan failed")
        return {"error": f"Scan failed: {str(e)}"}


def _action_open(params: dict) -> dict:
    """Open a new position."""
    symbol = params.get("symbol", "").upper()
    side = params.get("side", "").upper()

    if not symbol:
        return {"error": "Missing required param: 'symbol' (e.g. BTCUSDT)"}
    if side not in ("LONG", "SHORT"):
        return {"error": "Missing or invalid param: 'side' — must be LONG or SHORT"}

    result = _executor.open_position(symbol, side)

    if result.get("status") == "success":
        return {
            "status": "position_opened",
            "symbol": symbol,
            "side": side,
            "entry_price": result["entry_price"],
            "quantity": result["quantity"],
            "sl_price": result["sl_price"],
            "tp_price": result["tp_price"],
            "message": (
                f"Đã mở lệnh {side} {symbol}: "
                f"vào tại {result['entry_price']:.2f}, "
                f"khối lượng {result['quantity']:.4f}, "
                f"SL={result['sl_price']:.2f}, TP={result['tp_price']:.2f}"
            ),
        }
    else:
        return {
            "status": "error",
            "message": f"Không thể mở lệnh {side} {symbol}: {result.get('message', 'Lỗi không xác định')}",
        }


def _action_close(params: dict) -> dict:
    """Close an existing position."""
    symbol = params.get("symbol", "").upper()

    if not symbol:
        return {"error": "Missing required param: 'symbol' (e.g. BTCUSDT)"}

    if not _state_mgr.has_position(symbol):
        return {
            "status": "info",
            "message": f"Không có vị thế {symbol} nào đang mở.",
        }

    result = _executor.close_position(symbol)

    if result.get("status") == "success":
        pnl_str = f"{result['pnl']:+.2f} USDT" if result.get("pnl") else "N/A"
        return {
            "status": "position_closed",
            "symbol": symbol,
            "side": result.get("side"),
            "entry_price": result.get("entry_price"),
            "exit_price": result.get("exit_price"),
            "pnl": result.get("pnl"),
            "roi_pct": result.get("roi_pct"),
            "message": (
                f"Đã đóng lệnh {result.get('side', '')} {symbol}: "
                f"vào {result.get('entry_price', 0):.2f}, "
                f"ra {result.get('exit_price', 0):.2f}, "
                f"PnL = {pnl_str}"
            ),
        }
    else:
        return {
            "status": "error",
            "message": f"Không thể đóng lệnh {symbol}: {result.get('message', 'Lỗi không xác định')}",
        }


def _action_status() -> dict:
    """Show current bot state and positions."""
    positions = _executor.get_positions_with_pnl()

    return {
        "status": "ok",
        "bot_state": _state_mgr.state.value,
        "positions_count": len(positions),
        "max_positions": _state_mgr.max_positions,
        "can_open": _state_mgr.can_open(),
        "positions": positions,
        "balance_usdt": _client.get_balance("USDT") if _client else 0.0,
        "message": (
            f"Bot đang ở trạng thái '{_state_mgr.state.value}'. "
            f"Vị thế: {len(positions)}/{_state_mgr.max_positions}. "
            + ("Có thể mở thêm lệnh." if _state_mgr.can_open() else "Đã đạt giới hạn, đang tạm dừng quét.")
            if positions
            else "Chưa có vị thế nào."
        ),
    }


# ---- Convenience for standalone testing ----

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    
    # Quick test
    result = trade_skill("status")
    print(f"Status: {result}")
    
    result = trade_skill("scan")
    print(f"Scan: {result}")

"""Read-only Hermes integration for the Binance Futures bot.

This module deliberately exposes scan/status only. Trading requests must pass
through the bot's authenticated, audited control plane and local operator flow.
"""
from __future__ import annotations

import logging
import os
import sys

_skill_dir = os.path.dirname(os.path.abspath(__file__))
if _skill_dir not in sys.path:
    sys.path.insert(0, _skill_dir)

from api_client import BinanceFuturesClient
from scanner import SmartScanner
from settings import BotSettings
from state_manager import StateManager

logger = logging.getLogger(__name__)
_client = None
_scanner = None
_state_mgr = None


def _ensure_initialized() -> None:
    global _client, _scanner, _state_mgr
    if _client is not None:
        return
    config = BotSettings.from_env()
    _client = BinanceFuturesClient(testnet=config.testnet)
    _state_mgr = StateManager(config.max_positions)
    _scanner = SmartScanner(_client, top_n=int(os.getenv("TOP_N_SYMBOLS", "10")))


def get_tool_schema() -> dict:
    return {
        "name": "trading_bot",
        "description": "Read-only Binance Futures bot monitoring: scan closed-candle signals or display status. It cannot place or close orders.",
        "parameters": {"type": "object", "properties": {"action": {"type": "string", "enum": ["scan", "status"]}}, "required": ["action"]},
    }


def trade_skill(action: str, params: dict | None = None) -> dict:
    if action not in {"scan", "status"}:
        return {"error": "Trading actions are disabled in Hermes integration. Valid actions: scan, status."}
    _ensure_initialized()
    if action == "status":
        return {"status": "ok", "bot_state": _state_mgr.state.value, "positions_count": len(_state_mgr.get_positions()), "can_open": _state_mgr.can_open(), "balance_usdt": _client.get_balance("USDT"), "trading_actions": "disabled"}
    symbol, signal = _scanner.scan()
    if not symbol:
        return {"status": "no_signal", "message": "No closed-candle signal found."}
    return {"status": "signal_found", "symbol": symbol, "side": signal["side"], "entry_price": signal["entry_price"], "sl_price": signal["sl_price"], "tp1_price": signal["tp1_price"], "rsi": signal["rsi"], "confidence": signal["confidence"], "message": f"Closed-candle {signal['side']} signal for {symbol} at {signal['entry_price']:.6g}; no order was placed."}

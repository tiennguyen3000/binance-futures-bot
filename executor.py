"""Fail-safe, serialized Binance Futures order execution."""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from api_client import BinanceFuturesClient
from state_manager import Position, StateManager
from trade_journal import TradeJournal

logger = logging.getLogger(__name__)


class OrderExecutor:
    def __init__(self, client: BinanceFuturesClient, state_mgr: StateManager, capital_usdt: float = 100.0, risk_per_trade: float = 0.015, leverage: int = 10, max_position_notional_pct: float = 0.10, journal: TradeJournal | None = None):
        self.client = client
        self.state_mgr = state_mgr
        self.capital_usdt = capital_usdt
        self.risk_per_trade = risk_per_trade
        self.leverage = leverage
        self.max_position_notional_pct = max_position_notional_pct
        self.journal = journal or TradeJournal()
        self._lock = threading.RLock()

    @staticmethod
    def _exchange_id(result: dict[str, Any]) -> str | None:
        return result.get("orderId") or result.get("clientAlgoId") or result.get("algoId")

    def calculate_position_size(self, symbol: str, entry_price: float, sl_price: float, balance: float | None = None) -> float:
        if entry_price <= 0 or sl_price <= 0 or entry_price == sl_price:
            return 0.0
        equity = balance if balance is not None else self.client.get_balance("USDT")
        risk_budget = equity * self.risk_per_trade
        risk_per_unit = abs(entry_price - sl_price)
        risk_qty = risk_budget / risk_per_unit
        notional_cap = equity * self.leverage * self.max_position_notional_pct
        capped_qty = min(risk_qty, notional_cap / entry_price)
        return max(self.client._normalize_qty(symbol, capped_qty), 0.0)

    def _emergency_close(self, symbol: str, side: str, quantity: float) -> bool:
        close_side = "SELL" if side == "LONG" else "BUY"
        result = self.client.place_market_order(symbol, close_side, quantity, position_side=None, reduce_only=True)
        if not self._exchange_id(result):
            return False
        try:
            return abs(self.client.get_position_amt(symbol)) < 1e-8
        except (RuntimeError, TypeError):
            return False

    def _halt_unknown_exposure(self, position: Position, reason: str) -> None:
        self.state_mgr.record_unknown_exposure(position, reason)
        logger.critical("SAFE_HALT: %s", reason)

    def open_position(self, symbol: str, side: str, sl_price: float | None = None, tp1_price: float | None = None, tp2_price: float | None = None) -> dict:
        with self._lock:
            if not self.state_mgr.can_open() or self.state_mgr.has_position(symbol):
                return {"status": "error", "message": "Position capacity unavailable"}
            if not self.client.is_tradable(symbol):
                return {"status": "error", "message": f"{symbol} is not a tradable USDT perpetual"}
            if sl_price is None or tp1_price is None:
                return {"status": "error", "message": "SL and TP1 are required"}
            leverage_result = self.client.set_leverage(symbol, self.leverage)
            if leverage_result.get("_error"):
                return {"status": "error", "message": f"Leverage configuration failed: {leverage_result}"}
            margin_result = self.client.set_margin_type(symbol, "ISOLATED")
            # Binance returns -4046 when the requested mode is already set.
            if margin_result.get("_error") or (margin_result.get("code") and margin_result.get("code") != -4046):
                return {"status": "error", "message": f"Margin configuration failed: {margin_result}"}
            requested_price = self.client.get_symbol_price(symbol)
            quantity = self.calculate_position_size(symbol, requested_price, sl_price)
            if quantity <= 0:
                return {"status": "error", "message": "Quantity is below exchange minimum"}
            order_side, close_side = ("BUY", "SELL") if side == "LONG" else ("SELL", "BUY")
            # This bot intentionally supports One-way Mode only. Position mode is
            # asserted at startup, so omission of positionSide is deliberate.
            position_side = None
            entry_client_id = f"entry-{symbol}-{int(time.time() * 1000)}"
            self.journal.record_intent(entry_client_id, symbol, "entry", {"side": side, "quantity": quantity})
            entry = self.client.place_market_order(symbol, order_side, quantity, position_side=position_side, client_order_id=entry_client_id)
            entry_id = self._exchange_id(entry)
            if not entry_id and entry.get("_retryable"):
                entry = self.client.get_order_by_client_id(symbol, entry_client_id)
                entry_id = self._exchange_id(entry)
            entry_position = Position(symbol, side, requested_price, quantity, sl_price, tp1_price, str(entry_id) if entry_id else None)
            if not entry_id:
                self._halt_unknown_exposure(entry_position, f"entry outcome unknown: {entry}")
                return {"status": "error", "message": f"Entry order outcome unknown; SAFE_HALT: {entry}"}
            filled_price = float(entry.get("avgPrice") or requested_price)
            filled_qty = float(entry.get("executedQty") or quantity)
            valid = sl_price < filled_price < tp1_price if side == "LONG" else tp1_price < filled_price < sl_price
            if not valid:
                closed = self._emergency_close(symbol, side, filled_qty)
                if not closed:
                    self._halt_unknown_exposure(Position(symbol, side, filled_price, filled_qty, sl_price, tp1_price, str(entry_id)), "invalid SL/TP after fill and emergency close unconfirmed")
                    return {"status": "error", "message": "Filled price invalidates SL/TP; SAFE_HALT"}
                return {"status": "error", "message": "Filled price invalidates SL/TP; emergency close confirmed"}
            stop = self.client.place_stop_loss(symbol, close_side, filled_qty, sl_price, position_side=position_side)
            stop_id = self._exchange_id(stop)
            if not stop_id:
                closed = self._emergency_close(symbol, side, filled_qty)
                if not closed:
                    self._halt_unknown_exposure(Position(symbol, side, filled_price, filled_qty, sl_price, tp1_price, str(entry_id)), "stop rejected and emergency close unconfirmed")
                    return {"status": "error", "message": "Stop-loss rejected; emergency close unconfirmed; SAFE_HALT"}
                return {"status": "error", "message": "Stop-loss rejected; emergency close confirmed"}
            take_profit = self.client.place_take_profit(symbol, close_side, filled_qty, tp1_price, position_side=position_side)
            tp_id = self._exchange_id(take_profit)
            position = Position(symbol, side, filled_price, filled_qty, sl_price, tp1_price, str(entry_id), str(stop_id), str(tp_id) if tp_id else None)
            if not self.state_mgr.add_position(position):
                closed = self._emergency_close(symbol, side, filled_qty)
                if not closed:
                    self._halt_unknown_exposure(position, "local state reservation failed and emergency close unconfirmed")
                    return {"status": "error", "message": "Local state reservation failed; SAFE_HALT"}
                return {"status": "error", "message": "Could not reserve local state; emergency close confirmed"}
            return {"status": "success", "symbol": symbol, "side": side, "entry_price": filled_price, "quantity": filled_qty, "sl_price": sl_price, "tp1_price": tp1_price, "tp2_price": tp2_price, "order_id_entry": str(entry_id), "order_id_sl": str(stop_id), "order_id_tp": str(tp_id) if tp_id else None}

    def close_position(self, symbol: str) -> dict:
        with self._lock:
            position = self.state_mgr.begin_close(symbol)
            if position is None:
                return {"status": "error", "message": "Position unavailable or already closing"}
            close_side = "SELL" if position.side == "LONG" else "BUY"
            result = self.client.place_market_order(symbol, close_side, position.quantity, position_side=None, reduce_only=True)
            try:
                closed = bool(self._exchange_id(result)) and abs(self.client.get_position_amt(symbol)) < 1e-8
            except (RuntimeError, TypeError) as exc:
                self.state_mgr.cancel_close(symbol)
                self.state_mgr.safe_halt(f"Cannot confirm close for {symbol}: {exc}")
                return {"status": "error", "message": "Close outcome unknown; SAFE_HALT; protective orders retained"}
            if not closed:
                self.state_mgr.cancel_close(symbol)
                return {"status": "error", "message": "Close not confirmed; protective orders retained"}
            for order_id in (position.order_id_sl, position.order_id_tp):
                if order_id:
                    self.client.cancel_order(symbol, order_id)
            exit_price = float(result.get("avgPrice", position.entry_price))
            pnl = (exit_price - position.entry_price) * position.quantity * (1 if position.side == "LONG" else -1)
            self.state_mgr.remove_position(symbol)
            return {"status": "success", "symbol": symbol, "side": position.side, "entry_price": position.entry_price, "exit_price": exit_price, "pnl": round(pnl, 2)}

    def get_positions_with_pnl(self) -> list[dict]:
        positions = []
        for position in self.state_mgr.get_positions():
            risk = next((item for item in self.client.get_position_risk(position.symbol) if item.get("symbol") == position.symbol), {})
            pnl = float(risk.get("unRealizedProfit", 0))
            positions.append({"symbol": position.symbol, "side": position.side, "entry_price": position.entry_price, "quantity": position.quantity, "sl_price": position.sl_price, "tp_price": position.tp_price, "unrealized_pnl": pnl})
        return positions

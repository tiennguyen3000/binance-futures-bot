"""
Order executor — calculates position size, places market entry + SL/TP orders.
"""
import logging
import math
from api_client import BinanceFuturesClient
from state_manager import StateManager, Position

try:
    from telegram_notifier import send_message, exit_msg
    _HAS_TELEGRAM = True
except ImportError:
    _HAS_TELEGRAM = False

logger = logging.getLogger(__name__)

# Default risk parameters
CAPITAL_USDT = 100.0          # Total capital
RISK_PER_TRADE_PCT = 0.015    # 1.5% risk per trade
DEFAULT_LEVERAGE = 10          # Đòn bẩy 10x


class OrderExecutor:
    """
    Handles order placement: position sizing, market entry, SL/TP.
    """

    def __init__(
        self,
        client: BinanceFuturesClient,
        state_mgr: StateManager,
        capital_usdt: float = CAPITAL_USDT,
        risk_per_trade: float = RISK_PER_TRADE_PCT,
        leverage: int = DEFAULT_LEVERAGE,
    ):
        self.client = client
        self.state_mgr = state_mgr
        self.capital_usdt = capital_usdt
        self.risk_per_trade = risk_per_trade
        self.leverage = leverage

        logger.info(f"OrderExecutor initialized: capital={capital_usdt} USDT, "
                     f"risk={risk_per_trade*100:.1f}%, lev={leverage}x")

    POSITION_SIZE_PCT: float = 0.10  # 10% tổng vốn mỗi lệnh (fixed, ko theo risk)

    def calculate_position_size(self, symbol: str, entry_price: float, sl_price: float = None) -> float:
        """
        Tính khối lượng dựa trên % cố định của tổng vốn (mặc định 10%).
        
        position_value = capital * POSITION_SIZE_PCT * leverage
        qty = position_value / entry_price
        
        Với capital=100, pct=10%, lev=10x:
        position_value = 100 * 0.10 * 10 = 100 USDT
        qty = 100 / entry_price
        """
        position_value = self.capital_usdt * self.POSITION_SIZE_PCT * self.leverage
        logger.debug(
            f"Position size for {symbol}: "
            f"capital={self.capital_usdt} * {self.POSITION_SIZE_PCT*100:.0f}% * {self.leverage}x "
            f"= {position_value:.2f} USDT"
        )
        raw_qty = position_value / entry_price

        # Normalize to exchange lot size
        qty = self.client._normalize_qty(symbol, raw_qty)
        
        logger.debug(
            f"Position size for {symbol}: "
            f"notional={position_value:.2f} USDT, qty={qty:.6f} @ {entry_price}"
        )
        
        return max(qty, 0.0)

    def open_position(self, symbol: str, side: str, 
                      sl_price: float = None, tp1_price: float = None, tp2_price: float = None) -> dict:
        """
        Open a new position with ATR-based SL/TP from scanner.
        
        Args:
            symbol: e.g. 'BTCUSDT'
            side: 'LONG' or 'SHORT'
            sl_price: Stop-loss price from scanner (ATR-based)
            tp1_price: First take-profit price
            tp2_price: Second take-profit price
        
        Returns:
            dict with status, details
        """
        if not self.state_mgr.can_open():
            return {"status": "error", "message": "Max positions already open"}

        if self.state_mgr.has_position(symbol):
            return {"status": "error", "message": f"Already have a position in {symbol}"}

        try:
            # 1. Set leverage
            logger.info(f"Setting leverage to {self.leverage}x for {symbol}")
            self.client.set_leverage(symbol, self.leverage)

            # 2. Set margin type to ISOLATED
            try:
                self.client.set_margin_type(symbol, "ISOLATED")
            except Exception:
                logger.debug(f"Margin type already set for {symbol}")

            # 3. Get current price
            price = self.client.get_symbol_price(symbol)
            if price <= 0:
                return {"status": "error", "message": f"Invalid price for {symbol}: {price}"}

            # 4. Calculate with SL from scanner (or fallback)
            if sl_price is None:
                # Fallback: SL cố định 4% nếu scanner không cung cấp
                sl_price = price * (0.96 if side == "LONG" else 1.04)
                logger.warning(f"No SL from scanner, using 4% fallback for {symbol}")

            quantity = self.calculate_position_size(symbol, price, sl_price)
            if quantity <= 0:
                return {"status": "error", "message": f"Calculated quantity too small for {symbol}"}

            if side == "LONG":
                order_side = "BUY"
                close_side = "SELL"
                # TP: nếu scanner cung cấp thì dùng, nếu không thì 2x SL distance
                if tp1_price is None or tp1_price <= price:
                    tp1_price = price + (price - sl_price) * 1.5
                if tp2_price is None or tp2_price <= tp1_price:
                    tp2_price = price + (price - sl_price) * 2.5
            else:  # SHORT
                order_side = "SELL"
                close_side = "BUY"
                if tp1_price is None or tp1_price >= price:
                    tp1_price = price - (sl_price - price) * 1.5
                if tp2_price is None or tp2_price >= tp1_price:
                    tp2_price = price - (sl_price - price) * 2.5

            # 5. Place market entry order
            logger.info(f"Opening {side} {symbol}: qty={quantity:.4f} @ ~{price:.2f}")
            entry_result = self.client.place_market_order(symbol, order_side, quantity, position_side=side)
            
            if not entry_result or "orderId" not in entry_result:
                logger.error(f"Entry order failed for {symbol}: {entry_result}")
                return {"status": "error", "message": f"Entry order failed: {entry_result}"}

            filled_price = float(entry_result.get("avgPrice", price))

            # 6. Place SL and TP (dùng tp1 làm mục tiêu chính)
            logger.info(f"Placing SL@{sl_price:.2f} and TP@{tp1_price:.2f} for {symbol}")
            logger.info(f"  TP2 target: {tp2_price:.2f} (trailing/adjust later)")
            
            sl_result = self.client.place_stop_loss(symbol, close_side, quantity, sl_price, position_side=side)
            if not sl_result or "orderId" not in sl_result:
                logger.warning(f"SL order not placed for {symbol} — position still open, will monitor via sync")
            
            tp_result = self.client.place_take_profit(symbol, close_side, quantity, tp1_price, position_side=side)
            if not tp_result or "orderId" not in tp_result:
                logger.warning(f"TP order not placed for {symbol} — position still open, will monitor via sync")

            # 7. Create position record
            position = Position(
                symbol=symbol,
                side=side,
                entry_price=filled_price,
                quantity=quantity,
                sl_price=sl_price,
                tp_price=tp1_price,
                order_id_entry=str(entry_result.get("orderId", "")),
                order_id_sl=str(sl_result.get("orderId", "")) if sl_result else None,
                order_id_tp=str(tp_result.get("orderId", "")) if tp_result else None,
            )

            self.state_mgr.add_position(position)

            result = {
                "status": "success",
                "symbol": symbol,
                "side": side,
                "entry_price": filled_price,
                "quantity": quantity,
                "sl_price": sl_price,
                "tp1_price": tp1_price,
                "tp2_price": tp2_price,
            }
            logger.info(f"Position opened: {result}")
            return result

        except Exception as e:
            logger.exception(f"Failed to open {side} position for {symbol}")
            return {"status": "error", "message": str(e)}

    def close_position(self, symbol: str) -> dict:
        """
        Close an open position:
        1. Cancel all open orders for symbol
        2. Place market opposite order
        3. Remove from state manager
        
        Args:
            symbol: e.g. 'BTCUSDT'
        
        Returns:
            dict with status, details
        """
        if not self.state_mgr.has_position(symbol):
            return {"status": "error", "message": f"No position tracked for {symbol}"}

        position = self.state_mgr.get_position(symbol)
        if not position:
            return {"status": "error", "message": f"No position found for {symbol}"}

        try:
            # 1. Cancel all open orders
            self.client.cancel_all_orders(symbol)

            # 2. Place opposing market order
            close_side = "SELL" if position.side == "LONG" else "BUY"
            logger.info(f"Closing {position.side} {symbol} (market {close_side})")

            close_result = self.client.place_market_order(symbol, close_side, position.quantity, position_side=position.side)
            
            if not close_result or "orderId" not in close_result:
                logger.error(f"Close order failed for {symbol}: {close_result}")
                return {"status": "error", "message": f"Close order failed: {close_result}"}

            filled_price = float(close_result.get("avgPrice", 0))
            
            # Calculate PnL
            if position.side == "LONG":
                pnl = (filled_price - position.entry_price) * position.quantity
            else:
                pnl = (position.entry_price - filled_price) * position.quantity
            
            roi_pct = (pnl / (position.entry_price * position.quantity / self.leverage)) * 100

            # Update PnL before removing
            self.state_mgr.update_position_pnl(symbol, pnl, roi_pct)
            self.state_mgr.remove_position(symbol)

            # Telegram: manual close notification
            if _HAS_TELEGRAM:
                send_message(exit_msg(symbol, position.side, position.entry_price,
                                      filled_price, pnl, roi_pct, "manual"))

            result = {
                "status": "success",
                "symbol": symbol,
                "side": position.side,
                "entry_price": position.entry_price,
                "exit_price": filled_price,
                "pnl": round(pnl, 2),
                "roi_pct": round(roi_pct, 2),
            }
            logger.info(f"Position closed: {result}")
            return result

        except Exception as e:
            logger.exception(f"Failed to close {symbol}")
            return {"status": "error", "message": str(e)}

    def get_positions_with_pnl(self) -> list[dict]:
        """
        Get all tracked positions with current PnL from exchange.
        """
        results = []
        for pos in self.state_mgr.get_positions():
            try:
                # Fetch position risk from exchange for real-time PnL
                risk_data = self.client.get_position_risk(pos.symbol)
                unrealized_pnl = 0.0
                for r in risk_data:
                    if r.get("symbol") == pos.symbol:
                        unrealized_pnl = float(r.get("unRealizedProfit", 0))
                        break
                
                entry_value = pos.entry_price * pos.quantity / self.leverage
                roi = (unrealized_pnl / entry_value * 100) if entry_value > 0 else 0.0

                results.append({
                    "symbol": pos.symbol,
                    "side": pos.side,
                    "entry_price": pos.entry_price,
                    "quantity": pos.quantity,
                    "sl_price": pos.sl_price,
                    "tp_price": pos.tp_price,
                    "unrealized_pnl": round(unrealized_pnl, 2),
                    "roi_pct": round(roi, 2),
                })
            except Exception as e:
                logger.warning(f"Failed to fetch PnL for {pos.symbol}: {e}")
                results.append({
                    "symbol": pos.symbol,
                    "side": pos.side,
                    "entry_price": pos.entry_price,
                    "quantity": pos.quantity,
                    "sl_price": pos.sl_price,
                    "tp_price": pos.tp_price,
                    "unrealized_pnl": 0.0,
                    "roi_pct": 0.0,
                })
        return results

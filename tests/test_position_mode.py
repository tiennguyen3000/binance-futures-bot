import unittest

from executor import OrderExecutor
from state_manager import StateManager


class OneWayClient:
    def __init__(self):
        self.position_sides = []

    def is_tradable(self, symbol): return True
    def set_leverage(self, *args): return {"leverage": 10}
    def set_margin_type(self, *args): return {"code": -4046}
    def get_symbol_price(self, symbol): return 100.0
    def get_balance(self, asset): return 100.0
    def _normalize_qty(self, symbol, quantity): return quantity
    def validate_order_notional(self, symbol, quantity, price): return None
    def normalize_protection_prices(self, symbol, side, sl, tp): return str(sl), str(tp)
    def place_market_order(self, *args, **kwargs):
        self.position_sides.append(kwargs.get("position_side"))
        return {"orderId": "entry-1", "avgPrice": "100", "executedQty": "1"}
    def place_stop_loss(self, *args, **kwargs):
        self.position_sides.append(kwargs.get("position_side"))
        return {"orderId": "sl-1"}
    def place_take_profit(self, *args, **kwargs):
        self.position_sides.append(kwargs.get("position_side"))
        return {"orderId": "tp-1"}


class PositionModeTests(unittest.TestCase):
    def test_one_way_execution_never_sends_hedge_position_side(self):
        client = OneWayClient()
        result = OrderExecutor(client, StateManager()).open_position("BTCUSDT", "LONG", 95, 110)
        self.assertEqual("success", result["status"])
        self.assertEqual([None, None, None], client.position_sides)


if __name__ == "__main__":
    unittest.main()

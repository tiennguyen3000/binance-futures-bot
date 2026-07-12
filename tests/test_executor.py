import unittest

from executor import OrderExecutor
from state_manager import StateManager


class FakeClient:
    def __init__(self, sl_response=None):
        self.sl_response = sl_response or {"clientAlgoId": "sl-1"}
        self.closed = 0

    def is_tradable(self, symbol): return True
    def set_leverage(self, *args): return {"leverage": 10}
    def set_margin_type(self, *args): return {}
    def get_symbol_price(self, symbol): return 100.0
    def get_balance(self, asset): return 100.0
    def _normalize_qty(self, symbol, quantity): return quantity
    def validate_order_notional(self, symbol, quantity, price): return None
    def normalize_protection_prices(self, symbol, side, sl, tp): return str(sl), str(tp)
    def place_market_order(self, *args, **kwargs):
        if kwargs.get("reduce_only"):
            self.closed += 1
        return {"orderId": "entry-1", "avgPrice": "100", "executedQty": "1"}
    def place_stop_loss(self, *args, **kwargs): return self.sl_response
    def place_take_profit(self, *args, **kwargs): return {"clientAlgoId": "tp-1"}
    def get_position_amt(self, symbol, position_side=None): return 0.0 if self.closed else 1.0
    def cancel_order(self, *args, **kwargs): raise AssertionError("must not cancel protection before close is confirmed")


class ExecutorTests(unittest.TestCase):
    def test_algo_identifiers_are_accepted_as_protection(self):
        client = FakeClient()
        result = OrderExecutor(client, StateManager()).open_position("BTCUSDT", "LONG", 95, 110)
        self.assertEqual("success", result["status"])
        self.assertEqual("sl-1", result["order_id_sl"])

    def test_stop_loss_failure_keeps_position_tracked(self):
        client = FakeClient(sl_response={"_error": "rejected"})
        state = StateManager()
        result = OrderExecutor(client, state).open_position("BTCUSDT", "LONG", 95, 110)
        self.assertEqual("success", result["status"])
        self.assertEqual(0, client.closed)
        self.assertIsNotNone(state.get_position("BTCUSDT"))


if __name__ == "__main__":
    unittest.main()

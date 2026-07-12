import unittest

from main import reconcile_exchange_positions
from state_manager import BotState, StateManager


class FakeClient:
    def __init__(self, orders): self.orders = orders
    def get_account_info(self):
        return {"positions": [{"symbol": "BTCUSDT", "positionAmt": "1", "entryPrice": "100"}]}
    def get_open_orders(self, symbol): return self.orders
    def get_open_algo_orders(self, symbol): return []


class ReconciliationTests(unittest.TestCase):
    def test_unprotected_exchange_position_enters_safe_halt(self):
        state = StateManager()
        report = reconcile_exchange_positions(FakeClient([]), state)
        self.assertFalse(report["ready"])
        self.assertEqual(BotState.SAFE_HALT, state.state)

    def test_protected_exchange_position_is_imported(self):
        orders = [{"type": "STOP_MARKET", "side": "SELL", "stopPrice": "95"}, {"type": "TAKE_PROFIT_MARKET", "side": "SELL", "stopPrice": "110"}]
        state = StateManager()
        report = reconcile_exchange_positions(FakeClient(orders), state)
        self.assertTrue(report["ready"])
        position = state.get_position("BTCUSDT")
        self.assertEqual(95.0, position.sl_price)
        self.assertEqual(110.0, position.tp_price)


if __name__ == "__main__":
    unittest.main()

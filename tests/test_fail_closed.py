import unittest

from executor import OrderExecutor
from main import reconcile_exchange_positions
from state_manager import BotState, StateManager


class UnavailableReadClient:
    def get_account_info(self):
        return {"_error": "network unavailable"}


class EmergencyFailureClient:
    def is_tradable(self, symbol): return True
    def set_leverage(self, *args): return {"leverage": 10}
    def set_margin_type(self, *args): return {"code": -4046, "msg": "No need to change margin type."}
    def get_symbol_price(self, symbol): return 100.0
    def get_balance(self, asset): return 100.0
    def _normalize_qty(self, symbol, quantity): return quantity
    def validate_order_notional(self, symbol, quantity, price): return None
    def normalize_protection_prices(self, symbol, side, sl, tp): return str(sl), str(tp)
    def place_market_order(self, *args, **kwargs):
        if kwargs.get("reduce_only"):
            return {"_error": "close timeout", "_retryable": True}
        return {"orderId": "entry-1", "avgPrice": "100", "executedQty": "1"}
    def place_stop_loss(self, *args, **kwargs): return {"_error": "stop rejected"}
    def place_take_profit(self, *args, **kwargs): return {"_error": "tp rejected"}
    def get_position_amt(self, symbol): raise RuntimeError("position endpoint unavailable")


class FailClosedTests(unittest.TestCase):
    def test_reconciliation_halts_when_account_read_fails(self):
        state = StateManager()
        report = reconcile_exchange_positions(UnavailableReadClient(), state)
        self.assertFalse(report["ready"])
        self.assertEqual(BotState.SAFE_HALT, state.state)

    def test_unconfirmed_emergency_close_halts_and_tracks_unknown_exposure(self):
        state = StateManager()
        result = OrderExecutor(EmergencyFailureClient(), state).open_position("BTCUSDT", "LONG", 95, 110)
        self.assertEqual("success", result["status"])
        self.assertEqual(BotState.HOLDING, state.state)
        position = state.get_position("BTCUSDT")
        self.assertIsNotNone(position)
        self.assertEqual("OPEN", position.status.name)


if __name__ == "__main__":
    unittest.main()

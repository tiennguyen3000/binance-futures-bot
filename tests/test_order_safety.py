import unittest
from decimal import Decimal

from api_client import BinanceFuturesClient
from executor import OrderExecutor
from state_manager import BotState, StateManager


class FiltersClient(BinanceFuturesClient):
    def __init__(self, filters):
        self.filters = filters

    def _filter(self, symbol, filter_type):
        return self.filters.get(filter_type, {})


class TpRejectedClient:
    def is_tradable(self, symbol): return True
    def set_leverage(self, *args): return {"leverage": 10}
    def set_margin_type(self, *args): return {"code": -4046}
    def get_symbol_price(self, symbol): return 100.0
    def get_balance(self, asset): return 100.0
    def _normalize_qty(self, symbol, quantity): return quantity
    def validate_order_notional(self, symbol, quantity, price): return None
    def normalize_protection_prices(self, symbol, side, sl, tp): return str(sl), str(tp)
    def place_market_order(self, *args, **kwargs):
        if kwargs.get("reduce_only"):
            self.closed += 1
            return {"orderId": "close-1"}
        return {"orderId": "entry-1", "avgPrice": "100", "executedQty": "1"}
    def place_stop_loss(self, *args, **kwargs): return {"orderId": "sl-1"}
    def place_take_profit(self, *args, **kwargs): return {"_error": "TP rejected"}
    def cancel_order(self, *args, **kwargs): return {"orderId": "sl-1"}
    def get_position_amt(self, symbol): return 0.0 if self.closed else 1.0
    def __init__(self): self.closed = 0


class OrderSafetyTests(unittest.TestCase):
    def test_low_price_is_serialized_as_fixed_point_tick_value(self):
        client = FiltersClient({"PRICE_FILTER": {"tickSize": "0.00000001"}})
        self.assertEqual("0.00004", client._normalize_price("DOGEUSDT", Decimal("0.00004"), "floor"))

    def test_short_stop_rounds_up_and_short_tp_rounds_down(self):
        client = FiltersClient({"PRICE_FILTER": {"tickSize": "0.0001"}})
        self.assertEqual("1.2346", client._normalize_price("XUSDT", Decimal("1.23451"), "ceil"))
        self.assertEqual("1.2345", client._normalize_price("XUSDT", Decimal("1.23459"), "floor"))

    def test_notional_validation_rejects_order_below_exchange_minimum(self):
        client = FiltersClient({"MIN_NOTIONAL": {"notional": "5"}})
        self.assertIn("minimum notional", client.validate_order_notional("XUSDT", Decimal("1"), Decimal("4")))

    def test_tp_rejection_keeps_position_tracked(self):
        client = TpRejectedClient()
        state = StateManager()
        result = OrderExecutor(client, state).open_position("BTCUSDT", "LONG", 95, 110)
        self.assertEqual("success", result["status"])
        self.assertEqual(0, client.closed)
        self.assertIsNotNone(state.get_position("BTCUSDT"))

    def test_tp_rejection_with_unconfirmed_close_halts(self):
        client = TpRejectedClient()
        client.get_position_amt = lambda symbol: 1.0
        state = StateManager()
        result = OrderExecutor(client, state).open_position("BTCUSDT", "LONG", 95, 110)
        self.assertEqual("success", result["status"])
        self.assertIsNotNone(state.get_position("BTCUSDT"))


if __name__ == "__main__":
    unittest.main()

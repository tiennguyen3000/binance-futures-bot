import unittest

from executor import OrderExecutor
from state_manager import StateManager


class BalanceClient:
    def get_balance(self, asset): return 1000.0
    def _normalize_qty(self, symbol, quantity): return str(quantity)


class CapitalCapTests(unittest.TestCase):
    def test_configured_capital_limits_sizing_equity(self):
        executor = OrderExecutor(
            BalanceClient(), StateManager(), capital_usdt=100.0,
            risk_per_trade=0.01, leverage=10, max_position_notional_pct=0.10,
        )
        # Risk sizing allows 1 unit; fixed capital cap limits notional to 100 USDT,
        # therefore 0.1 unit at an entry price of 1,000 USDT.
        self.assertEqual(0.1, executor.calculate_position_size("BTCUSDT", 1000.0, 990.0))


if __name__ == "__main__":
    unittest.main()

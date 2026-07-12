import unittest

import pandas as pd

from scanner import SmartScanner


class FundingFailureClient:
    def get_klines(self, *args): return []
    def get_funding_rate(self, symbol): return None


class ScannerSafetyTests(unittest.TestCase):
    def test_funding_read_failure_rejects_signal(self):
        scanner = SmartScanner(FundingFailureClient())
        self.assertFalse(scanner._funding_is_acceptable("BTCUSDT"))

    def test_macro_breakout_uses_prior_52_candles(self):
        scanner = SmartScanner(FundingFailureClient())
        macro = pd.DataFrame({"high": [100.0] * 52 + [110.0], "low": [90.0] * 52 + [95.0]})
        high, low = scanner._prior_range(macro, 52)
        self.assertEqual(100.0, high)
        self.assertEqual(90.0, low)


if __name__ == "__main__":
    unittest.main()

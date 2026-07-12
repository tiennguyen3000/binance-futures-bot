import unittest

from scanner import SmartScanner


class NoopClient:
    def get_top_volume_symbols(self, limit): return []


class SignalEligibilityTests(unittest.TestCase):
    def test_scan_skips_higher_score_signal_that_cannot_be_executed(self):
        scanner = SmartScanner(NoopClient())
        scanner._get_top_symbols = lambda: ["EXPENSIVE", "EXECUTABLE"]
        scanner._evaluate = lambda symbol: {
            "confidence": 90 if symbol == "EXPENSIVE" else 80,
            "entry_price": 100.0,
            "sl_price": 95.0,
            "tp1_price": 110.0,
        }
        symbol, _ = scanner.scan(eligible=lambda symbol, signal: symbol == "EXECUTABLE")
        self.assertEqual("EXECUTABLE", symbol)


if __name__ == "__main__":
    unittest.main()

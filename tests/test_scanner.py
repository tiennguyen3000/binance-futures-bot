import unittest

import pandas as pd

from scanner import SmartScanner


class FakeClient:
    def get_klines(self, symbol, interval, limit): return []


class ScannerIntegrityTests(unittest.TestCase):
    def test_closed_candle_data_excludes_in_progress_last_row(self):
        scanner = SmartScanner(FakeClient())
        frame = pd.DataFrame({"close": [1.0, 2.0, 999.0], "high": [1.1, 2.1, 1000.0], "low": [0.9, 1.9, 998.0], "volume": [10.0, 11.0, 9999.0]})
        closed = scanner._closed(frame)
        self.assertEqual(2, len(closed))
        self.assertEqual(2.0, closed["close"].iloc[-1])

    def test_breakout_compares_signal_close_to_prior_swing(self):
        scanner = SmartScanner(FakeClient())
        frame = pd.DataFrame({"close": [90, 95, 101], "high": [100, 100, 102], "low": [80, 85, 90], "volume": [1, 1, 1]})
        self.assertTrue(scanner._breakout(frame, "LONG", lookback=2))
        self.assertFalse(scanner._breakout(frame, "SHORT", lookback=2))

    def test_volume_baseline_excludes_signal_candle(self):
        scanner = SmartScanner(FakeClient())
        volumes = pd.Series([10.0] * 20 + [30.0])
        ratio = scanner._volume_ratio(volumes)
        self.assertEqual(3.0, ratio)


if __name__ == "__main__":
    unittest.main()

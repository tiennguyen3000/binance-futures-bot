import os
import unittest
from unittest.mock import patch

from settings import BotSettings


class RiskBoundTests(unittest.TestCase):
    def test_rejects_risk_fraction_above_one(self):
        with patch.dict(os.environ, {"RISK_PER_TRADE": "1.1"}, clear=True):
            with self.assertRaises(ValueError):
                BotSettings.from_env()

    def test_rejects_notional_fraction_above_one(self):
        with patch.dict(os.environ, {"MAX_POSITION_NOTIONAL_PCT": "1.1"}, clear=True):
            with self.assertRaises(ValueError):
                BotSettings.from_env()


if __name__ == "__main__":
    unittest.main()

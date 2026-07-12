import os
import unittest
from unittest.mock import patch


class BotSettingsTests(unittest.TestCase):
    def test_defaults_are_fail_safe(self):
        from settings import BotSettings

        with patch.dict(os.environ, {}, clear=True):
            settings = BotSettings.from_env()

        self.assertFalse(settings.trading_enabled)
        self.assertEqual("127.0.0.1", settings.api_host)
        self.assertEqual("", settings.api_control_token)
        self.assertFalse(settings.api_mutations_enabled)

    def test_control_token_enables_mutations_only_on_explicit_opt_in(self):
        from settings import BotSettings

        with patch.dict(
            os.environ,
            {
                "API_CONTROL_TOKEN": "unit-test-token",
                "ENABLE_API_MUTATIONS": "true",
                "TRADING_ENABLED": "true",
            },
            clear=True,
        ):
            settings = BotSettings.from_env()

        self.assertTrue(settings.trading_enabled)
        self.assertTrue(settings.api_mutations_enabled)
        self.assertEqual("unit-test-token", settings.api_control_token)


if __name__ == "__main__":
    unittest.main()

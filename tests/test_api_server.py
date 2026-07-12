import unittest
from unittest.mock import patch


class ControlPlaneTests(unittest.TestCase):
    def test_mutations_require_matching_bearer_token(self):
        from api_server import is_mutation_authorized

        with patch.dict("os.environ", {"API_CONTROL_TOKEN": "secret", "ENABLE_API_MUTATIONS": "true"}, clear=True):
            self.assertFalse(is_mutation_authorized({}))
            self.assertFalse(is_mutation_authorized({"Authorization": "Bearer wrong"}))
            self.assertTrue(is_mutation_authorized({"Authorization": "Bearer secret"}))

    def test_mutations_remain_disabled_without_explicit_opt_in(self):
        from api_server import is_mutation_authorized

        with patch.dict("os.environ", {"API_CONTROL_TOKEN": "secret"}, clear=True):
            self.assertFalse(is_mutation_authorized({"Authorization": "Bearer secret"}))


if __name__ == "__main__":
    unittest.main()

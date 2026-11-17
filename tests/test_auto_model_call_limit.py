"""Regression coverage for the configured provider call cap."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "automation"))
import book_runner as worker


class ModelCallLimitTests(unittest.TestCase):
    def test_configured_cap_accepts_1500(self):
        for value in ("1", "400", "1500"):
            with self.subTest(value=value), patch.dict(os.environ, {"OMNIROUTE_MODEL_CALL_LIMIT": value}):
                self.assertEqual(worker.model_call_limit(), int(value))

    def test_configured_cap_rejects_over_limit_and_invalid_values(self):
        for value in ("0", "1501", "-1", "invalid"):
            with self.subTest(value=value), patch.dict(os.environ, {"OMNIROUTE_MODEL_CALL_LIMIT": value}):
                with self.assertRaisesRegex(worker.JobError, "1 to 1500"):
                    worker.model_call_limit()

"""Public runtime stays independent of private book data and credentials."""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

AUTO = Path(__file__).resolve().parents[1] / "automation"
sys.path.insert(0, str(AUTO))

import book_runner as worker
import literary_context as literary
import omniroute as provider


class PublicAutoBoundaryTests(unittest.TestCase):
    def test_transport_and_literary_adapter_are_importable_without_private_repo(self):
        self.assertTrue(callable(provider.complete))
        self.assertTrue(callable(literary.parse_literary_review))
        self.assertTrue(callable(worker.next_operation))

    def test_state_root_is_external_and_can_be_changed_explicitly(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"BOOK_TRANSLATOR_STATE_ROOT": tmp}
            with patch.dict(os.environ, env):
                fresh = importlib.reload(worker)
                self.assertEqual(fresh.ROOT, Path(tmp).resolve())
                self.assertEqual(fresh.status_path("a" * 32),
                                 Path(tmp) / "automation" / "status" /
                                 ("a" * 32 + ".json"))
            importlib.reload(worker)

    def test_public_runtime_is_not_book_state_or_secret_storage(self):
        self.assertFalse((AUTO / "status").exists())
        self.assertFalse((AUTO / "notifications").exists())
        self.assertFalse((AUTO / "user-approvals").exists())
        for module in ("book_runner.py", "literary_context.py", "omniroute.py"):
            source = (AUTO / module).read_text("utf-8")
            self.assertNotIn("books/a-home-in-you-", source)
            self.assertNotIn("N8N_DELIVERY_CALLBACK_URL=\"", source)


if __name__ == "__main__":
    unittest.main()

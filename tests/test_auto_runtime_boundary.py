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

    def test_hydrating_pinned_contracts_does_not_erase_historical_book_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state = root / "state"
            engine = root / "public-checkout"
            (state / "docs/migrations").mkdir(parents=True)
            (engine / "scripts").mkdir(parents=True)
            (engine / "docs").mkdir()
            (engine / "scripts/book.py").write_text("# pinned CLI", encoding="utf-8")
            (engine / "docs/TRANSLATION.md").write_text(
                "# pinned contract", encoding="utf-8")
            (engine / "AGENTS.md").write_text("# pinned agents", encoding="utf-8")
            (engine / "agent-manifest.json").write_text("{}", encoding="utf-8")
            (engine / "SKILL.md").write_text("# pinned skill", encoding="utf-8")
            evidence = state / "docs/migrations/historical-review.json"
            evidence.write_text('{"immutable": true}', encoding="utf-8")
            success = type("Process", (), {"returncode": 0})()
            with patch.object(worker, "ROOT", state), \
                 patch.object(worker.tempfile, "mkdtemp", return_value=str(engine)), \
                 patch.object(worker.subprocess, "run", return_value=success), \
                 patch.object(worker.subprocess, "check_output",
                              return_value="a" * 40 + "\n"):
                result, actual = worker.install_engine("a" * 40)
            self.assertEqual((result, actual), (engine, "a" * 40))
            self.assertEqual(evidence.read_text("utf-8"), '{"immutable": true}')
            self.assertIn("pinned contract", (
                state / "docs/TRANSLATION.md").read_text("utf-8"))
            self.assertTrue((state / "scripts/book.py").is_file())
            self.assertEqual(
                __import__("json").loads(
                    (state / ".book-translator-install.json").read_text("utf-8")
                )["resolved_revision"], "a" * 40)

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

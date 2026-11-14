"""Run real immutable public CLI against a distinct state Git checkout.

No source uploads, model calls, private books or mutable branch upgrades.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PUBLIC = Path(__file__).resolve().parents[1]
ADAPTER = PUBLIC / "scripts/pinned_cli.py"


def git(*args):
    return subprocess.run(["git", *map(str, args)], capture_output=True,
                          text=True, check=True).stdout.strip()


class OutOfTreeWorkflowTests(unittest.TestCase):
    def test_current_pinned_book_corpus_and_claim_use_external_state_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            state, engine = parent / "state", parent / "public-pinned"
            state.mkdir()
            (state / "books").mkdir()
            git("init", str(state))
            git("-C", str(state), "config", "user.name", "Synthetic")
            git("-C", str(state), "config", "user.email", "synthetic@example.invalid")
            source = state / "original.md"
            original = "# One\n\nMara arrived at the station.\n"
            source.write_text(original, encoding="utf-8")
            git("-C", str(state), "add", "--", "original.md")
            git("-C", str(state), "commit", "-m", "source")
            state_head = git("-C", str(state), "rev-parse", "HEAD")
            git("clone", "--quiet", str(PUBLIC), str(engine))
            workflow_pin = git("-C", str(engine), "rev-parse", "HEAD")
            self.assertNotEqual(state_head, workflow_pin)

            def canonical(tool, *args, pin=workflow_pin):
                return subprocess.run(
                    [sys.executable, str(ADAPTER), "--engine", str(engine),
                     "--state", str(state), "--revision", pin, tool, *args],
                    cwd=state, capture_output=True, text=True, timeout=45)

            result = canonical("book", "extract", str(source), "--slug",
                               "synthetic-book", "--target-language", "ru")
            self.assertEqual(result.returncode, 0, result.stderr)
            result = canonical("corpus", "seal", "synthetic-book")
            self.assertEqual(result.returncode, 0, result.stderr)
            for tool, command in (("book", "validate"), ("corpus", "verify")):
                result = canonical(tool, command, "synthetic-book")
                self.assertEqual(result.returncode, 0, result.stderr)

            book = state / "books/synthetic-book"
            info = json.loads((book / "metadata.json").read_text("utf-8"))
            self.assertEqual(info["workflow"]["resolved_revision"], workflow_pin)
            result = canonical("book", "claim", "synthetic-book", "1",
                               "--role", "translator", "--session-id", "writer",
                               "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            progress = json.loads((book / "progress.json").read_text("utf-8"))
            unit = progress["chapters"][0]["unit_id"]
            claim = json.loads((book / ".workflow/claims" / (unit + ".json"))
                               .read_text("utf-8"))
            self.assertEqual(claim["base_commit"], state_head)
            self.assertEqual(original, source.read_text("utf-8"))
            self.assertTrue((engine / "books").is_symlink())
            self.assertEqual((engine / "books").resolve(), (state / "books").resolve())
            self.assertTrue((engine / ".book-translator-install.json").is_file())
            for relative in ("scripts", "docs", "AGENTS.md",
                             "agent-manifest.json", ".book-translator-install.json"):
                self.assertFalse((state / relative).exists(), relative)
            changed = git("-C", str(state), "status", "--short")
            self.assertNotIn("scripts/", changed)
            self.assertNotIn("agent-manifest.json", changed)

            invalid = canonical("book", "validate", "synthetic-book", pin="b" * 40)
            self.assertNotEqual(invalid.returncode, 0)
            self.assertEqual(info["workflow"]["resolved_revision"],
                             json.loads((book / "metadata.json").read_text("utf-8"))
                             ["workflow"]["resolved_revision"])
            self.assertFalse((state / ".book-translator-install.json").exists())

    def test_symlink_to_other_state_is_rejected_without_ownership_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("state-a", "state-b"):
                (root / name / "books").mkdir(parents=True)
                git("init", str(root / name))
            git("clone", "--quiet", str(PUBLIC), str(root / "engine"))
            from scripts.pinned_runtime import prepare, PinnedRuntimeError
            engine = root / "engine"
            revision = git("-C", str(engine), "rev-parse", "HEAD")
            prepare(engine, root / "state-a", revision)
            with self.assertRaisesRegex(PinnedRuntimeError, "outside the selected state"):
                prepare(engine, root / "state-b", revision)
            self.assertEqual((engine / "books").resolve(),
                             (root / "state-a/books").resolve())


if __name__ == "__main__":
    unittest.main()

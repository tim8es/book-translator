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

            # Complete the *real* canonical acceptance/review/final EPUB
            # lifecycle, with no installed code in state or LLM API calls.
            translated = "# Один\n\nМара прибыла на станцию.\n"
            chapter = progress["chapters"][0]
            translation_path = book / chapter["translation_path"]
            translation_path.parent.mkdir(parents=True, exist_ok=True)
            translation_path.write_text(translated, encoding="utf-8")
            def accepted(*arguments):
                result = canonical("book", *arguments)
                self.assertEqual(result.returncode, 0, result.stderr)
                return result.stdout
            accepted("accept-translation", "synthetic-book", "1",
                     "--session-id", "writer", "--json")
            accepted("release", "synthetic-book", "1",
                     "--session-id", "writer", "--json")
            accepted("claim", "synthetic-book", "1", "--role", "reviewer",
                     "--session-id", "reviewer", "--json")
            quality = parent / "quality-report.json"
            quality.write_text(json.dumps({
                "context": "independent_session",
                "checks": {"coverage": True, "meaning": True,
                           "terminology": True, "style": True, "structure": True},
                "findings": [],
            }), encoding="utf-8")
            accepted("review-record", "synthetic-book", "1",
                     "--outcome", "PASS", "--session-id", "reviewer",
                     "--quality-report", str(quality), "--json")
            accepted("accept-review", "synthetic-book", "1", "--json")
            accepted("finalize", "synthetic-book", "--json")
            accepted("build", "synthetic-book", "--format", "epub")
            final_progress = json.loads((book / "progress.json").read_text("utf-8"))
            self.assertEqual(final_progress["chapters"][0]["status"], "reviewed")
            manifest = json.loads((book / "output/manifest.json").read_text("utf-8"))
            self.assertEqual(manifest["format"], "epub")
            self.assertFalse(manifest["preview"])
            artifact = book / manifest["artifact_path"]
            self.assertTrue(artifact.is_file())
            self.assertEqual(hashlib.sha256(artifact.read_bytes()).hexdigest(),
                             manifest["artifact_sha256"])
            self.assertFalse((state / ".book-translator-install.json").exists())
            self.assertFalse((state / "scripts").exists())

            invalid = canonical("book", "validate", "synthetic-book", pin="b" * 40)
            self.assertNotEqual(invalid.returncode, 0)
            self.assertEqual(info["workflow"]["resolved_revision"],
                             json.loads((book / "metadata.json").read_text("utf-8"))
                             ["workflow"]["resolved_revision"])
            self.assertFalse((state / ".book-translator-install.json").exists())

    def test_historical_existing_book_workflow_revision_runs_without_state_install(self):
        # A real previously pinned revision of A Home in You; the fixture
        # contains only original, synthetic text, never its copyrighted book.
        historical = "ea9963ca546b7cc7f6aba4dc5350fa19ea7da862"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state, engine = root / "state", root / "public-history"
            state.mkdir()
            (state / "books").mkdir()
            git("init", str(state))
            git("clone", "--quiet", str(PUBLIC), str(engine))
            available = subprocess.run(
                ["git", "-C", str(engine), "cat-file", "-e", historical + "^{commit}"],
                capture_output=True)
            if available.returncode:
                git("-C", str(engine), "fetch", "--quiet",
                    "https://github.com/tim8es/book-translator.git", historical)
            git("-C", str(engine), "checkout", "--quiet", "--detach", historical)
            old = git("-C", str(engine), "rev-parse", "HEAD")
            self.assertEqual(old, historical)
            source = state / "synthetic.md"
            source.write_text("# Old Version\n\nA fictional fox crossed a bridge.\n",
                              encoding="utf-8")
            for tool, params in (
                ("book", ("extract", str(source), "--slug",
                          "legacy-synthetic", "--target-language", "ru")),
                ("corpus", ("seal", "legacy-synthetic")),
                ("book", ("validate", "legacy-synthetic")),
                ("corpus", ("verify", "legacy-synthetic")),
            ):
                run = subprocess.run(
                    [sys.executable, str(ADAPTER), "--engine", str(engine),
                     "--state", str(state), "--revision", historical,
                     tool, *params], cwd=state,
                    capture_output=True, text=True, timeout=60)
                self.assertEqual(run.returncode, 0, run.stderr)
            book = state / "books/legacy-synthetic"
            pin = json.loads((book / "metadata.json").read_text("utf-8"))
            self.assertEqual(pin["workflow"]["resolved_revision"], historical)
            self.assertFalse((state / ".book-translator-install.json").exists())
            self.assertFalse((state / "scripts").exists())
            self.assertTrue((engine / "books").is_symlink())

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

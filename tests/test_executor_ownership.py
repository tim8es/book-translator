"""Executor ownership gates: legacy Auto, Pi reservation, and durable book mirror."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import os

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "automation"))
import book_runner as runner
import executor_ownership as owner

JOB = "9" * 32
SHA = "a" * 64
SLUG = "new-test-book"


def put(root: Path, rel: str, payload: dict) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return path


def git(directory: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=directory, text=True,
                            capture_output=True, check=True)
    return result.stdout.strip()


class OwnershipTests(unittest.TestCase):
    def test_legacy_auto_and_explicit_pi_book_owner(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            self.assertEqual(owner.selected(root, JOB, SHA), "auto")
            self.assertTrue(owner.require(root, JOB, SHA, backend="omniroute"))
            self.assertFalse(owner.require(root, JOB, SHA, backend="pi"))
            put(root, f"intake/auto/{JOB}/executor.json", owner.marker(JOB, SHA, "pi"))
            self.assertEqual(owner.selected(root, JOB, SHA), "pi")
            self.assertFalse(owner.require(root, JOB, SHA, backend="omniroute"))
            self.assertTrue(owner.require(root, JOB, SHA, backend="pi"))
            (root / "books" / SLUG).mkdir(parents=True)
            with self.assertRaisesRegex(owner.OwnershipError, "requires a matching"):
                owner.selected(root, JOB, SHA, SLUG)
            self.assertTrue(owner.write_book_marker(root, JOB, SHA, SLUG))
            self.assertFalse(owner.write_book_marker(root, JOB, SHA, SLUG))
            self.assertEqual(owner.selected(root, JOB, SHA, SLUG), "pi")
            self.assertEqual(json.loads(owner.book_marker_path(root, SLUG).read_text())["job_id"], JOB)

    def test_wrong_hash_other_job_or_malformed_marker_fails_closed(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            file = put(root, f"intake/auto/{JOB}/executor.json", owner.marker(JOB, SHA, "pi"))
            with self.assertRaises(owner.OwnershipError):
                owner.selected(root, JOB, "b" * 64)
            with self.assertRaises(owner.OwnershipError):
                owner.selected(root, "8" * 32, SHA, SLUG)
            file.write_text('{"schema_version":1,"executor":"pi"}')
            with self.assertRaises(owner.OwnershipError):
                owner.selected(root, JOB, SHA)
            file.write_text('broken json')
            with self.assertRaises(owner.OwnershipError):
                owner.selected(root, JOB, SHA)

    def test_conflicting_book_and_job_markers_cannot_switch_execution(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            put(root, f"intake/auto/{JOB}/executor.json", owner.marker(JOB, SHA, "pi"))
            put(root, f"books/{SLUG}/.workflow/executor.json",
                owner.marker(JOB, SHA, "auto", SLUG))
            with self.assertRaisesRegex(owner.OwnershipError, "conflicts"):
                owner.selected(root, JOB, SHA, SLUG)

    def test_legacy_auto_scheduler_skips_pi_intake_without_book(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            source = b"PK-synthetic-book-fixture"
            request = {"schema_version": 1, "job_id": JOB,
                       "source_sha256": hashlib.sha256(source).hexdigest(),
                       "target_language": "ru"}
            put(root, f"intake/auto/{JOB}/request.json", request)
            (root / "intake" / "auto" / JOB / "source.epub").write_bytes(source)
            with patch.object(runner, "ROOT", root):
                self.assertEqual([x["job_id"] for x in runner.ready_jobs()], [JOB])
                put(root, f"intake/auto/{JOB}/executor.json",
                    owner.marker(JOB, request["source_sha256"], "pi"))
                self.assertEqual(runner.ready_jobs(), [])
                self.assertEqual(runner.select(None), "")
                with patch.dict(os.environ, {"BOOK_TRANSLATOR_LLM_BACKEND": "omniroute"}):
                    with self.assertRaises(runner.ExecutorMismatch):
                        runner.ensure_job_executor(JOB, request)
                with patch.dict(os.environ, {"BOOK_TRANSLATOR_LLM_BACKEND": "pi"}):
                    runner.ensure_job_executor(JOB, request)

    def test_pi_admission_commits_only_immutable_intake_marker(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            bare = root / "bare.git"
            origin = root / "state"
            git(root, "init", "--bare", str(bare))
            git(root, "clone", str(bare), str(origin))
            git(origin, "config", "user.name", "fixture")
            git(origin, "config", "user.email", "fixture@example.test")
            (origin / ".gitignore").write_text("*.tmp\n")
            git(origin, "add", ".gitignore")
            git(origin, "commit", "-m", "initial")
            git(origin, "branch", "-M", "main")
            git(origin, "push", "origin", "main")
            source = origin / "intake" / "auto" / JOB / "source.epub"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"PK-synthetic")
            request = {"schema_version": 1, "job_id": JOB,
                       "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                       "target_language": "ru"}
            with patch.object(runner, "ROOT", origin), \
                 patch.object(runner, "request_for", return_value=request), \
                 patch.object(runner, "epub_identity", return_value=("New Test Book", "Fixture")), \
                 patch.object(runner, "locate_book", return_value=(SLUG, {})):
                runner.admit_pi_job(JOB)
                runner.admit_pi_job(JOB)  # Idempotent replay.
                self.assertEqual(owner.selected(origin, JOB, request["source_sha256"]), "pi")
                self.assertEqual(git(origin, "ls-files"), ".gitignore\nintake/auto/" + JOB + "/executor.json")
                self.assertEqual(git(origin, "rev-parse", "HEAD"),
                                 git(origin, "rev-parse", "refs/remotes/origin/main"))
                # Existing legacy work cannot be silently migrated.
                file = origin / "intake" / "auto" / JOB / "executor.json"
                file.unlink()
                put(origin, f"automation/status/{JOB}.json", {"state":"running"})
                with self.assertRaises(owner.OwnershipError):
                    runner.admit_pi_job(JOB)


if __name__ == "__main__":
    unittest.main()

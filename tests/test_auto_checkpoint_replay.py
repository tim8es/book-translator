"""Regression: disjoint Git replay must not break the next automatic checkpoint."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "automation"))
import book_runner as worker


def git(where: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=where, text=True,
                            capture_output=True, check=True)
    return result.stdout.strip()


class ReplayCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.remote = root / "remote.git"
        self.writer = root / "writer"
        self.runner = root / "runner"
        git(root, "init", "--bare", "--initial-branch=main", str(self.remote))
        git(root, "clone", str(self.remote), str(self.writer))
        for name in ("writer",):
            git(self.writer, "config", "user.name", "Regression Test")
            git(self.writer, "config", "user.email", "test@example.invalid")
        (self.writer / "books" / "sample").mkdir(parents=True)
        (self.writer / "books" / "other").mkdir(parents=True)
        (self.writer / "scripts").mkdir()
        (self.writer / "books" / "sample" / "progress.json").write_text(
            '{"chapters": []}\n', encoding="utf-8")
        (self.writer / "books" / "other" / "progress.json").write_text(
            '{"revision": 1}\n', encoding="utf-8")
        (self.writer / "scripts" / "book.py").write_text(
            "original pinned engine\n", encoding="utf-8")
        git(self.writer, "add", ".")
        git(self.writer, "commit", "-m", "initial state")
        git(self.writer, "push", "-u", "origin", "main")
        git(root, "clone", str(self.remote), str(self.runner))
        git(self.runner, "config", "user.name", "Regression Test")
        git(self.runner, "config", "user.email", "test@example.invalid")
        self.job_id = "a" * 32

    def other_book_push(self):
        path = self.writer / "books" / "other" / "progress.json"
        path.write_text('{"revision": 2}\n', encoding="utf-8")
        git(self.writer, "add", ".")
        git(self.writer, "commit", "-m", "concurrent other-book update")
        git(self.writer, "push", "origin", "main")

    def test_two_checkpoints_after_disjoint_replay_preserve_pinned_engine(self):
        # The worker starts at an old HEAD and has uncommitted engine files.
        pinned = self.runner / "scripts" / "book.py"
        pinned.write_text("modified pinned engine in worktree\n", encoding="utf-8")
        self.other_book_push()
        status = {"schema_version": 1, "job_id": self.job_id,
                  "state": "running", "phase": "matched-existing-book"}
        with patch.object(worker, "ROOT", self.runner):
            worker.checkpoint(self.job_id, status, "sample")
            self.assertEqual(
                git(self.runner, "rev-parse", "HEAD"),
                git(self.writer, "ls-remote", "origin", "refs/heads/main").split()[0])
            self.assertEqual(
                pinned.read_text(encoding="utf-8"),
                "modified pinned engine in worktree\n")
            self.assertEqual(git(self.runner, "diff", "--cached", "--name-only"), "")
            status["phase"] = "next-checkpoint"
            worker.checkpoint(self.job_id, status, "sample")

        git(self.writer, "pull", "--ff-only", "origin", "main")
        received = json.loads((self.writer / "automation" / "status" /
                               (self.job_id + ".json")).read_text(encoding="utf-8"))
        self.assertEqual(received["phase"], "next-checkpoint")
        self.assertEqual((self.writer / "books" / "other" / "progress.json").
                         read_text(encoding="utf-8"), '{"revision": 2}\n')
        self.assertEqual(
            pinned.read_text(encoding="utf-8"),
            "modified pinned engine in worktree\n")

    def test_replay_refuses_conflicting_changes_to_same_book(self):
        sample = self.writer / "books" / "sample" / "progress.json"
        sample.write_text('{"chapters": [{"id": "new"}]}\n', encoding="utf-8")
        git(self.writer, "add", ".")
        git(self.writer, "commit", "-m", "concurrent same-book update")
        git(self.writer, "push", "origin", "main")
        status = {"job_id": self.job_id, "phase": "matched-existing-book"}
        with patch.object(worker, "ROOT", self.runner):
            with self.assertRaisesRegex(worker.JobError, "remote changed this job"):
                worker.checkpoint(self.job_id, status, "sample")


if __name__ == "__main__":
    unittest.main()

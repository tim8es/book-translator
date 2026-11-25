"""Regression tests for the explicit Auto/Pi handoff."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "automation"))
import book_runner as runner
import executor_ownership as owner
import executor_transfer as handoff

JOB = "9" * 32
SLUG = "test-book"
PIN = "1" * 40
BOOK = f"books/{SLUG}"
INTAKE = f"intake/auto/{JOB}"
STATUS = f"automation/status/{JOB}.json"
PLAN = f"automation/executor-transfer-requests/{JOB}.json"


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        directory = Path(self.tmp.name)
        self.root = directory / "state"
        self.root.mkdir()
        subprocess.run(["git", "init", "--bare", str(directory / "remote.git")],
                       check=True, capture_output=True)
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.test")
        self.git("remote", "add", "origin", str(directory / "remote.git"))
        original = b"PK-test-source"
        self.sha = hashlib.sha256(original).hexdigest()
        self.put(INTAKE + "/source.epub", original)
        self.put(INTAKE + "/request.json", {"schema_version": 1, "job_id": JOB,
                 "source_sha256": self.sha, "target_language": "ru"})
        self.put(BOOK + "/metadata.json", {"source": {"sha256": self.sha},
                 "workflow": {"resolved_revision": PIN}, "target_language": "ru"})
        self.put(BOOK + "/progress.json", {"chapters": [{"status": "reviewed"}]})
        self.put(BOOK + "/review-ledger.json", {"records": ["unchanged"]})
        self.put(BOOK + "/translated/chapter.md", b"retained translation\n")
        self.put(STATUS, {"schema_version": 1, "job_id": JOB, "book_slug": SLUG,
                 "source_sha256": self.sha, "workflow_revision": PIN,
                 "state": "blocked", "phase": "attention-requires-operator",
                 "active": None, "model_calls_started": 68, "updated_at": "2026-11-25T00:00:00Z"})
        self.commit("initial")
        self.git("push", "origin", "main")
        self.engine = directory / "engine"
        self.canonical = {"claims": [], "errors": []}
        self.choice = {"operation": "review"}
        for mock in (patch.object(runner, "ROOT", self.root),
                     patch.object(runner, "install_engine", side_effect=lambda rev: (self.engine, rev)),
                     patch.object(runner, "verify"),
                     patch.object(runner, "cli", side_effect=lambda cmd, *_: json.dumps(
                         self.canonical if cmd == "status" else self.choice))):
            mock.start()
            self.addCleanup(mock.stop)

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, text=True,
                              check=True, capture_output=True).stdout.strip()

    def put(self, rel, data):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else
                         (json.dumps(data) + "\n").encode())

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-m", message)

    def plan(self, start="auto", target="pi"):
        self.put(PLAN, {"schema_version": 1, "job_id": JOB, "book_slug": SLUG,
                 "source_sha256": self.sha, "workflow_revision": PIN,
                 "from_executor": start, "to_executor": target,
                 "status_blob": self.git("rev-parse", f"HEAD:{STATUS}"),
                 "book_tree": self.git("rev-parse", f"HEAD:{BOOK}")})
        self.commit("request")
        self.git("push", "origin", "main")

    def test_round_trip_preserves_status_and_literary_state(self):
        status = (self.root / STATUS).read_bytes()
        book = {p.relative_to(self.root): p.read_bytes()
                for p in (self.root / BOOK).rglob("*") if p.is_file()}
        for start, target in (("auto", "pi"), ("pi", "auto")):
            self.plan(start, target)
            self.assertEqual(handoff.transfer(JOB), target)
            self.assertEqual(owner.selected(self.root, JOB, self.sha, SLUG), target)
            self.assertEqual(owner.require(self.root, JOB, self.sha, slug=SLUG,
                             backend="pi"), target == "pi")
            self.assertEqual(owner.require(self.root, JOB, self.sha, slug=SLUG,
                             backend="omniroute"), target == "auto")
            self.assertEqual((self.root / STATUS).read_bytes(), status)
            for path, content in book.items():
                self.assertEqual((self.root / path).read_bytes(), content)
            self.assertEqual(set(self.git("diff-tree", "--no-commit-id", "--name-only",
                             "-r", "HEAD").splitlines()),
                             {INTAKE + "/executor.json", BOOK + "/.workflow/executor.json",
                              BOOK + "/.workflow/auto-control.json"})
            self.assertEqual(self.git("rev-parse", "HEAD"), self.git("rev-parse", "origin/main"))

    def test_live_claim_rejected(self):
        self.plan()
        self.canonical["claims"] = [{"unit_id": "chapter-000001"}]
        with self.assertRaisesRegex(runner.JobError, "claims or errors"):
            handoff.transfer(JOB)
        self.assertEqual(owner.selected(self.root, JOB, self.sha, SLUG), "auto")

    def test_unresolved_attention_rejected(self):
        self.plan()
        self.choice = {"operation": "needs_attention"}
        with self.assertRaisesRegex(runner.JobError, "attention must be resolved"):
            handoff.transfer(JOB)

    def test_stale_plan_rejected(self):
        self.plan()
        self.put(BOOK + "/progress.json", {"chapters": [{"status": "translated"}]})
        self.commit("concurrent book change")
        with self.assertRaisesRegex(runner.JobError, "stale executor transfer"):
            handoff.transfer(JOB)

    def test_active_status_rejected(self):
        state = runner.read_json(self.root / STATUS)
        state["active"] = {"role": "reviewer"}
        self.put(STATUS, state)
        self.commit("active claim")
        self.plan()
        with self.assertRaisesRegex(runner.JobError, "inactive"):
            handoff.transfer(JOB)


if __name__ == "__main__":
    unittest.main()

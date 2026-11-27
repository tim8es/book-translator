"""Synthetic regression tests for exact-source completion reconciliation."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "automation"))
import completion_reconciliation as reconciliation
import job_monitor
import book_runner as runner


class CompletionReconciliationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.slug = "synthetic-reconciliation"
        self.book = self.root / "books" / self.slug
        self.job = "a" * 32
        self.pin = "b" * 40
        self.original = self.root / "intake" / "auto" / self.job / "source.epub"
        self.original.parent.mkdir(parents=True)
        with zipfile.ZipFile(self.original, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
        self.sha = job_monitor.digest(self.original)
        self.request = {"schema_version": 1, "job_id": self.job,
                        "source_sha256": self.sha, "target_language": "ru"}
        self.original.with_name("request.json").write_text(json.dumps(self.request))
        self.put("source/source.epub", self.original.read_bytes())
        self.put("extracted/one.md", b"synthetic source")
        self.put("translated/one.md", b"synthetic translation")
        s = job_monitor.digest(self.book / "extracted/one.md")
        t = job_monitor.digest(self.book / "translated/one.md")
        self.write("metadata.json", {
            "chapter_count": 1, "target_language": "ru",
            "workflow": {"resolved_revision": self.pin},
            "source": {"filename": "source.epub", "sha256": self.sha,
                       "storage_mode": "embedded"}})
        self.write("source-manifest.json", {
            "source_sha256": self.sha,
            "extracted": [{"unit_id": "chapter-000001", "path": "extracted/one.md",
                           "sha256": s}]})
        self.write("source-coverage.json", {
            "state": "verified", "source_sha256": self.sha, "chapter_count": 1,
            "extracted": [{"number": 1, "sha256": s}]})
        self.write("progress.json", {
            "book_slug": self.slug,
            "chapters": [{"unit_id": "chapter-000001", "number": 1, "status": "reviewed",
                          "source_path": "extracted/one.md", "translation_path": "translated/one.md",
                          "translation_acceptance": {"source_sha256": s, "translation_sha256": t,
                              "workflow_revision": self.pin, "session_id": "translator"}}]})
        self.write("review-ledger.json", {
            "book_slug": self.slug, "records": [{
                "unit_id": "chapter-000001", "sequence": 1, "outcome": "PASS",
                "source_sha256": s, "translation_sha256": t,
                "workflow_revision": self.pin, "review_contract_revision": "docs/TRANSLATION.md@" + self.pin,
                "reviewer_session_id": "reviewer", "quality_report": {
                    "checks": dict.fromkeys(("coverage", "meaning", "terminology", "style", "structure"), True),
                    "findings": []}}]})
        self.put("FINAL_QUALITY_GATES.md", b"synthetic report")
        self.put("REVIEW_REPORT.md", b"synthetic report")
        output = self.book / "output/book.epub"
        output.parent.mkdir(parents=True)
        with zipfile.ZipFile(output, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
        self.write("output/manifest.json", {
            "book_slug": self.slug, "format": "epub", "preview": False,
            "unit_count": 1, "artifact_path": "output/book.epub",
            "artifact_sha256": job_monitor.digest(output),
            "state_revisions": {key: job_monitor.digest(self.book / name)
                                for key, name in reconciliation.REVISIONS.items()}})
        self.previous = {"schema_version": 1, "job_id": self.job, "book_slug": self.slug,
                         "source_sha256": self.sha, "workflow_revision": self.pin,
                         "state": "blocked", "phase": "attention-requires-operator",
                         "last_error": "obsolete error", "model_calls_started": 12,
                         "active": {"unit_id": "chapter-000001", "session_id": "old"}}

    def put(self, path, data):
        file = self.book / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data)

    def write(self, path, value):
        self.put(path, json.dumps(value).encode())

    def read(self, path):
        return json.loads((self.book / path).read_text())

    def candidate(self, previous=None):
        return reconciliation.candidate(self.root, self.request, previous)

    def status_file(self, value):
        path = self.root / "automation/status" / (self.job + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return path

    def test_missing_blocked_running_paused_and_idempotent_done(self):
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertIsNotNone(self.candidate())
        for state in ("blocked", "running", "paused"):
            with self.subTest(state=state):
                previous = {**self.previous, "state": state}
                fields = self.candidate(previous)
                self.assertIsNotNone(fields)
                result = reconciliation.reconciled_status(self.job, previous, fields)
                self.assertEqual(result["state"], "done")
                self.assertEqual(result["phase"], "reconciled-completed")
                self.assertEqual(result["model_calls_started"], 12)
                self.assertEqual(result["completion_reconciliation"]["previous_error"], "obsolete error")
                self.assertIsNone(result["active"])
                self.assertIsNone(self.candidate(result))
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_reject_mismatched_edition_pin_or_ambiguous_workspace(self):
        self.assertIsNone(self.candidate({**self.previous, "source_sha256": "c"*64}))
        self.assertIsNone(self.candidate({**self.previous, "workflow_revision": "d"*40}))
        self.assertIsNone(self.candidate({**self.previous, "superseded_by_job_id": "e"*32}))
        self.request["workflow_revision"] = "f"*40
        self.assertIsNone(self.candidate())
        self.request.pop("workflow_revision")
        other = self.root / "books" / "other"
        other.mkdir()
        (other / "metadata.json").write_bytes((self.book / "metadata.json").read_bytes())
        self.assertIsNone(self.candidate())

    def test_reject_claims_stale_manifest_changed_translation_and_preview(self):
        self.put(".workflow/claims/chapter-000001.json", b"{}")
        self.assertIsNone(self.candidate())
        (self.book / ".workflow/claims/chapter-000001.json").unlink()
        target = self.book / "translated/one.md"
        saved = target.read_bytes()
        target.write_bytes(b"changed")
        self.assertIsNone(self.candidate())
        target.write_bytes(saved)
        p = self.read("progress.json")
        p["chapters"][0]["status"] = "translated"
        self.write("progress.json", p)
        self.assertIsNone(self.candidate())

    def test_reject_invalid_coverage_and_preview_and_corrupt_epub(self):
        coverage = self.read("source-coverage.json")
        self.write("source-coverage.json", {**coverage, "state": "invalid"})
        self.assertIsNone(self.candidate())
        self.write("source-coverage.json", coverage)
        manifest = self.read("output/manifest.json")
        self.write("output/manifest.json", {**manifest, "preview": True})
        self.assertIsNone(self.candidate())
        self.write("output/manifest.json", manifest)
        self.put("output/book.epub", b"not a ZIP")
        self.assertIsNone(self.candidate())

    def test_queue_selects_blocked_completion_and_runner_does_not_call_model(self):
        self.status_file(self.previous)
        with patch.object(runner, "ROOT", self.root), \
             patch.object(runner, "git", side_effect=lambda *args: "c"*40 if args[0] == "rev-parse" else ""), \
             patch.object(runner, "checkpoint") as checkpoint, \
             patch.object(runner, "install_engine", return_value=(self.root / "unused-engine", self.pin)) as engine, \
             patch.object(runner, "cli", return_value=json.dumps({
                 "state": "current", "preview": False, "artifact_path": "output/book.epub",
                 "artifact_sha256": self.read("output/manifest.json")["artifact_sha256"]})) as cli, \
             patch.object(runner, "complete") as model:
            self.assertEqual(runner.ready_jobs(), [{"job_id": self.job, "book_key": self.slug}])
            result = runner.run(self.job)
            self.assertEqual(result["state"], "done")
            checkpoint.assert_called_once_with(self.job, result, self.slug)
            engine.assert_called_once_with(self.pin)
            cli.assert_called_once_with("build-status", self.slug, "--json")
            model.assert_not_called()

    def test_pinned_build_preflight_failure_does_not_requeue_unchanged_tree(self):
        self.status_file(self.previous)
        with patch.object(runner, "ROOT", self.root), \
             patch.object(runner, "git", side_effect=lambda *args: "c"*40 if args[0] == "rev-parse" else ""), \
             patch.object(runner, "checkpoint") as checkpoint, \
             patch.object(runner, "install_engine", return_value=(self.root / "unused-engine", self.pin)), \
             patch.object(runner, "cli", return_value='{"state":"stale"}'), \
             patch.object(runner, "complete") as model:
            result = runner.run(self.job)
            self.assertEqual(result["state"], "blocked")
            self.assertEqual(result["phase"], "completion-reconciliation-preflight-failed")
            self.assertEqual(result["reconciliation_failed_tree"], "c"*40)
            checkpoint.assert_called_once()
            model.assert_not_called()
            self.status_file(result)
            self.assertEqual(runner.ready_jobs(), [])
            self.assertEqual(runner.run(self.job), result)
            checkpoint.assert_called_once()

    def test_new_intake_uses_existing_book_concurrency_lane(self):
        with patch.object(runner, "ROOT", self.root):
            self.assertEqual(runner.book_key(self.job), self.slug)
            self.assertEqual(runner.ready_jobs(), [{"job_id": self.job, "book_key": self.slug}])

    def test_monitor_rejects_mutated_evidence_after_reconciliation(self):
        fields = self.candidate()
        done = reconciliation.reconciled_status(self.job, self.previous, fields)
        self.status_file(done)
        now = datetime.now(timezone.utc)
        self.assertEqual(job_monitor.inspect(self.root, self.job, now, True)["type"], "completed")
        self.put("translated/one.md", b"post-checkpoint corruption")
        self.assertEqual(job_monitor.inspect(self.root, self.job, now, True)["type"], "integrity_failed")


if __name__ == "__main__":
    unittest.main()

"""Regression tests for independent state monitoring and webhook delivery."""
from __future__ import annotations

import hashlib
import hmac
import json
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "automation"))
import job_monitor as monitor

JOB = "a" * 32
SLUG = "synthetic-story"
NOW = datetime(2026, 11, 2, 18, 0, tzinfo=timezone.utc)


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


class BookJobMonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.book = self.root / "books" / SLUG
        self.status_file = self.root / "automation" / "status" / (JOB + ".json")

    def blocked(self):
        self.book.mkdir(parents=True, exist_ok=True)
        write_json(self.book / "progress.json", {"chapters": [
            {"status": "reviewed"} for _ in range(6)
        ] + [{"status": "translated"}]})
        write_json(self.status_file, {
            "job_id": JOB, "book_slug": SLUG, "state": "blocked",
            "phase": "correction-limit-reached", "correction_rounds": {
                "chapter-000007": 3}, "blocked_unit_id": "chapter-000007",
            "updated_at": NOW.isoformat(),
        })

    def done(self, preview=False):
        output = self.book / "output"
        output.mkdir(parents=True, exist_ok=True)
        artifact = output / "synthetic-story.epub"
        with zipfile.ZipFile(artifact, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr("EPUB/chapter.xhtml", "<p>example</p>")
        sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
        write_json(output / "manifest.json", {
            "artifact_path": "output/synthetic-story.epub",
            "artifact_sha256": sha, "book_slug": SLUG,
            "unit_count": 1, "preview": preview,
        })
        acceptance = {
            "session_id": "translator-different", "source_sha256": "1" * 64,
            "translation_sha256": "2" * 64,
        }
        write_json(self.book / "progress.json", {"chapters": [{
            "unit_id": "chapter-000001", "status": "translated" if preview else "reviewed",
            "translation_acceptance": acceptance,
        }]})
        write_json(self.book / "review-ledger.json", {"records": [] if preview else [{
            "sequence": 1, "unit_id": "chapter-000001",
            "outcome": "PASS", "reviewer_session_id": "reviewer-other",
            "source_sha256": acceptance["source_sha256"],
            "translation_sha256": acceptance["translation_sha256"],
            "quality_report": {"findings": []},
        }]})
        status = {"job_id": JOB, "book_slug": SLUG, "state": "done",
                  "phase": "delivered", "updated_at": NOW.isoformat(),
                  "workflow_revision": "3" * 40,
                  "artifact_path": "books/" + SLUG + "/output/synthetic-story.epub",
                  "artifact_sha256": sha}
        if preview:
            status["review_provenance"] = "user-approved-unreviewed"
            write_json(self.root / "automation" / "user-approvals" / (SLUG + ".json"), {
                "workflow_revision": "3" * 40,
                "units": [{"unit_id": "chapter-000001",
                           "source_sha256": acceptance["source_sha256"],
                           "translation_sha256": acceptance["translation_sha256"]}]})
        write_json(self.status_file, status)
        return status, artifact

    def test_blocked_even_when_actions_was_green(self):
        self.blocked()
        status = monitor.read_json(self.status_file)
        status["last_error"] = "reviewer evidence is invalid: stale anchor error"
        write_json(self.status_file, status)
        e = monitor.inspect(self.root, JOB, NOW, False)
        self.assertEqual(e["type"], "blocked")
        self.assertEqual(e["phase"], "correction-limit-reached")
        self.assertIn("correction limit reached", e["reason"].lower())
        self.assertNotIn("stale anchor error", e["reason"])
        self.assertEqual(e["reviewed_units"], 6)
        self.assertEqual(e["total_units"], 7)
        self.assertEqual(e["correction_rounds"]["chapter-000007"], 3)

    def test_deferred_review_blocks_delivery_and_identifies_units(self):
        self.blocked()
        status = monitor.read_json(self.status_file)
        status.update({"phase": "review-deferred-requires-operator",
                       "deferred_review_units": ["chapter-000007"]})
        write_json(self.status_file, status)
        event = monitor.inspect(self.root, JOB, NOW, False)
        self.assertIn("deferred chapters need a current PASS", event["reason"])
        self.assertEqual(event["deferred_review_units"], ["chapter-000007"])

    def test_superseded_blocked_request_does_not_duplicate_alert(self):
        self.blocked()
        status = monitor.read_json(self.status_file)
        status["phase"] = "superseded-invalid-converted-epub"
        status["superseded_by_job_id"] = "b" * 32
        write_json(self.status_file, status)
        self.assertIsNone(monitor.inspect(self.root, JOB, NOW, False))

    def test_stale_and_recent_running(self):
        write_json(self.status_file, {
            "job_id": JOB, "state": "running",
            "updated_at": (NOW - timedelta(hours=3)).isoformat(),
        })
        self.assertEqual(monitor.inspect(self.root, JOB, NOW, False)["type"], "stalled")
        write_json(self.status_file, {
            "job_id": JOB, "state": "running",
            "updated_at": (NOW - timedelta(minutes=3)).isoformat(),
        })
        self.assertIsNone(monitor.inspect(self.root, JOB, NOW, False))

    def test_malformed_state_generates_integrity_event(self):
        self.status_file.parent.mkdir(parents=True)
        self.status_file.write_text("{broken", encoding="utf-8")
        self.assertEqual(monitor.inspect(self.root, JOB, NOW, False)["type"], "integrity_failed")

    def test_done_requires_artifact_and_independent_pass(self):
        status, artifact = self.done()
        event = monitor.inspect(self.root, JOB, NOW, False)
        self.assertEqual(event["type"], "completed")
        self.assertEqual(event["reviewed_units"], 1)
        self.assertEqual(event["artifact_sha256"], status["artifact_sha256"])
        artifact.write_bytes(b"tampered")
        self.assertEqual(monitor.inspect(self.root, JOB, NOW, False)["type"], "integrity_failed")

    def test_done_rejects_self_review(self):
        self.done()
        path = self.book / "review-ledger.json"
        ledger = monitor.read_json(path)
        ledger["records"][0]["reviewer_session_id"] = "translator-different"
        write_json(path, ledger)
        self.assertEqual(monitor.inspect(self.root, JOB, NOW, False)["type"], "integrity_failed")

    def test_done_rejects_newer_reviewer_failure(self):
        self.done()
        path = self.book / "review-ledger.json"
        ledger = monitor.read_json(path)
        ledger["records"].append({"unit_id": "chapter-000001",
                                  "sequence": 2, "outcome": "CORRECTIONS_REQUIRED"})
        write_json(path, ledger)
        self.assertEqual(monitor.inspect(self.root, JOB, NOW, False)["type"], "integrity_failed")

    def test_user_approved_preview_must_be_explicit(self):
        self.done(preview=True)
        event = monitor.inspect(self.root, JOB, NOW, False)
        self.assertEqual(event["type"], "completed_preview")
        path = self.root / "automation" / "user-approvals" / (SLUG + ".json")
        path.unlink()
        self.assertEqual(monitor.inspect(self.root, JOB, NOW, False)["type"], "integrity_failed")

    def test_event_id_is_stable(self):
        e = {"job_id": JOB, "type": "blocked", "phase": "correction-limit-reached"}
        self.assertEqual(monitor.event_id(e), monitor.event_id(dict(reversed(list(e.items())))))

    def test_blocked_event_ignores_book_progress_but_not_new_block(self):
        self.blocked()
        first = monitor.inspect(self.root, JOB, NOW, False)
        changed = dict(first, reviewed_units=7)
        self.assertEqual(monitor.event_id(first), monitor.event_id(changed))
        self.assertNotEqual(monitor.event_id(first), monitor.event_id(
            dict(first, status_updated_at=(NOW + timedelta(minutes=1)).isoformat())))

    def test_existing_legacy_receipt_survives_progress_change(self):
        self.blocked()
        old = monitor.inspect(self.root, JOB, NOW, False)
        old["schema_version"] = 1
        raw = json.dumps(old, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        legacy_id = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        receipt = self.root / "automation" / "notifications" / "sent" / JOB / (legacy_id + ".json")
        write_json(receipt, {"event_id": legacy_id, "job_id": JOB, "type": "blocked"})
        new_block = dict(old, status_updated_at=(NOW + timedelta(minutes=1)).isoformat())
        self.assertFalse(monitor.legacy_blocked_receipt(new_block, receipt.parent))
        progress = monitor.read_json(self.book / "progress.json")
        progress["chapters"][-1]["status"] = "reviewed"
        write_json(self.book / "progress.json", progress)
        with patch.object(sys, "argv", ["monitor", "--root", str(self.root)]), \
             patch.dict("os.environ", {"BOOK_EVENTS_WEBHOOK_URL": "https://example.test",
                                       "BOOK_EVENTS_SIGNING_SECRET": "secret"}), \
             patch.object(monitor, "git_ack"), patch.object(monitor, "send") as send:
            self.assertEqual(monitor.main(), 0)
            send.assert_not_called()

    def test_webhook_rejects_insecure_url(self):
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            monitor.send("http://example.org/webhook", "secret",
                         {"event_id": "x", "type": "blocked"})

    def test_hmac_signature_bound_to_event_and_time(self):
        event = {"event_id": "f" * 64, "type": "blocked", "job_id": JOB}
        captured = []
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return False
        class Opener:
            def open(self, request, timeout):
                captured.append(request)
                return Response()
        with patch.object(monitor.urllib.request, "build_opener", return_value=Opener()), \
             patch.object(monitor.time, "time", return_value=12345):
            monitor.send("https://n8n.example.test/webhook", "hmac-test", event)
        req = captured[0]
        self.assertEqual(req.get_header("X-book-event-id"), "f" * 64)
        self.assertEqual(req.get_header("X-book-event-timestamp"), "12345")
        signed = hmac.new(b"hmac-test", b"12345." + req.data, hashlib.sha256).hexdigest()
        self.assertEqual(req.get_header("X-book-event-signature"), "sha256=" + signed)

    def test_dry_run_and_missing_configuration_do_not_record_delivery(self):
        self.blocked()
        with patch.object(sys, "argv", ["monitor", "--root", str(self.root), "--dry-run"]):
            self.assertEqual(monitor.main(), 0)
        with patch.dict("os.environ", {"BOOK_EVENTS_WEBHOOK_URL": "",
                                       "BOOK_EVENTS_SIGNING_SECRET": ""}), \
             patch.object(sys, "argv", ["monitor", "--root", str(self.root)]):
            self.assertEqual(monitor.main(), 0)
        self.assertFalse((self.root / "automation" / "notifications").exists())


if __name__ == "__main__":
    unittest.main()

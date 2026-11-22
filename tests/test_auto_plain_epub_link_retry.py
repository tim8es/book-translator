"""Exact-source Auto admission for a repaired plain-EPUB cross-spine link blocker."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))
import book_runner as runner


class PlainEpubLinkRetryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.job_id = "3e9f773e330acfbcd3fe29220a0df517"
        self.source = b"PK\x03\x04original-zip-identity-fixture"
        self.digest = hashlib.sha256(self.source).hexdigest()
        self.revision = "b" * 40
        self.job = self.root / "intake" / "auto" / self.job_id
        self.job.mkdir(parents=True)
        (self.job / "source.epub").write_bytes(self.source)
        self.save(self.job / "request.json", {
            "schema_version": 1, "job_id": self.job_id,
            "source_sha256": self.digest, "target_language": "ru",
        })
        (self.root / "automation").mkdir()
        self.save(self.root / "automation" / "runtime.lock.json", {
            "schema_version": 1, "repository": "tim8es/book-translator",
            "revision": self.revision,
        })
        self.status = {
            "schema_version": 1, "job_id": self.job_id,
            "state": "blocked", "phase": "operator-intervention-required",
            "book_slug": None, "failures": 1,
            "updated_at": (datetime.now(timezone.utc) -
                           timedelta(minutes=10)).isoformat(),
            "last_error": "JobError: canonical CLI rejected operation: ERROR: "
                          "Cannot extract EPUB spine document "
                          "Pizza_With_Pineapples_split_001.html: Unsupported "
                          "anchor destination; only same-document EPUB fragments "
                          "or safe external links are supported\n",
        }
        root_patch = patch.object(runner, "ROOT", self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)

    @staticmethod
    def save(path: Path, obj: dict):
        path.write_text(json.dumps(obj), encoding="utf-8")

    def approve(self):
        self.save(self.job / "auto-preflight-repair.json", {
            "schema_version": 1, "kind": "retry-after-epub-link-parser-fix",
            "job_id": self.job_id, "source_sha256": self.digest,
            "last_error": self.status["last_error"],
            "failed_at": self.status["updated_at"],
            "preflight_retries": self.status.get("preflight_retries", 0),
            "runtime_revision": self.revision,
        })

    def test_exact_approval_reopens_one_known_epub_error(self):
        self.assertFalse(runner.known_epub_preflight_blocker(self.status))
        self.approve()
        self.assertTrue(runner.known_epub_preflight_blocker(self.status))
        self.assertTrue(runner.recoverable_epub_preflight(self.status))
        self.assertFalse(runner.known_epub_preflight_blocker({
            **self.status, "preflight_retries": 1,
        }))

    def test_next_plain_html_leaf_div_blocker_can_be_repaired_once(self):
        self.status["last_error"] = (
            "JobError: canonical CLI rejected operation: ERROR: "
            "EPUB source-preserving extraction blocked: "
            "Pizza_With_Pineapples_split_001.html: Unmapped prose in <div>\n"
        )
        self.status["preflight_retries"] = 1
        self.approve()
        self.assertTrue(runner.known_epub_preflight_blocker(self.status))
        self.assertFalse(runner.known_epub_preflight_blocker({
            **self.status, "preflight_retries": 2,
        }))
        self.assertFalse(runner.known_epub_preflight_blocker({
            **self.status, "last_error": self.status["last_error"].replace(
                "Unmapped prose", "Unsafe script"),
        }))

    def test_reviewed_repair_approval_bypasses_only_timer(self):
        # The original block is too recent for normal retry backoff.
        self.status["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.assertFalse(runner.recoverable_epub_preflight(self.status))
        self.approve()
        self.assertTrue(runner.recoverable_epub_preflight(self.status))
        self.assertFalse(runner.recoverable_epub_preflight({
            **self.status, "preflight_retries": 1,
        }))
        self.assertFalse(runner.recoverable_epub_preflight({
            **self.status, "state": "running",
        }))

    def test_wrong_source_error_or_runtime_is_not_authorized(self):
        self.approve()
        for change in (
            {"job_id": "a" * 32}, {"book_slug": "another-book"},
            {"state": "running"}, {"active": {"role": "translator"}},
            {"workflow_revision": "b" * 40},
            {"last_error": "JobError: unsupported unsafe EPUB source"},
            {"updated_at": datetime.now(timezone.utc).isoformat()},
        ):
            with self.subTest(change=change):
                self.assertFalse(runner.known_epub_preflight_blocker({
                    **self.status, **change,
                }))
        self.save(self.root / "automation" / "runtime.lock.json", {
            "schema_version": 1, "repository": "tim8es/book-translator",
            "revision": "a" * 40,
        })
        self.assertFalse(runner.known_epub_preflight_blocker(self.status))
        (self.job / "source.epub").write_bytes(b"PKchanged-source")
        self.assertFalse(runner.known_epub_preflight_blocker(self.status))

    def test_unsupported_links_and_chatops_handoff_stay_quarantined(self):
        self.approve()
        for error in (
            self.status["last_error"].replace(".html", ".xhtml"),
            self.status["last_error"].replace(
                "Unsupported anchor destination", "Unsupported unsafe URL"),
        ):
            self.assertFalse(runner.known_epub_preflight_blocker({
                **self.status, "last_error": error,
            }))
        self.save(self.job / "chatops-control.json", {
            "schema_version": 1, "job_id": self.job_id,
            "source_sha256": self.digest, "mode": "chatops",
        })
        self.assertFalse(runner.known_epub_preflight_blocker(self.status))


if __name__ == "__main__":
    unittest.main()

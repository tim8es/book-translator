"""Source-bound immediate replay of reviewed EPUB parser repairs."""
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


class ApprovedParserReplayTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.job_id = "ac37df793a0a6b348c42de5376ce0e3d"
        self.source = b"PK\x03\x04fixed-epub-source-test"
        self.digest = hashlib.sha256(self.source).hexdigest()
        self.revision = "264b6b5517c73e9feb57b52d1e4c23ad983db66a"
        self.job = self.root / "intake" / "auto" / self.job_id
        self.job.mkdir(parents=True)
        (self.job / "source.epub").write_bytes(self.source)
        self.write_json(self.job / "request.json", {
            "schema_version": 1, "job_id": self.job_id,
            "source_sha256": self.digest, "target_language": "ru",
        })
        self.control = {
            "schema_version": 1, "job_id": self.job_id,
            "source_sha256": self.digest, "mode": "chatops",
        }
        self.write_json(self.job / "chatops-control.json", self.control)
        lock = self.root / "automation" / "runtime.lock.json"
        lock.parent.mkdir(exist_ok=True)
        self.write_json(lock, {
            "schema_version": 1, "repository": "tim8es/book-translator",
            "revision": self.revision,
        })
        self.status = {
            "schema_version": 1, "job_id": self.job_id,
            "state": "blocked", "phase": "operator-intervention-required",
            "book_slug": None, "preflight_retries": 2, "failures": 3,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "last_error": "JobError: canonical CLI rejected operation: ERROR: "
            "EPUB source-preserving extraction blocked: Alligator_Bites_Never_split_002.xhtml: "
            "Unsupported cross-document hyperlink in prose\n",
        }
        self.patch_root = patch.object(runner, "ROOT", self.root)
        self.patch_root.start()
        self.addCleanup(self.patch_root.stop)

    @staticmethod
    def write_json(path: Path, data: dict) -> None:
        path.write_text(json.dumps(data), encoding="utf-8")

    def authorize(self) -> None:
        self.write_json(self.job / "preflight-repair.json", {
            "schema_version": 1,
            "kind": "retry-after-reviewed-epub-parser-repair",
            "job_id": self.job_id,
            "source_sha256": self.digest,
            "last_error": self.status["last_error"],
            "failed_at": self.status["updated_at"],
            "preflight_retries": self.status["preflight_retries"],
            "runtime_revision": self.revision,
        })

    def test_exact_repair_bypasses_only_epub_timer(self):
        self.assertTrue(runner.known_epub_preflight_blocker(self.status))
        self.assertIsNotNone(runner.epub_preflight_retry_at(self.status))
        self.assertFalse(runner.recoverable_epub_preflight(self.status))
        self.authorize()
        self.assertTrue(runner.approved_epub_repair_retry(self.status))
        self.assertTrue(runner.recoverable_epub_preflight(self.status))
        # A successful replay reserves the existing attempt before extraction.
        self.assertFalse(runner.approved_epub_repair_retry({
            **self.status, "preflight_retries": 3,
        }))
        self.assertFalse(runner.approved_epub_repair_retry({
            **self.status, "updated_at": datetime.now(timezone.utc).isoformat(),
        }))

    def test_explicit_approval_is_bound_to_source_error_and_runtime(self):
        self.authorize()
        for mismatch in (
            {"last_error": "Unsupported bad input"},
            {"book_slug": "an-existing-book"},
            {"state": "running"},
            {"phase": "provider-backoff"},
            {"active": {"role": "translator"}},
            {"source_sha256": self.digest},
            {"preflight_retries": 5},
        ):
            with self.subTest(mismatch=mismatch):
                self.assertFalse(runner.approved_epub_repair_retry({
                    **self.status, **mismatch,
                }))
        (self.job / "source.epub").write_bytes(b"PKnot-the-original")
        self.assertFalse(runner.approved_epub_repair_retry(self.status))

    def test_stale_runtime_or_chatops_control_is_rejected(self):
        self.authorize()
        self.write_json(self.root / "automation" / "runtime.lock.json", {
            "schema_version": 1, "repository": "tim8es/book-translator",
            "revision": "1" * 40,
        })
        self.assertFalse(runner.approved_epub_repair_retry(self.status))
        self.write_json(self.root / "automation" / "runtime.lock.json", {
            "schema_version": 1, "repository": "tim8es/book-translator",
            "revision": self.revision,
        })
        self.write_json(self.job / "chatops-control.json", {
            **self.control, "source_sha256": "a" * 64,
        })
        self.assertFalse(runner.approved_epub_repair_retry(self.status))

    def test_approval_never_bypasses_retries_exhausted_or_other_errors(self):
        self.authorize()
        self.assertFalse(runner.approved_epub_repair_retry({
            **self.status, "preflight_retries": len(runner.AUTO_RETRY_DELAYS_SECONDS),
        }))
        self.assertFalse(runner.approved_epub_repair_retry({
            **self.status, "last_error": "JobError: provider HTTP 429",
        }))
        self.assertFalse(runner.approved_epub_repair_retry({
            **self.status, "last_error": self.status["last_error"].replace(
                "cross-document", "javascript"),
        }))
        stale = dict(self.status)
        stale["updated_at"] = (datetime.now(timezone.utc) -
                              timedelta(days=30)).isoformat()
        self.assertFalse(runner.approved_epub_repair_retry(stale))


if __name__ == "__main__":
    unittest.main()

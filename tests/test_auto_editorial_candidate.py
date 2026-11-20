"""Auto must not retranscribe user-approved edits or invent PASS."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "automation"))
import book_runner as worker


class OperatorEditorialCandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.slug = "operator-smoke"
        self.book = self.root / "books" / self.slug
        (self.book / ".workflow").mkdir(parents=True)
        (self.book / "extracted").mkdir()
        (self.book / "translated").mkdir()
        self.source = b"An original sentence.\n"
        self.translation = "Исправленное предложение.\n".encode("utf-8")
        self.old = hashlib.sha256("Старый перевод.\n".encode("utf-8")).hexdigest()
        self.revision = "a" * 40
        self.oldrevision = "b" * 40
        self.unit = "chapter-000001"
        self.book.joinpath("extracted/001.md").write_bytes(self.source)
        self.book.joinpath("translated/001.md").write_bytes(self.translation)
        self.chapter = {"unit_id": self.unit, "number": 1, "status": "extracted",
                        "source_path": "extracted/001.md",
                        "translation_path": "translated/001.md"}
        source_sha = hashlib.sha256(self.source).hexdigest()
        blob_sha = hashlib.sha1(
            b"blob " + str(len(self.translation)).encode("ascii") + b"\0"
            + self.translation).hexdigest()
        worker.store_json(self.book / "metadata.json", {
            "source": {"sha256": "c" * 64},
            "workflow": {"resolved_revision": self.revision},
        })
        self.plan = {
            "schema_version": 1, "kind": "reopen-edited-reviewed-units",
            "book_slug": self.slug,
            "source_workflow_revision": self.oldrevision,
            "target_workflow_revision": self.revision,
            "source_book_sha256": "c" * 64,
            "baseline_progress_blob_sha": "d" * 40,
            "baseline_ledger_blob_sha": "e" * 40,
            "units": [{
                "unit_id": self.unit,
                "source_sha256": source_sha,
                "original_translation_sha256": self.old,
                "candidate_translation_blob_sha": blob_sha,
                "original_pass_record_id": "1" * 32,
            }],
        }
        worker.store_json(self.book / ".workflow/editorial-reopen.json", self.plan)
        worker.store_json(self.book / "review-ledger.json", {
            "records": [{
                "unit_id": self.unit,
                "record_id": "1" * 32,
                "outcome": "PASS",
                "source_sha256": source_sha,
                "translation_sha256": self.old,
                "workflow_revision": self.oldrevision,
            }],
        })

    def test_exact_candidate_can_be_accepted_without_model_retranslation(self):
        with patch.object(worker, "ROOT", self.root):
            self.assertEqual(
                worker.verified_editorial_candidate(self.slug, self.chapter),
                self.translation.decode("utf-8"),
            )
            self.assertIsNone(worker.verified_editorial_candidate(
                self.slug, {**self.chapter, "status": "translated"}))

    def test_changed_candidate_or_source_is_blocked(self):
        with patch.object(worker, "ROOT", self.root):
            (self.book / "translated/001.md").write_bytes(b"unauthorized candidate")
            with self.assertRaisesRegex(worker.JobError, "no longer matches"):
                worker.verified_editorial_candidate(self.slug, self.chapter)
            (self.book / "translated/001.md").write_bytes(self.translation)
            (self.book / "extracted/001.md").write_bytes(b"unauthorized original")
            with self.assertRaisesRegex(worker.JobError, "no longer matches"):
                worker.verified_editorial_candidate(self.slug, self.chapter)

    def test_mutated_plan_or_pass_must_fail_closed(self):
        with patch.object(worker, "ROOT", self.root):
            self.plan["target_workflow_revision"] = "f" * 40
            worker.store_json(self.book / ".workflow/editorial-reopen.json", self.plan)
            with self.assertRaisesRegex(worker.JobError, "invalid operator-approved"):
                worker.verified_editorial_candidate(self.slug, self.chapter)
            self.plan["target_workflow_revision"] = self.revision
            worker.store_json(self.book / ".workflow/editorial-reopen.json", self.plan)
            ledger = json.loads((self.book / "review-ledger.json").read_text())
            ledger["records"][0]["record_id"] = "9" * 32
            worker.store_json(self.book / "review-ledger.json", ledger)
            with self.assertRaisesRegex(worker.JobError, "historical PASS"):
                worker.verified_editorial_candidate(self.slug, self.chapter)


if __name__ == "__main__":
    unittest.main()

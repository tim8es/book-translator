"""Regression tests for an explicit, non-forging reviewed-edit reopen transition."""
from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from workflow.editorial_reopen import (
    PLAN, EditorialReopenError, _blob_sha, reopen_reviewed,
)
from workflow.upgrade_evidence import BRIDGE_PATH, fingerprint

OLD = "a" * 40
NEW = "b" * 40
BOOK_HASH = "c" * 64
SLUG = "editorial-smoke"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class EditorialReopenTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.book = self.root / "books" / SLUG
        self.book.mkdir(parents=True)
        self.meta = {
            "schema_version": 1, "title": "Editorial smoke",
            "source_language": "en", "target_language": "ru",
            "source_format": "epub", "source_file": "source.epub",
            "chapter_count": 2,
            "workflow": {
                "repository": "https://github.com/tim8es/book-translator",
                "requested_ref": NEW, "resolved_revision": NEW,
                "review_evidence": "review-ledger-v1",
            },
            "source": {
                "storage_mode": "embedded", "filename": "source.epub",
                "size_bytes": 1, "sha256": BOOK_HASH,
                "revision_id": "source-000001",
            },
        }
        self.progress = {"schema_version": 1, "book_slug": SLUG, "chapters": []}
        self.ledger = {"schema_version": 1, "book_slug": SLUG,
                       "next_sequence": 3, "records": []}
        self.plan = {
            "schema_version": 1, "kind": "reopen-edited-reviewed-units",
            "book_slug": SLUG, "source_workflow_revision": OLD,
            "target_workflow_revision": NEW, "source_book_sha256": BOOK_HASH,
            "baseline_progress_blob_sha": "",
            "baseline_ledger_blob_sha": "", "units": [],
        }
        self.bridge = {
            "schema_version": 1,
            "kind": "adopt-unchanged-historical-translation-and-review",
            "book_slug": SLUG, "source_workflow_revision": OLD,
            "target_workflow_revision": NEW,
            "source_book_sha256": BOOK_HASH,
            "pre_upgrade_commit": "d" * 40,
            "pre_upgrade_progress_blob_sha": "",
            "pre_upgrade_review_ledger_blob_sha": "", "entries": [],
        }
        for number in (1, 2):
            uid = f"chapter-{number:06d}"
            original = f"Original {number}\n".encode()
            accepted = f"Перевод {number}\n".encode()
            candidate = (f"Исправленный перевод {number}\n".encode()
                         if number == 1 else accepted)
            source_file = f"extracted/{number:03}.md"
            target_file = f"translated/{number:03}.md"
            self.write_bytes(source_file, original)
            self.write_bytes(target_file, candidate)
            acceptance = {
                "schema_version": 1, "unit_id": uid,
                "claim_id": f"{number}" * 32,
                "claim_revision": "original-state",
                "role": "translator", "session_id": f"translator-{number}",
                "base_revision": "base", "base_commit": None,
                "workflow_revision": OLD,
                "accepted_from_progress_revision": "base",
                "shared_state_revisions": None,
                "source_sha256": sha(original),
                "translation_sha256": sha(accepted),
                "accepted_at": "2026-11-19T01:00:00Z",
            }
            chapter = {
                "unit_id": uid, "number": number,
                "title": f"Section {number}", "slug": f"section-{number}",
                "source_path": source_file, "translation_path": target_file,
                "status": "reviewed", "translation_acceptance": acceptance,
            }
            self.progress["chapters"].append(chapter)
            record = {
                "record_id": f"{number + 2}" * 32, "sequence": number,
                "unit_id": uid, "outcome": "PASS",
                "source_sha256": sha(original),
                "translation_sha256": sha(accepted),
                "workflow_revision": OLD,
                "review_contract_revision": "docs/TRANSLATION.md@" + OLD,
                "reviewer_session_id": f"reviewer-{number}",
                "reviewed_at": "2026-11-19T02:00:00Z",
                "state_revision": "original",
                "review_commit": None, "correction_round": 0,
                "supersedes_record_id": None,
            }
            self.ledger["records"].append(record)
            if number == 1:
                self.plan["units"].append({
                    "unit_id": uid,
                    "source_sha256": sha(original),
                    "original_translation_sha256": sha(accepted),
                    "candidate_translation_blob_sha": _blob_sha(candidate),
                    "original_pass_record_id": record["record_id"],
                })
            else:
                self.bridge["entries"].append({
                    "unit_id": uid,
                    "source_sha256": sha(original),
                    "translation_sha256": sha(accepted),
                    "acceptance_fingerprint": fingerprint(acceptance),
                    "pass_record_id": record["record_id"],
                    "pass_record_fingerprint": fingerprint(record),
                })
        self.write_json("metadata.json", self.meta)
        self.write_json("progress.json", self.progress)
        self.write_json("review-ledger.json", self.ledger)
        self.plan["baseline_progress_blob_sha"] = _blob_sha(
            (self.book / "progress.json").read_bytes())
        self.plan["baseline_ledger_blob_sha"] = _blob_sha(
            (self.book / "review-ledger.json").read_bytes())
        self.bridge["pre_upgrade_progress_blob_sha"] = self.plan["baseline_progress_blob_sha"]
        self.bridge["pre_upgrade_review_ledger_blob_sha"] = self.plan["baseline_ledger_blob_sha"]
        self.write_json(PLAN, self.plan)
        self.write_json(BRIDGE_PATH, self.bridge)

    def write_bytes(self, relative: str, data: bytes) -> None:
        path = self.book / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def write_json(self, relative: str, data: dict) -> None:
        self.write_bytes(relative, (
            json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        ).encode())

    def test_dry_run_preserves_state_and_real_reopen_demotes_only_one(self):
        before = (self.book / "progress.json").read_bytes()
        ledger_before = (self.book / "review-ledger.json").read_bytes()
        proposed = reopen_reviewed(self.root, SLUG, dry_run=True)
        self.assertEqual(proposed["reopened"], ["chapter-000001"])
        self.assertEqual(proposed["reused_unchanged"], 1)
        self.assertEqual((self.book / "progress.json").read_bytes(), before)
        result = reopen_reviewed(self.root, SLUG)
        self.assertEqual(result["reopened"], ["chapter-000001"])
        current = json.loads((self.book / "progress.json").read_text())
        self.assertEqual(current["chapters"][0]["status"], "extracted")
        self.assertNotIn("translation_acceptance", current["chapters"][0])
        self.assertEqual(current["chapters"][1], self.progress["chapters"][1])
        self.assertEqual((self.book / "review-ledger.json").read_bytes(), ledger_before)
        with self.assertRaisesRegex(EditorialReopenError, "baseline changed"):
            reopen_reviewed(self.root, SLUG)

    def test_candidate_change_blocks_state_write(self):
        before = (self.book / "progress.json").read_bytes()
        self.write_bytes("translated/001.md", b"tampered candidate\n")
        with self.assertRaisesRegex(EditorialReopenError, "selected unit evidence mismatch"):
            reopen_reviewed(self.root, SLUG)
        self.assertEqual((self.book / "progress.json").read_bytes(), before)

    def test_bridge_tampering_blocks_reopen(self):
        before = (self.book / "progress.json").read_bytes()
        bad = copy.deepcopy(self.bridge)
        bad["entries"][0]["acceptance_fingerprint"] = "f" * 64
        self.write_json(BRIDGE_PATH, bad)
        with self.assertRaisesRegex(EditorialReopenError, "not bridged"):
            reopen_reviewed(self.root, SLUG)
        self.assertEqual((self.book / "progress.json").read_bytes(), before)

    def test_missing_or_mutated_historical_pass_blocks_reopen(self):
        before = (self.book / "progress.json").read_bytes()
        bad = copy.deepcopy(self.ledger)
        bad["records"][0]["outcome"] = "CORRECTIONS_REQUIRED"
        self.write_json("review-ledger.json", bad)
        with self.assertRaisesRegex(EditorialReopenError, "ledger baseline changed"):
            reopen_reviewed(self.root, SLUG)
        self.assertEqual((self.book / "progress.json").read_bytes(), before)

    def test_unlisted_edit_is_rejected(self):
        before = (self.book / "progress.json").read_bytes()
        self.write_bytes("translated/002.md", b"unauthorized change")
        with self.assertRaisesRegex(EditorialReopenError, "unselected translation changed"):
            reopen_reviewed(self.root, SLUG)
        self.assertEqual((self.book / "progress.json").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()

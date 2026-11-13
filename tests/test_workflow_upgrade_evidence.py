"""Regression gates for explicit cross-revision reuse of unchanged book evidence."""

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from workflow.filesystem import FilesystemStorage
from workflow.repository import LoadedDocument, WorkflowStateRepository
from workflow.review_cli import ReviewCliError, _require_current_translation_acceptance
from workflow.reviews import ReviewEvidenceError, ReviewLedgerManager
from workflow.upgrade_evidence import (
    BRIDGE_PATH, EvidenceUpgradeError, adopted_acceptance, adopted_pass,
    fingerprint, read_upgrade_bridge,
)

OLD = "a" * 40
NEW = "b" * 40
SOURCE = b"Alpha was here.\n"
TRANSLATION = "Альфа здесь.\n".encode()
HASH_SOURCE = hashlib.sha256(SOURCE).hexdigest()
HASH_TARGET = hashlib.sha256(TRANSLATION).hexdigest()


class WorkflowUpgradeEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.storage = FilesystemStorage(self.root)
        self.repo = WorkflowStateRepository(self.storage)
        self.meta = {
            "workflow": {"resolved_revision": NEW, "review_evidence": "review-ledger-v1"},
            "source": {"sha256": "c" * 64},
        }
        self.acceptance = {
            "unit_id": "chapter-000001", "role": "translator",
            "workflow_revision": OLD,
            "source_sha256": HASH_SOURCE,
            "translation_sha256": HASH_TARGET,
            "claim_id": "1" * 32,
        }
        self.chapter = {
            "number": 1, "unit_id": "chapter-000001",
            "source_path": "extracted/one.md",
            "translation_path": "translated/one.md", "status": "reviewed",
            "translation_acceptance": self.acceptance,
        }
        self.old_pass = {
            "record_id": "2" * 32, "unit_id": "chapter-000001",
            "outcome": "PASS", "workflow_revision": OLD,
            "review_contract_revision": "docs/TRANSLATION.md@" + OLD,
            "source_sha256": HASH_SOURCE,
            "translation_sha256": HASH_TARGET,
            "sequence": 1,
        }
        self.bridge = {
            "schema_version": 1,
            "kind": "adopt-unchanged-historical-translation-and-review",
            "book_slug": "example",
            "source_workflow_revision": OLD,
            "target_workflow_revision": NEW,
            "source_book_sha256": "c" * 64,
            "pre_upgrade_commit": "d" * 40,
            "pre_upgrade_progress_blob_sha": "e" * 40,
            "pre_upgrade_review_ledger_blob_sha": "f" * 40,
            "entries": [{
                "unit_id": "chapter-000001",
                "source_sha256": HASH_SOURCE,
                "translation_sha256": HASH_TARGET,
                "acceptance_fingerprint": fingerprint(self.acceptance),
                "pass_record_id": self.old_pass["record_id"],
                "pass_record_fingerprint": fingerprint(self.old_pass),
            }],
        }
        for name, content in [
            ("extracted/one.md", SOURCE),
            ("translated/one.md", TRANSLATION),
        ]:
            location = self.root / name
            location.parent.mkdir(parents=True, exist_ok=True)
            location.write_bytes(content)
        self.write_bridge()
        self.progress = {"book_slug": "example", "chapters": [self.chapter]}

    def write_bridge(self):
        path = self.root / BRIDGE_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.bridge), encoding="utf-8")

    def manager(self):
        repo = Mock()
        repo.storage = self.storage
        repo.read.return_value = LoadedDocument(
            data={"book_slug": "example", "records": [self.old_pass]}, version="ledger",
        )
        return ReviewLedgerManager(repo, artifact_reader=lambda p: (self.root / p).read_bytes())

    def test_historical_review_stays_original_and_passes_exact_bytes(self):
        _require_current_translation_acceptance(self.progress, self.meta, 1, self.repo)
        current = self.manager().resolve_unit(self.progress, self.meta, 1)
        self.assertEqual(current.state, "pass")
        self.assertEqual(current.current_record, self.old_pass)
        self.assertEqual(current.current_record["workflow_revision"], OLD)
        self.assertNotEqual(current.current_record["workflow_revision"], NEW)

    def test_without_bridge_old_acceptance_is_rejected(self):
        (self.root / BRIDGE_PATH).unlink()
        with self.assertRaisesRegex(ReviewCliError, "another workflow revision"):
            _require_current_translation_acceptance(self.progress, self.meta, 1, self.repo)
        self.assertEqual(self.manager().resolve_unit(self.progress, self.meta, 1).state, "stale")

    def test_new_workflow_pin_or_book_source_drift_rejected(self):
        for changed in [
            {"workflow": {"resolved_revision": "9" * 40}},
            {"source": {"sha256": "9" * 64}},
        ]:
            meta = copy.deepcopy(self.meta)
            meta.update(changed)
            with self.assertRaises(EvidenceUpgradeError):
                read_upgrade_bridge(self.storage, meta)

    def test_changed_acceptance_or_provenance_must_not_adopt(self):
        self.chapter["translation_acceptance"] = {
            **self.acceptance, "translation_sha256": "0" * 64,
        }
        self.assertIsNone(adopted_acceptance(self.storage, self.meta, self.chapter))
        self.chapter["translation_acceptance"] = self.acceptance
        self.bridge["entries"][0]["acceptance_fingerprint"] = "9" * 64
        self.write_bridge()
        self.assertIsNone(adopted_acceptance(self.storage, self.meta, self.chapter))

    def test_modified_translation_or_review_record_is_never_pass(self):
        self.assertEqual(
            self.manager().resolve_unit(
                self.progress, self.meta, 1, chapter_record={
                    **self.chapter, "translation_path": "missing.md",
                },
            ).state, "untranslated",
        )
        # The old review is not silently re-labelled after an edited translation.
        changed = self.root / "translated/one.md"
        changed.write_bytes(b"edited target\n")
        self.assertEqual(self.manager().resolve_unit(self.progress, self.meta, 1).state, "stale")
        changed.write_bytes(TRANSLATION)
        altered_record = {**self.old_pass, "outcome": "CORRECTIONS_REQUIRED"}
        self.assertIsNone(adopted_pass(
            self.storage, self.meta, self.chapter,
            [altered_record], HASH_SOURCE, HASH_TARGET,
        ))
        later_record = {**self.old_pass, "record_id": "3" * 32, "sequence": 2}
        self.assertIsNone(adopted_pass(
            self.storage, self.meta, self.chapter,
            [self.old_pass, later_record], HASH_SOURCE, HASH_TARGET,
        ))

    def test_missing_or_fabricated_prior_pass_must_not_promote(self):
        self.bridge["entries"][0]["pass_record_id"] = None
        self.bridge["entries"][0]["pass_record_fingerprint"] = None
        self.write_bridge()
        self.assertEqual(self.manager().resolve_unit(self.progress, self.meta, 1).state, "stale")
        with self.assertRaisesRegex(ReviewCliError, "another workflow revision"):
            _require_current_translation_acceptance(self.progress, self.meta, 1)
        self.bridge["entries"][0]["pass_record_id"] = "2" * 32
        self.bridge["entries"][0]["pass_record_fingerprint"] = "0" * 64
        self.write_bridge()
        self.assertEqual(self.manager().resolve_unit(self.progress, self.meta, 1).state, "stale")

    def test_duplicate_units_or_invalid_json_fail_closed(self):
        self.bridge["entries"].append(copy.deepcopy(self.bridge["entries"][0]))
        self.write_bridge()
        with self.assertRaisesRegex(EvidenceUpgradeError, "duplicate"):
            read_upgrade_bridge(self.storage, self.meta)
        (self.root / BRIDGE_PATH).write_bytes(b'{"schema_version": 1, "schema_version": 1}')
        with self.assertRaises(EvidenceUpgradeError):
            read_upgrade_bridge(self.storage, self.meta)


if __name__ == "__main__":
    unittest.main()

import copy
import hashlib
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from workflow_v2.filesystem import FilesystemStorage
from workflow_v2.repository import WorkflowStateRepository
from workflow_v2.schemas import SchemaKind
from workflow_v2.storage import StorageNotFound

try:
    from workflow_v2.finalize import (
        FINALIZATION_PATH,
        FinalizationBlocked,
        FinalizationConflict,
        FinalizationManager,
        build_reviewed_candidate,
        sha256_bytes,
    )
except (ImportError, ModuleNotFoundError):
    FINALIZATION_PATH = ".workflow/finalization.json"
    FinalizationBlocked = None
    FinalizationConflict = None
    FinalizationManager = None
    build_reviewed_candidate = None
    sha256_bytes = None


NOW = datetime(2026, 9, 6, 20, 30, 0, tzinfo=timezone.utc)
WORKFLOW_REVISION = "workflow-revision-12"
CONTRACT_REVISION = f"docs/TRANSLATION.md@{WORKFLOW_REVISION}"


def digest(content):
    return hashlib.sha256(content).hexdigest()


class CountingStorage:
    def __init__(self, delegate):
        self.delegate = delegate
        self.progress_writes = 0

    def read(self, path):
        return self.delegate.read(path)

    def create_if_absent(self, path, content):
        return self.delegate.create_if_absent(path, content)

    def write_if_version(self, path, content, expected_version):
        if path == "progress.json":
            self.progress_writes += 1
        return self.delegate.write_if_version(path, content, expected_version)

    def delete_if_version(self, path, expected_version):
        return self.delegate.delete_if_version(path, expected_version)

    def list(self, prefix=""):
        return self.delegate.list(prefix)


class WorkflowV2FinalizeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.base_storage = FilesystemStorage(self.root)
        self.storage = CountingStorage(self.base_storage)
        self.repository = WorkflowStateRepository(self.storage)
        self.artifacts = {
            "extracted/001.md": b"Source one\n",
            "translated/001.md": "Перевод один\n".encode("utf-8"),
            "extracted/002.md": b"Source two\n",
            "translated/002.md": "Перевод два\n".encode("utf-8"),
        }
        self.metadata = {
            "schema_version": 1,
            "title": "Demo",
            "author": "Author",
            "source_language": "en",
            "target_language": "ru",
            "source_format": "markdown",
            "source_file": "demo.md",
            "chapter_count": 2,
            "workflow": {
                "repository": "https://github.com/tim8es/book-translator",
                "requested_ref": "refactor/workflow-engine-v2",
                "resolved_revision": WORKFLOW_REVISION,
                "review_evidence": "review-ledger-v1",
            },
        }
        self.progress = {
            "schema_version": 1,
            "book_slug": "demo",
            "chapters": [
                {
                    "number": 1,
                    "title": "One",
                    "slug": "one",
                    "source_path": "extracted/001.md",
                    "translation_path": "translated/001.md",
                    "status": "translated",
                },
                {
                    "number": 2,
                    "title": "Two",
                    "slug": "two",
                    "source_path": "extracted/002.md",
                    "translation_path": "translated/002.md",
                    "status": "translated",
                },
            ],
        }
        self.repository.create("metadata.json", SchemaKind.METADATA, self.metadata)
        self.base_progress_revision = self.repository.create(
            "progress.json", SchemaKind.PROGRESS, self.progress
        )
        self.repository.create("review-ledger.json", SchemaKind.REVIEW_LEDGER, self.pass_ledger())
        self.preflight_value = ([], {"state": "verified", "storage_mode": "embedded"})

    def tearDown(self):
        self.tmp.cleanup()

    def require_finalize_api(self):
        self.assertIsNotNone(FinalizationManager, "workflow_v2.finalize is not implemented")
        self.assertIsNotNone(FinalizationBlocked, "FinalizationBlocked is not implemented")
        self.assertIsNotNone(FinalizationConflict, "FinalizationConflict is not implemented")
        self.assertIsNotNone(build_reviewed_candidate, "build_reviewed_candidate is not implemented")
        self.assertIsNotNone(sha256_bytes, "sha256_bytes is not implemented")

    def pass_ledger(self):
        records = []
        for sequence, number in enumerate((1, 2), start=1):
            records.append(
                {
                    "record_id": f"{number}" * 32,
                    "sequence": sequence,
                    "unit_id": f"chapter-{number:06d}",
                    "outcome": "PASS",
                    "source_sha256": digest(self.artifacts[f"extracted/{number:03d}.md"]),
                    "translation_sha256": digest(self.artifacts[f"translated/{number:03d}.md"]),
                    "workflow_revision": WORKFLOW_REVISION,
                    "review_contract_revision": CONTRACT_REVISION,
                    "reviewer_session_id": f"reviewer-{number}",
                    "reviewed_at": f"2026-09-06T19:0{number}:00Z",
                    "state_revision": "review-state",
                    "review_commit": f"review-commit-{number}",
                    "correction_round": 0,
                    "supersedes_record_id": None,
                }
            )
        return {
            "schema_version": 1,
            "book_slug": "demo",
            "next_sequence": 3,
            "records": records,
        }

    def read_artifact(self, path):
        try:
            return self.artifacts[path]
        except KeyError as exc:
            raise FileNotFoundError(path) from exc

    def manager(self):
        return FinalizationManager(
            self.repository,
            artifact_reader=self.read_artifact,
            preflight=lambda: self.preflight_value,
            now=lambda: NOW,
            id_factory=lambda: "a" * 32,
        )

    def assert_no_finalize_mutation(self, progress_before):
        self.assertEqual(self.base_storage.read("progress.json").content, progress_before)
        self.assertEqual(self.storage.progress_writes, 0)
        with self.assertRaises(StorageNotFound):
            self.base_storage.read(FINALIZATION_PATH)

    def replace_ledger(self, ledger):
        current = self.repository.read("review-ledger.json", SchemaKind.REVIEW_LEDGER)
        self.repository.write_if_version(
            "review-ledger.json", SchemaKind.REVIEW_LEDGER, ledger, current.version
        )

    def test_preflight_rejects_structure_and_corpus_without_mutation(self):
        self.require_finalize_api()
        for name, preflight in (
            ("structural", (["broken structure"], {"state": "verified"})),
            ("unsealed", ([], {"state": "unsealed"})),
            ("invalid", ([], {"state": "invalid", "error": "hash mismatch"})),
        ):
            with self.subTest(name=name):
                self.preflight_value = preflight
                before = self.base_storage.read("progress.json").content
                with self.assertRaises(FinalizationBlocked):
                    self.manager().finalize(session_id="finalizer")
                self.assert_no_finalize_mutation(before)

    def test_preflight_rejects_missing_review_and_translation_without_mutation(self):
        self.require_finalize_api()
        empty = {"schema_version": 1, "book_slug": "demo", "next_sequence": 1, "records": []}
        self.replace_ledger(empty)
        before = self.base_storage.read("progress.json").content
        with self.assertRaises(FinalizationBlocked):
            self.manager().finalize(session_id="finalizer")
        self.assert_no_finalize_mutation(before)

        self.replace_ledger(self.pass_ledger())
        del self.artifacts["translated/002.md"]
        with self.assertRaises(FinalizationBlocked):
            self.manager().finalize(session_id="finalizer")
        self.assert_no_finalize_mutation(before)

    def test_active_claim_blocks_before_progress_mutation_and_claim_remains(self):
        self.require_finalize_api()
        self.repository.create(
            ".workflow/claims/chapter-000001.json",
            SchemaKind.CLAIM,
            {
                "schema_version": 1,
                "claim_id": "d" * 32,
                "unit_id": "chapter-000001",
                "role": "reviewer",
                "session_id": "reviewer-live",
                "base_revision": self.base_progress_revision,
                "base_commit": None,
                "workflow_revision": WORKFLOW_REVISION,
                "claimed_at": "2026-09-06T20:00:00Z",
                "expires_at": "2026-09-06T21:00:00Z",
            },
        )
        before = self.base_storage.read("progress.json").content
        with self.assertRaises(FinalizationBlocked):
            self.manager().finalize(session_id="finalizer")
        self.assert_no_finalize_mutation(before)
        self.assertEqual(len(self.base_storage.list(".workflow/claims")), 1)

    def test_success_promotes_all_chapters_with_one_progress_cas(self):
        self.require_finalize_api()
        result = self.manager().finalize(session_id="finalizer")
        progress = self.repository.read("progress.json", SchemaKind.PROGRESS)
        self.assertEqual([item["status"] for item in progress.data["chapters"]], ["reviewed", "reviewed"])
        self.assertEqual(self.storage.progress_writes, 1)
        self.assertTrue(result.promoted)
        self.assertFalse(result.recovered)
        self.assertEqual(result.progress_revision, progress.version)

        marker = self.repository.read(FINALIZATION_PATH, SchemaKind.FINALIZATION_LOCK)
        self.assertEqual(marker.data["phase"], "promoted")
        self.assertEqual(marker.data["promoted_progress_revision"], progress.version)
        candidate = build_reviewed_candidate(self.progress)
        expected_hash = sha256_bytes(
            self.repository.serialize("progress.json", SchemaKind.PROGRESS, candidate)
        )
        self.assertEqual(marker.data["candidate_progress_sha256"], expected_hash)

    def test_recovery_is_idempotent_and_incompatible_progress_fails_closed(self):
        self.require_finalize_api()
        candidate = build_reviewed_candidate(self.progress)
        candidate_bytes = self.repository.serialize("progress.json", SchemaKind.PROGRESS, candidate)
        candidate_hash = sha256_bytes(candidate_bytes)
        marker = {
            "schema_version": 1,
            "lock_id": "e" * 32,
            "book_slug": "demo",
            "workflow_revision": WORKFLOW_REVISION,
            "base_progress_revision": self.base_progress_revision,
            "candidate_progress_sha256": candidate_hash,
            "phase": "preparing",
            "promoted_progress_revision": None,
            "session_id": "crashed-finalizer",
            "started_at": "2026-09-06T20:20:00Z",
        }
        self.repository.create(FINALIZATION_PATH, SchemaKind.FINALIZATION_LOCK, marker)
        self.base_storage.write_if_version("progress.json", candidate_bytes, self.base_progress_revision)
        self.storage.progress_writes = 0

        recovered = self.manager().finalize(session_id="recovery-session")
        self.assertTrue(recovered.recovered)
        self.assertFalse(recovered.promoted)
        self.assertEqual(self.storage.progress_writes, 0)
        current = self.base_storage.read("progress.json")
        marker_after = self.repository.read(FINALIZATION_PATH, SchemaKind.FINALIZATION_LOCK)
        self.assertEqual(marker_after.data["phase"], "promoted")
        self.assertEqual(marker_after.data["promoted_progress_revision"], current.version)

        again = self.manager().finalize(session_id="recovery-session")
        self.assertTrue(again.recovered)
        self.assertEqual(self.storage.progress_writes, 0)

        durable = self.repository.read("progress.json", SchemaKind.PROGRESS)
        unexpected = copy.deepcopy(durable.data)
        unexpected["chapters"][0]["status"] = "translated"
        self.repository.write_if_version("progress.json", SchemaKind.PROGRESS, unexpected, durable.version)
        self.storage.progress_writes = 0
        with self.assertRaises(FinalizationConflict):
            self.manager().finalize(session_id="recovery-session")
        self.assertEqual(self.storage.progress_writes, 0)
        self.assertEqual(
            self.repository.read(FINALIZATION_PATH, SchemaKind.FINALIZATION_LOCK).data["phase"],
            "promoted",
        )


if __name__ == "__main__":
    unittest.main()

import hashlib
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from workflow_v2.filesystem import FilesystemStorage
from workflow_v2.repository import WorkflowStateRepository
from workflow_v2.schemas import SchemaKind

try:
    from workflow_v2.reviews import (
        ReviewClaimError,
        ReviewConflict,
        ReviewEvidenceError,
        ReviewLedgerManager,
    )
except (ImportError, ModuleNotFoundError):
    ReviewClaimError = None
    ReviewConflict = None
    ReviewEvidenceError = None
    ReviewLedgerManager = None


NOW = datetime(2026, 9, 6, 0, 0, 0, tzinfo=timezone.utc)
WORKFLOW_REVISION = "0123456789abcdef"


class WorkflowV2ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.storage = FilesystemStorage(self.root)
        self.repository = WorkflowStateRepository(self.storage)
        self.artifacts = {
            "extracted/001-one.md": b"# One\n\nSource.\n",
            "translated/001-one.md": "# Один\n\nПеревод.\n".encode("utf-8"),
            "extracted/002-two.md": b"# Two\n\nSource two.\n",
        }
        self.metadata = {
            "schema_version": 1,
            "title": "Example",
            "author": "Author",
            "source_language": "en",
            "target_language": "ru",
            "source_format": "markdown",
            "source_file": "example.md",
            "chapter_count": 2,
            "imported_at": "2026-09-05T20:00:00Z",
            "workflow": {
                "repository": "https://github.com/tim8es/book-translator",
                "requested_ref": "refactor/workflow-engine-v2",
                "resolved_revision": WORKFLOW_REVISION,
                "review_evidence": "review-ledger-v1",
            },
        }
        self.progress = {
            "schema_version": 1,
            "book_slug": "example",
            "chapters": [
                {
                    "number": 1,
                    "title": "One",
                    "slug": "one",
                    "source_path": "extracted/001-one.md",
                    "translation_path": "translated/001-one.md",
                    "status": "translated",
                },
                {
                    "number": 2,
                    "title": "Two",
                    "slug": "two",
                    "source_path": "extracted/002-two.md",
                    "translation_path": "translated/002-two.md",
                    "status": "extracted",
                },
            ],
        }
        self.progress_revision = self.repository.create("progress.json", SchemaKind.PROGRESS, self.progress)
        self.repository.create(
            "review-ledger.json",
            SchemaKind.REVIEW_LEDGER,
            {"schema_version": 1, "book_slug": "example", "next_sequence": 1, "records": []},
        )
        self.ids = iter(f"{value:032x}" for value in range(100, 200))

    def tearDown(self):
        self.tmp.cleanup()

    def require_api(self):
        self.assertIsNotNone(ReviewLedgerManager, "workflow_v2.reviews ReviewLedgerManager is not implemented")
        self.assertIsNotNone(ReviewEvidenceError, "workflow_v2.reviews ReviewEvidenceError is not implemented")
        self.assertIsNotNone(ReviewClaimError, "workflow_v2.reviews ReviewClaimError is not implemented")
        self.assertIsNotNone(ReviewConflict, "workflow_v2.reviews ReviewConflict is not implemented")

    def artifact_reader(self, path):
        if path not in self.artifacts:
            raise FileNotFoundError(path)
        return self.artifacts[path]

    def manager(self, *, repository=None, id_factory=None):
        self.require_api()
        return ReviewLedgerManager(
            repository or self.repository,
            artifact_reader=self.artifact_reader,
            now=lambda: NOW,
            id_factory=id_factory or (lambda: next(self.ids)),
        )

    def claim(self, *, role="reviewer", session_id="reviewer-a", workflow_revision=WORKFLOW_REVISION, expired=False):
        claimed_at = "2026-09-05T22:00:00Z" if expired else "2026-09-05T23:30:00Z"
        expires_at = "2026-09-05T23:00:00Z" if expired else "2026-09-06T01:00:00Z"
        claim = {
            "schema_version": 1,
            "claim_id": "c" * 32,
            "unit_id": "chapter-000001",
            "role": role,
            "session_id": session_id,
            "base_revision": self.progress_revision,
            "base_commit": None,
            "workflow_revision": workflow_revision,
            "claimed_at": claimed_at,
            "expires_at": expires_at,
        }
        return self.repository.create(
            ".workflow/claims/chapter-000001.json",
            SchemaKind.CLAIM,
            claim,
        )

    def record(self, manager, outcome="PASS", session_id="reviewer-a"):
        return manager.record(
            self.progress,
            self.progress_revision,
            self.metadata,
            1,
            outcome=outcome,
            reviewer_session_id=session_id,
            review_commit=None,
        )

    def test_pass_binds_exact_source_and_translation_bytes_and_restores_when_bytes_restore(self):
        self.claim()
        manager = self.manager()
        result = self.record(manager)

        resolution = manager.resolve_unit(self.progress, self.metadata, 1)
        self.assertEqual(resolution.state, "pass")
        self.assertEqual(resolution.current_record["record_id"], result.record["record_id"])
        self.assertEqual(
            resolution.source_sha256,
            hashlib.sha256(self.artifacts["extracted/001-one.md"]).hexdigest(),
        )
        original_translation = self.artifacts["translated/001-one.md"]
        expected_translation_hash = hashlib.sha256(original_translation).hexdigest()
        self.assertEqual(resolution.translation_sha256, expected_translation_hash)

        self.artifacts["translated/001-one.md"] = original_translation + b"changed"
        self.assertEqual(manager.resolve_unit(self.progress, self.metadata, 1).state, "stale")

        self.artifacts["translated/001-one.md"] = original_translation
        restored = manager.resolve_unit(self.progress, self.metadata, 1)
        self.assertEqual(restored.state, "pass")
        self.assertEqual(restored.translation_sha256, expected_translation_hash)

    def test_source_or_workflow_change_makes_existing_review_stale(self):
        self.claim()
        manager = self.manager()
        self.record(manager)

        original_source = self.artifacts["extracted/001-one.md"]
        self.artifacts["extracted/001-one.md"] = original_source + b"changed"
        self.assertEqual(manager.resolve_unit(self.progress, self.metadata, 1).state, "stale")
        self.artifacts["extracted/001-one.md"] = original_source

        changed_metadata = {**self.metadata, "workflow": {**self.metadata["workflow"], "resolved_revision": "fedcba9876543210"}}
        self.assertEqual(manager.resolve_unit(self.progress, changed_metadata, 1).state, "stale")

    def test_resolution_reports_missing_and_untranslated_without_fabricating_evidence(self):
        manager = self.manager()
        self.assertEqual(manager.resolve_unit(self.progress, self.metadata, 1).state, "missing")
        chapter_two = manager.resolve_unit(self.progress, self.metadata, 2)
        self.assertEqual(chapter_two.state, "untranslated")
        self.assertIsNone(chapter_two.translation_sha256)

        self.artifacts["translated/001-one.md"] = b""
        self.assertEqual(manager.resolve_unit(self.progress, self.metadata, 1).state, "untranslated")

    def test_record_requires_immutable_resolved_workflow_provenance(self):
        self.claim()
        manager = self.manager()
        metadata = {**self.metadata, "workflow": {**self.metadata["workflow"], "resolved_revision": None}}
        with self.assertRaises(ReviewEvidenceError):
            manager.record(
                self.progress,
                self.progress_revision,
                metadata,
                1,
                outcome="PASS",
                reviewer_session_id="reviewer-a",
            )

    def test_record_requires_live_matching_reviewer_claim(self):
        manager = self.manager()
        with self.assertRaises(ReviewClaimError):
            self.record(manager)

        self.claim(role="translator")
        with self.assertRaises(ReviewClaimError):
            self.record(manager)

    def test_record_rejects_foreign_expired_or_wrong_workflow_reviewer_claim(self):
        manager = self.manager()
        self.claim(session_id="someone-else")
        with self.assertRaises(ReviewClaimError):
            self.record(manager)

        loaded = self.repository.read(".workflow/claims/chapter-000001.json", SchemaKind.CLAIM)
        self.repository.delete_if_version(".workflow/claims/chapter-000001.json", SchemaKind.CLAIM, loaded.version)
        self.claim(expired=True)
        with self.assertRaises(ReviewClaimError):
            self.record(manager)
        self.assertTrue(self.storage.read(".workflow/claims/chapter-000001.json").content)

        loaded = self.repository.read(".workflow/claims/chapter-000001.json", SchemaKind.CLAIM)
        self.repository.delete_if_version(".workflow/claims/chapter-000001.json", SchemaKind.CLAIM, loaded.version)
        self.claim(workflow_revision="other-workflow")
        with self.assertRaises(ReviewClaimError):
            self.record(manager)

    def test_record_refuses_missing_or_empty_translation(self):
        self.claim()
        manager = self.manager()
        original = self.artifacts.pop("translated/001-one.md")
        with self.assertRaises(ReviewEvidenceError):
            self.record(manager)
        self.artifacts["translated/001-one.md"] = b""
        with self.assertRaises(ReviewEvidenceError):
            self.record(manager)
        self.artifacts["translated/001-one.md"] = original

    def test_correction_rounds_and_supersession_are_deterministic(self):
        self.claim()
        manager = self.manager()
        first = self.record(manager, "CORRECTIONS_REQUIRED")
        second = self.record(manager, "PASS")
        third = self.record(manager, "CORRECTIONS_REQUIRED")
        fourth = self.record(manager, "PASS")

        self.assertEqual(
            [item.record["correction_round"] for item in (first, second, third, fourth)],
            [1, 1, 2, 2],
        )
        self.assertIsNone(first.record["supersedes_record_id"])
        self.assertEqual(second.record["supersedes_record_id"], first.record["record_id"])
        self.assertEqual(third.record["supersedes_record_id"], second.record["record_id"])
        self.assertEqual(fourth.record["supersedes_record_id"], third.record["record_id"])
        self.assertEqual(manager.resolve_unit(self.progress, self.metadata, 1).state, "pass")

    def test_later_corrections_required_for_same_artifact_supersedes_pass(self):
        self.claim()
        manager = self.manager()
        self.record(manager, "PASS")
        self.record(manager, "CORRECTIONS_REQUIRED")
        resolution = manager.resolve_unit(self.progress, self.metadata, 1)
        self.assertEqual(resolution.state, "corrections_required")
        self.assertEqual(resolution.current_record["outcome"], "CORRECTIONS_REQUIRED")

    def test_concurrent_ledger_writers_surface_one_conflict_without_blind_retry(self):
        self.claim()
        barrier = threading.Barrier(2, timeout=3)

        class CoordinatedRepository(WorkflowStateRepository):
            def read(self, path, schema, *, allow_legacy=False):
                loaded = super().read(path, schema, allow_legacy=allow_legacy)
                if path == "review-ledger.json":
                    try:
                        barrier.wait()
                    except threading.BrokenBarrierError:
                        pass
                return loaded

        repository = CoordinatedRepository(self.storage)
        outcomes = []
        guard = threading.Lock()

        def worker(record_id):
            manager = ReviewLedgerManager(
                repository,
                artifact_reader=self.artifact_reader,
                now=lambda: NOW,
                id_factory=lambda: record_id,
            )
            try:
                self.record(manager, "PASS")
            except ReviewConflict:
                result = "conflict"
            else:
                result = "success"
            with guard:
                outcomes.append(result)

        threads = [
            threading.Thread(target=worker, args=("a" * 32,)),
            threading.Thread(target=worker, args=("b" * 32,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive(), "review ledger writer deadlocked")

        self.assertEqual(sorted(outcomes), ["conflict", "success"])
        ledger = self.repository.read("review-ledger.json", SchemaKind.REVIEW_LEDGER).data
        self.assertEqual(len(ledger["records"]), 1)
        self.assertEqual(ledger["next_sequence"], 2)


if __name__ == "__main__":
    unittest.main()

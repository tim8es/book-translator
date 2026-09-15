import hashlib
import json
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from workflow.claims import ClaimManager
from workflow.filesystem import FilesystemStorage
from workflow.repository import WorkflowStateRepository
from workflow.reviews import ReviewConflict, ReviewEvidenceError, ReviewLedgerManager
from workflow.schemas import SchemaKind


NOW = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)


class WorkflowReviewTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.storage = FilesystemStorage(self.root)
        self.repository = WorkflowStateRepository(self.storage)
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
                }
            ],
        }
        self.metadata = {
            "schema_version": 1,
            "title": "Example",
            "target_language": "ru",
            "source_format": "markdown",
            "source_file": "example.md",
            "chapter_count": 1,
            "workflow": {
                "repository": "https://github.com/tim8es/book-translator",
                "requested_ref": "main",
                "resolved_revision": "workflow-rev-1",
                "review_evidence": "review-ledger-v1",
            },
        }
        self.source = b"Source chapter\n"
        self.translation = b"Translated chapter\n"
        self.storage.create_if_absent("extracted/001-one.md", self.source)
        self.storage.create_if_absent("translated/001-one.md", self.translation)
        self.repository.create("progress.json", SchemaKind.PROGRESS, self.progress)
        self.repository.create("metadata.json", SchemaKind.METADATA, self.metadata)
        self.repository.create(
            "review-ledger.json",
            SchemaKind.REVIEW_LEDGER,
            {
                "schema_version": 1,
                "book_slug": "example",
                "next_sequence": 1,
                "records": [],
            },
        )
        self.claim_manager = ClaimManager(
            self.repository,
            now=lambda: NOW,
            id_factory=lambda: "a" * 32,
        )

    def artifact_reader(self, relative_path):
        return self.storage.read(relative_path).content

    def manager(self, repository=None, *, record_ids=None):
        ids = iter(record_ids or ["b" * 32, "c" * 32, "d" * 32, "e" * 32])
        return ReviewLedgerManager(
            repository or self.repository,
            artifact_reader=self.artifact_reader,
            now=lambda: NOW,
            id_factory=lambda: next(ids),
        )

    def claim(self, *, session_id="reviewer-session", role="reviewer", expires=None):
        return self.claim_manager.acquire(
            self.progress,
            "1",
            role=role,
            session_id=session_id,
            workflow_revision="workflow-rev-1",
            ttl=expires or timedelta(minutes=30),
        )[0]

    def record(self, manager, outcome, *, session_id="reviewer-session"):
        progress = self.repository.read("progress.json", SchemaKind.PROGRESS)
        return manager.record(
            self.progress,
            self.metadata,
            1,
            outcome=outcome,
            reviewer_session_id=session_id,
            progress_revision=progress.version,
        )

    def test_record_requires_live_matching_reviewer_claim(self):
        manager = self.manager()
        progress = self.repository.read("progress.json", SchemaKind.PROGRESS)
        with self.assertRaises(ReviewEvidenceError):
            manager.record(
                self.progress,
                self.metadata,
                1,
                outcome="PASS",
                reviewer_session_id="reviewer-session",
                progress_revision=progress.version,
            )

    def test_record_rejects_foreign_expired_or_wrong_workflow_reviewer_claim(self):
        manager = self.manager()

        self.claim(session_id="foreign")
        progress = self.repository.read("progress.json", SchemaKind.PROGRESS)
        with self.assertRaises(ReviewEvidenceError):
            manager.record(
                self.progress,
                self.metadata,
                1,
                outcome="PASS",
                reviewer_session_id="reviewer-session",
                progress_revision=progress.version,
            )
        active = self.claim_manager.list_active()
        self.claim_manager.release(active[0].claim["unit_id"], session_id="foreign")

        self.claim(expires=timedelta(seconds=1))
        expired_manager = ReviewLedgerManager(
            self.repository,
            artifact_reader=self.artifact_reader,
            now=lambda: NOW + timedelta(seconds=2),
            id_factory=lambda: "f" * 32,
        )
        progress = self.repository.read("progress.json", SchemaKind.PROGRESS)
        with self.assertRaises(ReviewEvidenceError):
            expired_manager.record(
                self.progress,
                self.metadata,
                1,
                outcome="PASS",
                reviewer_session_id="reviewer-session",
                progress_revision=progress.version,
            )

        self.storage.delete_if_version(
            active[0].claim_path if active else "claims/chapter-000001.json",
            self.storage.read("claims/chapter-000001.json").version,
        )
        claim = self.claim()
        path = claim.claim_path
        loaded = self.repository.read(path, SchemaKind.CLAIM)
        bad = dict(loaded.data)
        bad["workflow_revision"] = "wrong-workflow"
        self.repository.write_if_version(path, SchemaKind.CLAIM, bad, loaded.version)
        progress = self.repository.read("progress.json", SchemaKind.PROGRESS)
        with self.assertRaises(ReviewEvidenceError):
            manager.record(
                self.progress,
                self.metadata,
                1,
                outcome="PASS",
                reviewer_session_id="reviewer-session",
                progress_revision=progress.version,
            )

    def test_record_requires_immutable_resolved_workflow_provenance(self):
        self.claim()
        manager = self.manager()
        progress = self.repository.read("progress.json", SchemaKind.PROGRESS)

        for workflow in (
            {},
            {"resolved_revision": ""},
        ):
            metadata = dict(self.metadata)
            metadata["workflow"] = workflow
            with self.assertRaises(ReviewEvidenceError):
                manager.record(
                    self.progress,
                    metadata,
                    1,
                    outcome="PASS",
                    reviewer_session_id="reviewer-session",
                    progress_revision=progress.version,
                )

    def test_record_refuses_missing_or_empty_translation(self):
        self.claim()
        manager = self.manager()
        progress = self.repository.read("progress.json", SchemaKind.PROGRESS)
        self.storage.delete_if_version(
            "translated/001-one.md",
            self.storage.read("translated/001-one.md").version,
        )
        with self.assertRaises(ReviewEvidenceError):
            manager.record(
                self.progress,
                self.metadata,
                1,
                outcome="PASS",
                reviewer_session_id="reviewer-session",
                progress_revision=progress.version,
            )

        self.storage.create_if_absent("translated/001-one.md", b"")
        with self.assertRaises(ReviewEvidenceError):
            manager.record(
                self.progress,
                self.metadata,
                1,
                outcome="PASS",
                reviewer_session_id="reviewer-session",
                progress_revision=progress.version,
            )

    def test_pass_binds_exact_source_and_translation_bytes_and_restores_when_bytes_restore(self):
        self.claim()
        manager = self.manager()
        result = self.record(manager, "PASS")
        resolution = manager.resolve_unit(self.progress, self.metadata, 1)
        self.assertEqual(resolution.state, "pass")
        self.assertEqual(result.record["source_sha256"], hashlib.sha256(self.source).hexdigest())
        self.assertEqual(result.record["translation_sha256"], hashlib.sha256(self.translation).hexdigest())

        translation = self.storage.read("translated/001-one.md")
        self.storage.write_if_version("translated/001-one.md", b"changed\n", translation.version)
        self.assertEqual(manager.resolve_unit(self.progress, self.metadata, 1).state, "stale")
        translation = self.storage.read("translated/001-one.md")
        self.storage.write_if_version("translated/001-one.md", self.translation, translation.version)
        self.assertEqual(manager.resolve_unit(self.progress, self.metadata, 1).state, "pass")

    def test_source_or_workflow_change_makes_existing_review_stale(self):
        self.claim()
        manager = self.manager(record_ids=["b" * 32])
        self.record(manager, "PASS")

        source = self.storage.read("extracted/001-one.md")
        self.storage.write_if_version("extracted/001-one.md", b"changed source\n", source.version)
        self.assertEqual(manager.resolve_unit(self.progress, self.metadata, 1).state, "stale")

        source = self.storage.read("extracted/001-one.md")
        self.storage.write_if_version("extracted/001-one.md", self.source, source.version)
        changed_metadata = json.loads(json.dumps(self.metadata))
        changed_metadata["workflow"]["resolved_revision"] = "workflow-rev-2"
        self.assertEqual(manager.resolve_unit(self.progress, changed_metadata, 1).state, "stale")

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
            def read(self, path, schema):
                loaded = super().read(path, schema)
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
            threading.Thread(target=worker, args=("c" * 32,)),
            threading.Thread(target=worker, args=("d" * 32,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertCountEqual(outcomes, ["success", "conflict"])
        ledger = self.repository.read("review-ledger.json", SchemaKind.REVIEW_LEDGER).data
        self.assertEqual(len(ledger["records"]), 1)


if __name__ == "__main__":
    unittest.main()

import base64
import hashlib
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from workflow_v2 import FilesystemStorage, WorkflowStateRepository
from workflow_v2.claims import ClaimError, ClaimManager
from workflow_v2.coordination import BookCoordinationManager
from workflow_v2.finalize import FinalizationError, FinalizationManager
from workflow_v2.migration_journal import MIGRATION_PATH, serialize_migration_journal
from workflow_v2.reviews import ReviewEvidenceError, ReviewLedgerManager
from workflow_v2.schemas import SchemaKind
from workflow_v2.status import StatusResolver
from workflow_v2.storage import StorageNotFound


NOW = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
OLD = "old-revision"
NEW = "new-revision"


class WorkflowV2MigrationVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.book_dir = Path(self.temp.name)
        (self.book_dir / ".workflow/claims").mkdir(parents=True)
        (self.book_dir / "extracted").mkdir()
        (self.book_dir / "translated").mkdir()
        (self.book_dir / "extracted/001-one.md").write_bytes(b"source chapter\n")
        (self.book_dir / "translated/001-one.md").write_bytes(b"translation\n")
        self.storage = FilesystemStorage(self.book_dir)
        self.repository = WorkflowStateRepository(self.storage)
        self.metadata = {
            "schema_version": 1,
            "title": "Book",
            "target_language": "ru",
            "source_format": "markdown",
            "source_file": "book.md",
            "chapter_count": 1,
            "workflow": {
                "repository": "https://github.com/tim8es/book-translator",
                "requested_ref": "legacy-ref",
                "resolved_revision": OLD,
            },
        }
        self.progress = {
            "schema_version": 1,
            "book_slug": "book",
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
        self.metadata_revision = self.repository.create(
            "metadata.json", SchemaKind.METADATA, self.metadata
        )
        self.progress_revision = self.repository.create(
            "progress.json", SchemaKind.PROGRESS, self.progress
        )
        self._create_valid_journal()

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _journal():
        original = b"original metadata bytes\n"
        target = b"target metadata bytes\n"
        return {
            "schema_version": 1,
            "operation": "workflow_upgrade",
            "book_slug": "book",
            "from_revision": OLD,
            "to_revision": NEW,
            "phase": "prepared",
            "documents": [
                {
                    "path": "metadata.json",
                    "kind": "metadata",
                    "original_exists": True,
                    "original_revision": "original-revision",
                    "original_sha256": hashlib.sha256(original).hexdigest(),
                    "original_bytes_base64": base64.b64encode(original).decode("ascii"),
                    "target_sha256": hashlib.sha256(target).hexdigest(),
                    "resulting_revision": None,
                }
            ],
        }

    def _create_valid_journal(self):
        self.storage.create_if_absent(
            MIGRATION_PATH,
            serialize_migration_journal(self._journal()),
        )

    def coordination(self):
        return BookCoordinationManager(
            self.repository,
            now=lambda: NOW,
            id_factory=lambda: "c" * 32,
        )

    def test_active_migration_blocks_claim_admission_without_mutating_journal(self):
        before = self.storage.read(MIGRATION_PATH)
        manager = ClaimManager(
            self.repository,
            now=lambda: NOW,
            id_factory=lambda: "d" * 32,
            coordination=self.coordination(),
        )

        with self.assertRaises(ClaimError) as ctx:
            manager.acquire(
                self.progress,
                "1",
                role="translator",
                session_id="translator-1",
                base_revision=self.progress_revision,
                base_commit=None,
                workflow_revision=OLD,
            )

        self.assertIn("migration", str(ctx.exception).lower())
        self.assertEqual(self.storage.read(MIGRATION_PATH), before)
        self.assertEqual(self.storage.list(".workflow/claims"), [])

    def test_active_migration_blocks_finalization_admission_before_marker_creation(self):
        manager = FinalizationManager(
            self.repository,
            artifact_reader=lambda path: (self.book_dir / path).read_bytes(),
            preflight=lambda: ((), {"state": "verified"}),
            coordination=self.coordination(),
            now=lambda: NOW,
            id_factory=lambda: "e" * 32,
        )

        with self.assertRaises(FinalizationError) as ctx:
            manager._admit(
                session_id="finalizer-1",
                progress_revision=self.progress_revision,
                book_slug="book",
                workflow_revision=OLD,
                candidate_hash="a" * 64,
            )

        self.assertIn("migration", str(ctx.exception).lower())
        with self.assertRaises(StorageNotFound):
            self.storage.read(".workflow/finalization.json")

    def test_active_migration_blocks_accept_review_for_the_migration_reason(self):
        manager = ReviewLedgerManager(
            self.repository,
            artifact_reader=lambda path: (self.book_dir / path).read_bytes(),
        )

        with self.assertRaises(ReviewEvidenceError) as ctx:
            manager.accept_review(
                self.progress,
                self.progress_revision,
                self.metadata,
                1,
            )

        self.assertIn("migration", str(ctx.exception).lower())
        self.assertEqual(
            self.repository.read("progress.json", SchemaKind.PROGRESS).version,
            self.progress_revision,
        )

    def test_status_exposes_bounded_migration_and_resume_prioritizes_workflow_upgrade(self):
        before = self.storage.read(MIGRATION_PATH)
        resolver = StatusResolver(
            self.repository,
            artifact_reader=lambda path: (self.book_dir / path).read_bytes(),
        )

        status = resolver.status(corpus={"state": "unsealed"})
        self.assertTrue(status["valid"])
        self.assertEqual(
            status["migration"],
            {
                "active": True,
                "phase": "prepared",
                "from_revision": OLD,
                "to_revision": NEW,
                "document_count": 1,
            },
        )
        resume = resolver.resume(status)
        self.assertEqual(resume["operation"], "workflow_upgrade")
        self.assertIn(MIGRATION_PATH, resume["context"]["files"])
        self.assertEqual(self.storage.read(MIGRATION_PATH), before)

    def test_malformed_migration_journal_invalidates_status_without_mutation(self):
        current = self.storage.read(MIGRATION_PATH)
        malformed = b"{not-json\n"
        self.storage.write_if_version(MIGRATION_PATH, malformed, current.version)
        before = self.storage.read(MIGRATION_PATH)
        resolver = StatusResolver(
            self.repository,
            artifact_reader=lambda path: (self.book_dir / path).read_bytes(),
        )

        status = resolver.status(corpus={"state": "unsealed"})
        self.assertFalse(status["valid"])
        self.assertEqual(status["migration"], {"active": False})
        self.assertTrue(any("migration" in error.lower() for error in status["errors"]))
        resume = resolver.resume(status)
        self.assertEqual(resume["operation"], "blocked")
        self.assertEqual(resume["reason"], "preflight_failed")
        self.assertEqual(self.storage.read(MIGRATION_PATH), before)


if __name__ == "__main__":
    unittest.main()

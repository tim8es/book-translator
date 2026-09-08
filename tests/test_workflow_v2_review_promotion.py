import sys
import tempfile
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
    from workflow_v2.reviews import ReviewConflict, ReviewEvidenceError, ReviewLedgerManager
except (ImportError, ModuleNotFoundError):
    ReviewConflict = None
    ReviewEvidenceError = None
    ReviewLedgerManager = None


NOW = datetime(2026, 9, 6, 0, 0, 0, tzinfo=timezone.utc)
REVISION = "0123456789abcdef"


class WorkflowV2ReviewPromotionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = FilesystemStorage(Path(self.tmp.name))
        self.repository = WorkflowStateRepository(self.storage)
        self.artifacts = {
            "extracted/001.md": b"Source.\n",
            "translated/001.md": "Перевод.\n".encode("utf-8"),
            "extracted/002.md": b"Source two.\n",
            "translated/002.md": "Перевод два.\n".encode("utf-8"),
        }
        self.metadata = {
            "schema_version": 1,
            "title": "Example",
            "target_language": "ru",
            "source_format": "markdown",
            "source_file": "example.md",
            "chapter_count": 2,
            "workflow": {
                "repository": "https://github.com/tim8es/book-translator",
                "requested_ref": "refactor/workflow-engine-v2",
                "resolved_revision": REVISION,
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
        self.progress_revision = self.repository.create("progress.json", SchemaKind.PROGRESS, self.progress)
        self.repository.create(
            "review-ledger.json",
            SchemaKind.REVIEW_LEDGER,
            {"schema_version": 1, "book_slug": "example", "next_sequence": 1, "records": []},
        )
        self.ids = iter(f"{value:032x}" for value in range(300, 400))

    def tearDown(self):
        self.tmp.cleanup()

    def artifact_reader(self, path):
        if path not in self.artifacts:
            raise FileNotFoundError(path)
        return self.artifacts[path]

    def manager(self):
        self.assertIsNotNone(ReviewLedgerManager)
        return ReviewLedgerManager(
            self.repository,
            artifact_reader=self.artifact_reader,
            now=lambda: NOW,
            id_factory=lambda: next(self.ids),
        )

    def create_reviewer_claim(self):
        return self.repository.create(
            ".workflow/claims/chapter-000001.json",
            SchemaKind.CLAIM,
            {
                "schema_version": 1,
                "claim_id": "d" * 32,
                "unit_id": "chapter-000001",
                "role": "reviewer",
                "session_id": "reviewer-a",
                "base_revision": self.progress_revision,
                "base_commit": None,
                "workflow_revision": REVISION,
                "claimed_at": "2026-09-05T23:30:00Z",
                "expires_at": "2026-09-06T01:00:00Z",
            },
        )

    def record(self, outcome="PASS"):
        self.create_reviewer_claim()
        return self.manager().record(
            self.progress,
            self.progress_revision,
            self.metadata,
            1,
            outcome=outcome,
            reviewer_session_id="reviewer-a",
        )

    def require_accept_api(self, manager):
        self.assertTrue(hasattr(manager, "accept_review"), "ReviewLedgerManager.accept_review is not implemented")

    def test_current_pass_promotes_only_selected_chapter(self):
        self.record("PASS")
        manager = self.manager()
        self.require_accept_api(manager)

        result = manager.accept_review(self.progress, self.progress_revision, self.metadata, 1)

        self.assertEqual(result.unit_id, "chapter-000001")
        self.assertEqual(result.status, "reviewed")
        self.assertTrue(result.changed)
        durable = self.repository.read("progress.json", SchemaKind.PROGRESS)
        self.assertEqual([chapter["status"] for chapter in durable.data["chapters"]], ["reviewed", "translated"])
        self.assertEqual(result.progress_revision, durable.version)

    def test_missing_corrections_or_stale_pass_cannot_promote(self):
        manager = self.manager()
        self.require_accept_api(manager)
        with self.assertRaises(ReviewEvidenceError):
            manager.accept_review(self.progress, self.progress_revision, self.metadata, 1)

        self.record("CORRECTIONS_REQUIRED")
        manager = self.manager()
        with self.assertRaises(ReviewEvidenceError):
            manager.accept_review(self.progress, self.progress_revision, self.metadata, 1)

        claim = self.repository.read(".workflow/claims/chapter-000001.json", SchemaKind.CLAIM)
        self.repository.delete_if_version(".workflow/claims/chapter-000001.json", SchemaKind.CLAIM, claim.version)
        self.create_reviewer_claim()
        manager.record(
            self.progress,
            self.progress_revision,
            self.metadata,
            1,
            outcome="PASS",
            reviewer_session_id="reviewer-a",
        )
        self.artifacts["translated/001.md"] += b"changed"
        with self.assertRaises(ReviewEvidenceError):
            manager.accept_review(self.progress, self.progress_revision, self.metadata, 1)

    def test_promotion_requires_translated_or_reviewed_lifecycle_state(self):
        self.record("PASS")
        extracted = {
            **self.progress,
            "chapters": [dict(chapter) for chapter in self.progress["chapters"]],
        }
        extracted["chapters"][0]["status"] = "extracted"
        manager = self.manager()
        self.require_accept_api(manager)
        with self.assertRaises(ReviewEvidenceError):
            manager.accept_review(extracted, self.progress_revision, self.metadata, 1)

    def test_stale_progress_revision_conflicts_without_mutating_newer_state(self):
        self.record("PASS")
        newer = {
            **self.progress,
            "book_note": "newer-state",
            "chapters": [dict(chapter) for chapter in self.progress["chapters"]],
        }
        newer_revision = self.repository.write_if_version(
            "progress.json",
            SchemaKind.PROGRESS,
            newer,
            self.progress_revision,
        )
        manager = self.manager()
        self.require_accept_api(manager)

        with self.assertRaises(ReviewConflict):
            manager.accept_review(self.progress, self.progress_revision, self.metadata, 1)

        durable = self.repository.read("progress.json", SchemaKind.PROGRESS)
        self.assertEqual(durable.version, newer_revision)
        self.assertEqual(durable.data["chapters"][0]["status"], "translated")
        self.assertEqual(durable.data["book_note"], "newer-state")

    def test_already_reviewed_current_pass_is_idempotent_without_rewrite(self):
        self.record("PASS")
        reviewed = {
            **self.progress,
            "chapters": [dict(chapter) for chapter in self.progress["chapters"]],
        }
        reviewed["chapters"][0]["status"] = "reviewed"
        reviewed_revision = self.repository.write_if_version(
            "progress.json",
            SchemaKind.PROGRESS,
            reviewed,
            self.progress_revision,
        )
        manager = self.manager()
        self.require_accept_api(manager)

        result = manager.accept_review(reviewed, reviewed_revision, self.metadata, 1)

        self.assertFalse(result.changed)
        self.assertEqual(result.progress_revision, reviewed_revision)
        self.assertEqual(self.repository.read("progress.json", SchemaKind.PROGRESS).version, reviewed_revision)

    def test_already_reviewed_stale_evidence_fails(self):
        self.record("PASS")
        reviewed = {
            **self.progress,
            "chapters": [dict(chapter) for chapter in self.progress["chapters"]],
        }
        reviewed["chapters"][0]["status"] = "reviewed"
        reviewed_revision = self.repository.write_if_version(
            "progress.json",
            SchemaKind.PROGRESS,
            reviewed,
            self.progress_revision,
        )
        self.artifacts["translated/001.md"] += b"changed"
        manager = self.manager()
        self.require_accept_api(manager)

        with self.assertRaises(ReviewEvidenceError):
            manager.accept_review(reviewed, reviewed_revision, self.metadata, 1)


if __name__ == "__main__":
    unittest.main()

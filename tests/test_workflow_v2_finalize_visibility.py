import hashlib
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from workflow_v2.filesystem import FilesystemStorage
from workflow_v2.repository import WorkflowStateRepository
from workflow_v2.reviews import ReviewEvidenceError, ReviewLedgerManager
from workflow_v2.schemas import SchemaKind
from workflow_v2.status import StatusResolver


WORKFLOW_REVISION = "workflow-revision-12"
CONTRACT_REVISION = f"docs/TRANSLATION.md@{WORKFLOW_REVISION}"
FINALIZATION_PATH = ".workflow/finalization.json"


def digest(content):
    return hashlib.sha256(content).hexdigest()


class WorkflowV2FinalizeVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.storage = FilesystemStorage(self.root)
        self.repository = WorkflowStateRepository(self.storage)
        self.source = b"Source\n"
        self.translation = "Перевод\n".encode("utf-8")
        self.metadata = {
            "schema_version": 1,
            "title": "Demo",
            "target_language": "ru",
            "source_format": "markdown",
            "source_file": "demo.md",
            "chapter_count": 1,
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
                }
            ],
        }
        self.repository.create("metadata.json", SchemaKind.METADATA, self.metadata)
        self.progress_revision = self.repository.create(
            "progress.json", SchemaKind.PROGRESS, self.progress
        )
        self.repository.create(
            "review-ledger.json",
            SchemaKind.REVIEW_LEDGER,
            {
                "schema_version": 1,
                "book_slug": "demo",
                "next_sequence": 2,
                "records": [
                    {
                        "record_id": "1" * 32,
                        "sequence": 1,
                        "unit_id": "chapter-000001",
                        "outcome": "PASS",
                        "source_sha256": digest(self.source),
                        "translation_sha256": digest(self.translation),
                        "workflow_revision": WORKFLOW_REVISION,
                        "review_contract_revision": CONTRACT_REVISION,
                        "reviewer_session_id": "reviewer",
                        "reviewed_at": "2026-09-06T19:00:00Z",
                        "state_revision": "state-1",
                        "review_commit": "review-commit",
                        "correction_round": 0,
                        "supersedes_record_id": None,
                    }
                ],
            },
        )

    def tearDown(self):
        self.tmp.cleanup()

    def artifact_reader(self, path):
        if path == "extracted/001.md":
            return self.source
        if path == "translated/001.md":
            return self.translation
        raise FileNotFoundError(path)

    def marker(self):
        return {
            "schema_version": 1,
            "lock_id": "f" * 32,
            "book_slug": "demo",
            "workflow_revision": WORKFLOW_REVISION,
            "base_progress_revision": self.progress_revision,
            "candidate_progress_sha256": "c" * 64,
            "phase": "preparing",
            "promoted_progress_revision": None,
            "session_id": "finalizer",
            "started_at": "2026-09-06T20:00:00Z",
        }

    def test_status_exposes_active_finalization_and_resume_prioritizes_finalize(self):
        self.repository.create(FINALIZATION_PATH, SchemaKind.FINALIZATION_LOCK, self.marker())
        resolver = StatusResolver(self.repository, artifact_reader=self.artifact_reader)

        status = resolver.status(corpus={"state": "verified", "storage_mode": "embedded"})

        self.assertTrue(status["valid"])
        self.assertEqual(
            status["finalization"],
            {
                "active": True,
                "phase": "preparing",
                "workflow_revision": WORKFLOW_REVISION,
                "started_at": "2026-09-06T20:00:00Z",
            },
        )
        resume = resolver.resume(status)
        self.assertEqual(resume["operation"], "finalize")
        self.assertEqual(resume["context"]["role"], "orchestrator")
        self.assertIn("review-ledger.json", resume["context"]["files"])
        self.assertIn(FINALIZATION_PATH, resume["context"]["files"])

    def test_status_reports_inactive_and_malformed_marker_invalidates_status(self):
        resolver = StatusResolver(self.repository, artifact_reader=self.artifact_reader)
        inactive = resolver.status(corpus={"state": "verified"})
        self.assertEqual(inactive["finalization"], {"active": False})

        self.storage.create_if_absent(FINALIZATION_PATH, b"{not json\n")
        invalid = resolver.status(corpus={"state": "verified"})
        self.assertFalse(invalid["valid"])
        self.assertTrue(any("finalization" in error.lower() for error in invalid["errors"]))

    def test_accept_review_is_blocked_while_finalization_marker_exists(self):
        self.repository.create(FINALIZATION_PATH, SchemaKind.FINALIZATION_LOCK, self.marker())
        manager = ReviewLedgerManager(self.repository, artifact_reader=self.artifact_reader)
        before = self.storage.read("progress.json").content

        with self.assertRaises(ReviewEvidenceError) as ctx:
            manager.accept_review(
                self.progress,
                self.progress_revision,
                self.metadata,
                1,
            )

        self.assertIn("finalization", str(ctx.exception).lower())
        self.assertEqual(self.storage.read("progress.json").content, before)


if __name__ == "__main__":
    unittest.main()

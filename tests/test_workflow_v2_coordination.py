import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from workflow_v2.claims import ClaimError, ClaimManager
from workflow_v2.filesystem import FilesystemStorage
from workflow_v2.repository import WorkflowStateRepository
from workflow_v2.schemas import SchemaKind
from workflow_v2.storage import StorageNotFound

try:
    from workflow_v2.coordination import (
        BookCoordinationManager,
        CoordinationConflict,
    )
except (ImportError, ModuleNotFoundError):
    BookCoordinationManager = None
    CoordinationConflict = None


NOW = datetime(2026, 9, 6, 20, 0, 0, tzinfo=timezone.utc)


class WorkflowV2CoordinationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.storage = FilesystemStorage(self.root)
        self.repository = WorkflowStateRepository(self.storage)
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
                    "status": "extracted",
                }
            ],
        }

    def tearDown(self):
        self.tmp.cleanup()

    def coordinator(self, now=NOW, token="a"):
        self.assertIsNotNone(
            BookCoordinationManager,
            "workflow_v2.coordination is not implemented",
        )
        return BookCoordinationManager(
            self.repository,
            now=lambda: now,
            id_factory=lambda: token * 32,
        )

    def finalization_marker(self):
        return {
            "schema_version": 1,
            "lock_id": "f" * 32,
            "book_slug": "demo",
            "workflow_revision": "workflow-rev",
            "base_progress_revision": "progress-rev",
            "candidate_progress_sha256": "c" * 64,
            "phase": "preparing",
            "promoted_progress_revision": None,
            "session_id": "finalizer",
            "started_at": "2026-09-06T20:00:00Z",
        }

    def test_live_mutex_conflicts_and_exact_expiry_is_recoverable(self):
        first = self.coordinator(token="a")
        lease = first.acquire(
            operation="claim_admission",
            session_id="session-a",
            lease_seconds=60,
        )
        self.assertEqual(lease.data["operation"], "claim_admission")

        live = self.coordinator(now=NOW + timedelta(seconds=59), token="b")
        with self.assertRaises(CoordinationConflict):
            live.acquire(
                operation="finalize_admission",
                session_id="session-b",
                lease_seconds=60,
            )

        expired = self.coordinator(now=NOW + timedelta(seconds=60), token="c")
        replacement = expired.acquire(
            operation="finalize_admission",
            session_id="session-c",
            lease_seconds=60,
        )
        self.assertEqual(replacement.data["lock_id"], "c" * 32)
        expired.release(replacement)
        with self.assertRaises(StorageNotFound):
            self.storage.read(".workflow/coordination-lock.json")

    def test_claim_acquire_rejects_active_finalization_marker(self):
        self.coordinator()
        self.repository.create(
            ".workflow/finalization.json",
            SchemaKind.FINALIZATION_LOCK,
            self.finalization_marker(),
        )
        manager = ClaimManager(
            self.repository,
            now=lambda: NOW,
            id_factory=lambda: "1" * 32,
        )

        with self.assertRaises(ClaimError) as ctx:
            manager.acquire(
                self.progress,
                "1",
                role="translator",
                session_id="translator-a",
                base_revision="progress-rev",
                base_commit=None,
                workflow_revision="workflow-rev",
            )

        self.assertIn("finalization", str(ctx.exception).lower())
        self.assertEqual(self.storage.list(".workflow/claims"), [])

    def test_live_coordination_mutex_blocks_claim_admission(self):
        coordinator = self.coordinator(token="a")
        lease = coordinator.acquire(
            operation="finalize_admission",
            session_id="finalizer",
            lease_seconds=60,
        )
        manager = ClaimManager(
            self.repository,
            now=lambda: NOW,
            id_factory=lambda: "2" * 32,
        )

        with self.assertRaises(ClaimError):
            manager.acquire(
                self.progress,
                "1",
                role="translator",
                session_id="translator-a",
                base_revision="progress-rev",
                base_commit=None,
                workflow_revision="workflow-rev",
            )

        self.assertEqual(self.storage.list(".workflow/claims"), [])
        coordinator.release(lease)

    def test_successful_claim_releases_coordination_mutex(self):
        self.coordinator()
        manager = ClaimManager(
            self.repository,
            now=lambda: NOW,
            id_factory=lambda: "3" * 32,
        )
        claims = manager.acquire(
            self.progress,
            "1",
            role="translator",
            session_id="translator-a",
            base_revision="progress-rev",
            base_commit=None,
            workflow_revision="workflow-rev",
        )
        self.assertEqual(len(claims), 1)
        with self.assertRaises(StorageNotFound):
            self.storage.read(".workflow/coordination-lock.json")


if __name__ == "__main__":
    unittest.main()

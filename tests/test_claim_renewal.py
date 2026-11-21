"""CAS-safe claim renewal behavior."""

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from workflow.claims import ClaimConflict, ClaimManager, ClaimOwnershipError
from workflow.filesystem import FilesystemStorage
from workflow.repository import WorkflowStateRepository


PROGRESS = {"chapters": [
    {"number": 1, "unit_id": "chapter-000001"},
    {"number": 2, "unit_id": "chapter-000002"},
]}


class ClaimRenewalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repository = WorkflowStateRepository(FilesystemStorage(Path(self.temp.name)))
        self.ids = iter(f"{n:032x}" for n in range(1, 50))

    def tearDown(self):
        self.temp.cleanup()

    def manager(self, when):
        return ClaimManager(
            self.repository, now=lambda: datetime.fromisoformat(when),
            id_factory=lambda: next(self.ids),
        )

    def test_owner_renews_without_losing_identity_or_provenance(self):
        manager = self.manager("2026-10-21T12:00:00+00:00")
        original = manager.acquire(
            PROGRESS, "1", role="translator", session_id="chat-a",
            base_revision="rev", base_commit="git-head",
            workflow_revision="workflow", lease_seconds=600,
        )[0]
        next_manager = self.manager("2026-10-21T12:05:00+00:00")
        renewed = next_manager.renew(PROGRESS, "1", session_id="chat-a", lease_seconds=600)[0]
        self.assertEqual(renewed.data["claim_id"], original.data["claim_id"])
        self.assertEqual(renewed.data["base_commit"], "git-head")
        self.assertEqual(renewed.data["expires_at"], "2026-10-21T12:15:00Z")
        self.assertNotEqual(renewed.version, original.version)
        event_paths = self.repository.storage.list(".workflow/claim-events")
        self.assertEqual(len(event_paths), 2)

    def test_renew_cannot_extend_beyond_fifteen_minutes_from_first_claim(self):
        first = self.manager("2026-10-21T12:00:00+00:00")
        first.acquire(
            PROGRESS, "1", role="translator", session_id="chat-a",
            base_revision="rev", base_commit=None,
            workflow_revision="workflow", lease_seconds=900,
        )
        with self.assertRaises(ClaimConflict):
            self.manager("2026-10-21T12:05:00+00:00").renew(
                PROGRESS, "1", session_id="chat-a", lease_seconds=600
            )

    def test_non_owner_and_expired_claim_cannot_renew(self):
        manager = self.manager("2026-10-21T12:00:00+00:00")
        manager.acquire(
            PROGRESS, "1", role="translator", session_id="chat-a",
            base_revision="rev", base_commit="git-head",
            workflow_revision="workflow", lease_seconds=60,
        )
        with self.assertRaises(ClaimOwnershipError):
            manager.renew(PROGRESS, "1", session_id="chat-b")
        with self.assertRaises(ClaimConflict):
            self.manager("2026-10-21T12:01:00+00:00").renew(
                PROGRESS, "1", session_id="chat-a"
            )

    def test_renew_rejects_invalid_lease(self):
        with self.assertRaisesRegex(Exception, "lease_seconds"):
            self.manager("2026-10-21T12:00:00+00:00").renew(
                PROGRESS, "1", session_id="chat-a", lease_seconds=0
            )
        with self.assertRaisesRegex(Exception, "lease_seconds"):
            self.manager("2026-10-21T12:00:00+00:00").renew(
                PROGRESS, "1", session_id="chat-a", lease_seconds=901
            )


if __name__ == "__main__":
    unittest.main()

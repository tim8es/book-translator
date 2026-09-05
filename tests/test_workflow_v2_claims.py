import json
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
from workflow_v2.storage import StorageAlreadyExists, StorageNotFound

try:
    from workflow_v2.claims import (
        ClaimConflict,
        ClaimManager,
        ClaimOwnershipError,
        ClaimRollbackError,
        InvalidClaimSelector,
        canonical_unit_id,
        resolve_selector,
    )
except (ImportError, ModuleNotFoundError):
    ClaimConflict = None
    ClaimManager = None
    ClaimOwnershipError = None
    ClaimRollbackError = None
    InvalidClaimSelector = None
    canonical_unit_id = None
    resolve_selector = None


class WorkflowV2ClaimTestMixin:
    def progress(self):
        return {
            "schema_version": 1,
            "book_slug": "example",
            "chapters": [
                {
                    "number": number,
                    "title": f"Chapter {number}",
                    "slug": f"chapter-{number}",
                    "source_path": f"extracted/{number:03d}.md",
                    "translation_path": f"translated/{number:03d}.md",
                    "status": "extracted",
                }
                for number in (1, 2, 3, 4, 5)
            ],
        }


class WorkflowV2ClaimSelectorTests(WorkflowV2ClaimTestMixin, unittest.TestCase):
    def require_api(self):
        self.assertIsNotNone(canonical_unit_id, "workflow_v2.claims canonical_unit_id is not implemented")
        self.assertIsNotNone(resolve_selector, "workflow_v2.claims resolve_selector is not implemented")
        self.assertIsNotNone(InvalidClaimSelector, "workflow_v2.claims InvalidClaimSelector is not implemented")

    def test_canonical_unit_id_uses_six_digit_chapter_number(self):
        self.require_api()
        self.assertEqual(canonical_unit_id(1), "chapter-000001")
        self.assertEqual(canonical_unit_id(42), "chapter-000042")
        with self.assertRaises(InvalidClaimSelector):
            canonical_unit_id(0)

    def test_selector_resolves_single_and_inclusive_range_in_canonical_order(self):
        self.require_api()
        progress = self.progress()
        self.assertEqual(resolve_selector(progress, "2"), ["chapter-000002"])
        self.assertEqual(
            resolve_selector(progress, "2-4"),
            ["chapter-000002", "chapter-000003", "chapter-000004"],
        )

    def test_selector_rejects_invalid_reversed_and_missing_units(self):
        self.require_api()
        progress = self.progress()
        for selector in ("0", "-1", "1-", "1-a", "3-2", "1,2", "chapter-1"):
            with self.subTest(selector=selector):
                with self.assertRaises(InvalidClaimSelector):
                    resolve_selector(progress, selector)

        missing = self.progress()
        missing["chapters"] = [chapter for chapter in missing["chapters"] if chapter["number"] != 3]
        with self.assertRaises(InvalidClaimSelector):
            resolve_selector(missing, "2-4")

    def test_selector_rejects_duplicate_progress_chapter_numbers_before_mutation(self):
        self.require_api()
        progress = self.progress()
        progress["chapters"].append(dict(progress["chapters"][0]))
        with self.assertRaises(InvalidClaimSelector):
            resolve_selector(progress, "1")


class WorkflowV2ClaimLifecycleTests(WorkflowV2ClaimTestMixin, unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.storage = FilesystemStorage(self.root)
        self.repository = WorkflowStateRepository(self.storage)
        self.ids = iter(f"{number:032x}" for number in range(1, 100))

    def tearDown(self):
        self.tmp.cleanup()

    def require_api(self):
        self.assertIsNotNone(ClaimManager, "workflow_v2.claims ClaimManager is not implemented")
        self.assertIsNotNone(ClaimConflict, "workflow_v2.claims ClaimConflict is not implemented")
        self.assertIsNotNone(ClaimOwnershipError, "workflow_v2.claims ClaimOwnershipError is not implemented")
        self.assertIsNotNone(ClaimRollbackError, "workflow_v2.claims ClaimRollbackError is not implemented")

    def manager(self, timestamp="2026-09-05T12:00:00+00:00", *, storage=None):
        self.require_api()
        now = datetime.fromisoformat(timestamp)
        repository = self.repository if storage is None else WorkflowStateRepository(storage)
        return ClaimManager(
            repository,
            now=lambda: now,
            id_factory=lambda: next(self.ids),
        )

    def acquire(self, manager, selector, *, role="translator", session_id="session-a", lease_seconds=3600):
        return manager.acquire(
            self.progress(),
            selector,
            role=role,
            session_id=session_id,
            base_revision="progress-revision",
            base_commit=None,
            workflow_revision="workflow-revision",
            lease_seconds=lease_seconds,
        )

    def test_two_sessions_and_roles_cannot_claim_same_unit_even_after_lease_expiry(self):
        manager = self.manager()
        first = self.acquire(manager, "1")
        self.assertEqual(first[0].data["unit_id"], "chapter-000001")

        later = self.manager("2026-09-05T14:00:00+00:00")
        with self.assertRaises(ClaimConflict):
            self.acquire(later, "1", role="reviewer", session_id="session-b")

        active = later.list_active()
        self.assertEqual([claim.data["session_id"] for claim in active], ["session-a"])

    def test_overlapping_range_conflict_is_deterministic_before_new_writes(self):
        manager = self.manager()
        self.acquire(manager, "3", session_id="existing")

        with self.assertRaises(ClaimConflict) as ctx:
            self.acquire(manager, "1-4", session_id="new")

        self.assertEqual(ctx.exception.unit_id, "chapter-000003")
        self.assertEqual(
            [claim.data["unit_id"] for claim in manager.list_active()],
            ["chapter-000003"],
        )

    def test_range_race_rolls_back_claims_created_by_failed_batch(self):
        class RacingStorage(FilesystemStorage):
            def __init__(self, root):
                super().__init__(root)
                self.triggered = False

            def create_if_absent(self, path, content):
                if path == ".workflow/claims/chapter-000003.json" and not self.triggered:
                    self.triggered = True
                    raise StorageAlreadyExists(path)
                return super().create_if_absent(path, content)

        storage = RacingStorage(self.root)
        manager = self.manager(storage=storage)

        with self.assertRaises(ClaimConflict) as ctx:
            self.acquire(manager, "1-3")

        self.assertEqual(ctx.exception.unit_id, "chapter-000003")
        self.assertEqual(storage.list(".workflow/claims"), [])

    def test_release_requires_owner_and_records_request_and_completion_audit(self):
        manager = self.manager()
        acquired = self.acquire(manager, "1")
        claim_version = acquired[0].version

        with self.assertRaises(ClaimOwnershipError):
            manager.release(self.progress(), "1", session_id="session-b")
        self.assertEqual(manager.list_active()[0].version, claim_version)
        self.assertEqual(self.storage.list(".workflow/claim-events"), [])

        results = manager.release(self.progress(), "1", session_id="session-a")
        self.assertEqual([(result.unit_id, result.status) for result in results], [("chapter-000001", "released")])
        self.assertEqual(manager.list_active(), [])

        event_paths = self.storage.list(".workflow/claim-events")
        self.assertEqual(len(event_paths), 2)
        events = [self.repository.read(path, SchemaKind.CLAIM_EVENT).data for path in event_paths]
        actions = {event["action"] for event in events}
        self.assertEqual(actions, {"release_requested", "released"})
        request = next(event for event in events if event["action"] == "release_requested")
        completion = next(event for event in events if event["action"] == "released")
        self.assertEqual(request["claim_revision"], claim_version)
        self.assertEqual(request["reason"], "owner_release")
        self.assertEqual(completion["request_event_id"], request["event_id"])

    def test_recreated_claim_gets_new_revision_even_with_same_owner_and_clock(self):
        manager = self.manager()
        first = self.acquire(manager, "1")[0]
        manager.release(self.progress(), "1", session_id="session-a")
        second = self.acquire(manager, "1")[0]

        self.assertNotEqual(first.data["claim_id"], second.data["claim_id"])
        self.assertNotEqual(first.version, second.version)

    def test_cleanup_removes_only_expired_claims_and_records_lease_expired_reason(self):
        manager = self.manager()
        self.acquire(manager, "1", lease_seconds=30)
        self.acquire(manager, "2", lease_seconds=3600)

        later = self.manager("2026-09-05T12:01:00+00:00")
        results = later.cleanup_expired()
        self.assertEqual(
            [(result.unit_id, result.status) for result in results],
            [("chapter-000001", "cleaned"), ("chapter-000002", "live")],
        )
        self.assertEqual(
            [claim.data["unit_id"] for claim in later.list_active()],
            ["chapter-000002"],
        )

        event_paths = self.storage.list(".workflow/claim-events")
        self.assertEqual(len(event_paths), 2)
        events = [self.repository.read(path, SchemaKind.CLAIM_EVENT).data for path in event_paths]
        request = next(event for event in events if event["action"] == "cleanup_requested")
        completion = next(event for event in events if event["action"] == "cleaned")
        self.assertEqual(request["reason"], "lease_expired")
        self.assertEqual(request["unit_id"], "chapter-000001")
        self.assertEqual(completion["request_event_id"], request["event_id"])

    def test_cleanup_at_exact_expiry_boundary_treats_claim_as_expired(self):
        manager = self.manager()
        self.acquire(manager, "1", lease_seconds=60)

        boundary = self.manager("2026-09-05T12:01:00+00:00")
        results = boundary.cleanup_expired()

        self.assertEqual([(result.unit_id, result.status) for result in results], [("chapter-000001", "cleaned")])
        with self.assertRaises(StorageNotFound):
            self.storage.read(".workflow/claims/chapter-000001.json")


if __name__ == "__main__":
    unittest.main()

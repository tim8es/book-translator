from datetime import datetime, timezone
import unittest

from github_fake import FakeGitHubApiClient
from workflow_v2.claims import ClaimConflict, ClaimManager
from workflow_v2.coordination import BookCoordinationManager
from workflow_v2.github_api import GitHubApiError
from workflow_v2.github_storage import GitHubStorage
from workflow_v2.repository import WorkflowStateRepository
from workflow_v2.schemas import SchemaKind
from workflow_v2.storage import StorageError, StorageVersionConflict


NOW = datetime(2026, 9, 7, 12, 0, 0, tzinfo=timezone.utc)


class WorkflowV2GitHubBackendReliabilityTests(unittest.TestCase):
    def backend(self):
        client = FakeGitHubApiClient()
        storage = GitHubStorage(
            client,
            repository="owner/repository",
            branch="refactor/workflow-engine-v2",
            root_prefix="books/sample",
        )
        return client, storage, WorkflowStateRepository(storage)

    def claim_manager(self, repository, seed=1):
        ids = iter([f"{seed + index:032x}" for index in range(32)])
        coordination = BookCoordinationManager(
            repository,
            now=lambda: NOW,
            id_factory=lambda: next(ids),
        )
        return ClaimManager(
            repository,
            now=lambda: NOW,
            id_factory=lambda: next(ids),
            coordination=coordination,
        )

    @staticmethod
    def progress():
        return {
            "schema_version": 1,
            "book_slug": "sample",
            "chapters": [
                {
                    "number": 1,
                    "title": "One",
                    "slug": "one",
                    "source_path": "chapters/chapter-001.md",
                    "translation_path": "translations/chapter-001.md",
                    "status": "extracted",
                }
            ],
        }

    def test_competing_writer_between_preread_and_update_preserves_winner(self):
        client, storage, _repository = self.backend()
        expected = storage.create_if_absent("state.bin", b"base")
        client.mutations.clear()

        def race(action, path):
            if action == "update":
                client.seed(path, b"winner")

        client.before_mutation = race
        with self.assertRaises(StorageVersionConflict):
            storage.write_if_version("state.bin", b"ours", expected)

        self.assertEqual(storage.read("state.bin").content, b"winner")
        self.assertEqual([call[0] for call in client.mutations], ["update"])

    def test_competing_writer_after_successful_update_is_detected_by_readback(self):
        client, storage, _repository = self.backend()
        expected = storage.create_if_absent("state.bin", b"base")
        client.mutations.clear()

        def overwrite(action, path):
            if action == "update":
                client.seed(path, b"winner")

        client.after_mutation = overwrite
        with self.assertRaises(StorageVersionConflict):
            storage.write_if_version("state.bin", b"ours", expected)

        self.assertEqual(storage.read("state.bin").content, b"winner")
        self.assertEqual([call[0] for call in client.mutations], ["update"])

    def test_transport_failure_during_mutation_is_not_retried(self):
        client, storage, _repository = self.backend()
        expected = storage.create_if_absent("state.bin", b"base")
        client.mutations.clear()

        def fail_transport(action, _path):
            if action == "update":
                client.next_error = GitHubApiError("transport unavailable", status=None)

        client.before_mutation = fail_transport
        with self.assertRaises(StorageError):
            storage.write_if_version("state.bin", b"ours", expected)

        self.assertEqual([call[0] for call in client.mutations], ["update"])
        self.assertEqual(storage.read("state.bin").content, b"base")

    def test_truncated_tree_blocks_storage_and_domain_discovery(self):
        client, storage, repository = self.backend()
        manager = self.claim_manager(repository)
        client.truncated = True

        with self.assertRaises(StorageError):
            storage.list("")
        with self.assertRaises(StorageError):
            manager.list_active()

    def test_claim_create_race_keeps_single_winner_and_surfaces_recoverable_conflict(self):
        client, _storage, repository = self.backend()
        manager = self.claim_manager(repository)
        progress = self.progress()
        logical_path = ".workflow/claims/chapter-000001.json"
        repo_path = f"books/sample/{logical_path}"
        winner = {
            "schema_version": 1,
            "claim_id": "f" * 32,
            "unit_id": "chapter-000001",
            "role": "translator",
            "session_id": "winner-session",
            "base_revision": "progress-revision",
            "base_commit": None,
            "workflow_revision": "rev-1",
            "claimed_at": "2026-09-07T12:00:00Z",
            "expires_at": "2026-09-07T13:00:00Z",
        }
        winner_bytes = repository.serialize(logical_path, SchemaKind.CLAIM, winner)

        def win_race(action, path):
            if action == "create" and path == repo_path:
                client.seed(path, winner_bytes)

        client.before_mutation = win_race
        with self.assertRaises(ClaimConflict):
            manager.acquire(
                progress,
                "1",
                role="translator",
                session_id="loser-session",
                base_revision="progress-revision",
                base_commit=None,
                workflow_revision="rev-1",
            )

        active = manager.list_active()
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].data["session_id"], "winner-session")
        claim_creates = [
            call for call in client.mutations if call[0] == "create" and call[1] == repo_path
        ]
        self.assertEqual(len(claim_creates), 1)

    def test_fresh_read_after_conflict_allows_normal_recovery(self):
        client, storage, _repository = self.backend()
        expected = storage.create_if_absent("state.bin", b"base")

        def race(action, path):
            if action == "update":
                client.seed(path, b"winner")

        client.before_mutation = race
        with self.assertRaises(StorageVersionConflict):
            storage.write_if_version("state.bin", b"stale-write", expected)

        client.before_mutation = None
        fresh = storage.read("state.bin")
        recovered_version = storage.write_if_version(
            "state.bin", b"recovered", fresh.version
        )

        recovered = storage.read("state.bin")
        self.assertEqual(recovered.content, b"recovered")
        self.assertEqual(recovered.version, recovered_version)


if __name__ == "__main__":
    unittest.main()

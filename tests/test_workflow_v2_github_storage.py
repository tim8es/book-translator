import unittest

from github_fake import FakeGitHubApiClient
from storage_contract import exercise_backend_contract
from workflow_v2.github_api import GitHubApiError
from workflow_v2.storage import (
    StorageAlreadyExists,
    StorageError,
    StorageVersionConflict,
)

try:
    from workflow_v2.github_storage import GitHubStorage
except (ImportError, ModuleNotFoundError):
    GitHubStorage = None


class WorkflowV2GitHubStorageContractTests(unittest.TestCase):
    def require_api(self):
        self.assertIsNotNone(GitHubStorage, "GitHubStorage is not implemented")

    def storage(self):
        self.require_api()
        client = FakeGitHubApiClient()
        storage = GitHubStorage(
            client,
            repository="owner/repository",
            branch="refactor/workflow-engine-v2",
            root_prefix="books/sample",
        )
        return client, storage

    def test_reusable_storage_contract_matches_filesystem_semantics(self):
        self.require_api()

        def factory():
            client = FakeGitHubApiClient()
            return GitHubStorage(
                client,
                repository="owner/repository",
                branch="refactor/workflow-engine-v2",
                root_prefix="books/sample",
            )

        exercise_backend_contract(self, factory)

    def test_root_prefix_maps_logical_paths_without_leaking_repository_paths(self):
        client, storage = self.storage()
        storage.create_if_absent("progress.json", b"{}\n")
        storage.create_if_absent("nested/state.bin", b"state")

        self.assertEqual(
            sorted(client.files),
            ["books/sample/nested/state.bin", "books/sample/progress.json"],
        )
        self.assertEqual(storage.list(), ["nested/state.bin", "progress.json"])


class WorkflowV2GitHubStorageRaceTests(unittest.TestCase):
    def storage(self):
        client = FakeGitHubApiClient()
        storage = GitHubStorage(
            client,
            repository="owner/repository",
            branch="refactor/workflow-engine-v2",
            root_prefix="books/sample",
        )
        return client, storage

    def test_stale_expected_sha_is_rejected_before_mutation(self):
        client, storage = self.storage()
        first = storage.create_if_absent("state.bin", b"first")
        current = storage.write_if_version("state.bin", b"winner", first)
        mutations_before = len(client.mutations)

        with self.assertRaises(StorageVersionConflict):
            storage.write_if_version("state.bin", b"loser", first)

        self.assertEqual(len(client.mutations), mutations_before)
        self.assertEqual(storage.read("state.bin").content, b"winner")
        self.assertEqual(storage.read("state.bin").version, current)

    def test_create_conflict_is_existing_only_when_fresh_read_proves_file(self):
        client, storage = self.storage()

        def winner(_action, path):
            client.seed(path, b"winner")
            client.next_error = GitHubApiError("conflict", status=409)

        client.before_mutation = winner
        with self.assertRaises(StorageAlreadyExists):
            storage.create_if_absent("state.bin", b"ours")
        self.assertEqual(client.files["books/sample/state.bin"], b"winner")
        self.assertEqual(len(client.mutations), 1)

        client2, storage2 = self.storage()
        client2.next_error = GitHubApiError("validation failed", status=422)
        with self.assertRaises(StorageError) as ctx:
            storage2.create_if_absent("state.bin", b"ours")
        self.assertNotIsInstance(ctx.exception, StorageAlreadyExists)
        self.assertEqual(len(client2.mutations), 1)

    def test_update_conflict_is_version_conflict_only_when_fresh_read_changed(self):
        client, storage = self.storage()
        expected = storage.create_if_absent("state.bin", b"base")

        def changed(_action, path):
            client.seed(path, b"winner")
            client.next_error = GitHubApiError("conflict", status=409)

        client.before_mutation = changed
        with self.assertRaises(StorageVersionConflict):
            storage.write_if_version("state.bin", b"ours", expected)
        self.assertEqual(storage.read("state.bin").content, b"winner")

        client2, storage2 = self.storage()
        expected2 = storage2.create_if_absent("state.bin", b"base")

        def unchanged(_action, _path):
            client2.next_error = GitHubApiError("validation failed", status=422)

        client2.before_mutation = unchanged
        with self.assertRaises(StorageError) as ctx:
            storage2.write_if_version("state.bin", b"ours", expected2)
        self.assertNotIsInstance(ctx.exception, StorageVersionConflict)
        self.assertEqual(storage2.read("state.bin").content, b"base")

    def test_delete_conflict_is_generic_when_expected_sha_is_still_current(self):
        client, storage = self.storage()
        expected = storage.create_if_absent("state.bin", b"base")

        def unchanged(_action, _path):
            client.next_error = GitHubApiError("validation failed", status=409)

        client.before_mutation = unchanged
        with self.assertRaises(StorageError) as ctx:
            storage.delete_if_version("state.bin", expected)
        self.assertNotIsInstance(ctx.exception, StorageVersionConflict)
        self.assertEqual(storage.read("state.bin").content, b"base")

    def test_post_mutation_overwrite_and_recreation_are_conflicts_without_retry(self):
        client, storage = self.storage()
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

        client.after_mutation = None
        delete_version = storage.read("state.bin").version
        client.mutations.clear()

        def recreate(action, path):
            if action == "delete":
                client.seed(path, b"recreated")

        client.after_mutation = recreate
        with self.assertRaises(StorageVersionConflict):
            storage.delete_if_version("state.bin", delete_version)
        self.assertEqual(storage.read("state.bin").content, b"recreated")
        self.assertEqual([call[0] for call in client.mutations], ["delete"])

    def test_permission_failure_is_capability_error_and_is_not_retried(self):
        client, storage = self.storage()
        expected = storage.create_if_absent("state.bin", b"base")
        client.mutations.clear()

        def denied(_action, _path):
            client.next_error = GitHubApiError("forbidden secret detail", status=403)

        client.before_mutation = denied
        with self.assertRaises(StorageError) as ctx:
            storage.write_if_version("state.bin", b"new", expected)

        self.assertIn("capability", str(ctx.exception).lower())
        self.assertEqual([call[0] for call in client.mutations], ["update"])
        self.assertEqual(storage.read("state.bin").content, b"base")


if __name__ == "__main__":
    unittest.main()

import unittest

from github_fake import FakeGitHubApiClient
from storage_contract import exercise_backend_contract

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


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any


class FakeGitHubApiClient:
    """Deterministic in-memory GitHub file API used by storage/backend tests."""

    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.mutations: list[tuple[Any, ...]] = []
        self.before_mutation: Callable[[str, str], None] | None = None
        self.after_mutation: Callable[[str, str], None] | None = None
        self.next_error: Exception | None = None
        self.truncated = False

    @staticmethod
    def _blob_sha(content: bytes) -> str:
        return "blob-" + hashlib.sha256(content).hexdigest()

    @staticmethod
    def _types():
        from workflow_v2.github_api import GitHubFile, GitHubMutation, GitHubTree, GitHubTreeEntry

        return GitHubFile, GitHubMutation, GitHubTree, GitHubTreeEntry

    def _maybe_error(self) -> None:
        if self.next_error is None:
            return
        exc = self.next_error
        self.next_error = None
        raise exc

    def seed(self, path: str, content: bytes) -> str:
        self.files[path] = bytes(content)
        return self._blob_sha(content)

    def get_file(self, repository: str, path: str, ref: str):
        del repository, ref
        self._maybe_error()
        GitHubFile, _, _, _ = self._types()
        if path not in self.files:
            from workflow_v2.github_api import GitHubApiError

            raise GitHubApiError(f"missing file: {path}", status=404)
        content = self.files[path]
        return GitHubFile(path=path, blob_sha=self._blob_sha(content), content=content)

    def get_tree(self, repository: str, ref: str):
        del repository, ref
        self._maybe_error()
        _, _, GitHubTree, GitHubTreeEntry = self._types()
        entries = tuple(
            GitHubTreeEntry(path=path, type="blob", sha=self._blob_sha(content), mode="100644")
            for path, content in sorted(self.files.items())
        )
        return GitHubTree(entries=entries, truncated=self.truncated)

    def create_file(self, repository: str, path: str, content: bytes, branch: str, message: str):
        del repository, branch
        self.mutations.append(("create", path, bytes(content), message))
        if self.before_mutation is not None:
            self.before_mutation("create", path)
        self._maybe_error()
        if path in self.files:
            from workflow_v2.github_api import GitHubApiError

            raise GitHubApiError(f"file already exists: {path}", status=422)
        self.files[path] = bytes(content)
        if self.after_mutation is not None:
            self.after_mutation("create", path)
        _, GitHubMutation, _, _ = self._types()
        return GitHubMutation(blob_sha=self._blob_sha(content), commit_sha="commit-create")

    def update_file(
        self,
        repository: str,
        path: str,
        content: bytes,
        expected_blob_sha: str,
        branch: str,
        message: str,
    ):
        del repository, branch
        self.mutations.append(("update", path, bytes(content), expected_blob_sha, message))
        if self.before_mutation is not None:
            self.before_mutation("update", path)
        self._maybe_error()
        if path not in self.files or self._blob_sha(self.files[path]) != expected_blob_sha:
            from workflow_v2.github_api import GitHubApiError

            raise GitHubApiError(f"version conflict: {path}", status=409)
        self.files[path] = bytes(content)
        if self.after_mutation is not None:
            self.after_mutation("update", path)
        _, GitHubMutation, _, _ = self._types()
        return GitHubMutation(blob_sha=self._blob_sha(content), commit_sha="commit-update")

    def delete_file(
        self,
        repository: str,
        path: str,
        expected_blob_sha: str,
        branch: str,
        message: str,
    ):
        del repository, branch
        self.mutations.append(("delete", path, expected_blob_sha, message))
        if self.before_mutation is not None:
            self.before_mutation("delete", path)
        self._maybe_error()
        if path not in self.files or self._blob_sha(self.files[path]) != expected_blob_sha:
            from workflow_v2.github_api import GitHubApiError

            raise GitHubApiError(f"version conflict: {path}", status=409)
        del self.files[path]
        if self.after_mutation is not None:
            self.after_mutation("delete", path)
        _, GitHubMutation, _, _ = self._types()
        return GitHubMutation(blob_sha=None, commit_sha="commit-delete")

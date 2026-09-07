"""GitHub-backed implementation of the Workflow v2 storage contract."""

from __future__ import annotations

from pathlib import PurePosixPath

from .github_api import GitHubApiClient, GitHubApiError, GitHubFile, GitHubTree
from .storage import (
    InvalidStoragePath,
    StorageAlreadyExists,
    StorageError,
    StorageNotFound,
    StorageVersionConflict,
    StoredValue,
)


class GitHubStorage:
    """Store logical Workflow v2 paths as files on one GitHub branch."""

    def __init__(
        self,
        client: GitHubApiClient,
        *,
        repository: str,
        branch: str,
        root_prefix: str = "",
        commit_prefix: str = "workflow-v2",
    ):
        if not isinstance(repository, str):
            raise ValueError("repository must be owner/name")
        parts = repository.split("/")
        if len(parts) != 2 or any(not part.strip() for part in parts):
            raise ValueError("repository must be owner/name")
        if not isinstance(branch, str) or not branch.strip():
            raise ValueError("branch must be a non-empty string")
        if not isinstance(commit_prefix, str) or not commit_prefix.strip():
            raise ValueError("commit_prefix must be a non-empty string")
        self.client = client
        self.repository = repository
        self.branch = branch
        self.commit_prefix = commit_prefix.strip()
        self.root_prefix = self._validate_root_prefix(root_prefix)

    @staticmethod
    def _validate_root_prefix(value: str) -> str:
        if not isinstance(value, str):
            raise InvalidStoragePath("root_prefix must be a string")
        if value == "":
            return ""
        if value.startswith("/") or "\\" in value:
            raise InvalidStoragePath(f"unsafe root_prefix: {value!r}")
        parts = value.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise InvalidStoragePath(f"unsafe root_prefix: {value!r}")
        parsed = PurePosixPath(value)
        if parsed.is_absolute():
            raise InvalidStoragePath(f"unsafe root_prefix: {value!r}")
        return "/".join(parts)

    @staticmethod
    def _validate_path(path: str, *, allow_empty: bool = False) -> str:
        if not isinstance(path, str):
            raise InvalidStoragePath("storage path must be a string")
        if path == "":
            if allow_empty:
                return path
            raise InvalidStoragePath("storage path must not be empty")
        if path.startswith("/") or "\\" in path:
            raise InvalidStoragePath(f"unsafe storage path: {path!r}")
        parts = path.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise InvalidStoragePath(f"unsafe storage path: {path!r}")
        parsed = PurePosixPath(path)
        if parsed.is_absolute():
            raise InvalidStoragePath(f"unsafe storage path: {path!r}")
        return "/".join(parts)

    def _repo_path(self, logical_path: str) -> str:
        return f"{self.root_prefix}/{logical_path}" if self.root_prefix else logical_path

    def _logical_path(self, repo_path: str) -> str | None:
        if not self.root_prefix:
            return repo_path
        prefix = self.root_prefix + "/"
        if not repo_path.startswith(prefix):
            return None
        logical = repo_path[len(prefix) :]
        return logical or None

    @staticmethod
    def _require_content(content: bytes) -> bytes:
        if not isinstance(content, bytes):
            raise StorageError("storage content must be bytes")
        return content

    @staticmethod
    def _validate_file(file: GitHubFile, expected_path: str) -> None:
        if not isinstance(file, GitHubFile):
            raise StorageError("GitHub file response has an invalid shape")
        if file.path != expected_path:
            raise StorageError(
                f"GitHub file response path mismatch: expected {expected_path!r}, got {file.path!r}"
            )
        if not isinstance(file.blob_sha, str) or not file.blob_sha.strip():
            raise StorageError("GitHub file response has no blob revision")
        if not isinstance(file.content, bytes):
            raise StorageError("GitHub file response content must be bytes")

    def _message(self, action: str, repo_path: str) -> str:
        return f"{self.commit_prefix}: {action} {repo_path}"

    @staticmethod
    def _capability_error(action: str, path: str, exc: GitHubApiError) -> StorageError:
        return StorageError(
            f"GitHub contents capability unavailable for {action} {path}; status={exc.status}"
        )

    def read(self, path: str) -> StoredValue:
        logical = self._validate_path(path)
        repo_path = self._repo_path(logical)
        try:
            file = self.client.get_file(self.repository, repo_path, self.branch)
        except GitHubApiError as exc:
            if exc.status == 404:
                raise StorageNotFound(path) from exc
            if exc.status in {401, 403}:
                raise self._capability_error("read", path, exc) from exc
            raise StorageError(f"GitHub contents read failed for {path}: {exc}") from exc
        self._validate_file(file, repo_path)
        return StoredValue(content=file.content, version=file.blob_sha)

    def list(self, prefix: str = "") -> list[str]:
        logical_prefix = self._validate_path(prefix, allow_empty=True)
        try:
            tree = self.client.get_tree(self.repository, self.branch)
        except GitHubApiError as exc:
            if exc.status in {401, 403}:
                raise self._capability_error("list", prefix or ".", exc) from exc
            raise StorageError(f"GitHub tree read failed: {exc}") from exc
        if not isinstance(tree, GitHubTree):
            raise StorageError("GitHub tree response has an invalid shape")
        if tree.truncated:
            raise StorageError("GitHub recursive tree is truncated; refusing partial listing")

        results: list[str] = []
        for entry in tree.entries:
            if entry.type != "blob" or entry.mode not in {"100644", "100755"}:
                continue
            logical = self._logical_path(entry.path)
            if logical is None:
                continue
            if logical_prefix:
                if logical != logical_prefix and not logical.startswith(logical_prefix + "/"):
                    continue
            results.append(logical)
        return sorted(results)

    def _fresh_after_rejection(self, path: str) -> StoredValue | None:
        try:
            return self.read(path)
        except StorageNotFound:
            return None

    def create_if_absent(self, path: str, content: bytes) -> str:
        logical = self._validate_path(path)
        content = self._require_content(content)
        repo_path = self._repo_path(logical)
        try:
            self.client.create_file(
                self.repository,
                repo_path,
                content,
                self.branch,
                self._message("create", repo_path),
            )
        except GitHubApiError as exc:
            if exc.status in {409, 422}:
                current = self._fresh_after_rejection(path)
                if current is not None:
                    raise StorageAlreadyExists(path) from exc
                raise StorageError(
                    f"GitHub create rejected for {path} but no existing file could be proven"
                ) from exc
            if exc.status in {401, 403}:
                raise self._capability_error("create", path, exc) from exc
            if exc.status == 404:
                raise StorageError(f"GitHub contents write capability unavailable for {path}") from exc
            raise StorageError(f"GitHub create failed for {path}: {exc}") from exc

        try:
            confirmed = self.read(path)
        except StorageNotFound as exc:
            raise StorageVersionConflict(
                f"{path}: created file disappeared before read-back verification"
            ) from exc
        if confirmed.content != content:
            raise StorageVersionConflict(
                f"{path}: created file changed before read-back verification"
            )
        return confirmed.version

    def write_if_version(self, path: str, content: bytes, expected_version: str) -> str:
        logical = self._validate_path(path)
        content = self._require_content(content)
        if not isinstance(expected_version, str) or not expected_version:
            raise StorageError("expected_version must be a non-empty string")
        current = self.read(path)
        if current.version != expected_version:
            raise StorageVersionConflict(
                f"{path}: expected revision {expected_version}, current revision {current.version}"
            )
        repo_path = self._repo_path(logical)
        try:
            self.client.update_file(
                self.repository,
                repo_path,
                content,
                expected_version,
                self.branch,
                self._message("update", repo_path),
            )
        except GitHubApiError as exc:
            if exc.status in {409, 422}:
                fresh = self._fresh_after_rejection(path)
                if fresh is None:
                    raise StorageNotFound(path) from exc
                if fresh.version != expected_version:
                    raise StorageVersionConflict(
                        f"{path}: expected revision {expected_version}, current revision {fresh.version}"
                    ) from exc
                raise StorageError(
                    f"GitHub update rejected for {path} while expected revision is still current"
                ) from exc
            if exc.status in {401, 403}:
                raise self._capability_error("update", path, exc) from exc
            if exc.status == 404:
                raise StorageNotFound(path) from exc
            raise StorageError(f"GitHub update failed for {path}: {exc}") from exc

        try:
            confirmed = self.read(path)
        except StorageNotFound as exc:
            raise StorageVersionConflict(
                f"{path}: updated file disappeared before read-back verification"
            ) from exc
        if confirmed.content != content:
            raise StorageVersionConflict(
                f"{path}: updated file changed before read-back verification"
            )
        return confirmed.version

    def delete_if_version(self, path: str, expected_version: str) -> None:
        logical = self._validate_path(path)
        if not isinstance(expected_version, str) or not expected_version:
            raise StorageError("expected_version must be a non-empty string")
        current = self.read(path)
        if current.version != expected_version:
            raise StorageVersionConflict(
                f"{path}: expected revision {expected_version}, current revision {current.version}"
            )
        repo_path = self._repo_path(logical)
        try:
            self.client.delete_file(
                self.repository,
                repo_path,
                expected_version,
                self.branch,
                self._message("delete", repo_path),
            )
        except GitHubApiError as exc:
            if exc.status in {409, 422}:
                fresh = self._fresh_after_rejection(path)
                if fresh is None:
                    raise StorageNotFound(path) from exc
                if fresh.version != expected_version:
                    raise StorageVersionConflict(
                        f"{path}: expected revision {expected_version}, current revision {fresh.version}"
                    ) from exc
                raise StorageError(
                    f"GitHub delete rejected for {path} while expected revision is still current"
                ) from exc
            if exc.status in {401, 403}:
                raise self._capability_error("delete", path, exc) from exc
            if exc.status == 404:
                raise StorageNotFound(path) from exc
            raise StorageError(f"GitHub delete failed for {path}: {exc}") from exc

        try:
            self.read(path)
        except StorageNotFound:
            return
        raise StorageVersionConflict(
            f"{path}: file exists after delete read-back verification"
        )

"""Transport-facing GitHub API contracts for Workflow v2 storage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class GitHubFile:
    path: str
    blob_sha: str
    content: bytes


@dataclass(frozen=True)
class GitHubTreeEntry:
    path: str
    type: str
    sha: str
    mode: str


@dataclass(frozen=True)
class GitHubTree:
    entries: tuple[GitHubTreeEntry, ...]
    truncated: bool


@dataclass(frozen=True)
class GitHubMutation:
    blob_sha: str | None
    commit_sha: str | None


class GitHubApiError(RuntimeError):
    """A GitHub API operation failed before storage semantics could be proven."""

    def __init__(self, message: str, *, status: int | None = None):
        if not isinstance(message, str) or not message.strip():
            raise ValueError("GitHub API error message must be a non-empty string")
        if status is not None and type(status) is not int:
            raise ValueError("GitHub API error status must be an integer or None")
        self.status = status
        self.message = message.strip()
        super().__init__(self.message)


@runtime_checkable
class GitHubApiClient(Protocol):
    def get_file(self, repository: str, path: str, ref: str) -> GitHubFile:
        ...

    def get_tree(self, repository: str, ref: str) -> GitHubTree:
        ...

    def create_file(
        self,
        repository: str,
        path: str,
        content: bytes,
        branch: str,
        message: str,
    ) -> GitHubMutation:
        ...

    def update_file(
        self,
        repository: str,
        path: str,
        content: bytes,
        expected_blob_sha: str,
        branch: str,
        message: str,
    ) -> GitHubMutation:
        ...

    def delete_file(
        self,
        repository: str,
        path: str,
        expected_blob_sha: str,
        branch: str,
        message: str,
    ) -> GitHubMutation:
        ...

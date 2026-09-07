"""Transport-facing GitHub API contracts for Workflow v2 storage."""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, build_opener


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


class GitHubRestClient:
    """Small standard-library GitHub REST transport with no mutation retries."""

    def __init__(
        self,
        token: str | None,
        *,
        base_url: str = "https://api.github.com",
        api_version: str = "2026-03-10",
        opener=None,
    ):
        if token is not None and (not isinstance(token, str) or not token.strip()):
            raise ValueError("token must be None or a non-empty string")
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("base_url must be a non-empty string")
        if not isinstance(api_version, str) or not api_version.strip():
            raise ValueError("api_version must be a non-empty string")
        self._token = token
        self.base_url = base_url.rstrip("/")
        self.api_version = api_version.strip()
        self._opener = opener or build_opener()

    @staticmethod
    def _repository(repository: str) -> tuple[str, str]:
        if not isinstance(repository, str):
            raise GitHubApiError("repository must be owner/name")
        parts = repository.split("/")
        if len(parts) != 2 or any(not part.strip() for part in parts):
            raise GitHubApiError("repository must be owner/name")
        return parts[0], parts[1]

    @staticmethod
    def _nonempty(value: object, label: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise GitHubApiError(f"GitHub response {label} must be a non-empty string")
        return value

    def _headers(self, *, has_body: bool) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": self.api_version,
        }
        if self._token is not None:
            headers["Authorization"] = f"Bearer {self._token}"
        if has_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        url = self.base_url + path
        if query:
            url += "?" + urlencode(query)
        data = None
        if body is not None:
            try:
                data = json.dumps(
                    body,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            except (TypeError, ValueError) as exc:
                raise GitHubApiError("GitHub request body is not JSON-serializable") from exc
        request = Request(
            url,
            data=data,
            headers=self._headers(has_body=data is not None),
            method=method,
        )
        try:
            with self._opener.open(request, timeout=30) as response:
                raw = response.read()
        except HTTPError as exc:
            raise GitHubApiError(
                f"GitHub API request failed with HTTP {exc.code}",
                status=exc.code,
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise GitHubApiError("GitHub API transport request failed") from exc

        if not isinstance(raw, bytes):
            raise GitHubApiError("GitHub API response body must be bytes")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise GitHubApiError("GitHub API response is not valid UTF-8") from exc
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise GitHubApiError("GitHub API response is not valid JSON") from exc

    def _contents_path(self, repository: str, path: str) -> str:
        owner, repo = self._repository(repository)
        if not isinstance(path, str) or not path:
            raise GitHubApiError("path must be a non-empty string")
        return (
            f"/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/contents/"
            f"{quote(path, safe='/')}"
        )

    def get_file(self, repository: str, path: str, ref: str) -> GitHubFile:
        if not isinstance(ref, str) or not ref.strip():
            raise GitHubApiError("ref must be a non-empty string")
        metadata = self._request_json(
            "GET",
            self._contents_path(repository, path),
            query={"ref": ref},
        )
        if not isinstance(metadata, dict):
            raise GitHubApiError("GitHub contents response must be an object")
        if metadata.get("type") != "file":
            raise GitHubApiError("GitHub contents entry is not an ordinary file")
        blob_sha = self._nonempty(metadata.get("sha"), "contents.sha")
        owner, repo = self._repository(repository)
        blob = self._request_json(
            "GET",
            f"/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/git/blobs/{quote(blob_sha, safe='')}",
        )
        if not isinstance(blob, dict):
            raise GitHubApiError("GitHub blob response must be an object")
        if blob.get("encoding") != "base64":
            raise GitHubApiError("GitHub blob encoding must be base64")
        encoded = blob.get("content")
        if not isinstance(encoded, str):
            raise GitHubApiError("GitHub blob content must be a base64 string")
        try:
            content = base64.b64decode(encoded.encode("ascii"), validate=True)
        except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
            raise GitHubApiError("GitHub blob content is not valid base64") from exc
        return GitHubFile(path=path, blob_sha=blob_sha, content=content)

    def get_tree(self, repository: str, ref: str) -> GitHubTree:
        if not isinstance(ref, str) or not ref.strip():
            raise GitHubApiError("ref must be a non-empty string")
        owner, repo = self._repository(repository)
        data = self._request_json(
            "GET",
            f"/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/git/trees/{quote(ref, safe='')}",
            query={"recursive": "1"},
        )
        if not isinstance(data, dict):
            raise GitHubApiError("GitHub tree response must be an object")
        truncated = data.get("truncated")
        if type(truncated) is not bool:
            raise GitHubApiError("GitHub tree truncated must be a boolean")
        raw_entries = data.get("tree")
        if not isinstance(raw_entries, list):
            raise GitHubApiError("GitHub tree entries must be an array")
        entries: list[GitHubTreeEntry] = []
        for index, raw in enumerate(raw_entries):
            if not isinstance(raw, dict):
                raise GitHubApiError(f"GitHub tree entry {index} must be an object")
            entries.append(
                GitHubTreeEntry(
                    path=self._nonempty(raw.get("path"), f"tree[{index}].path"),
                    type=self._nonempty(raw.get("type"), f"tree[{index}].type"),
                    sha=self._nonempty(raw.get("sha"), f"tree[{index}].sha"),
                    mode=self._nonempty(raw.get("mode"), f"tree[{index}].mode"),
                )
            )
        return GitHubTree(entries=tuple(entries), truncated=truncated)

    @staticmethod
    def _mutation(data: Any) -> GitHubMutation:
        if not isinstance(data, dict):
            raise GitHubApiError("GitHub mutation response must be an object")
        raw_content = data.get("content")
        blob_sha: str | None = None
        if raw_content is not None:
            if not isinstance(raw_content, dict):
                raise GitHubApiError("GitHub mutation content must be an object or null")
            value = raw_content.get("sha")
            if value is not None:
                blob_sha = GitHubRestClient._nonempty(value, "mutation.content.sha")
        raw_commit = data.get("commit")
        commit_sha: str | None = None
        if raw_commit is not None:
            if not isinstance(raw_commit, dict):
                raise GitHubApiError("GitHub mutation commit must be an object or null")
            value = raw_commit.get("sha")
            if value is not None:
                commit_sha = GitHubRestClient._nonempty(value, "mutation.commit.sha")
        return GitHubMutation(blob_sha=blob_sha, commit_sha=commit_sha)

    def create_file(
        self,
        repository: str,
        path: str,
        content: bytes,
        branch: str,
        message: str,
    ) -> GitHubMutation:
        if not isinstance(content, bytes):
            raise GitHubApiError("content must be bytes")
        body = {
            "branch": self._nonempty(branch, "branch"),
            "content": base64.b64encode(content).decode("ascii"),
            "message": self._nonempty(message, "message"),
        }
        return self._mutation(
            self._request_json("PUT", self._contents_path(repository, path), body=body)
        )

    def update_file(
        self,
        repository: str,
        path: str,
        content: bytes,
        expected_blob_sha: str,
        branch: str,
        message: str,
    ) -> GitHubMutation:
        if not isinstance(content, bytes):
            raise GitHubApiError("content must be bytes")
        body = {
            "branch": self._nonempty(branch, "branch"),
            "content": base64.b64encode(content).decode("ascii"),
            "message": self._nonempty(message, "message"),
            "sha": self._nonempty(expected_blob_sha, "expected_blob_sha"),
        }
        return self._mutation(
            self._request_json("PUT", self._contents_path(repository, path), body=body)
        )

    def delete_file(
        self,
        repository: str,
        path: str,
        expected_blob_sha: str,
        branch: str,
        message: str,
    ) -> GitHubMutation:
        body = {
            "branch": self._nonempty(branch, "branch"),
            "message": self._nonempty(message, "message"),
            "sha": self._nonempty(expected_blob_sha, "expected_blob_sha"),
        }
        return self._mutation(
            self._request_json("DELETE", self._contents_path(repository, path), body=body)
        )

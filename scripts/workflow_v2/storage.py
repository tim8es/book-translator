"""Storage primitives for Workflow v2 durable state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class StorageError(RuntimeError):
    """Base error for storage backend failures."""


class StorageNotFound(StorageError):
    """The requested logical path does not exist."""


class StorageAlreadyExists(StorageError):
    """A create-if-absent target already exists."""


class StorageVersionConflict(StorageError):
    """The expected revision does not match current durable content."""


class InvalidStoragePath(StorageError):
    """A logical storage path is unsafe or outside the backend root."""


@dataclass(frozen=True)
class StoredValue:
    content: bytes
    version: str


@runtime_checkable
class StorageBackend(Protocol):
    def read(self, path: str) -> StoredValue:
        ...

    def write_if_version(self, path: str, content: bytes, expected_version: str) -> str:
        ...

    def create_if_absent(self, path: str, content: bytes) -> str:
        ...

    def list(self, prefix: str = "") -> list[str]:
        ...

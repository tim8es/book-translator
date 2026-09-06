"""Schema-aware JSON repository for Workflow v2 durable state."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .schemas import SchemaKind, parse_document
from .storage import StorageBackend, StorageVersionConflict


class RepositoryError(RuntimeError):
    """A durable state document cannot be decoded or serialized safely."""


@dataclass(frozen=True)
class LoadedDocument:
    data: dict[str, Any]
    version: str
    legacy: bool


class WorkflowStateRepository:
    """Compose schema validation with backend-neutral storage primitives."""

    def __init__(self, storage: StorageBackend):
        self.storage = storage

    @staticmethod
    def _serialize(path: str, schema: SchemaKind, data: Mapping[str, object]) -> bytes:
        parsed = parse_document(schema, data)
        try:
            text = json.dumps(
                parsed.data,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            ) + "\n"
        except (TypeError, ValueError) as exc:
            raise RepositoryError(f"{path}: document is not JSON-serializable: {exc}") from exc
        return text.encode("utf-8")

    def read(
        self,
        path: str,
        schema: SchemaKind,
        *,
        allow_legacy: bool = False,
    ) -> LoadedDocument:
        stored = self.storage.read(path)
        try:
            text = stored.content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RepositoryError(f"{path}: document is not valid UTF-8: {exc}") from exc
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RepositoryError(f"{path}: invalid JSON: {exc}") from exc

        parsed = parse_document(schema, raw, allow_legacy=allow_legacy)
        return LoadedDocument(
            data=parsed.data,
            version=stored.version,
            legacy=parsed.legacy,
        )

    def create(
        self,
        path: str,
        schema: SchemaKind,
        data: Mapping[str, object],
    ) -> str:
        content = self._serialize(path, schema, data)
        return self.storage.create_if_absent(path, content)

    def write_if_version(
        self,
        path: str,
        schema: SchemaKind,
        data: Mapping[str, object],
        expected_version: str,
    ) -> str:
        content = self._serialize(path, schema, data)
        return self.storage.write_if_version(path, content, expected_version)

    def delete_if_version(
        self,
        path: str,
        schema: SchemaKind,
        expected_version: str,
    ) -> None:
        loaded = self.read(path, schema)
        if loaded.version != expected_version:
            raise StorageVersionConflict(
                f"{path}: expected revision {expected_version}, current revision {loaded.version}"
            )
        self.storage.delete_if_version(path, expected_version)

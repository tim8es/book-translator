"""Schema-aware JSON repository for Workflow v2 durable state."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .schemas import SchemaKind, parse_document
from .storage import StorageBackend, StorageNotFound, StorageVersionConflict


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
    def serialize(path: str, schema: SchemaKind, data: Mapping[str, object]) -> bytes:
        """Return the exact canonical UTF-8 bytes used by repository writes."""

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

    @staticmethod
    def _claim_expiry(value: object) -> datetime | None:
        if not isinstance(value, str) or not value.strip():
            return None
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(timezone.utc)

    def _live_translator_claim_allows_rebind(self, unit_id: object) -> bool:
        """Allow a stale current acceptance only while a fresh Translator correction owns the unit."""

        if not isinstance(unit_id, str) or not unit_id.strip():
            return False
        path = f".workflow/claims/{unit_id}.json"
        try:
            stored = self.storage.read(path)
        except StorageNotFound:
            return False
        try:
            raw = json.loads(stored.content.decode("utf-8"))
            claim = parse_document(SchemaKind.CLAIM, raw).data
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return False
        expires_at = self._claim_expiry(claim.get("expires_at"))
        return (
            claim.get("role") == "translator"
            and expires_at is not None
            and expires_at > datetime.now(timezone.utc)
        )

    def _verify_translation_acceptance_integrity(self, progress: Mapping[str, Any]) -> None:
        chapters = progress.get("chapters")
        if not isinstance(chapters, list):
            return
        for chapter in chapters:
            if not isinstance(chapter, Mapping):
                continue
            evidence = chapter.get("translation_acceptance")
            if not isinstance(evidence, Mapping):
                continue
            mismatches: list[str] = []
            for path_key, hash_key in (
                ("source_path", "source_sha256"),
                ("translation_path", "translation_sha256"),
            ):
                artifact_path = chapter.get(path_key)
                expected = evidence.get(hash_key)
                if not isinstance(artifact_path, str) or not artifact_path.strip():
                    continue
                try:
                    artifact = self.storage.read(artifact_path)
                except StorageNotFound:
                    mismatches.append(f"{hash_key} cannot be verified because {artifact_path} is missing")
                    continue
                actual = hashlib.sha256(artifact.content).hexdigest()
                if expected != actual:
                    mismatches.append(
                        f"{hash_key}={expected!r} does not match current artifact sha256={actual}"
                    )
            if not mismatches:
                continue
            if self._live_translator_claim_allows_rebind(evidence.get("unit_id")):
                continue
            number = chapter.get("number")
            raise RepositoryError(
                f"progress.json: chapter {number} translation_acceptance sha256 mismatch: "
                + "; ".join(mismatches)
            )

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
        if schema == SchemaKind.PROGRESS:
            self._verify_translation_acceptance_integrity(parsed.data)
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
        content = self.serialize(path, schema, data)
        return self.storage.create_if_absent(path, content)

    def write_if_version(
        self,
        path: str,
        schema: SchemaKind,
        data: Mapping[str, object],
        expected_version: str,
    ) -> str:
        content = self.serialize(path, schema, data)
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

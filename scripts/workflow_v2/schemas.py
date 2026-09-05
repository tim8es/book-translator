"""Versioned Workflow v2 JSON document schemas.

The schema layer validates durable document shape only. Cross-document lifecycle,
concurrency, review, and finalization invariants belong to later workflow-domain
operations.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath
from typing import Any


SCHEMA_VERSION = 1
ALLOWED_PROGRESS_STATUSES = {"pending", "extracted", "translated", "reviewed"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
LEGACY_COMPATIBLE_KINDS: set["SchemaKind"]


class SchemaError(ValueError):
    """A durable workflow document does not satisfy its declared schema."""


class UnsupportedSchemaVersion(SchemaError):
    """A document declares a schema version this workflow does not support."""


class SchemaKind(str, Enum):
    METADATA = "metadata"
    PROGRESS = "progress"
    CLAIM = "claim"
    REVIEW_LEDGER = "review_ledger"
    SOURCE_MANIFEST = "source_manifest"
    GENERATED_STATE = "generated_state"


LEGACY_COMPATIBLE_KINDS = {SchemaKind.METADATA, SchemaKind.PROGRESS}


@dataclass(frozen=True)
class ParsedDocument:
    data: dict[str, Any]
    legacy: bool


def _field(schema: SchemaKind, path: str, message: str) -> SchemaError:
    return SchemaError(f"{schema.value}.{path}: {message}")


def _require_nonempty_string(data: Mapping[str, Any], key: str, schema: SchemaKind, *, path: str | None = None) -> str:
    value = data.get(key)
    label = path or key
    if not isinstance(value, str) or not value.strip():
        raise _field(schema, label, "must be a non-empty string")
    return value


def _require_int(
    data: Mapping[str, Any],
    key: str,
    schema: SchemaKind,
    *,
    minimum: int | None = None,
    path: str | None = None,
) -> int:
    value = data.get(key)
    label = path or key
    if type(value) is not int:
        raise _field(schema, label, "must be an integer")
    if minimum is not None and value < minimum:
        raise _field(schema, label, f"must be >= {minimum}")
    return value


def _require_list(data: Mapping[str, Any], key: str, schema: SchemaKind, *, path: str | None = None) -> list[Any]:
    value = data.get(key)
    label = path or key
    if not isinstance(value, list):
        raise _field(schema, label, "must be an array")
    return value


def _require_mapping(
    data: Mapping[str, Any],
    key: str,
    schema: SchemaKind,
    *,
    path: str | None = None,
) -> Mapping[str, Any]:
    value = data.get(key)
    label = path or key
    if not isinstance(value, Mapping):
        raise _field(schema, label, "must be an object")
    return value


def _validate_basename(value: str, schema: SchemaKind, path: str) -> None:
    if value in {".", ".."} or "/" in value or "\\" in value:
        raise _field(schema, path, "must be a filename basename")


def _validate_relative_path(value: str, schema: SchemaKind, path: str) -> None:
    if "\\" in value:
        raise _field(schema, path, "must use a safe relative POSIX path")
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or not parsed.parts or any(part in {"", ".", ".."} for part in parsed.parts):
        raise _field(schema, path, "must use a safe relative POSIX path")


def _validate_sha256(value: str, schema: SchemaKind, path: str) -> None:
    if not SHA256_RE.fullmatch(value):
        raise _field(schema, path, "must be a 64-character lowercase hexadecimal SHA-256")


def _validate_metadata(data: Mapping[str, Any], schema: SchemaKind) -> None:
    _require_nonempty_string(data, "title", schema)
    _require_nonempty_string(data, "target_language", schema)
    _require_nonempty_string(data, "source_format", schema)
    source_file = _require_nonempty_string(data, "source_file", schema)
    _validate_basename(source_file, schema, "source_file")
    _require_int(data, "chapter_count", schema, minimum=0)
    if "workflow" in data and data["workflow"] is not None and not isinstance(data["workflow"], Mapping):
        raise _field(schema, "workflow", "must be an object when present")


def _validate_progress(data: Mapping[str, Any], schema: SchemaKind) -> None:
    _require_nonempty_string(data, "book_slug", schema)
    chapters = _require_list(data, "chapters", schema)
    for index, chapter in enumerate(chapters):
        prefix = f"chapters[{index}]"
        if not isinstance(chapter, Mapping):
            raise _field(schema, prefix, "must be an object")
        _require_int(chapter, "number", schema, minimum=1, path=f"{prefix}.number")
        _require_nonempty_string(chapter, "title", schema, path=f"{prefix}.title")
        _require_nonempty_string(chapter, "slug", schema, path=f"{prefix}.slug")
        source_path = _require_nonempty_string(chapter, "source_path", schema, path=f"{prefix}.source_path")
        translation_path = _require_nonempty_string(
            chapter,
            "translation_path",
            schema,
            path=f"{prefix}.translation_path",
        )
        _validate_relative_path(source_path, schema, f"{prefix}.source_path")
        _validate_relative_path(translation_path, schema, f"{prefix}.translation_path")
        status = _require_nonempty_string(chapter, "status", schema, path=f"{prefix}.status")
        if status not in ALLOWED_PROGRESS_STATUSES:
            raise _field(
                schema,
                f"{prefix}.status",
                "must be one of pending, extracted, translated, reviewed",
            )


def _validate_claim(data: Mapping[str, Any], schema: SchemaKind) -> None:
    _require_nonempty_string(data, "unit_id", schema)
    role = _require_nonempty_string(data, "role", schema)
    if role not in {"translator", "reviewer"}:
        raise _field(schema, "role", "must be translator or reviewer")
    for key in (
        "session_id",
        "base_revision",
        "workflow_revision",
        "claimed_at",
        "expires_at",
    ):
        _require_nonempty_string(data, key, schema)


def _validate_review_ledger(data: Mapping[str, Any], schema: SchemaKind) -> None:
    _require_nonempty_string(data, "book_slug", schema)
    _require_list(data, "records", schema)


def _validate_source_manifest(data: Mapping[str, Any], schema: SchemaKind) -> None:
    source_file = _require_nonempty_string(data, "source_file", schema)
    _validate_basename(source_file, schema, "source_file")
    _require_nonempty_string(data, "source_format", schema)
    source_sha256 = _require_nonempty_string(data, "source_sha256", schema)
    _validate_sha256(source_sha256, schema, "source_sha256")
    chapter_count = _require_int(data, "chapter_count", schema, minimum=0)
    extracted = _require_list(data, "extracted", schema)
    if chapter_count != len(extracted):
        raise _field(schema, "chapter_count", "must equal the number of extracted entries")

    for index, item in enumerate(extracted):
        prefix = f"extracted[{index}]"
        if not isinstance(item, Mapping):
            raise _field(schema, prefix, "must be an object")
        _require_int(item, "number", schema, minimum=1, path=f"{prefix}.number")
        _require_nonempty_string(item, "title", schema, path=f"{prefix}.title")
        path = _require_nonempty_string(item, "path", schema, path=f"{prefix}.path")
        _validate_relative_path(path, schema, f"{prefix}.path")
        sha256 = _require_nonempty_string(item, "sha256", schema, path=f"{prefix}.sha256")
        _validate_sha256(sha256, schema, f"{prefix}.sha256")


def _validate_generated_state(data: Mapping[str, Any], schema: SchemaKind) -> None:
    _require_nonempty_string(data, "book_slug", schema)
    _require_nonempty_string(data, "source_revision", schema)
    _require_nonempty_string(data, "generated_at", schema)
    _require_mapping(data, "data", schema)


_VALIDATORS = {
    SchemaKind.METADATA: _validate_metadata,
    SchemaKind.PROGRESS: _validate_progress,
    SchemaKind.CLAIM: _validate_claim,
    SchemaKind.REVIEW_LEDGER: _validate_review_ledger,
    SchemaKind.SOURCE_MANIFEST: _validate_source_manifest,
    SchemaKind.GENERATED_STATE: _validate_generated_state,
}


def parse_document(
    schema: SchemaKind,
    data: Mapping[str, Any],
    *,
    allow_legacy: bool = False,
) -> ParsedDocument:
    """Validate and normalize one durable workflow document.

    Explicit unsupported versions are never interpreted as another version. Legacy
    compatibility is intentionally limited to metadata/progress documents that omit
    `schema_version`; the returned normalized copy is never written automatically.
    """

    if not isinstance(data, Mapping):
        raise SchemaError(f"{schema.value}: document must be an object")

    normalized = copy.deepcopy(dict(data))
    legacy = False

    if "schema_version" not in normalized:
        if allow_legacy and schema in LEGACY_COMPATIBLE_KINDS:
            normalized["schema_version"] = SCHEMA_VERSION
            legacy = True
        else:
            raise _field(schema, "schema_version", "is required")

    version = normalized.get("schema_version")
    if type(version) is not int:
        raise _field(schema, "schema_version", "must be an integer")
    if version != SCHEMA_VERSION:
        raise UnsupportedSchemaVersion(
            f"{schema.value}.schema_version: unsupported version {version}; expected {SCHEMA_VERSION}"
        )

    _VALIDATORS[schema](normalized, schema)
    return ParsedDocument(data=normalized, legacy=legacy)

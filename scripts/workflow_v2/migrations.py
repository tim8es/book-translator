"""Pure Workflow v2 schema migration primitives."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .schemas import SchemaError, SchemaKind, parse_document


class MigrationError(RuntimeError):
    """Base error for explicit Workflow v2 migration operations."""


class MigrationCompatibilityError(MigrationError):
    """Legacy durable state cannot be migrated without inventing data."""


class MigrationConflict(MigrationError):
    """Durable state changed across a migration coordination boundary."""


@dataclass(frozen=True)
class MigratedDocument:
    """One strictly validated document after a pure schema migration step."""

    kind: SchemaKind
    from_version: int
    to_version: int
    data: dict[str, Any]
    changed: bool


def detect_schema_version(data: Mapping[str, Any]) -> int:
    """Return logical schema version, treating an absent version as legacy v0."""

    if not isinstance(data, Mapping):
        raise MigrationCompatibilityError("document must be a JSON object")
    if "schema_version" not in data:
        return 0
    value = data["schema_version"]
    if type(value) is not int:
        raise MigrationCompatibilityError("schema_version must be an integer")
    return value


def migrate_document(kind: SchemaKind, data: Mapping[str, Any]) -> MigratedDocument:
    """Migrate one supported v0/v1 document to strict schema v1 in memory only."""

    if not isinstance(kind, SchemaKind):
        raise MigrationCompatibilityError("kind must be a SchemaKind")
    version = detect_schema_version(data)

    if version == 1:
        try:
            parsed = parse_document(kind, data)
        except SchemaError as exc:
            raise MigrationCompatibilityError(
                f"{kind.value} v1 is invalid: {exc}"
            ) from exc
        return MigratedDocument(
            kind=kind,
            from_version=1,
            to_version=1,
            data=copy.deepcopy(parsed.data),
            changed=False,
        )

    if version != 0:
        raise MigrationCompatibilityError(
            f"{kind.value}: unsupported schema version {version}; expected 0 or 1"
        )

    candidate = copy.deepcopy(dict(data))
    candidate["schema_version"] = 1
    try:
        parsed = parse_document(kind, candidate)
    except SchemaError as exc:
        raise MigrationCompatibilityError(
            f"{kind.value} v0 is not v1-compatible: {exc}"
        ) from exc

    return MigratedDocument(
        kind=kind,
        from_version=0,
        to_version=1,
        data=copy.deepcopy(parsed.data),
        changed=True,
    )

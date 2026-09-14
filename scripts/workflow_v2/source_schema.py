"""Package-level Workflow v2 explicit-source schema extensions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from . import schemas
from .schemas import SchemaKind


def _validate_metadata_source(data: Mapping[str, Any], schema: SchemaKind) -> None:
    if "source" not in data:
        return
    source = data.get("source")
    if not isinstance(source, Mapping):
        raise schemas._field(schema, "source", "must be an object")

    mode = source.get("storage_mode")
    if not isinstance(mode, str) or not mode.strip():
        raise schemas._field(schema, "source.storage_mode", "must be a non-empty string")
    if mode not in {"embedded", "private_external"}:
        raise schemas._field(schema, "source.storage_mode", "must be embedded or private_external")

    filename = source.get("filename")
    if not isinstance(filename, str) or not filename.strip():
        raise schemas._field(schema, "source.filename", "must be a non-empty string")
    schemas._validate_basename(filename, schema, "source.filename")
    if filename != data.get("source_file"):
        raise schemas._field(schema, "source.filename", "must equal source_file")

    size = source.get("size_bytes")
    if type(size) is not int:
        raise schemas._field(schema, "source.size_bytes", "must be an integer")
    if size < 0:
        raise schemas._field(schema, "source.size_bytes", "must be >= 0")

    sha256 = source.get("sha256")
    if not isinstance(sha256, str) or not sha256.strip():
        raise schemas._field(schema, "source.sha256", "must be a non-empty string")
    schemas._validate_sha256(sha256, schema, "source.sha256")


def _validate_manifest_source_extension(data: Mapping[str, Any], schema: SchemaKind) -> None:
    has_mode = "source_storage_mode" in data
    has_size = "source_size_bytes" in data
    if has_mode != has_size:
        missing = "source_size_bytes" if has_mode else "source_storage_mode"
        raise schemas._field(schema, missing, "is required with explicit source manifest identity")
    if not has_mode:
        return

    mode = data.get("source_storage_mode")
    if mode not in {"embedded", "private_external"}:
        raise schemas._field(
            schema,
            "source_storage_mode",
            "must be embedded or private_external",
        )
    size = data.get("source_size_bytes")
    if type(size) is not int:
        raise schemas._field(schema, "source_size_bytes", "must be an integer")
    if size < 0:
        raise schemas._field(schema, "source_size_bytes", "must be >= 0")


def install_source_schema_extensions() -> None:
    """Install explicit-source validators exactly once at package import time."""

    if getattr(schemas, "_explicit_source_v1_installed", False):
        return

    metadata_validator = schemas._VALIDATORS[SchemaKind.METADATA]
    manifest_validator = schemas._VALIDATORS[SchemaKind.SOURCE_MANIFEST]

    def validate_metadata(data: Mapping[str, Any], schema: SchemaKind) -> None:
        metadata_validator(data, schema)
        _validate_metadata_source(data, schema)

    def validate_manifest(data: Mapping[str, Any], schema: SchemaKind) -> None:
        manifest_validator(data, schema)
        _validate_manifest_source_extension(data, schema)

    schemas._VALIDATORS[SchemaKind.METADATA] = validate_metadata
    schemas._VALIDATORS[SchemaKind.SOURCE_MANIFEST] = validate_manifest
    setattr(schemas, "_explicit_source_v1_installed", True)

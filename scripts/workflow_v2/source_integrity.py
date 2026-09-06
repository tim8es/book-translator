"""Shared source identity and sealed-corpus construction primitives."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .schemas import SCHEMA_VERSION


class SourceIntegrityError(RuntimeError):
    """Source identity or extracted corpus cannot be sealed safely."""


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _checked_extracted_rel(book_dir: Path, value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise SourceIntegrityError(f"Invalid extracted source path in progress.json: {value!r}")
    rel = Path(value)
    if rel.is_absolute() or ".." in rel.parts or not rel.parts or rel.parts[0] != "extracted":
        raise SourceIntegrityError(f"Source path must stay under extracted/: {value}")
    target = (book_dir / rel).resolve()
    extracted_root = (book_dir / "extracted").resolve()
    try:
        target.relative_to(extracted_root)
    except ValueError as exc:
        raise SourceIntegrityError(f"Source path escapes extracted/: {value}") from exc
    return rel


def _explicit_source(metadata: Mapping[str, Any]) -> Mapping[str, Any] | None:
    value = metadata.get("source")
    return value if isinstance(value, Mapping) else None


def build_source_manifest(
    book_dir: Path,
    metadata: Mapping[str, Any],
    progress: Mapping[str, Any],
    source: Path,
) -> dict[str, Any]:
    """Build a sealed manifest from exact source/extracted bytes without writing state."""

    chapters = progress.get("chapters")
    if not isinstance(chapters, list):
        raise SourceIntegrityError("progress.json must contain a chapters array")
    if not source.is_file():
        raise SourceIntegrityError(f"Source file does not exist: {source}")

    source_sha = sha256_path(source)
    explicit = _explicit_source(metadata)
    source_size: int | None = None
    storage_mode: str | None = None
    if explicit is not None:
        expected_filename = explicit.get("filename")
        if source.name != expected_filename:
            raise SourceIntegrityError(
                f"Source filename mismatch: expected {expected_filename!r}, got {source.name!r}"
            )
        expected_size = explicit.get("size_bytes")
        actual_size = source.stat().st_size
        if actual_size != expected_size:
            raise SourceIntegrityError(
                f"Source size mismatch: expected {expected_size}, got {actual_size}"
            )
        expected_sha = explicit.get("sha256")
        if source_sha != expected_sha:
            raise SourceIntegrityError(
                f"Source SHA-256 mismatch: expected {expected_sha}, got {source_sha}"
            )
        source_size = actual_size
        storage_mode = str(explicit.get("storage_mode"))

    extracted: list[dict[str, Any]] = []
    for record in chapters:
        if not isinstance(record, Mapping):
            raise SourceIntegrityError("Every chapter entry must be an object")
        rel = _checked_extracted_rel(book_dir, record.get("source_path"))
        path = book_dir / rel
        if not path.is_file():
            raise SourceIntegrityError(
                f"Cannot seal incomplete corpus; missing {rel.as_posix()}"
            )
        extracted.append(
            {
                "number": record.get("number"),
                "title": record.get("title"),
                "path": rel.as_posix(),
                "sha256": sha256_path(path),
            }
        )

    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "source_file": metadata.get("source_file"),
        "source_format": metadata.get("source_format"),
        "source_sha256": source_sha,
        "chapter_count": len(extracted),
        "extracted": extracted,
    }
    if storage_mode is not None:
        manifest["source_storage_mode"] = storage_mode
        manifest["source_size_bytes"] = source_size
    return manifest

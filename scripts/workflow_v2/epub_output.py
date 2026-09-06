"""Deterministic Workflow v2 EPUB output identity and delivery metadata."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any

from .claims import canonical_unit_id


BUILD_CONTRACT = "epub-build-v1"
OUTPUT_MANIFEST_PATH = "output/manifest.json"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MANIFEST_KEYS = {
    "schema_version",
    "build_contract",
    "book_slug",
    "format",
    "preview",
    "artifact_path",
    "artifact_sha256",
    "unit_count",
    "input_fingerprint",
    "repository_commit",
    "state_revisions",
}
_STATE_REVISION_KEYS = {"metadata", "progress", "review_ledger"}


class EpubOutputError(RuntimeError):
    """EPUB output identity or generated delivery metadata is unsafe/invalid."""


def _nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EpubOutputError(f"{label} must be a non-empty string")
    return value


def _safe_relative_path(value: Any, label: str) -> str:
    path = _nonempty_string(value, label)
    if "\\" in path:
        raise EpubOutputError(f"{label} must be a safe relative POSIX path")
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or not parsed.parts or any(part in {"", ".", ".."} for part in parsed.parts):
        raise EpubOutputError(f"{label} must be a safe relative POSIX path")
    return path


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _read_bytes(reader: Callable[[str], bytes], path: str, *, kind: str) -> bytes:
    try:
        content = reader(path)
    except (FileNotFoundError, OSError, KeyError) as exc:
        raise EpubOutputError(f"missing {kind}: {path}") from exc
    if not isinstance(content, bytes):
        raise EpubOutputError(f"{kind} reader must return bytes for {path}")
    if not content.strip():
        raise EpubOutputError(f"{kind} is empty: {path}")
    return content


def _workflow_revision(metadata: Mapping[str, Any]) -> str:
    workflow = metadata.get("workflow")
    if not isinstance(workflow, Mapping):
        raise EpubOutputError("metadata workflow is unavailable")
    return _nonempty_string(workflow.get("resolved_revision"), "metadata workflow resolved_revision")


def _resolution_map(resolutions: Sequence[Any]) -> dict[int, Any]:
    result: dict[int, Any] = {}
    for resolution in resolutions:
        number = getattr(resolution, "chapter_number", None)
        if type(number) is not int or number < 1:
            raise EpubOutputError("review resolution chapter_number must be a positive integer")
        if number in result:
            raise EpubOutputError(f"duplicate review resolution for chapter {number}")
        result[number] = resolution
    return result


def build_input_snapshot(
    metadata: Mapping[str, Any],
    progress: Mapping[str, Any],
    resolutions: Sequence[Any],
    artifact_reader: Callable[[str], bytes],
    *,
    preview: bool,
    cover_reader: Callable[[str], bytes] | None = None,
) -> dict[str, Any]:
    """Build deterministic relevant-input identity for one EPUB output."""

    if not isinstance(metadata, Mapping):
        raise EpubOutputError("metadata must be an object")
    if not isinstance(progress, Mapping):
        raise EpubOutputError("progress must be an object")
    if type(preview) is not bool:
        raise EpubOutputError("preview must be a boolean")
    if not callable(artifact_reader):
        raise EpubOutputError("artifact_reader must be callable")

    book_slug = _nonempty_string(progress.get("book_slug"), "progress book_slug")
    title = _nonempty_string(metadata.get("title"), "metadata title")
    target_language = _nonempty_string(metadata.get("target_language"), "metadata target_language")
    author = metadata.get("author")
    if author is not None and not isinstance(author, str):
        raise EpubOutputError("metadata author must be a string or null")
    workflow_revision = _workflow_revision(metadata)

    chapters = progress.get("chapters")
    if not isinstance(chapters, list):
        raise EpubOutputError("progress chapters must be an array")
    review_by_number = _resolution_map(resolutions)
    if preview and review_by_number:
        # Review evidence is deliberately irrelevant to preview artifact identity.
        review_by_number = {}

    units: list[dict[str, Any]] = []
    seen_numbers: set[int] = set()
    for index, chapter in enumerate(chapters):
        if not isinstance(chapter, Mapping):
            raise EpubOutputError(f"progress chapter {index + 1} must be an object")
        number = chapter.get("number")
        if type(number) is not int or number < 1:
            raise EpubOutputError(f"progress chapter {index + 1} has invalid number")
        if number in seen_numbers:
            raise EpubOutputError(f"duplicate progress chapter number {number}")
        seen_numbers.add(number)

        translation_path = _safe_relative_path(
            chapter.get("translation_path"),
            f"chapter {number} translation_path",
        )
        translation = _read_bytes(
            artifact_reader,
            translation_path,
            kind="translation artifact",
        )
        status = _nonempty_string(chapter.get("status"), f"chapter {number} status")
        unit = {
            "unit_id": canonical_unit_id(number),
            "number": number,
            "title": _nonempty_string(chapter.get("title"), f"chapter {number} title"),
            "slug": _nonempty_string(chapter.get("slug"), f"chapter {number} slug"),
            "translation_path": translation_path,
            "status": status,
            "translation_sha256": _sha256(translation),
        }

        if not preview:
            resolution = review_by_number.get(number)
            if resolution is None:
                raise EpubOutputError(f"missing review resolution for chapter {number}")
            current = getattr(resolution, "current_record", None)
            if current is not None and not isinstance(current, Mapping):
                raise EpubOutputError(f"current review record for chapter {number} must be an object")
            current = current or {}
            unit["review"] = {
                "state": _nonempty_string(getattr(resolution, "state", None), f"chapter {number} review state"),
                "source_sha256": getattr(resolution, "source_sha256", None),
                "translation_sha256": getattr(resolution, "translation_sha256", None),
                "workflow_revision": current.get("workflow_revision"),
                "review_contract_revision": current.get("review_contract_revision"),
            }
        units.append(unit)

    if not preview and set(review_by_number) != seen_numbers:
        extras = sorted(set(review_by_number) - seen_numbers)
        if extras:
            raise EpubOutputError(f"review resolutions reference unknown chapters: {extras}")

    cover_path = metadata.get("cover_path")
    cover: dict[str, Any] | None = None
    if cover_path is not None:
        cover_path = _safe_relative_path(cover_path, "metadata cover_path")
        reader = cover_reader or artifact_reader
        cover_bytes = _read_bytes(reader, cover_path, kind="cover artifact")
        cover = {"path": cover_path, "sha256": _sha256(cover_bytes)}

    return {
        "build_contract": BUILD_CONTRACT,
        "book_slug": book_slug,
        "format": "epub",
        "preview": preview,
        "metadata": {
            "title": title,
            "author": author,
            "target_language": target_language,
            "cover_path": cover_path,
        },
        "workflow_revision": workflow_revision,
        "units": units,
        "cover": cover,
        "build_config": {"version": 1},
    }


def input_fingerprint(snapshot: Mapping[str, Any]) -> str:
    if not isinstance(snapshot, Mapping):
        raise EpubOutputError("build input snapshot must be an object")
    try:
        content = json.dumps(
            snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise EpubOutputError(f"build input snapshot is not canonical JSON data: {exc}") from exc
    return _sha256(content)


def validate_output_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Validate strict generated output-manifest v1 data and return a detached copy."""

    if not isinstance(manifest, Mapping):
        raise EpubOutputError("output manifest must be an object")
    if set(manifest) != _MANIFEST_KEYS:
        missing = sorted(_MANIFEST_KEYS - set(manifest))
        extra = sorted(set(manifest) - _MANIFEST_KEYS)
        raise EpubOutputError(f"output manifest fields mismatch: missing={missing} extra={extra}")
    if manifest.get("schema_version") != 1 or type(manifest.get("schema_version")) is not int:
        raise EpubOutputError("output manifest schema_version must be 1")
    if manifest.get("build_contract") != BUILD_CONTRACT:
        raise EpubOutputError(f"output manifest build_contract must be {BUILD_CONTRACT}")
    book_slug = _nonempty_string(manifest.get("book_slug"), "output manifest book_slug")
    if "/" in book_slug or "\\" in book_slug or book_slug in {".", ".."}:
        raise EpubOutputError("output manifest book_slug must be one directory name")
    if manifest.get("format") != "epub":
        raise EpubOutputError("output manifest format must be epub")
    if type(manifest.get("preview")) is not bool:
        raise EpubOutputError("output manifest preview must be a boolean")

    artifact_path = _safe_relative_path(manifest.get("artifact_path"), "output manifest artifact_path")
    if not artifact_path.startswith("output/") or not artifact_path.endswith(".epub"):
        raise EpubOutputError("output manifest artifact_path must be output/*.epub")
    artifact_sha = _nonempty_string(manifest.get("artifact_sha256"), "output manifest artifact_sha256")
    if not _SHA256_RE.fullmatch(artifact_sha):
        raise EpubOutputError("output manifest artifact_sha256 must be lowercase SHA-256")
    fingerprint = _nonempty_string(manifest.get("input_fingerprint"), "output manifest input_fingerprint")
    if not _SHA256_RE.fullmatch(fingerprint):
        raise EpubOutputError("output manifest input_fingerprint must be lowercase SHA-256")

    unit_count = manifest.get("unit_count")
    if type(unit_count) is not int or unit_count < 0:
        raise EpubOutputError("output manifest unit_count must be a non-negative integer")
    repository_commit = manifest.get("repository_commit")
    if repository_commit is not None and (not isinstance(repository_commit, str) or not repository_commit.strip()):
        raise EpubOutputError("output manifest repository_commit must be null or a non-empty string")

    revisions = manifest.get("state_revisions")
    if not isinstance(revisions, Mapping) or set(revisions) != _STATE_REVISION_KEYS:
        raise EpubOutputError("output manifest state_revisions must contain metadata, progress and review_ledger")
    for key in sorted(_STATE_REVISION_KEYS):
        _nonempty_string(revisions.get(key), f"output manifest state_revisions.{key}")
    return copy.deepcopy(dict(manifest))


def build_output_manifest(
    *,
    book_slug: str,
    preview: bool,
    artifact_path: str,
    artifact_sha256: str,
    unit_count: int,
    input_fingerprint: str,
    repository_commit: str | None,
    state_revisions: Mapping[str, str],
) -> dict[str, Any]:
    manifest = {
        "schema_version": 1,
        "build_contract": BUILD_CONTRACT,
        "book_slug": book_slug,
        "format": "epub",
        "preview": preview,
        "artifact_path": artifact_path,
        "artifact_sha256": artifact_sha256,
        "unit_count": unit_count,
        "input_fingerprint": input_fingerprint,
        "repository_commit": repository_commit,
        "state_revisions": dict(state_revisions),
    }
    return validate_output_manifest(manifest)


def resolve_output_status(
    manifest: Mapping[str, Any] | None,
    *,
    artifact_bytes: bytes | None,
    current_fingerprint: str,
    expected_unit_count: int,
) -> dict[str, Any]:
    """Classify a generated artifact/manifest pair without mutating durable state."""

    if manifest is None or artifact_bytes is None:
        return {"state": "missing"}
    if not isinstance(artifact_bytes, bytes):
        return {"state": "invalid", "reason": "artifact content must be bytes"}
    try:
        parsed = validate_output_manifest(manifest)
    except EpubOutputError as exc:
        return {"state": "invalid", "reason": str(exc)}
    if type(expected_unit_count) is not int or expected_unit_count < 0:
        return {"state": "invalid", "reason": "expected unit count is invalid"}
    if parsed["unit_count"] != expected_unit_count:
        return {"state": "invalid", "reason": "manifest unit_count does not match expected units"}
    if _sha256(artifact_bytes) != parsed["artifact_sha256"]:
        return {"state": "invalid", "reason": "artifact SHA-256 does not match output manifest"}
    if parsed["input_fingerprint"] != current_fingerprint:
        return {"state": "stale", "artifact_path": parsed["artifact_path"]}
    return {
        "state": "current",
        "artifact_path": parsed["artifact_path"],
        "artifact_sha256": parsed["artifact_sha256"],
        "preview": parsed["preview"],
    }

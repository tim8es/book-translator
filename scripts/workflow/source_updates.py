"""Durable source-edition revisions, delta reuse, and crash-safe promotion."""

from __future__ import annotations

import copy
import difflib
import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any

from .claims import ClaimManager, canonical_unit_id, unit_id_for_chapter
from .coordination import (
    BookCoordinationManager,
    CoordinationError,
    SOURCE_PROMOTION_PATH,
)
from .repository import LoadedDocument, RepositoryError, WorkflowStateRepository
from .schemas import SCHEMA_VERSION, SchemaError, SchemaKind
from .storage import (
    StorageAlreadyExists,
    StorageError,
    StorageNotFound,
    StorageVersionConflict,
)


SOURCE_REVISIONS_PATH = "source-revisions.json"
SOURCE_REVISIONS_ROOT = "source-revisions"
SOURCE_PROMOTION_LEASE_SECONDS = 900


class SourceRevisionError(RuntimeError):
    """Source edition state cannot be staged or promoted safely."""


class SourceRevisionConflict(SourceRevisionError):
    """Concurrent durable state prevents a safe source-edition transition."""


class SourceRevisionDecisionRequired(SourceRevisionError):
    """A staged source delta requires explicit destructive-change approval."""


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _now_utc(now: Callable[[], datetime] | None = None) -> str:
    value = (now or (lambda: datetime.now(timezone.utc)))()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise SourceRevisionError("source revision clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        return (
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SourceRevisionError(f"source revision data is not canonical JSON: {exc}") from exc


def _read_json(repository: WorkflowStateRepository, path: str) -> dict[str, Any]:
    try:
        stored = repository.storage.read(path)
    except StorageNotFound as exc:
        raise SourceRevisionError(f"missing durable source revision artifact: {path}") from exc
    try:
        value = json.loads(stored.content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceRevisionError(f"invalid durable source revision JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SourceRevisionError(f"durable source revision JSON must be an object: {path}")
    return value


def _create_or_verify(repository: WorkflowStateRepository, path: str, content: bytes) -> str:
    try:
        return repository.storage.create_if_absent(path, content)
    except StorageAlreadyExists:
        current = repository.storage.read(path)
        if current.content != content:
            raise SourceRevisionConflict(f"durable staged artifact already exists with different bytes: {path}")
        return current.version
    except StorageError as exc:
        raise SourceRevisionError(f"cannot persist staged source revision artifact {path}: {exc}") from exc


def _safe_basename(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceRevisionError("source filename must be non-empty")
    parsed = PurePosixPath(value)
    if len(parsed.parts) != 1 or value in {".", ".."} or "\\" in value:
        raise SourceRevisionError("source filename must be one safe basename")
    return value


def _slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^\w\s-]", "", value, flags=re.UNICODE)
    value = re.sub(r"[\s_]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value[:80] or "unit"


def _unit_sequence(unit_id: str) -> int:
    match = re.fullmatch(r"chapter-([0-9]{6})", unit_id or "")
    if match is None:
        raise SourceRevisionError(f"invalid stable unit id: {unit_id!r}")
    return int(match.group(1))


def _source_identity(metadata: Mapping[str, Any]) -> Mapping[str, Any]:
    source = metadata.get("source")
    if not isinstance(source, Mapping):
        raise SourceRevisionError("metadata source identity is required")
    return source


def _manifest_by_unit(
    progress: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> list[dict[str, Any]]:
    chapters = progress.get("chapters")
    entries = manifest.get("extracted")
    if not isinstance(chapters, list) or not isinstance(entries, list) or len(chapters) != len(entries):
        raise SourceRevisionError("active progress/source-manifest unit lists disagree")
    result: list[dict[str, Any]] = []
    for chapter, entry in zip(chapters, entries):
        if not isinstance(chapter, Mapping) or not isinstance(entry, Mapping):
            raise SourceRevisionError("active source manifest contains invalid unit entries")
        unit_id = unit_id_for_chapter(chapter)
        if entry.get("unit_id") not in {None, unit_id}:
            raise SourceRevisionError(f"active source manifest unit id disagrees for {unit_id}")
        digest = entry.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise SourceRevisionError(f"active source manifest has invalid SHA-256 for {unit_id}")
        result.append(
            {
                "unit_id": unit_id,
                "number": chapter.get("number"),
                "title": chapter.get("title"),
                "sha256": digest,
                "chapter": copy.deepcopy(dict(chapter)),
            }
        )
    return result


def _snapshot_path(revision_id: str) -> str:
    return f"{SOURCE_REVISIONS_ROOT}/{revision_id}/revision.json"


def _delta_path(revision_id: str) -> str:
    return f"{SOURCE_REVISIONS_ROOT}/{revision_id}/delta.json"


def initialize_source_revisions(
    repository: WorkflowStateRepository,
    *,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Create the first immutable source-revision snapshot for current valid state.

    The operation is idempotent. It is used during new-book extraction and once
    after an explicit workflow upgrade of a pre-revision workspace.
    """

    try:
        existing = repository.read(SOURCE_REVISIONS_PATH, SchemaKind.SOURCE_REVISIONS)
    except StorageNotFound:
        existing = None
    except (StorageError, RepositoryError, SchemaError) as exc:
        raise SourceRevisionError(f"source revision catalog is invalid: {exc}") from exc
    if existing is not None:
        return copy.deepcopy(existing.data)

    try:
        metadata_doc = repository.read("metadata.json", SchemaKind.METADATA)
        progress_doc = repository.read("progress.json", SchemaKind.PROGRESS)
        manifest_doc = repository.read("source-manifest.json", SchemaKind.SOURCE_MANIFEST)
    except (StorageError, RepositoryError, SchemaError) as exc:
        raise SourceRevisionError(f"cannot initialize source revisions from current state: {exc}") from exc

    try:
        active_claims = ClaimManager(repository).list_active()
    except Exception as exc:
        raise SourceRevisionError(f"cannot inspect claims before source revision initialization: {exc}") from exc
    if active_claims:
        raise SourceRevisionConflict("cannot initialize source revisions while literary claims are active")

    revision_id = "source-000001"
    created_at = _now_utc(now)

    progress = copy.deepcopy(progress_doc.data)
    chapters = progress.get("chapters")
    if not isinstance(chapters, list):
        raise SourceRevisionError("progress chapters must be an array")
    max_unit = 0
    for index, chapter in enumerate(chapters, 1):
        if not isinstance(chapter, dict):
            raise SourceRevisionError("progress chapter entries must be objects")
        unit_id = chapter.get("unit_id")
        if unit_id is None:
            unit_id = canonical_unit_id(index)
            chapter["unit_id"] = unit_id
        max_unit = max(max_unit, _unit_sequence(str(unit_id)))

    metadata = copy.deepcopy(metadata_doc.data)
    source = copy.deepcopy(dict(_source_identity(metadata)))
    source.setdefault("original_filename", str(source.get("filename") or metadata.get("source_file")))
    source["revision_id"] = revision_id
    metadata["source"] = source

    manifest = copy.deepcopy(manifest_doc.data)
    entries = manifest.get("extracted")
    if not isinstance(entries, list) or len(entries) != len(chapters):
        raise SourceRevisionError("source manifest cannot be aligned with progress during initialization")
    for chapter, entry in zip(chapters, entries):
        if not isinstance(entry, dict):
            raise SourceRevisionError("source manifest entries must be objects")
        entry["unit_id"] = unit_id_for_chapter(chapter)

    source_mode = str(source.get("storage_mode"))
    source_file = str(metadata.get("source_file"))
    snapshot_path = _snapshot_path(revision_id)
    catalog = {
        "schema_version": SCHEMA_VERSION,
        "book_slug": str(progress.get("book_slug")),
        "active_revision": revision_id,
        "next_sequence": 2,
        "next_unit_sequence": max_unit + 1,
        "revisions": [
            {
                "revision_id": revision_id,
                "parent_revision_id": None,
                "state": "active",
                "created_at": created_at,
                "source_file": source_file,
                "source_format": str(metadata.get("source_format")),
                "source_sha256": str(source.get("sha256")),
                "source_storage_mode": source_mode,
                "source_size_bytes": int(source.get("size_bytes")),
                "snapshot_path": snapshot_path,
                "delta_path": None,
            }
        ],
    }
    archived_units: list[dict[str, Any]] = []
    for chapter, entry in zip(chapters, entries):
        unit_id = unit_id_for_chapter(chapter)
        root_path = str(chapter["source_path"])
        try:
            raw = repository.storage.read(root_path).content
        except StorageNotFound as exc:
            raise SourceRevisionError(
                f"cannot archive initial source unit because it is missing: {root_path}"
            ) from exc
        expected_sha = str(entry.get("sha256"))
        if _sha256(raw) != expected_sha:
            raise SourceRevisionError(
                f"cannot archive initial source unit because its hash changed: {root_path}"
            )
        archive_path = (
            f"{SOURCE_REVISIONS_ROOT}/{revision_id}/corpus/"
            f"{int(chapter['number']):03d}-{unit_id}.md"
        )
        _create_or_verify(repository, archive_path, raw)
        archived_units.append(
            {
                "unit_id": unit_id,
                "number": chapter["number"],
                "title": chapter["title"],
                "classification": "initial",
                "source_path": root_path,
                "staged_path": archive_path,
            }
        )

    if source_mode == "embedded":
        root_source_path = f"source/{source_file}"
        try:
            source_raw = repository.storage.read(root_source_path).content
        except StorageNotFound as exc:
            raise SourceRevisionError(
                f"cannot archive initial embedded source because it is missing: {root_source_path}"
            ) from exc
        if len(source_raw) != int(source["size_bytes"]) or _sha256(source_raw) != str(source["sha256"]):
            raise SourceRevisionError(
                "cannot archive initial embedded source because its durable identity changed"
            )
        initial_source_archive = (
            f"{SOURCE_REVISIONS_ROOT}/{revision_id}/source/"
            f"{source.get('original_filename') or source_file}"
        )
        _create_or_verify(repository, initial_source_archive, source_raw)

    snapshot = {
        "schema_version": 1,
        "revision_id": revision_id,
        "parent_revision_id": None,
        "created_at": created_at,
        "metadata": metadata,
        "progress": progress,
        "source_manifest": manifest,
        "delta": {
            "unchanged": 0,
            "changed": 0,
            "new": len(chapters),
            "deleted": 0,
        },
        "units": archived_units,
    }

    _create_or_verify(repository, snapshot_path, _json_bytes(snapshot))

    try:
        if progress != progress_doc.data:
            progress_version = repository.write_if_version(
                "progress.json", SchemaKind.PROGRESS, progress, progress_doc.version
            )
        else:
            progress_version = progress_doc.version
        if metadata != metadata_doc.data:
            metadata_version = repository.write_if_version(
                "metadata.json", SchemaKind.METADATA, metadata, metadata_doc.version
            )
        else:
            metadata_version = metadata_doc.version
        if manifest != manifest_doc.data:
            manifest_version = repository.write_if_version(
                "source-manifest.json",
                SchemaKind.SOURCE_MANIFEST,
                manifest,
                manifest_doc.version,
            )
        else:
            manifest_version = manifest_doc.version
        repository.create(SOURCE_REVISIONS_PATH, SchemaKind.SOURCE_REVISIONS, catalog)
    except (StorageError, RepositoryError, SchemaError) as exc:
        raise SourceRevisionConflict(
            "source revision initialization was interrupted; re-run initialization before literary work: "
            + str(exc)
        ) from exc

    # Read back every mutable document so partial bootstrap cannot be mistaken for completion.
    try:
        repository.read("metadata.json", SchemaKind.METADATA)
        repository.read("progress.json", SchemaKind.PROGRESS)
        repository.read("source-manifest.json", SchemaKind.SOURCE_MANIFEST)
        result = repository.read(SOURCE_REVISIONS_PATH, SchemaKind.SOURCE_REVISIONS)
    except (StorageError, RepositoryError, SchemaError) as exc:
        raise SourceRevisionConflict(f"source revision initialization failed read-back validation: {exc}") from exc
    del progress_version, metadata_version, manifest_version
    return copy.deepcopy(result.data)


def historical_extracted_paths(repository: WorkflowStateRepository) -> set[str]:
    """Return extracted root paths referenced by immutable source revision snapshots."""

    try:
        catalog = repository.read(SOURCE_REVISIONS_PATH, SchemaKind.SOURCE_REVISIONS).data
    except (StorageNotFound, StorageError, RepositoryError, SchemaError):
        return set()
    result: set[str] = set()
    for revision in catalog.get("revisions", []):
        if not isinstance(revision, Mapping):
            continue
        try:
            snapshot = _read_json(repository, str(revision.get("snapshot_path")))
        except SourceRevisionError:
            continue
        progress = snapshot.get("progress")
        if not isinstance(progress, Mapping):
            continue
        chapters = progress.get("chapters")
        if not isinstance(chapters, list):
            continue
        for chapter in chapters:
            if isinstance(chapter, Mapping):
                path = chapter.get("source_path")
                if isinstance(path, str) and path.startswith("extracted/"):
                    result.add(path)
    return result


def source_revision_integrity_errors(
    repository: WorkflowStateRepository,
    metadata: Mapping[str, Any],
    progress: Mapping[str, Any],
    manifest: Mapping[str, Any],
    *,
    deep: bool = False,
) -> list[str]:
    """Verify revision catalog, immutable corpus archives, and active-revision binding."""

    errors: list[str] = []
    try:
        catalog = repository.read(SOURCE_REVISIONS_PATH, SchemaKind.SOURCE_REVISIONS).data
    except StorageNotFound:
        return ["Missing source-revisions.json for current workflow book"]
    except (StorageError, RepositoryError, SchemaError) as exc:
        return [f"Invalid source-revisions.json: {exc}"]

    source = metadata.get("source")
    if not isinstance(source, Mapping):
        return ["metadata.json source identity is required for source revision validation"]

    active = catalog.get("active_revision")
    if source.get("revision_id") != active:
        errors.append("metadata.json source.revision_id disagrees with source-revisions.json active_revision")
    active_entry: Mapping[str, Any] | None = None
    for entry in catalog.get("revisions", []):
        if isinstance(entry, Mapping) and entry.get("revision_id") == active:
            active_entry = entry
            break
    if active_entry is None:
        errors.append("source-revisions.json active revision entry is missing")
    else:
        if active_entry.get("source_sha256") != source.get("sha256"):
            errors.append("active source revision SHA-256 disagrees with metadata.json")
        if active_entry.get("source_sha256") != manifest.get("source_sha256"):
            errors.append("active source revision SHA-256 disagrees with source-manifest.json")
        if active_entry.get("source_file") != metadata.get("source_file"):
            errors.append("active source revision filename disagrees with metadata.json")

    try:
        active_manifest_units = _manifest_by_unit(progress, manifest)
    except SourceRevisionError as exc:
        errors.append(str(exc))
        active_manifest_units = []
    active_by_unit = {item["unit_id"]: item for item in active_manifest_units}

    for entry in catalog.get("revisions", []):
        if not isinstance(entry, Mapping) or entry.get("state") == "discarded":
            continue
        revision_id = str(entry.get("revision_id"))
        snapshot_path = entry.get("snapshot_path")
        if not isinstance(snapshot_path, str):
            errors.append(f"{revision_id}: immutable snapshot path is invalid")
            continue
        try:
            snapshot = _read_json(repository, snapshot_path)
        except SourceRevisionError as exc:
            errors.append(str(exc))
            continue
        if snapshot.get("revision_id") != revision_id:
            errors.append(f"{revision_id}: immutable snapshot revision identity mismatch")
            continue

        snapshot_manifest = snapshot.get("source_manifest")
        snapshot_units = snapshot.get("units")
        if not isinstance(snapshot_manifest, Mapping) or not isinstance(snapshot_units, list):
            errors.append(f"{revision_id}: immutable snapshot corpus metadata is invalid")
            continue
        if not deep:
            continue
        manifest_items = snapshot_manifest.get("extracted")
        if not isinstance(manifest_items, list) or len(manifest_items) != len(snapshot_units):
            errors.append(f"{revision_id}: immutable snapshot unit count mismatch")
            continue
        manifest_by_unit: dict[str, Mapping[str, Any]] = {}
        for item in manifest_items:
            if not isinstance(item, Mapping):
                errors.append(f"{revision_id}: immutable manifest contains an invalid unit entry")
                continue
            unit_id = item.get("unit_id")
            if not isinstance(unit_id, str):
                errors.append(f"{revision_id}: immutable manifest unit identity is missing")
                continue
            manifest_by_unit[unit_id] = item

        for unit in snapshot_units:
            if not isinstance(unit, Mapping):
                errors.append(f"{revision_id}: immutable snapshot contains an invalid unit record")
                continue
            unit_id = unit.get("unit_id")
            archive_path = unit.get("staged_path")
            item = manifest_by_unit.get(str(unit_id))
            if item is None:
                errors.append(f"{revision_id}: immutable snapshot unit {unit_id!r} is absent from manifest")
                continue
            if not isinstance(archive_path, str) or not archive_path:
                errors.append(f"{revision_id}: immutable corpus archive path is missing for {unit_id}")
                continue
            try:
                archived = repository.storage.read(archive_path).content
            except StorageNotFound:
                errors.append(f"{revision_id}: immutable corpus archive is missing: {archive_path}")
                continue
            except StorageError as exc:
                errors.append(f"{revision_id}: cannot read immutable corpus archive {archive_path}: {exc}")
                continue
            if _sha256(archived) != item.get("sha256"):
                errors.append(f"{revision_id}: immutable corpus archive hash mismatch: {archive_path}")

        snapshot_metadata = snapshot.get("metadata")
        if isinstance(snapshot_metadata, Mapping):
            snapshot_source = snapshot_metadata.get("source")
            if (
                isinstance(snapshot_source, Mapping)
                and snapshot_source.get("storage_mode") == "embedded"
            ):
                original = snapshot_source.get("original_filename")
                if not isinstance(original, str) or not original:
                    errors.append(f"{revision_id}: embedded source archive filename is missing")
                else:
                    archive_path = f"{SOURCE_REVISIONS_ROOT}/{revision_id}/source/{original}"
                    try:
                        archived_source = repository.storage.read(archive_path).content
                    except StorageNotFound:
                        errors.append(f"{revision_id}: immutable source binary is missing: {archive_path}")
                    except StorageError as exc:
                        errors.append(f"{revision_id}: cannot read immutable source binary {archive_path}: {exc}")
                    else:
                        if (
                            len(archived_source) != snapshot_source.get("size_bytes")
                            or _sha256(archived_source) != snapshot_source.get("sha256")
                        ):
                            errors.append(f"{revision_id}: immutable source binary identity mismatch")

    if active_entry is not None:
        try:
            active_snapshot = _read_json(repository, str(active_entry.get("snapshot_path")))
        except SourceRevisionError:
            active_snapshot = None
        if isinstance(active_snapshot, Mapping):
            snapshot_manifest = active_snapshot.get("source_manifest")
            snapshot_items = (
                snapshot_manifest.get("extracted")
                if isinstance(snapshot_manifest, Mapping)
                else None
            )
            current_items = manifest.get("extracted")
            if isinstance(snapshot_items, list) and isinstance(current_items, list):
                def identity_rows(items: list[Any]) -> list[tuple[Any, ...]]:
                    return [
                        (
                            item.get("unit_id"),
                            item.get("number"),
                            item.get("title"),
                            item.get("path"),
                            item.get("sha256"),
                        )
                        for item in items
                        if isinstance(item, Mapping)
                    ]

                if identity_rows(snapshot_items) != identity_rows(current_items):
                    errors.append(
                        "active immutable source snapshot reading order/identity disagrees with current corpus"
                    )
            else:
                errors.append("active immutable source snapshot manifest is invalid")

    return errors


class SourceRevisionManager:
    """Stage source editions and promote them through a fail-closed durable marker."""

    def __init__(
        self,
        repository: WorkflowStateRepository,
        *,
        now: Callable[[], datetime] | None = None,
        coordination: BookCoordinationManager | None = None,
    ):
        self.repository = repository
        self._now = now
        self._coordination = coordination or BookCoordinationManager(repository, now=now)

    def catalog(self) -> LoadedDocument:
        try:
            return self.repository.read(SOURCE_REVISIONS_PATH, SchemaKind.SOURCE_REVISIONS)
        except StorageNotFound as exc:
            raise SourceRevisionError(
                "source-revisions.json is missing; initialize or upgrade the book workspace first"
            ) from exc
        except (StorageError, RepositoryError, SchemaError) as exc:
            raise SourceRevisionError(f"source revision catalog is invalid: {exc}") from exc

    @staticmethod
    def _entry(catalog: Mapping[str, Any], revision_id: str) -> dict[str, Any]:
        for entry in catalog.get("revisions", []):
            if isinstance(entry, Mapping) and entry.get("revision_id") == revision_id:
                return copy.deepcopy(dict(entry))
        raise SourceRevisionError(f"unknown source revision: {revision_id}")

    def _ensure_no_other_staged(self, catalog: Mapping[str, Any], source_sha256: str) -> dict[str, Any] | None:
        for entry in catalog.get("revisions", []):
            if not isinstance(entry, Mapping) or entry.get("state") != "staged":
                continue
            if entry.get("source_sha256") == source_sha256:
                return copy.deepcopy(dict(entry))
            raise SourceRevisionDecisionRequired(
                f"source revision {entry.get('revision_id')} is already staged; promote or discard it before staging another edition"
            )
        return None

    def _verify_active_corpus(
        self,
        metadata: Mapping[str, Any],
        progress: Mapping[str, Any],
        manifest: Mapping[str, Any],
    ) -> None:
        source = _source_identity(metadata)
        expected = {
            "source_file": metadata.get("source_file"),
            "source_format": metadata.get("source_format"),
            "source_sha256": source.get("sha256"),
            "source_storage_mode": source.get("storage_mode"),
            "source_size_bytes": source.get("size_bytes"),
        }
        for key, value in expected.items():
            if manifest.get(key) != value:
                raise SourceRevisionError(
                    f"active source-manifest {key} disagrees with metadata source identity"
                )

        current = _manifest_by_unit(progress, manifest)
        for item in current:
            path = str(item["chapter"]["source_path"])
            try:
                raw = self.repository.storage.read(path).content
            except StorageNotFound as exc:
                raise SourceRevisionError(f"active extracted artifact is missing: {path}") from exc
            if _sha256(raw) != item["sha256"]:
                raise SourceRevisionError(
                    f"active extracted artifact hash mismatch before source update: {path}"
                )

        if source.get("storage_mode") == "embedded":
            path = f"source/{metadata.get('source_file')}"
            try:
                raw = self.repository.storage.read(path).content
            except StorageNotFound as exc:
                raise SourceRevisionError(f"active embedded source binary is missing: {path}") from exc
            if len(raw) != source.get("size_bytes") or _sha256(raw) != source.get("sha256"):
                raise SourceRevisionError(
                    "active embedded source binary identity mismatch before source update"
                )

    @staticmethod
    def _candidate_units(units: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for index, unit in enumerate(units, 1):
            if not isinstance(unit, Mapping):
                raise SourceRevisionError(f"candidate unit {index} must be an object")
            title = unit.get("title")
            content = unit.get("content")
            if not isinstance(title, str) or not title.strip():
                raise SourceRevisionError(f"candidate unit {index} title must be non-empty")
            if isinstance(content, str):
                raw = content.encode("utf-8")
            elif isinstance(content, bytes):
                raw = content
            else:
                raise SourceRevisionError(f"candidate unit {index} content must be text or bytes")
            if not raw:
                raise SourceRevisionError(f"candidate unit {index} content must not be empty")
            result.append(
                {
                    "number": index,
                    "title": title,
                    "content": raw,
                    "sha256": _sha256(raw),
                }
            )
        if not result:
            raise SourceRevisionError("candidate source must contain at least one readable unit")
        return result

    @staticmethod
    def _delta_mapping(
        current: list[dict[str, Any]],
        candidate: list[dict[str, Any]],
    ) -> tuple[dict[int, int], dict[int, int], set[int], set[int]]:
        old_hashes = [item["sha256"] for item in current]
        new_hashes = [item["sha256"] for item in candidate]
        matcher = difflib.SequenceMatcher(a=old_hashes, b=new_hashes, autojunk=False)

        unchanged: dict[int, int] = {}
        used_old: set[int] = set()
        used_new: set[int] = set()
        for block in matcher.get_matching_blocks():
            for offset in range(block.size):
                old_index = block.a + offset
                new_index = block.b + offset
                unchanged[new_index] = old_index
                used_old.add(old_index)
                used_new.add(new_index)

        old_unmatched_hashes: dict[str, list[int]] = {}
        new_unmatched_hashes: dict[str, list[int]] = {}
        for index, item in enumerate(current):
            if index not in used_old:
                old_unmatched_hashes.setdefault(str(item["sha256"]), []).append(index)
        for index, item in enumerate(candidate):
            if index not in used_new:
                new_unmatched_hashes.setdefault(str(item["sha256"]), []).append(index)

        for digest in sorted(set(old_unmatched_hashes) & set(new_unmatched_hashes)):
            old_indexes = old_unmatched_hashes[digest]
            new_indexes = new_unmatched_hashes[digest]
            if len(old_indexes) != 1 or len(new_indexes) != 1:
                continue
            old_index = old_indexes[0]
            new_index = new_indexes[0]
            unchanged[new_index] = old_index
            used_old.add(old_index)
            used_new.add(new_index)

        def norm(title: str) -> str:
            return re.sub(r"\s+", " ", title).strip().casefold()

        old_titles: dict[str, list[int]] = {}
        new_titles: dict[str, list[int]] = {}
        for index, item in enumerate(current):
            if index not in used_old:
                old_titles.setdefault(norm(str(item["title"])), []).append(index)
        for index, item in enumerate(candidate):
            if index not in used_new:
                new_titles.setdefault(norm(str(item["title"])), []).append(index)

        changed: dict[int, int] = {}
        for title in sorted(set(old_titles) & set(new_titles)):
            if len(old_titles[title]) != 1 or len(new_titles[title]) != 1:
                continue
            old_index = old_titles[title][0]
            new_index = new_titles[title][0]
            changed[new_index] = old_index
            used_old.add(old_index)
            used_new.add(new_index)

        new_units = set(range(len(candidate))) - used_new
        deleted = set(range(len(current))) - used_old
        return unchanged, changed, new_units, deleted

    def stage(
        self,
        *,
        source_filename: str,
        source_format: str,
        source_sha256: str,
        source_size_bytes: int,
        source_bytes: bytes | None,
        private_source: bool,
        candidate_units: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        source_filename = _safe_basename(source_filename)
        if not isinstance(source_format, str) or not source_format.strip():
            raise SourceRevisionError("source format must be non-empty")
        if not isinstance(source_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
            raise SourceRevisionError("candidate source SHA-256 is invalid")
        if type(source_size_bytes) is not int or source_size_bytes < 0:
            raise SourceRevisionError("candidate source size must be a non-negative integer")
        if not private_source and not isinstance(source_bytes, bytes):
            raise SourceRevisionError("embedded source update requires exact source bytes")
        if isinstance(source_bytes, bytes):
            if len(source_bytes) != source_size_bytes or _sha256(source_bytes) != source_sha256:
                raise SourceRevisionError("candidate source bytes disagree with supplied identity")

        try:
            metadata_doc = self.repository.read("metadata.json", SchemaKind.METADATA)
            progress_doc = self.repository.read("progress.json", SchemaKind.PROGRESS)
            manifest_doc = self.repository.read("source-manifest.json", SchemaKind.SOURCE_MANIFEST)
        except (StorageError, RepositoryError, SchemaError) as exc:
            raise SourceRevisionError(f"current source state is invalid: {exc}") from exc
        catalog_doc = self.catalog()
        catalog = copy.deepcopy(catalog_doc.data)
        self._verify_active_corpus(metadata_doc.data, progress_doc.data, manifest_doc.data)
        source = _source_identity(metadata_doc.data)
        if source.get("revision_id") != catalog.get("active_revision"):
            raise SourceRevisionError("metadata active source revision disagrees with source-revisions.json")
        if source.get("sha256") == source_sha256:
            return {
                "state": "already_active",
                "revision_id": catalog["active_revision"],
                "delta": {"unchanged": len(progress_doc.data["chapters"]), "changed": 0, "new": 0, "deleted": 0},
            }

        if self._promotion_marker() is not None:
            raise SourceRevisionConflict(
                "cannot stage a source update while source promotion recovery is pending"
            )
        if self._coordination.finalization_active():
            raise SourceRevisionConflict("cannot stage a source update while finalization is active")
        active_claims = ClaimManager(self.repository).list_active()
        if active_claims:
            raise SourceRevisionConflict(
                "cannot stage a source update while literary claims are active"
            )

        existing_staged = self._ensure_no_other_staged(catalog, source_sha256)
        if existing_staged is not None:
            delta = _read_json(self.repository, str(existing_staged["delta_path"]))
            return {
                "state": "already_staged",
                "revision_id": existing_staged["revision_id"],
                "delta": copy.deepcopy(delta["summary"]),
            }

        current = _manifest_by_unit(progress_doc.data, manifest_doc.data)
        candidate = self._candidate_units(candidate_units)
        unchanged, changed, new_indexes, deleted_indexes = self._delta_mapping(current, candidate)

        revision_id = f"source-{int(catalog['next_sequence']):06d}"
        parent_revision = str(catalog["active_revision"])
        next_unit_sequence = int(catalog["next_unit_sequence"])
        created_at = _now_utc(self._now)

        candidate_progress: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "book_slug": progress_doc.data["book_slug"],
            "chapters": [],
        }
        candidate_entries: list[dict[str, Any]] = []
        unit_records: list[dict[str, Any]] = []

        for candidate_index, item in enumerate(candidate):
            new_number = candidate_index + 1
            staged_path: str
            if candidate_index in unchanged:
                old_index = unchanged[candidate_index]
                old = current[old_index]
                chapter = copy.deepcopy(old["chapter"])
                chapter["number"] = new_number
                chapter["title"] = item["title"]
                unit_id = str(old["unit_id"])
                classification = "unchanged"
                root_source_path = str(chapter["source_path"])
            else:
                if candidate_index in changed:
                    old_index = changed[candidate_index]
                    old = current[old_index]
                    unit_id = str(old["unit_id"])
                    slug = str(old["chapter"].get("slug") or _slugify(item["title"]))
                    classification = "changed"
                    previous_number = old["chapter"].get("number")
                else:
                    unit_id = f"chapter-{next_unit_sequence:06d}"
                    next_unit_sequence += 1
                    slug = _slugify(item["title"])
                    classification = "new"
                    previous_number = None
                root_source_path = f"extracted/{unit_id}-{revision_id}.md"
                chapter = {
                    "unit_id": unit_id,
                    "number": new_number,
                    "title": item["title"],
                    "slug": slug,
                    "source_path": root_source_path,
                    "translation_path": f"translated/{unit_id}-{revision_id}.md",
                    "status": "extracted",
                }

            staged_path = f"{SOURCE_REVISIONS_ROOT}/{revision_id}/corpus/{new_number:03d}-{unit_id}.md"
            _create_or_verify(self.repository, staged_path, item["content"])
            candidate_progress["chapters"].append(chapter)
            candidate_entries.append(
                {
                    "unit_id": unit_id,
                    "number": new_number,
                    "title": item["title"],
                    "path": root_source_path,
                    "sha256": item["sha256"],
                }
            )
            unit_record = {
                "unit_id": unit_id,
                "number": new_number,
                "title": item["title"],
                "classification": classification,
                "source_path": root_source_path,
                "staged_path": staged_path,
            }
            if classification == "changed":
                unit_record["previous_number"] = previous_number
            unit_records.append(unit_record)

        deleted_records = [
            {
                "unit_id": current[index]["unit_id"],
                "number": current[index]["chapter"]["number"],
                "title": current[index]["title"],
                "source_path": current[index]["chapter"]["source_path"],
                "translation_path": current[index]["chapter"]["translation_path"],
            }
            for index in sorted(deleted_indexes)
        ]

        target_source_file = f"{revision_id}-{source_filename}"
        target_metadata = copy.deepcopy(metadata_doc.data)
        target_metadata["source_format"] = source_format
        target_metadata["source_file"] = target_source_file
        target_metadata["chapter_count"] = len(candidate)
        target_metadata["source_updated_at"] = created_at
        target_metadata["source"] = {
            "storage_mode": "private_external" if private_source else "embedded",
            "filename": target_source_file,
            "original_filename": source_filename,
            "size_bytes": source_size_bytes,
            "sha256": source_sha256,
            "revision_id": revision_id,
        }

        target_manifest = {
            "schema_version": SCHEMA_VERSION,
            "source_file": target_source_file,
            "source_format": source_format,
            "source_sha256": source_sha256,
            "source_storage_mode": "private_external" if private_source else "embedded",
            "source_size_bytes": source_size_bytes,
            "chapter_count": len(candidate_entries),
            "extracted": candidate_entries,
        }

        summary = {
            "unchanged": len(unchanged),
            "changed": len(changed),
            "new": len(new_indexes),
            "deleted": len(deleted_indexes),
        }
        delta = {
            "schema_version": 1,
            "revision_id": revision_id,
            "parent_revision_id": parent_revision,
            "summary": summary,
            "units": unit_records,
            "deleted_units": deleted_records,
        }

        snapshot_path = _snapshot_path(revision_id)
        delta_path = _delta_path(revision_id)
        snapshot = {
            "schema_version": 1,
            "revision_id": revision_id,
            "parent_revision_id": parent_revision,
            "created_at": created_at,
            "metadata": target_metadata,
            "progress": candidate_progress,
            "source_manifest": target_manifest,
            "delta": summary,
            "units": unit_records,
            "deleted_units": deleted_records,
        }
        _create_or_verify(self.repository, delta_path, _json_bytes(delta))
        _create_or_verify(self.repository, snapshot_path, _json_bytes(snapshot))
        if not private_source:
            assert source_bytes is not None
            source_stage_path = f"{SOURCE_REVISIONS_ROOT}/{revision_id}/source/{source_filename}"
            _create_or_verify(self.repository, source_stage_path, source_bytes)

        entry = {
            "revision_id": revision_id,
            "parent_revision_id": parent_revision,
            "state": "staged",
            "created_at": created_at,
            "source_file": target_source_file,
            "source_format": source_format,
            "source_sha256": source_sha256,
            "source_storage_mode": "private_external" if private_source else "embedded",
            "source_size_bytes": source_size_bytes,
            "snapshot_path": snapshot_path,
            "delta_path": delta_path,
        }
        catalog["revisions"].append(entry)
        catalog["next_sequence"] = int(catalog["next_sequence"]) + 1
        catalog["next_unit_sequence"] = next_unit_sequence
        try:
            new_version = self.repository.write_if_version(
                SOURCE_REVISIONS_PATH,
                SchemaKind.SOURCE_REVISIONS,
                catalog,
                catalog_doc.version,
            )
        except (StorageVersionConflict, StorageError, RepositoryError, SchemaError) as exc:
            raise SourceRevisionConflict(
                "source revision catalog changed while staging; re-read before retrying"
            ) from exc
        del new_version
        return {"state": "staged", "revision_id": revision_id, "delta": summary}

    def _target_catalog(
        self,
        catalog: Mapping[str, Any],
        revision_id: str,
    ) -> dict[str, Any]:
        result = copy.deepcopy(dict(catalog))
        found = False
        for entry in result["revisions"]:
            if entry["revision_id"] == result["active_revision"] and entry["state"] == "active":
                entry["state"] = "superseded"
            if entry["revision_id"] == revision_id:
                entry["state"] = "active"
                found = True
        if not found:
            raise SourceRevisionError(f"source revision {revision_id} is missing from catalog")
        result["active_revision"] = revision_id
        return result

    def _promotion_marker(self) -> LoadedDocument | None:
        try:
            return self.repository.read(SOURCE_PROMOTION_PATH, SchemaKind.SOURCE_PROMOTION)
        except StorageNotFound:
            return None
        except (StorageError, RepositoryError, SchemaError) as exc:
            raise SourceRevisionConflict(f"source promotion marker is invalid: {exc}") from exc

    def _copy_staged_artifacts(self, snapshot: Mapping[str, Any], entry: Mapping[str, Any]) -> None:
        metadata = snapshot.get("metadata")
        if not isinstance(metadata, Mapping):
            raise SourceRevisionError("staged revision metadata is invalid")
        source = metadata.get("source")
        if not isinstance(source, Mapping):
            raise SourceRevisionError("staged revision source identity is invalid")
        revision_id = str(entry["revision_id"])

        if source.get("storage_mode") == "embedded":
            original = _safe_basename(str(source.get("original_filename")))
            staged = f"{SOURCE_REVISIONS_ROOT}/{revision_id}/source/{original}"
            try:
                raw = self.repository.storage.read(staged).content
            except StorageNotFound as exc:
                raise SourceRevisionError(f"staged source binary is missing: {staged}") from exc
            if _sha256(raw) != source.get("sha256"):
                raise SourceRevisionError("staged source binary SHA-256 changed before promotion")
            _create_or_verify(self.repository, f"source/{metadata['source_file']}", raw)

        units = snapshot.get("units")
        if not isinstance(units, list):
            raise SourceRevisionError("staged revision unit map is invalid")
        for unit in units:
            if not isinstance(unit, Mapping) or unit.get("classification") == "unchanged":
                continue
            staged_path = unit.get("staged_path")
            target_path = unit.get("source_path")
            if not isinstance(staged_path, str) or not isinstance(target_path, str):
                raise SourceRevisionError("staged changed/new unit paths are invalid")
            raw = self.repository.storage.read(staged_path).content
            _create_or_verify(self.repository, target_path, raw)

    def _ensure_document(
        self,
        *,
        path: str,
        schema: SchemaKind,
        target: Mapping[str, Any],
        base_version: str,
    ) -> str:
        target_bytes = self.repository.serialize(path, schema, target)
        try:
            current_raw = self.repository.storage.read(path)
        except StorageError as exc:
            raise SourceRevisionConflict(f"cannot read {path} during source promotion: {exc}") from exc
        if current_raw.content == target_bytes:
            return current_raw.version
        if current_raw.version != base_version:
            raise SourceRevisionConflict(
                f"{path} changed during source promotion; expected {base_version}, got {current_raw.version}"
            )
        try:
            return self.repository.write_if_version(path, schema, target, base_version)
        except (StorageVersionConflict, StorageError, RepositoryError, SchemaError) as exc:
            raise SourceRevisionConflict(f"cannot promote {path}: {exc}") from exc

    def promote(
        self,
        revision_id: str,
        *,
        allow_deletions: bool = False,
        session_id: str = "source-update",
    ) -> dict[str, Any]:
        catalog_doc = self.catalog()
        catalog = catalog_doc.data
        if catalog.get("active_revision") == revision_id:
            marker = self._promotion_marker()
            if marker is not None and marker.data.get("revision_id") == revision_id:
                try:
                    self.repository.delete_if_version(
                        SOURCE_PROMOTION_PATH,
                        SchemaKind.SOURCE_PROMOTION,
                        marker.version,
                    )
                except Exception:
                    pass
            return {"state": "already_active", "revision_id": revision_id}

        entry = self._entry(catalog, revision_id)
        if entry.get("state") != "staged":
            raise SourceRevisionError(
                f"source revision {revision_id} is not staged; state={entry.get('state')!r}"
            )
        snapshot = _read_json(self.repository, str(entry["snapshot_path"]))
        delta = snapshot.get("delta")
        if not isinstance(delta, Mapping):
            raise SourceRevisionError("staged source revision delta is invalid")
        if int(delta.get("deleted", 0)) > 0 and not allow_deletions:
            raise SourceRevisionDecisionRequired(
                f"source revision {revision_id} deletes {delta.get('deleted')} unit(s); "
                "re-run promotion with --allow-deletions after confirming the edition change"
            )

        try:
            lease = self._coordination.acquire(
                operation="source_promotion",
                session_id=session_id,
                lease_seconds=SOURCE_PROMOTION_LEASE_SECONDS,
            )
        except CoordinationError as exc:
            raise SourceRevisionConflict(f"source promotion is blocked by book coordination: {exc}") from exc

        try:
            if self._coordination.finalization_active():
                raise SourceRevisionConflict("source promotion is blocked while finalization is active")
            claims = ClaimManager(self.repository).list_active()
            if claims:
                owners = ", ".join(f"{c.data['unit_id']}:{c.data['role']}" for c in claims)
                raise SourceRevisionConflict(
                    "source promotion is blocked while literary claims are active: " + owners
                )

            metadata_doc = self.repository.read("metadata.json", SchemaKind.METADATA)
            progress_doc = self.repository.read("progress.json", SchemaKind.PROGRESS)
            manifest_doc = self.repository.read("source-manifest.json", SchemaKind.SOURCE_MANIFEST)
            catalog_doc = self.catalog()
            if catalog_doc.data.get("active_revision") != entry.get("parent_revision_id"):
                raise SourceRevisionConflict(
                    "active source revision changed after this candidate was staged"
                )

            target_metadata = snapshot.get("metadata")
            target_progress = snapshot.get("progress")
            target_manifest = snapshot.get("source_manifest")
            if not all(isinstance(value, Mapping) for value in (target_metadata, target_progress, target_manifest)):
                raise SourceRevisionError("staged source revision snapshot lacks target machine state")
            target_catalog = self._target_catalog(catalog_doc.data, revision_id)

            targets = {
                "metadata": ("metadata.json", SchemaKind.METADATA, target_metadata, metadata_doc.version),
                "progress": ("progress.json", SchemaKind.PROGRESS, target_progress, progress_doc.version),
                "source_manifest": (
                    "source-manifest.json",
                    SchemaKind.SOURCE_MANIFEST,
                    target_manifest,
                    manifest_doc.version,
                ),
                "source_revisions": (
                    SOURCE_REVISIONS_PATH,
                    SchemaKind.SOURCE_REVISIONS,
                    target_catalog,
                    catalog_doc.version,
                ),
            }
            target_sha = {
                key: _sha256(self.repository.serialize(path, schema, target))
                for key, (path, schema, target, _) in targets.items()
            }
            base_revisions = {
                "metadata": metadata_doc.version,
                "progress": progress_doc.version,
                "source_manifest": manifest_doc.version,
                "source_revisions": catalog_doc.version,
            }

            marker = self._promotion_marker()
            if marker is None:
                marker_data = {
                    "schema_version": SCHEMA_VERSION,
                    "book_slug": str(progress_doc.data["book_slug"]),
                    "revision_id": revision_id,
                    "parent_revision_id": str(entry["parent_revision_id"]),
                    "phase": "preparing",
                    "session_id": session_id,
                    "started_at": _now_utc(self._now),
                    "base_revisions": base_revisions,
                    "target_sha256": target_sha,
                }
                try:
                    marker_version = self.repository.create(
                        SOURCE_PROMOTION_PATH,
                        SchemaKind.SOURCE_PROMOTION,
                        marker_data,
                    )
                except StorageAlreadyExists:
                    marker = self._promotion_marker()
                    if marker is None:
                        raise SourceRevisionConflict("source promotion marker changed during admission")
                else:
                    marker = LoadedDocument(marker_data, marker_version)
            assert marker is not None
            if marker.data.get("revision_id") != revision_id:
                raise SourceRevisionConflict(
                    f"another source promotion is pending: {marker.data.get('revision_id')}"
                )
            if marker.data.get("target_sha256") != target_sha:
                raise SourceRevisionConflict("staged source promotion target changed after admission")

            self._copy_staged_artifacts(snapshot, entry)

            resulting: dict[str, str] = {}
            marker_base = marker.data["base_revisions"]
            for key in ("progress", "metadata", "source_manifest", "source_revisions"):
                path, schema, target, _ = targets[key]
                resulting[key] = self._ensure_document(
                    path=path,
                    schema=schema,
                    target=target,
                    base_version=str(marker_base[key]),
                )

            # Read-back validates translation hashes, source state, and catalog shape.
            active_progress = self.repository.read("progress.json", SchemaKind.PROGRESS)
            active_metadata = self.repository.read("metadata.json", SchemaKind.METADATA)
            active_manifest = self.repository.read("source-manifest.json", SchemaKind.SOURCE_MANIFEST)
            active_catalog = self.catalog()
            if active_catalog.data.get("active_revision") != revision_id:
                raise SourceRevisionConflict("source revision catalog did not promote the target revision")
            if active_metadata.data.get("source", {}).get("revision_id") != revision_id:
                raise SourceRevisionConflict("metadata did not promote the target source revision")
            if active_manifest.data.get("source_sha256") != entry.get("source_sha256"):
                raise SourceRevisionConflict("source manifest did not promote the target source identity")
            del active_progress

            promoted_marker = copy.deepcopy(marker.data)
            promoted_marker["phase"] = "promoted"
            try:
                promoted_marker_version = self.repository.write_if_version(
                    SOURCE_PROMOTION_PATH,
                    SchemaKind.SOURCE_PROMOTION,
                    promoted_marker,
                    marker.version,
                )
                self.repository.delete_if_version(
                    SOURCE_PROMOTION_PATH,
                    SchemaKind.SOURCE_PROMOTION,
                    promoted_marker_version,
                )
            except (StorageError, RepositoryError, SchemaError) as exc:
                raise SourceRevisionConflict(
                    "source revision was promoted but cleanup is incomplete; re-run promotion to recover: "
                    + str(exc)
                ) from exc

            return {
                "state": "promoted",
                "revision_id": revision_id,
                "delta": copy.deepcopy(dict(delta)),
                "state_revisions": resulting,
            }
        finally:
            try:
                self._coordination.release(lease)
            except CoordinationError as exc:
                raise SourceRevisionConflict(
                    f"source promotion could not release coordination lease: {exc}"
                ) from exc

    def discard(self, revision_id: str) -> dict[str, Any]:
        marker = self._promotion_marker()
        if marker is not None:
            raise SourceRevisionConflict("cannot discard a source revision while promotion recovery is pending")
        catalog_doc = self.catalog()
        catalog = copy.deepcopy(catalog_doc.data)
        if catalog.get("active_revision") == revision_id:
            raise SourceRevisionError("cannot discard the active source revision")
        found = False
        for entry in catalog["revisions"]:
            if entry["revision_id"] == revision_id:
                found = True
                if entry["state"] == "discarded":
                    return {"state": "already_discarded", "revision_id": revision_id}
                if entry["state"] != "staged":
                    raise SourceRevisionError(
                        f"only staged source revisions can be discarded; state={entry['state']!r}"
                    )
                entry["state"] = "discarded"
        if not found:
            raise SourceRevisionError(f"unknown source revision: {revision_id}")
        try:
            self.repository.write_if_version(
                SOURCE_REVISIONS_PATH,
                SchemaKind.SOURCE_REVISIONS,
                catalog,
                catalog_doc.version,
            )
        except (StorageError, RepositoryError, SchemaError) as exc:
            raise SourceRevisionConflict(f"source revision catalog changed before discard: {exc}") from exc
        return {"state": "discarded", "revision_id": revision_id}

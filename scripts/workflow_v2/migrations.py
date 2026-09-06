"""Explicit Workflow v2 schema migration, planning, and execution."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .coordination import CoordinationError
from .migration_journal import (
    MIGRATION_PATH,
    MigrationJournalError,
    load_migration_journal,
    serialize_migration_journal,
)
from .repository import RepositoryError, WorkflowStateRepository
from .reviews import REVIEW_CONTRACT_PATH, REVIEW_EVIDENCE_VERSION
from .schemas import SchemaError, SchemaKind, parse_document
from .source_integrity import (
    SourceIntegrityError,
    build_private_source_manifest_from_identity,
    build_source_manifest,
    sha256_path,
)
from .storage import (
    StorageAlreadyExists,
    StorageError,
    StorageNotFound,
    StorageVersionConflict,
)


CANONICAL_REPOSITORY = "https://github.com/tim8es/book-translator"
FINALIZATION_PATH = ".workflow/finalization.json"
CLAIM_PREFIX = ".workflow/claims"


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


@dataclass(frozen=True)
class PlannedWrite:
    """One exact target write produced by a read-only migration plan."""

    path: str
    kind: SchemaKind
    original_exists: bool
    original_version: str | None
    original_bytes: bytes | None
    target_data: dict[str, Any]
    target_bytes: bytes
    from_version: int | None
    to_version: int


@dataclass(frozen=True)
class MigrationPlan:
    """A fully validated, immutable description of one explicit upgrade."""

    book_slug: str
    from_revision: str | None
    to_revision: str
    writes: tuple[PlannedWrite, ...]
    lifecycle_downgrades: tuple[int, ...]
    changed: bool

    def write_for(self, path: str) -> PlannedWrite | None:
        for write in self.writes:
            if write.path == path:
                return write
        return None


@dataclass(frozen=True)
class MigrationResult:
    """Stable outcome returned by explicit workflow upgrade execution/recovery."""

    book_slug: str
    from_revision: str | None
    to_revision: str
    outcome: str
    migrated_paths: tuple[str, ...]
    lifecycle_downgrades: tuple[int, ...]


@dataclass(frozen=True)
class _RawDocument:
    path: str
    kind: SchemaKind
    data: dict[str, Any]
    content: bytes
    version: str
    migrated: MigratedDocument


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


class MigrationPlanner:
    """Build a complete upgrade plan without mutating durable state."""

    def __init__(
        self,
        repository: WorkflowStateRepository,
        *,
        book_dir: Path,
        artifact_reader: Callable[[str], bytes],
        now: Callable[[], datetime] | None = None,
    ):
        self.repository = repository
        self.book_dir = Path(book_dir)
        self._artifact_reader = artifact_reader
        self._now_factory = now or (lambda: datetime.now(timezone.utc))

    def _now(self) -> datetime:
        value = self._now_factory()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise MigrationCompatibilityError("migration clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _parse_utc(value: object, label: str) -> datetime:
        if not isinstance(value, str) or not value.strip():
            raise MigrationCompatibilityError(f"{label} must be a timestamp")
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise MigrationCompatibilityError(f"{label} is not a valid timestamp") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise MigrationCompatibilityError(f"{label} must be timezone-aware")
        return parsed.astimezone(timezone.utc)

    def _read_raw(self, path: str, kind: SchemaKind, *, required: bool) -> _RawDocument | None:
        try:
            stored = self.repository.storage.read(path)
        except StorageNotFound:
            if required:
                raise MigrationCompatibilityError(f"required migration document is missing: {path}")
            return None
        except StorageError as exc:
            raise MigrationCompatibilityError(f"cannot read {path}: {exc}") from exc

        try:
            text = stored.content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MigrationCompatibilityError(f"{path}: invalid UTF-8: {exc}") from exc
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise MigrationCompatibilityError(f"{path}: invalid JSON: {exc}") from exc
        if not isinstance(raw, Mapping):
            raise MigrationCompatibilityError(f"{path}: document must be a JSON object")
        migrated = migrate_document(kind, raw)
        return _RawDocument(
            path=path,
            kind=kind,
            data=copy.deepcopy(dict(raw)),
            content=stored.content,
            version=stored.version,
            migrated=migrated,
        )

    def _serialize(self, path: str, kind: SchemaKind, data: Mapping[str, Any]) -> bytes:
        try:
            return self.repository.serialize(path, kind, data)
        except (SchemaError, RepositoryError) as exc:
            raise MigrationCompatibilityError(f"cannot serialize migration target {path}: {exc}") from exc

    def _write_if_changed(
        self,
        raw: _RawDocument | None,
        *,
        path: str,
        kind: SchemaKind,
        target: Mapping[str, Any],
        absent_from_version: int | None = None,
    ) -> PlannedWrite | None:
        target_data = copy.deepcopy(dict(target))
        target_bytes = self._serialize(path, kind, target_data)
        if raw is None:
            return PlannedWrite(
                path=path,
                kind=kind,
                original_exists=False,
                original_version=None,
                original_bytes=None,
                target_data=target_data,
                target_bytes=target_bytes,
                from_version=absent_from_version,
                to_version=1,
            )
        if not raw.migrated.changed and target_data == raw.migrated.data:
            return None
        return PlannedWrite(
            path=path,
            kind=kind,
            original_exists=True,
            original_version=raw.version,
            original_bytes=raw.content,
            target_data=target_data,
            target_bytes=target_bytes,
            from_version=raw.migrated.from_version,
            to_version=1,
        )

    @staticmethod
    def _installed_target(installed: Mapping[str, Any], to_revision: str) -> tuple[str, str | None]:
        if not isinstance(installed, Mapping):
            raise MigrationCompatibilityError("installed workflow provenance is unavailable")
        repository = installed.get("canonical_repository")
        if repository != CANONICAL_REPOSITORY:
            raise MigrationCompatibilityError(
                f"installed canonical repository must be {CANONICAL_REPOSITORY!r}"
            )
        resolved = installed.get("resolved_revision")
        if not isinstance(resolved, str) or not resolved.strip():
            raise MigrationCompatibilityError("installed resolved_revision is unavailable")
        if not isinstance(to_revision, str) or not to_revision.strip():
            raise MigrationCompatibilityError("target workflow revision must be non-empty")
        if to_revision != resolved:
            raise MigrationCompatibilityError(
                f"requested target {to_revision!r} does not match installed resolved revision {resolved!r}"
            )
        requested_ref = installed.get("requested_ref")
        if requested_ref is not None and (not isinstance(requested_ref, str) or not requested_ref.strip()):
            raise MigrationCompatibilityError("installed requested_ref must be null or a non-empty string")
        return resolved, requested_ref

    def _ensure_no_finalization(self) -> None:
        try:
            self.repository.storage.read(FINALIZATION_PATH)
        except StorageNotFound:
            return
        except StorageError as exc:
            raise MigrationCompatibilityError(f"cannot inspect finalization state: {exc}") from exc
        raise MigrationCompatibilityError("workflow upgrade is blocked while finalization is active")

    def _claims(
        self,
        valid_units: set[str],
    ) -> list[_RawDocument]:
        now = self._now()
        claims: list[_RawDocument] = []
        try:
            paths = self.repository.storage.list(CLAIM_PREFIX)
        except StorageError as exc:
            raise MigrationCompatibilityError(f"cannot list workflow claims: {exc}") from exc
        for path in sorted(path for path in paths if path.endswith(".json")):
            raw = self._read_raw(path, SchemaKind.CLAIM, required=True)
            assert raw is not None
            unit_id = raw.migrated.data.get("unit_id")
            if unit_id not in valid_units:
                raise MigrationCompatibilityError(
                    f"claim {path} references unknown unit {unit_id!r}"
                )
            expires_at = self._parse_utc(raw.migrated.data.get("expires_at"), f"{path}.expires_at")
            if expires_at > now:
                raise MigrationCompatibilityError(
                    f"workflow upgrade is blocked by live claim {unit_id} until {raw.migrated.data['expires_at']}"
                )
            claims.append(raw)
        return claims

    def _read_artifact(self, relative_path: object, *, label: str) -> bytes:
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise MigrationCompatibilityError(f"{label} path is missing")
        try:
            content = self._artifact_reader(relative_path)
        except (FileNotFoundError, OSError) as exc:
            raise MigrationCompatibilityError(f"missing {label} artifact: {relative_path}") from exc
        if not isinstance(content, bytes):
            raise MigrationCompatibilityError(f"artifact reader must return bytes for {relative_path}")
        return content

    def _reconcile_reviewed(
        self,
        progress: Mapping[str, Any],
        metadata: Mapping[str, Any],
        ledger: Mapping[str, Any],
    ) -> tuple[dict[str, Any], tuple[int, ...]]:
        updated = copy.deepcopy(dict(progress))
        chapters = updated.get("chapters")
        assert isinstance(chapters, list)
        workflow = metadata.get("workflow")
        assert isinstance(workflow, Mapping)
        revision = workflow.get("resolved_revision")
        assert isinstance(revision, str)
        contract = f"{REVIEW_CONTRACT_PATH}@{revision}"
        records = ledger.get("records")
        assert isinstance(records, list)
        downgrades: list[int] = []

        for chapter in chapters:
            if not isinstance(chapter, dict) or chapter.get("status") != "reviewed":
                continue
            number = chapter.get("number")
            if type(number) is not int:
                raise MigrationCompatibilityError("reviewed chapter has invalid number")
            source = self._read_artifact(chapter.get("source_path"), label="source")
            translation = self._read_artifact(chapter.get("translation_path"), label="translation")
            if not translation.strip():
                raise MigrationCompatibilityError(
                    f"chapter {number}: reviewed translation artifact is empty"
                )
            source_sha = hashlib.sha256(source).hexdigest()
            translation_sha = hashlib.sha256(translation).hexdigest()
            unit_id = f"chapter-{number:06d}"
            exact = [
                record
                for record in records
                if isinstance(record, Mapping)
                and record.get("unit_id") == unit_id
                and record.get("source_sha256") == source_sha
                and record.get("translation_sha256") == translation_sha
                and record.get("workflow_revision") == revision
                and record.get("review_contract_revision") == contract
            ]
            if not exact or exact[-1].get("outcome") != "PASS":
                chapter["status"] = "translated"
                downgrades.append(number)

        try:
            parsed = parse_document(SchemaKind.PROGRESS, updated)
        except SchemaError as exc:
            raise MigrationCompatibilityError(f"candidate progress is invalid: {exc}") from exc
        return copy.deepcopy(parsed.data), tuple(downgrades)

    def _verify_manifest(
        self,
        metadata: Mapping[str, Any],
        progress: Mapping[str, Any],
        manifest: Mapping[str, Any],
    ) -> None:
        if manifest.get("source_file") != metadata.get("source_file"):
            raise MigrationCompatibilityError("source manifest source_file does not match metadata")
        if manifest.get("source_format") != metadata.get("source_format"):
            raise MigrationCompatibilityError("source manifest source_format does not match metadata")

        chapters = progress.get("chapters")
        extracted = manifest.get("extracted")
        if not isinstance(chapters, list) or not isinstance(extracted, list):
            raise MigrationCompatibilityError("source manifest/progress chapter arrays are invalid")
        if manifest.get("chapter_count") != len(chapters) or len(extracted) != len(chapters):
            raise MigrationCompatibilityError("source manifest chapter_count does not match progress")

        for index, (chapter, entry) in enumerate(zip(chapters, extracted), start=1):
            if not isinstance(chapter, Mapping) or not isinstance(entry, Mapping):
                raise MigrationCompatibilityError(f"source manifest chapter {index} is invalid")
            expected = (chapter.get("number"), chapter.get("title"), chapter.get("source_path"))
            actual = (entry.get("number"), entry.get("title"), entry.get("path"))
            if actual != expected:
                raise MigrationCompatibilityError(
                    f"source manifest chapter {index} identity does not match progress"
                )
            relative = entry.get("path")
            if not isinstance(relative, str):
                raise MigrationCompatibilityError(f"source manifest chapter {index} path is invalid")
            path = self.book_dir / relative
            if not path.is_file():
                raise MigrationCompatibilityError(f"missing extracted source artifact: {relative}")
            if sha256_path(path) != entry.get("sha256"):
                raise MigrationCompatibilityError(f"extracted source hash mismatch: {relative}")

        explicit = metadata.get("source") if isinstance(metadata.get("source"), Mapping) else None
        source_file = metadata.get("source_file")
        source_path = self.book_dir / "source" / str(source_file)
        if explicit is None:
            if not source_path.is_file():
                raise MigrationCompatibilityError(
                    f"source identity cannot be proven; missing source/{source_file}"
                )
            if sha256_path(source_path) != manifest.get("source_sha256"):
                raise MigrationCompatibilityError("source manifest hash does not match embedded source")
            return

        mode = explicit.get("storage_mode")
        if manifest.get("source_storage_mode") != mode:
            raise MigrationCompatibilityError("source manifest storage mode does not match metadata")
        if manifest.get("source_size_bytes") != explicit.get("size_bytes"):
            raise MigrationCompatibilityError("source manifest size does not match metadata source identity")
        if manifest.get("source_sha256") != explicit.get("sha256"):
            raise MigrationCompatibilityError("source manifest hash does not match metadata source identity")
        if explicit.get("filename") != source_file:
            raise MigrationCompatibilityError("metadata source filename identity does not match source_file")
        if mode == "embedded" and not source_path.is_file():
            raise MigrationCompatibilityError(f"embedded source is missing: source/{source_file}")
        if source_path.is_file():
            if source_path.stat().st_size != explicit.get("size_bytes"):
                raise MigrationCompatibilityError("attached source size does not match metadata identity")
            if sha256_path(source_path) != explicit.get("sha256"):
                raise MigrationCompatibilityError("attached source hash does not match metadata identity")

    @staticmethod
    def _schema_history(
        metadata: MigratedDocument,
        progress: MigratedDocument,
        ledger: _RawDocument | None,
        manifest: _RawDocument | None,
        claims: list[_RawDocument],
    ) -> dict[str, dict[str, int]]:
        versions: dict[str, dict[str, int]] = {}
        if metadata.from_version == 0:
            versions["metadata"] = {"from": 0, "to": 1}
        if progress.from_version == 0:
            versions["progress"] = {"from": 0, "to": 1}
        if ledger is None:
            versions["review_ledger"] = {"from": 0, "to": 1}
        elif ledger.migrated.from_version == 0:
            versions["review_ledger"] = {"from": 0, "to": 1}
        if manifest is None:
            versions["source_manifest"] = {"from": 0, "to": 1}
        elif manifest.migrated.from_version == 0:
            versions["source_manifest"] = {"from": 0, "to": 1}
        if any(claim.migrated.from_version == 0 for claim in claims):
            versions["claims"] = {"from": 0, "to": 1}
        return versions

    def plan(
        self,
        *,
        slug: str,
        to_revision: str,
        installed: Mapping[str, Any],
    ) -> MigrationPlan:
        target_revision, requested_ref = self._installed_target(installed, to_revision)
        if not isinstance(slug, str) or not slug.strip():
            raise MigrationCompatibilityError("book slug must be non-empty")

        metadata_raw = self._read_raw("metadata.json", SchemaKind.METADATA, required=True)
        progress_raw = self._read_raw("progress.json", SchemaKind.PROGRESS, required=True)
        assert metadata_raw is not None and progress_raw is not None
        metadata = copy.deepcopy(metadata_raw.migrated.data)
        progress = copy.deepcopy(progress_raw.migrated.data)

        if progress.get("book_slug") != slug:
            raise MigrationCompatibilityError(
                f"progress book_slug {progress.get('book_slug')!r} does not match requested book {slug!r}"
            )
        chapters = progress.get("chapters")
        if not isinstance(chapters, list) or metadata.get("chapter_count") != len(chapters):
            raise MigrationCompatibilityError("metadata chapter_count does not match progress")
        numbers = [chapter.get("number") for chapter in chapters if isinstance(chapter, Mapping)]
        if len(numbers) != len(chapters) or len(numbers) != len(set(numbers)):
            raise MigrationCompatibilityError("progress chapter numbers must be unique")
        valid_units = {f"chapter-{number:06d}" for number in numbers if type(number) is int}

        workflow = metadata.get("workflow")
        if workflow is not None and not isinstance(workflow, Mapping):
            raise MigrationCompatibilityError("metadata workflow must be an object")
        workflow_map = copy.deepcopy(dict(workflow)) if isinstance(workflow, Mapping) else {}
        repository_name = workflow_map.get("repository")
        if repository_name is not None and repository_name != CANONICAL_REPOSITORY:
            raise MigrationCompatibilityError(
                f"metadata workflow repository {repository_name!r} is incompatible"
            )
        from_revision = workflow_map.get("resolved_revision")
        if from_revision is not None and (not isinstance(from_revision, str) or not from_revision.strip()):
            raise MigrationCompatibilityError("metadata workflow resolved_revision must be null or non-empty")

        self._ensure_no_finalization()
        claims = self._claims(valid_units)

        ledger_raw = self._read_raw("review-ledger.json", SchemaKind.REVIEW_LEDGER, required=False)
        if ledger_raw is None:
            ledger = {
                "schema_version": 1,
                "book_slug": slug,
                "next_sequence": 1,
                "records": [],
            }
            try:
                parse_document(SchemaKind.REVIEW_LEDGER, ledger)
            except SchemaError as exc:
                raise MigrationCompatibilityError(f"candidate review ledger is invalid: {exc}") from exc
        else:
            ledger = copy.deepcopy(ledger_raw.migrated.data)
            if ledger.get("book_slug") != slug:
                raise MigrationCompatibilityError("review ledger book_slug does not match progress")

        target_metadata = copy.deepcopy(metadata)
        target_workflow = copy.deepcopy(workflow_map)
        target_workflow["repository"] = CANONICAL_REPOSITORY
        target_workflow["requested_ref"] = requested_ref
        target_workflow["resolved_revision"] = target_revision
        target_workflow["review_evidence"] = REVIEW_EVIDENCE_VERSION
        target_metadata["workflow"] = target_workflow

        target_progress, downgrades = self._reconcile_reviewed(
            progress,
            target_metadata,
            ledger,
        )

        manifest_raw = self._read_raw("source-manifest.json", SchemaKind.SOURCE_MANIFEST, required=False)
        if manifest_raw is None:
            explicit = target_metadata.get("source")
            try:
                if isinstance(explicit, Mapping) and explicit.get("storage_mode") == "private_external":
                    manifest = build_private_source_manifest_from_identity(
                        self.book_dir,
                        target_metadata,
                        target_progress,
                    )
                else:
                    source_file = target_metadata.get("source_file")
                    if not isinstance(source_file, str) or not source_file.strip():
                        raise SourceIntegrityError("metadata source_file is unavailable")
                    manifest = build_source_manifest(
                        self.book_dir,
                        target_metadata,
                        target_progress,
                        self.book_dir / "source" / source_file,
                    )
            except SourceIntegrityError as exc:
                raise MigrationCompatibilityError(f"source compatibility failed: {exc}") from exc
            try:
                manifest = parse_document(SchemaKind.SOURCE_MANIFEST, manifest).data
            except SchemaError as exc:
                raise MigrationCompatibilityError(f"candidate source manifest is invalid: {exc}") from exc
        else:
            manifest = copy.deepcopy(manifest_raw.migrated.data)

        self._verify_manifest(target_metadata, target_progress, manifest)

        non_metadata_writes: list[PlannedWrite] = []
        manifest_write = self._write_if_changed(
            manifest_raw,
            path="source-manifest.json",
            kind=SchemaKind.SOURCE_MANIFEST,
            target=manifest,
            absent_from_version=0,
        )
        if manifest_write is not None:
            non_metadata_writes.append(manifest_write)

        ledger_write = self._write_if_changed(
            ledger_raw,
            path="review-ledger.json",
            kind=SchemaKind.REVIEW_LEDGER,
            target=ledger,
            absent_from_version=0,
        )
        if ledger_write is not None:
            non_metadata_writes.append(ledger_write)

        for claim in claims:
            write = self._write_if_changed(
                claim,
                path=claim.path,
                kind=SchemaKind.CLAIM,
                target=claim.migrated.data,
            )
            if write is not None:
                non_metadata_writes.append(write)

        progress_write = self._write_if_changed(
            progress_raw,
            path="progress.json",
            kind=SchemaKind.PROGRESS,
            target=target_progress,
        )
        if progress_write is not None:
            non_metadata_writes.append(progress_write)

        schema_versions = self._schema_history(
            metadata_raw.migrated,
            progress_raw.migrated,
            ledger_raw,
            manifest_raw,
            claims,
        )
        existing_history = target_workflow.get("upgrade_history", [])
        if not isinstance(existing_history, list):
            raise MigrationCompatibilityError("metadata workflow upgrade_history must be an array")

        workflow_without_new_history = copy.deepcopy(target_workflow)
        target_metadata["workflow"] = workflow_without_new_history

        metadata_needs_change = (
            metadata_raw.migrated.changed
            or target_metadata != metadata_raw.migrated.data
            or bool(non_metadata_writes)
        )
        if metadata_needs_change:
            history_entry = {
                "from_revision": from_revision,
                "to_revision": target_revision,
                "schema_versions": schema_versions,
            }
            workflow_with_history = copy.deepcopy(workflow_without_new_history)
            workflow_with_history["upgrade_history"] = [*copy.deepcopy(existing_history), history_entry]
            target_metadata["workflow"] = workflow_with_history

        try:
            target_metadata = parse_document(SchemaKind.METADATA, target_metadata).data
            target_progress = parse_document(SchemaKind.PROGRESS, target_progress).data
            ledger = parse_document(SchemaKind.REVIEW_LEDGER, ledger).data
            manifest = parse_document(SchemaKind.SOURCE_MANIFEST, manifest).data
        except SchemaError as exc:
            raise MigrationCompatibilityError(f"candidate workflow state is invalid: {exc}") from exc

        writes: list[PlannedWrite] = []
        for raw, path, kind, target, absent in (
            (manifest_raw, "source-manifest.json", SchemaKind.SOURCE_MANIFEST, manifest, 0),
            (ledger_raw, "review-ledger.json", SchemaKind.REVIEW_LEDGER, ledger, 0),
        ):
            write = self._write_if_changed(
                raw,
                path=path,
                kind=kind,
                target=target,
                absent_from_version=absent,
            )
            if write is not None:
                writes.append(write)
        for claim in sorted(claims, key=lambda item: item.path):
            write = self._write_if_changed(
                claim,
                path=claim.path,
                kind=SchemaKind.CLAIM,
                target=claim.migrated.data,
            )
            if write is not None:
                writes.append(write)
        progress_write = self._write_if_changed(
            progress_raw,
            path="progress.json",
            kind=SchemaKind.PROGRESS,
            target=target_progress,
        )
        if progress_write is not None:
            writes.append(progress_write)
        metadata_write = self._write_if_changed(
            metadata_raw,
            path="metadata.json",
            kind=SchemaKind.METADATA,
            target=target_metadata,
        )
        if metadata_write is not None:
            writes.append(metadata_write)

        return MigrationPlan(
            book_slug=slug,
            from_revision=from_revision,
            to_revision=target_revision,
            writes=tuple(writes),
            lifecycle_downgrades=downgrades,
            changed=bool(writes),
        )


class MigrationExecutor:
    """Apply and recover one journaled multi-document workflow upgrade."""

    def __init__(
        self,
        repository: WorkflowStateRepository,
        planner: MigrationPlanner,
        *,
        coordination: Any,
    ):
        self.repository = repository
        self.planner = planner
        self.coordination = coordination

    @staticmethod
    def _unchanged(plan: MigrationPlan) -> MigrationResult:
        return MigrationResult(
            book_slug=plan.book_slug,
            from_revision=plan.from_revision,
            to_revision=plan.to_revision,
            outcome="unchanged",
            migrated_paths=(),
            lifecycle_downgrades=plan.lifecycle_downgrades,
        )

    @staticmethod
    def _installed_from_plan(plan: MigrationPlan) -> dict[str, Any]:
        metadata = plan.write_for("metadata.json")
        if metadata is None:
            raise MigrationCompatibilityError(
                "changed workflow upgrade plan must include metadata provenance last"
            )
        workflow = metadata.target_data.get("workflow")
        if not isinstance(workflow, Mapping):
            raise MigrationCompatibilityError("migration target metadata workflow is unavailable")
        repository = workflow.get("repository")
        resolved = workflow.get("resolved_revision")
        if repository != CANONICAL_REPOSITORY or resolved != plan.to_revision:
            raise MigrationCompatibilityError("migration target metadata provenance is inconsistent")
        return {
            "canonical_repository": repository,
            "requested_ref": workflow.get("requested_ref"),
            "resolved_revision": resolved,
        }

    @staticmethod
    def _journal_for(plan: MigrationPlan) -> dict[str, Any]:
        documents: list[dict[str, Any]] = []
        for write in plan.writes:
            original = write.original_bytes
            documents.append(
                {
                    "path": write.path,
                    "kind": write.kind.value,
                    "original_exists": write.original_exists,
                    "original_revision": write.original_version if write.original_exists else None,
                    "original_sha256": (
                        hashlib.sha256(original).hexdigest()
                        if write.original_exists and original is not None
                        else None
                    ),
                    "original_bytes_base64": (
                        base64.b64encode(original).decode("ascii")
                        if write.original_exists and original is not None
                        else None
                    ),
                    "target_sha256": hashlib.sha256(write.target_bytes).hexdigest(),
                    "resulting_revision": None,
                }
            )
        return {
            "schema_version": 1,
            "operation": "workflow_upgrade",
            "book_slug": plan.book_slug,
            "from_revision": plan.from_revision,
            "to_revision": plan.to_revision,
            "phase": "prepared",
            "documents": documents,
        }

    def _load_journal(self) -> tuple[dict[str, Any], str]:
        try:
            return load_migration_journal(self.repository.storage)
        except MigrationJournalError as exc:
            raise MigrationConflict(f"migration journal is invalid; recovery is blocked: {exc}") from exc
        except StorageError:
            raise

    def _acquire(self, session_id: str):
        try:
            return self.coordination.acquire(
                operation="workflow_upgrade",
                session_id=session_id,
            )
        except CoordinationError as exc:
            raise MigrationConflict(f"cannot acquire workflow upgrade coordination: {exc}") from exc

    def _release(self, lease) -> None:
        try:
            self.coordination.release(lease)
        except CoordinationError as exc:
            raise MigrationConflict(f"cannot release workflow upgrade coordination: {exc}") from exc

    def _preflight_plan(self, plan: MigrationPlan) -> Mapping[str, Any]:
        installed = self._installed_from_plan(plan)
        fresh = self.planner.plan(
            slug=plan.book_slug,
            to_revision=plan.to_revision,
            installed=installed,
        )
        if fresh != plan:
            raise MigrationConflict("workflow upgrade plan changed before commit; re-plan required")
        return installed

    @staticmethod
    def _hash(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    def _classify(self, entry: Mapping[str, Any]) -> str:
        path = entry["path"]
        try:
            current = self.repository.storage.read(path)
        except StorageNotFound:
            return "original" if not entry["original_exists"] else "unknown"
        except StorageError as exc:
            raise MigrationConflict(f"cannot inspect migration recovery path {path}: {exc}") from exc

        current_hash = self._hash(current.content)
        if current_hash == entry["target_sha256"]:
            return "target"
        if entry["original_exists"] and current_hash == entry["original_sha256"]:
            return "original"
        return "unknown"

    def _classifications(self, journal: Mapping[str, Any]) -> list[str]:
        return [self._classify(entry) for entry in journal["documents"]]

    @staticmethod
    def _decode_original(entry: Mapping[str, Any]) -> bytes:
        encoded = entry.get("original_bytes_base64")
        if not isinstance(encoded, str):
            raise MigrationConflict(f"journal original bytes are unavailable for {entry.get('path')}")
        return base64.b64decode(encoded.encode("ascii"), validate=True)

    def _delete_journal(self, version: str) -> None:
        try:
            self.repository.storage.delete_if_version(MIGRATION_PATH, version)
        except (StorageNotFound, StorageVersionConflict) as exc:
            raise MigrationConflict("migration journal changed before deletion") from exc
        except StorageError as exc:
            raise MigrationConflict(f"cannot delete migration journal: {exc}") from exc

    def _rollback_loaded(
        self,
        journal: Mapping[str, Any],
        journal_version: str,
    ) -> None:
        states = self._classifications(journal)
        unknown = [
            entry["path"]
            for entry, state in zip(journal["documents"], states)
            if state == "unknown"
        ]
        if unknown:
            raise MigrationConflict(
                "migration recovery found unknown concurrent state at "
                + ", ".join(unknown)
                + "; journal preserved"
            )

        for entry, state in reversed(list(zip(journal["documents"], states))):
            if state != "target":
                continue
            path = entry["path"]
            try:
                current = self.repository.storage.read(path)
            except StorageError as exc:
                raise MigrationConflict(
                    f"migration recovery path {path} changed before rollback"
                ) from exc
            if self._hash(current.content) != entry["target_sha256"]:
                raise MigrationConflict(
                    f"migration recovery path {path} changed before rollback; journal preserved"
                )
            try:
                if entry["original_exists"]:
                    self.repository.storage.write_if_version(
                        path,
                        self._decode_original(entry),
                        current.version,
                    )
                else:
                    self.repository.storage.delete_if_version(path, current.version)
            except (StorageNotFound, StorageVersionConflict) as exc:
                raise MigrationConflict(
                    f"migration recovery path {path} changed during rollback; journal preserved"
                ) from exc
            except StorageError as exc:
                raise MigrationConflict(
                    f"cannot restore migration recovery path {path}; journal preserved: {exc}"
                ) from exc

        after = self._classifications(journal)
        not_original = [
            entry["path"]
            for entry, state in zip(journal["documents"], after)
            if state != "original"
        ]
        if not_original:
            raise MigrationConflict(
                "migration rollback could not prove original state at "
                + ", ".join(not_original)
                + "; journal preserved"
            )
        self._delete_journal(journal_version)

    def _rollback_durable_journal(self) -> None:
        try:
            journal, version = self._load_journal()
        except StorageNotFound:
            raise MigrationConflict("migration failed but recovery journal is missing")
        except StorageError as exc:
            raise MigrationConflict(f"cannot read migration journal for rollback: {exc}") from exc
        self._rollback_loaded(journal, version)

    def _post_validate(self, plan: MigrationPlan, installed: Mapping[str, Any]) -> None:
        fresh = self.planner.plan(
            slug=plan.book_slug,
            to_revision=plan.to_revision,
            installed=installed,
        )
        if fresh.changed:
            raise MigrationConflict(
                "workflow upgrade target did not converge to a strict compatible no-op"
            )

    def _execute_locked(self, plan: MigrationPlan, *, session_id: str) -> MigrationResult:
        if not plan.changed:
            return self._unchanged(plan)

        try:
            self.repository.storage.read(MIGRATION_PATH)
        except StorageNotFound:
            pass
        except StorageError as exc:
            raise MigrationConflict(f"cannot inspect migration journal: {exc}") from exc
        else:
            raise MigrationConflict("an unfinished migration journal already exists; recover it first")

        installed = self._preflight_plan(plan)
        journal = self._journal_for(plan)
        try:
            journal_version = self.repository.storage.create_if_absent(
                MIGRATION_PATH,
                serialize_migration_journal(journal),
            )
        except StorageAlreadyExists as exc:
            raise MigrationConflict("migration journal appeared before commit") from exc
        except (StorageError, MigrationJournalError) as exc:
            raise MigrationConflict(f"cannot create migration journal: {exc}") from exc

        try:
            for index, write in enumerate(plan.writes):
                if write.original_exists:
                    if write.original_version is None:
                        raise MigrationCompatibilityError(
                            f"migration plan lacks original revision for {write.path}"
                        )
                    resulting = self.repository.storage.write_if_version(
                        write.path,
                        write.target_bytes,
                        write.original_version,
                    )
                else:
                    resulting = self.repository.storage.create_if_absent(
                        write.path,
                        write.target_bytes,
                    )

                journal["documents"][index]["resulting_revision"] = resulting
                journal_version = self.repository.storage.write_if_version(
                    MIGRATION_PATH,
                    serialize_migration_journal(journal),
                    journal_version,
                )

            journal["phase"] = "applied"
            journal_version = self.repository.storage.write_if_version(
                MIGRATION_PATH,
                serialize_migration_journal(journal),
                journal_version,
            )
            self._post_validate(plan, installed)
            self._delete_journal(journal_version)
        except Exception as exc:
            try:
                self._rollback_durable_journal()
            except MigrationError as recovery_exc:
                raise recovery_exc from exc
            if isinstance(exc, MigrationError):
                raise exc
            raise MigrationConflict(
                f"workflow upgrade failed and exact original state was restored: {exc}"
            ) from exc

        return MigrationResult(
            book_slug=plan.book_slug,
            from_revision=plan.from_revision,
            to_revision=plan.to_revision,
            outcome="changed",
            migrated_paths=tuple(write.path for write in plan.writes),
            lifecycle_downgrades=plan.lifecycle_downgrades,
        )

    def execute(self, plan: MigrationPlan, *, session_id: str) -> MigrationResult:
        if not isinstance(plan, MigrationPlan):
            raise MigrationCompatibilityError("execute requires a MigrationPlan")
        if not plan.changed:
            return self._unchanged(plan)
        lease = self._acquire(session_id)
        try:
            result = self._execute_locked(plan, session_id=session_id)
        except Exception:
            try:
                self._release(lease)
            except MigrationError:
                pass
            raise
        self._release(lease)
        return result

    def _journal_downgrades(self, journal: Mapping[str, Any]) -> tuple[int, ...]:
        progress_entry = next(
            (entry for entry in journal["documents"] if entry["kind"] == SchemaKind.PROGRESS.value),
            None,
        )
        if progress_entry is None or not progress_entry["original_exists"]:
            return ()
        try:
            original = json.loads(self._decode_original(progress_entry).decode("utf-8"))
            current = json.loads(self.repository.storage.read(progress_entry["path"]).content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, StorageError):
            return ()
        if not isinstance(original, Mapping) or not isinstance(current, Mapping):
            return ()
        old_chapters = original.get("chapters")
        new_chapters = current.get("chapters")
        if not isinstance(old_chapters, list) or not isinstance(new_chapters, list):
            return ()
        new_by_number = {
            chapter.get("number"): chapter
            for chapter in new_chapters
            if isinstance(chapter, Mapping) and type(chapter.get("number")) is int
        }
        downgrades = [
            chapter["number"]
            for chapter in old_chapters
            if isinstance(chapter, Mapping)
            and type(chapter.get("number")) is int
            and chapter.get("status") == "reviewed"
            and isinstance(new_by_number.get(chapter["number"]), Mapping)
            and new_by_number[chapter["number"]].get("status") == "translated"
        ]
        return tuple(sorted(downgrades))

    def _recover_locked(
        self,
        journal: Mapping[str, Any],
        journal_version: str,
        *,
        session_id: str,
        installed: Mapping[str, Any],
    ) -> MigrationResult:
        MigrationPlanner._installed_target(installed, journal["to_revision"])
        states = self._classifications(journal)
        unknown = [
            entry["path"]
            for entry, state in zip(journal["documents"], states)
            if state == "unknown"
        ]
        if unknown:
            raise MigrationConflict(
                "migration recovery found unknown concurrent state at "
                + ", ".join(unknown)
                + "; journal preserved"
            )

        if states and all(state == "target" for state in states):
            post = self.planner.plan(
                slug=journal["book_slug"],
                to_revision=journal["to_revision"],
                installed=installed,
            )
            if post.changed:
                raise MigrationConflict(
                    "all journaled targets exist but full workflow state is not a strict no-op; journal preserved"
                )
            downgrades = self._journal_downgrades(journal)
            self._delete_journal(journal_version)
            return MigrationResult(
                book_slug=journal["book_slug"],
                from_revision=journal["from_revision"],
                to_revision=journal["to_revision"],
                outcome="recovered",
                migrated_paths=tuple(entry["path"] for entry in journal["documents"]),
                lifecycle_downgrades=downgrades,
            )

        self._rollback_loaded(journal, journal_version)
        fresh = self.planner.plan(
            slug=journal["book_slug"],
            to_revision=journal["to_revision"],
            installed=installed,
        )
        return self._execute_locked(fresh, session_id=session_id)

    def recover(
        self,
        *,
        session_id: str,
        installed: Mapping[str, Any],
    ) -> MigrationResult | None:
        try:
            initial, initial_version = self._load_journal()
        except StorageNotFound:
            return None
        except StorageError as exc:
            raise MigrationConflict(f"cannot read migration journal: {exc}") from exc

        lease = self._acquire(session_id)
        try:
            try:
                journal, version = self._load_journal()
            except StorageNotFound as exc:
                raise MigrationConflict("migration journal disappeared during recovery admission") from exc
            except StorageError as exc:
                raise MigrationConflict(f"cannot re-read migration journal: {exc}") from exc
            if version != initial_version or journal != initial:
                raise MigrationConflict("migration journal changed during recovery admission")
            result = self._recover_locked(
                journal,
                version,
                session_id=session_id,
                installed=installed,
            )
        except Exception:
            try:
                self._release(lease)
            except MigrationError:
                pass
            raise
        self._release(lease)
        return result

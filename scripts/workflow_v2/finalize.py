"""Atomic, crash-recoverable Workflow v2 finalization core."""

from __future__ import annotations

import copy
import hashlib
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .claims import ClaimManager
from .coordination import (
    BookCoordinationManager,
    CoordinationError,
    FINALIZATION_PATH,
)
from .repository import LoadedDocument, RepositoryError, WorkflowStateRepository
from .review_report import build_review_report_snapshot
from .reviews import REVIEW_EVIDENCE_VERSION, ReviewEvidenceError, ReviewLedgerManager
from .schemas import SCHEMA_VERSION, SchemaError, SchemaKind
from .storage import (
    StorageAlreadyExists,
    StorageError,
    StorageNotFound,
    StorageVersionConflict,
)


HEX_ID_RE = re.compile(r"^[0-9a-f]{32}$")
PreflightProvider = Callable[[], tuple[Sequence[str], Mapping[str, Any]]]
LIFECYCLE_STATES = ("pending", "extracted", "translated", "reviewed")


class FinalizationError(RuntimeError):
    """Base error for Workflow v2 finalization."""


class FinalizationBlocked(FinalizationError):
    """Authoritative state does not currently satisfy completion preconditions."""


class FinalizationConflict(FinalizationError):
    """Durable state changed across a finalization coordination boundary."""


@dataclass(frozen=True)
class FinalizeResult:
    snapshot: dict[str, Any]
    progress_revision: str
    promoted: bool
    recovered: bool


def sha256_bytes(content: bytes) -> str:
    if not isinstance(content, bytes):
        raise FinalizationError("content identity requires bytes")
    return hashlib.sha256(content).hexdigest()


def build_reviewed_candidate(progress: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(progress, Mapping):
        raise FinalizationBlocked("progress state must be an object")
    candidate = copy.deepcopy(dict(progress))
    chapters = candidate.get("chapters")
    if not isinstance(chapters, list):
        raise FinalizationBlocked("progress state must contain a chapters array")
    for chapter in chapters:
        if not isinstance(chapter, dict):
            raise FinalizationBlocked("progress chapter must be an object")
        status = chapter.get("status")
        if status not in {"translated", "reviewed"}:
            raise FinalizationBlocked(
                f"chapter {chapter.get('number')} cannot finalize from lifecycle={status!r}"
            )
        chapter["status"] = "reviewed"
    return candidate


def build_completion_snapshot(
    repository: WorkflowStateRepository,
    *,
    artifact_reader: Callable[[str], bytes],
    metadata: Mapping[str, Any],
    metadata_revision: str,
    progress: Mapping[str, Any],
    progress_revision: str,
    corpus: Mapping[str, Any],
    structural_valid: bool = True,
) -> dict[str, Any]:
    """Project authoritative post-promotion state into deterministic completion data."""

    workflow = metadata.get("workflow") if isinstance(metadata, Mapping) else None
    if not isinstance(workflow, Mapping):
        raise FinalizationBlocked("metadata workflow is unavailable")
    workflow_revision = workflow.get("resolved_revision")
    if not isinstance(workflow_revision, str) or not workflow_revision.strip():
        raise FinalizationBlocked("immutable metadata workflow resolved_revision is required")

    try:
        ledger = repository.read("review-ledger.json", SchemaKind.REVIEW_LEDGER)
        review_manager = ReviewLedgerManager(repository, artifact_reader=artifact_reader)
        review_snapshot = build_review_report_snapshot(review_manager, progress, metadata)
        claims = ClaimManager(repository).list_active()
    except (ReviewEvidenceError, StorageError, RepositoryError, SchemaError, ValueError) as exc:
        raise FinalizationBlocked(f"cannot build completion snapshot: {exc}") from exc

    chapters = progress.get("chapters") if isinstance(progress, Mapping) else None
    if not isinstance(chapters, list):
        raise FinalizationBlocked("progress state must contain a chapters array")

    lifecycle = {state: 0 for state in LIFECYCLE_STATES}
    for chapter in chapters:
        if not isinstance(chapter, Mapping):
            raise FinalizationBlocked("progress chapter must be an object")
        state = chapter.get("status")
        if state not in lifecycle:
            raise FinalizationBlocked(f"unsupported lifecycle state in completion snapshot: {state!r}")
        lifecycle[state] += 1

    review_summary = review_snapshot["summary"]
    coverage = review_summary["pass_coverage"]
    translations_complete = all(
        unit.get("translation_sha256") is not None for unit in review_snapshot["units"]
    )
    review_complete = (
        coverage["passed"] == coverage["total"]
        and review_summary["corrections_required"] == 0
        and review_summary["missing"] == 0
        and review_summary["stale"] == 0
        and review_summary["untranslated"] == 0
    )
    quality_gates = {
        "structural_valid": bool(structural_valid),
        "corpus_verified": corpus.get("state") == "verified",
        "zero_active_claims": len(claims) == 0,
        "translations_complete": translations_complete,
        "review_pass_coverage_complete": review_complete,
        "all_reviewed": lifecycle["reviewed"] == len(chapters),
    }

    return {
        "schema": "completion-report-v1",
        "book_slug": progress.get("book_slug"),
        "workflow_revision": workflow_revision,
        "state_revisions": {
            "metadata": metadata_revision,
            "progress": progress_revision,
            "review_ledger": ledger.version,
        },
        "lifecycle": lifecycle,
        "corpus": copy.deepcopy(dict(corpus)),
        "review": review_snapshot,
        "quality_gates": quality_gates,
    }


def _percent_text(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"{value:g}%"
    return f"{value}%"


def render_state_markdown(snapshot: Mapping[str, Any]) -> str:
    """Render deterministic STATE.md from a completion snapshot."""

    revisions = snapshot["state_revisions"]
    lifecycle = snapshot["lifecycle"]
    corpus = snapshot["corpus"]
    review_summary = snapshot["review"]["summary"]
    coverage = review_summary["pass_coverage"]
    gates = snapshot["quality_gates"]
    passed_gates = sum(1 for value in gates.values() if value is True)

    lines = [
        f"# State — {snapshot['book_slug']}",
        "",
        "Deterministic projection of authoritative Workflow v2 completion state.",
        "",
        "## Revisions",
        "",
        f"- workflow: `{snapshot['workflow_revision']}`",
        f"- metadata: `{revisions['metadata']}`",
        f"- progress: `{revisions['progress']}`",
        f"- review ledger: `{revisions['review_ledger']}`",
        "",
        "## Lifecycle",
        "",
    ]
    for state in LIFECYCLE_STATES:
        lines.append(f"- `{state}`: {lifecycle[state]}")

    lines.extend(
        [
            "",
            "## Review",
            "",
            f"- PASS coverage: {coverage['passed']}/{coverage['total']} ({_percent_text(coverage['percent'])})",
            f"- corrections required: {review_summary['corrections_required']}",
            f"- missing: {review_summary['missing']}",
            f"- stale: {review_summary['stale']}",
            f"- untranslated: {review_summary['untranslated']}",
            "",
            "## Corpus",
            "",
            f"- state: `{corpus.get('state', 'unknown')}`",
            f"- storage mode: `{corpus.get('storage_mode', 'legacy')}`",
        ]
    )
    if "source_attached" in corpus:
        lines.append(f"- source attached: `{str(bool(corpus['source_attached'])).lower()}`")

    lines.extend(
        [
            "",
            "## Completion",
            "",
            f"- quality gates: {passed_gates}/{len(gates)}",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def render_quality_gates_markdown(snapshot: Mapping[str, Any]) -> str:
    """Render deterministic FINAL_QUALITY_GATES.md from a completion snapshot."""

    gates = snapshot["quality_gates"]
    coverage = snapshot["review"]["summary"]["pass_coverage"]
    entries = (
        ("structural_valid", "Structural validation clean"),
        ("corpus_verified", "Verified corpus"),
        ("zero_active_claims", "Zero active claims"),
        ("translations_complete", "Translations complete"),
        (
            "review_pass_coverage_complete",
            f"100% current PASS review coverage ({coverage['passed']}/{coverage['total']})",
        ),
        ("all_reviewed", "All lifecycle units reviewed"),
    )
    lines = [
        f"# Final Quality Gates — {snapshot['book_slug']}",
        "",
        f"Workflow revision: `{snapshot['workflow_revision']}`",
        "",
    ]
    for key, label in entries:
        mark = "x" if gates.get(key) is True else " "
        lines.append(f"- [{mark}] {label}")
    return "\n".join(lines).rstrip() + "\n"


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class FinalizationManager:
    """Coordinate one book-level completion promotion without partial lifecycle writes."""

    def __init__(
        self,
        repository: WorkflowStateRepository,
        *,
        artifact_reader: Callable[[str], bytes],
        preflight: PreflightProvider,
        coordination: BookCoordinationManager | None = None,
        now: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ):
        self.repository = repository
        self._artifact_reader = artifact_reader
        self._preflight = preflight
        self._now_factory = now or (lambda: datetime.now(timezone.utc))
        self._id_factory = id_factory or (lambda: uuid4().hex)
        self._coordination = coordination or BookCoordinationManager(
            repository,
            now=self._now_factory,
        )

    def _now(self) -> datetime:
        value = self._now_factory()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise FinalizationError("finalization clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc).replace(microsecond=0)

    def _new_id(self) -> str:
        value = self._id_factory()
        if not isinstance(value, str) or not HEX_ID_RE.fullmatch(value):
            raise FinalizationError(
                "finalization id factory must return 32 lowercase hexadecimal characters"
            )
        return value

    @staticmethod
    def _session_id(value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise FinalizationError("session_id must be a non-empty string")
        return value

    def _read_core(self) -> tuple[LoadedDocument, LoadedDocument]:
        try:
            metadata = self.repository.read("metadata.json", SchemaKind.METADATA)
            progress = self.repository.read("progress.json", SchemaKind.PROGRESS)
        except (StorageError, RepositoryError, SchemaError) as exc:
            raise FinalizationBlocked(f"workflow state is unavailable or invalid: {exc}") from exc
        return metadata, progress

    @staticmethod
    def _workflow_revision(metadata: Mapping[str, Any]) -> str:
        workflow = metadata.get("workflow") if isinstance(metadata, Mapping) else None
        if not isinstance(workflow, Mapping):
            raise FinalizationBlocked("metadata workflow is unavailable")
        if workflow.get("review_evidence") != REVIEW_EVIDENCE_VERSION:
            raise FinalizationBlocked(
                f"finalize requires {REVIEW_EVIDENCE_VERSION} review evidence"
            )
        revision = workflow.get("resolved_revision")
        if not isinstance(revision, str) or not revision.strip():
            raise FinalizationBlocked("immutable metadata workflow resolved_revision is required")
        return revision

    def _check_external_preflight(self) -> dict[str, Any]:
        try:
            structural_errors, corpus = self._preflight()
        except Exception as exc:
            raise FinalizationBlocked(f"cannot run finalization preflight: {exc}") from exc
        errors = [str(item) for item in structural_errors if str(item)]
        if errors:
            raise FinalizationBlocked("structural validation failed: " + "; ".join(errors))
        if not isinstance(corpus, Mapping):
            raise FinalizationBlocked("corpus preflight did not return a mapping")
        corpus_data = dict(corpus)
        if corpus_data.get("state") != "verified":
            detail = corpus_data.get("error")
            suffix = f": {detail}" if detail else ""
            raise FinalizationBlocked(
                f"source corpus must be verified; state={corpus_data.get('state')!r}{suffix}"
            )
        return corpus_data

    def _review_resolutions(
        self,
        metadata: Mapping[str, Any],
        progress: Mapping[str, Any],
    ):
        manager = ReviewLedgerManager(
            self.repository,
            artifact_reader=self._artifact_reader,
        )
        try:
            resolutions = manager.resolve_all(progress, metadata)
        except (ReviewEvidenceError, StorageError, RepositoryError, SchemaError) as exc:
            raise FinalizationBlocked(f"review evidence is unavailable or invalid: {exc}") from exc
        not_pass = [item for item in resolutions if item.state != "pass"]
        if not_pass:
            detail = ", ".join(f"{item.unit_id}={item.state}" for item in not_pass)
            raise FinalizationBlocked(f"current PASS review coverage is incomplete: {detail}")
        return resolutions

    def _business_preflight(self):
        corpus = self._check_external_preflight()
        metadata_doc, progress_doc = self._read_core()
        workflow_revision = self._workflow_revision(metadata_doc.data)
        candidate = build_reviewed_candidate(progress_doc.data)
        self._review_resolutions(metadata_doc.data, progress_doc.data)
        try:
            claims = ClaimManager(self.repository).list_active()
        except Exception as exc:
            raise FinalizationBlocked(f"claims are unavailable or invalid: {exc}") from exc
        if claims:
            units = ", ".join(claim.data["unit_id"] for claim in claims)
            raise FinalizationBlocked(f"active claims block finalization: {units}")
        candidate_bytes = self.repository.serialize(
            "progress.json",
            SchemaKind.PROGRESS,
            candidate,
        )
        return metadata_doc, progress_doc, workflow_revision, candidate, candidate_bytes, corpus

    def _read_marker(self) -> LoadedDocument | None:
        try:
            return self.repository.read(FINALIZATION_PATH, SchemaKind.FINALIZATION_LOCK)
        except StorageNotFound:
            return None
        except (StorageError, RepositoryError, SchemaError) as exc:
            raise FinalizationConflict(f"finalization marker is invalid or unavailable: {exc}") from exc

    def _delete_marker(self, version: str) -> None:
        try:
            self.repository.delete_if_version(
                FINALIZATION_PATH,
                SchemaKind.FINALIZATION_LOCK,
                version,
            )
        except (StorageNotFound, StorageVersionConflict) as exc:
            raise FinalizationConflict("finalization marker changed before cleanup") from exc
        except (StorageError, RepositoryError, SchemaError) as exc:
            raise FinalizationConflict(f"cannot clean finalization marker: {exc}") from exc

    def _validate_marker(
        self,
        marker: Mapping[str, Any],
        *,
        book_slug: str,
        workflow_revision: str,
        candidate_hash: str,
    ) -> None:
        if marker.get("book_slug") != book_slug:
            raise FinalizationConflict("finalization marker belongs to another book")
        if marker.get("workflow_revision") != workflow_revision:
            raise FinalizationConflict("finalization marker uses another workflow revision")
        if marker.get("candidate_progress_sha256") != candidate_hash:
            raise FinalizationConflict("finalization candidate no longer matches durable progress state")

    def _admit(
        self,
        *,
        session_id: str,
        progress_revision: str,
        book_slug: str,
        workflow_revision: str,
        candidate_hash: str,
    ) -> tuple[LoadedDocument, bool]:
        try:
            admission = self._coordination.acquire(
                operation="finalize_admission",
                session_id=session_id,
            )
        except CoordinationError as exc:
            raise FinalizationConflict(f"finalize admission blocked: {exc}") from exc

        marker: LoadedDocument | None = None
        created = False
        try:
            marker = self._read_marker()
            if marker is None:
                data = {
                    "schema_version": SCHEMA_VERSION,
                    "lock_id": self._new_id(),
                    "book_slug": book_slug,
                    "workflow_revision": workflow_revision,
                    "base_progress_revision": progress_revision,
                    "candidate_progress_sha256": candidate_hash,
                    "phase": "preparing",
                    "promoted_progress_revision": None,
                    "session_id": session_id,
                    "started_at": _format_utc(self._now()),
                }
                try:
                    version = self.repository.create(
                        FINALIZATION_PATH,
                        SchemaKind.FINALIZATION_LOCK,
                        data,
                    )
                except StorageAlreadyExists:
                    marker = self._read_marker()
                    if marker is None:
                        raise FinalizationConflict(
                            "finalization marker changed during admission"
                        )
                except (StorageError, RepositoryError, SchemaError) as exc:
                    raise FinalizationConflict(f"cannot create finalization marker: {exc}") from exc
                else:
                    marker = LoadedDocument(data=data, version=version, legacy=False)
                    created = True

            assert marker is not None
            self._validate_marker(
                marker.data,
                book_slug=book_slug,
                workflow_revision=workflow_revision,
                candidate_hash=candidate_hash,
            )
            claims = ClaimManager(self.repository).list_active()
            if claims:
                if created:
                    self._delete_marker(marker.version)
                    marker = None
                units = ", ".join(claim.data["unit_id"] for claim in claims)
                raise FinalizationBlocked(f"active claims block finalization: {units}")
            assert marker is not None
            return marker, created
        finally:
            try:
                self._coordination.release(admission)
            except CoordinationError as exc:
                if marker is not None and created:
                    try:
                        self._delete_marker(marker.version)
                    except FinalizationConflict:
                        pass
                raise FinalizationConflict(
                    f"finalize admission could not release coordination mutex: {exc}"
                ) from exc

    def _promote_marker(self, marker: LoadedDocument, progress_revision: str) -> LoadedDocument:
        if marker.data["phase"] == "promoted":
            return marker
        updated = copy.deepcopy(marker.data)
        updated["phase"] = "promoted"
        updated["promoted_progress_revision"] = progress_revision
        try:
            version = self.repository.write_if_version(
                FINALIZATION_PATH,
                SchemaKind.FINALIZATION_LOCK,
                updated,
                marker.version,
            )
        except StorageVersionConflict as exc:
            raise FinalizationConflict("finalization marker changed before phase promotion") from exc
        except (StorageError, RepositoryError, SchemaError) as exc:
            raise FinalizationConflict(f"cannot promote finalization marker phase: {exc}") from exc
        return LoadedDocument(data=updated, version=version, legacy=False)

    def _fresh_revalidation(self, *, marker: LoadedDocument, candidate_hash: str):
        corpus = self._check_external_preflight()
        metadata_doc, progress_doc = self._read_core()
        workflow_revision = self._workflow_revision(metadata_doc.data)
        candidate = build_reviewed_candidate(progress_doc.data)
        candidate_bytes = self.repository.serialize(
            "progress.json", SchemaKind.PROGRESS, candidate
        )
        if sha256_bytes(candidate_bytes) != candidate_hash:
            raise FinalizationConflict("finalization candidate changed during revalidation")
        self._review_resolutions(metadata_doc.data, progress_doc.data)
        claims = ClaimManager(self.repository).list_active()
        if claims:
            raise FinalizationConflict("active claims appeared after finalization admission")
        self._validate_marker(
            marker.data,
            book_slug=progress_doc.data.get("book_slug"),
            workflow_revision=workflow_revision,
            candidate_hash=candidate_hash,
        )
        return metadata_doc, progress_doc, candidate, candidate_bytes, corpus

    def _completion_snapshot(
        self,
        *,
        progress_revision: str,
        candidate_hash: str,
    ) -> dict[str, Any]:
        corpus = self._check_external_preflight()
        metadata_doc, progress_doc = self._read_core()
        self._workflow_revision(metadata_doc.data)
        if progress_doc.version != progress_revision:
            raise FinalizationConflict("progress revision changed during completion post-validation")
        raw = self.repository.storage.read("progress.json")
        if raw.version != progress_revision or sha256_bytes(raw.content) != candidate_hash:
            raise FinalizationConflict("progress content changed during completion post-validation")
        self._review_resolutions(metadata_doc.data, progress_doc.data)
        claims = ClaimManager(self.repository).list_active()
        if claims:
            raise FinalizationConflict("active claims appeared during completion post-validation")
        snapshot = build_completion_snapshot(
            self.repository,
            artifact_reader=self._artifact_reader,
            metadata=metadata_doc.data,
            metadata_revision=metadata_doc.version,
            progress=progress_doc.data,
            progress_revision=progress_doc.version,
            corpus=corpus,
            structural_valid=True,
        )
        failed = [name for name, value in snapshot["quality_gates"].items() if value is not True]
        if failed:
            raise FinalizationConflict(
                "completion post-validation failed quality gates: " + ", ".join(failed)
            )
        return snapshot

    def finalize(self, *, session_id: str) -> FinalizeResult:
        session_id = self._session_id(session_id)
        (
            _,
            progress_doc,
            workflow_revision,
            candidate,
            candidate_bytes,
            _,
        ) = self._business_preflight()
        candidate_hash = sha256_bytes(candidate_bytes)
        book_slug = progress_doc.data.get("book_slug")
        if not isinstance(book_slug, str) or not book_slug.strip():
            raise FinalizationBlocked("progress book_slug is required")

        marker, created = self._admit(
            session_id=session_id,
            progress_revision=progress_doc.version,
            book_slug=book_slug,
            workflow_revision=workflow_revision,
            candidate_hash=candidate_hash,
        )

        promoted = False
        recovered = not created
        progress_revision = progress_doc.version
        try:
            _, current_progress, candidate, _, _ = self._fresh_revalidation(
                marker=marker,
                candidate_hash=candidate_hash,
            )
            raw = self.repository.storage.read("progress.json")
            current_hash = sha256_bytes(raw.content)

            if marker.data["phase"] == "preparing":
                if current_hash == candidate_hash:
                    marker = self._promote_marker(marker, raw.version)
                    progress_revision = raw.version
                    recovered = recovered or raw.version != marker.data["base_progress_revision"]
                elif current_progress.version == marker.data["base_progress_revision"]:
                    try:
                        progress_revision = self.repository.write_if_version(
                            "progress.json",
                            SchemaKind.PROGRESS,
                            candidate,
                            current_progress.version,
                        )
                    except StorageVersionConflict as exc:
                        raise FinalizationConflict(
                            "progress state changed before atomic finalization promotion"
                        ) from exc
                    except (StorageError, RepositoryError, SchemaError) as exc:
                        raise FinalizationConflict(
                            f"cannot persist atomic reviewed lifecycle state: {exc}"
                        ) from exc
                    promoted = True
                    marker = self._promote_marker(marker, progress_revision)
                else:
                    raise FinalizationConflict(
                        "progress state does not match finalization base or candidate identity"
                    )
            else:
                promoted_revision = marker.data["promoted_progress_revision"]
                if raw.version != promoted_revision or current_hash != candidate_hash:
                    raise FinalizationConflict(
                        "promoted finalization marker does not match current progress identity"
                    )
                progress_revision = raw.version
                recovered = True

            snapshot = self._completion_snapshot(
                progress_revision=progress_revision,
                candidate_hash=candidate_hash,
            )
            return FinalizeResult(
                snapshot=snapshot,
                progress_revision=progress_revision,
                promoted=promoted,
                recovered=recovered,
            )
        except FinalizationBlocked:
            if marker.data["phase"] == "preparing":
                try:
                    latest = self._read_marker()
                    if latest is not None and latest.version == marker.version:
                        self._delete_marker(marker.version)
                except FinalizationConflict:
                    pass
            raise

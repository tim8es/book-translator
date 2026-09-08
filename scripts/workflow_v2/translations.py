"""Machine-gated Translator acceptance for Workflow v2."""

from __future__ import annotations

import copy
import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .claims import canonical_unit_id
from .coordination import BookCoordinationManager, CoordinationError
from .repository import LoadedDocument, RepositoryError, WorkflowStateRepository
from .schemas import SchemaError, SchemaKind
from .shared_state import SharedStateError, SharedStateStale, require_current_shared_state
from .storage import StorageError, StorageNotFound, StorageVersionConflict


TRANSLATION_ACCEPTANCE_LEASE_SECONDS = 900


class TranslationAcceptanceError(RuntimeError):
    """A Translator result cannot be accepted safely."""


class TranslationAcceptanceConflict(TranslationAcceptanceError):
    """Durable state changed before Translator acceptance completed."""


class TranslationClaimError(TranslationAcceptanceError):
    """A Translator result is not authorized by a current Translator claim."""


@dataclass(frozen=True)
class AcceptTranslationResult:
    unit_id: str
    status: str
    progress_revision: str
    translation_sha256: str
    changed: bool


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _parse_utc(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise TranslationClaimError("translator claim expiry must be a non-empty UTC timestamp")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise TranslationClaimError("translator claim expiry is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TranslationClaimError("translator claim expiry must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class TranslationAcceptanceManager:
    """Atomically bind a canonical translation to its durable Translator claim."""

    def __init__(
        self,
        repository: WorkflowStateRepository,
        *,
        artifact_reader: Callable[[str], bytes],
        now: Callable[[], datetime] | None = None,
        coordination: BookCoordinationManager | None = None,
    ):
        self.repository = repository
        self._artifact_reader = artifact_reader
        self._now_factory = now or (lambda: datetime.now(timezone.utc))
        self._coordination = coordination or BookCoordinationManager(
            repository,
            now=self._now_factory,
        )

    def _now(self) -> datetime:
        value = self._now_factory()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise TranslationAcceptanceError(
                "translation acceptance clock must return a timezone-aware datetime"
            )
        return value.astimezone(timezone.utc).replace(microsecond=0)

    @staticmethod
    def _workflow_revision(metadata: Mapping[str, Any]) -> str:
        workflow = metadata.get("workflow") if isinstance(metadata, Mapping) else None
        if not isinstance(workflow, Mapping):
            raise TranslationAcceptanceError("metadata workflow revision is unavailable")
        for key in ("resolved_revision", "requested_ref"):
            value = workflow.get(key)
            if isinstance(value, str) and value.strip():
                return value
        raise TranslationAcceptanceError("metadata workflow revision is unavailable")

    @staticmethod
    def _chapter(progress: Mapping[str, Any], chapter_number: int) -> Mapping[str, Any]:
        if type(chapter_number) is not int or chapter_number < 1:
            raise TranslationAcceptanceError("chapter number must be a positive integer")
        chapters = progress.get("chapters") if isinstance(progress, Mapping) else None
        if not isinstance(chapters, list):
            raise TranslationAcceptanceError("progress state must contain a chapters array")
        found = None
        seen: set[int] = set()
        for index, chapter in enumerate(chapters):
            if not isinstance(chapter, Mapping):
                raise TranslationAcceptanceError(f"progress chapter {index + 1} must be an object")
            number = chapter.get("number")
            if type(number) is not int or number < 1:
                raise TranslationAcceptanceError(
                    f"progress chapter {index + 1} has invalid number {number!r}"
                )
            if number in seen:
                raise TranslationAcceptanceError(
                    f"progress contains duplicate chapter number {number}"
                )
            seen.add(number)
            if number == chapter_number:
                found = chapter
        if found is None:
            raise TranslationAcceptanceError(f"progress does not contain chapter {chapter_number}")
        return found

    def _artifact(self, path: object, *, kind: str) -> bytes:
        if not isinstance(path, str) or not path.strip():
            raise TranslationAcceptanceError(f"{kind} artifact path is missing")
        try:
            content = self._artifact_reader(path)
        except (FileNotFoundError, OSError) as exc:
            raise TranslationAcceptanceError(f"missing {kind} artifact: {path}") from exc
        if not isinstance(content, bytes):
            raise TranslationAcceptanceError(f"artifact reader must return bytes for {path}")
        if kind == "translation" and not content.strip():
            raise TranslationAcceptanceError(f"translation artifact is empty: {path}")
        return content

    def _claim(
        self,
        unit_id: str,
        *,
        session_id: str,
        workflow_revision: str,
    ) -> LoadedDocument:
        path = f".workflow/claims/{unit_id}.json"
        try:
            loaded = self.repository.read(path, SchemaKind.CLAIM)
        except StorageNotFound as exc:
            raise TranslationClaimError(f"unit {unit_id} has no active translator claim") from exc
        except (StorageError, RepositoryError, SchemaError) as exc:
            raise TranslationClaimError(
                f"cannot validate translator claim for {unit_id}: {exc}"
            ) from exc
        claim = loaded.data
        if claim.get("role") != "translator":
            raise TranslationClaimError(f"unit {unit_id} is not claimed by a translator")
        if claim.get("session_id") != session_id:
            raise TranslationClaimError(
                f"unit {unit_id} translator claim belongs to session {claim.get('session_id')}"
            )
        if claim.get("workflow_revision") != workflow_revision:
            raise TranslationClaimError(
                f"unit {unit_id} translator claim uses another workflow revision"
            )
        if _parse_utc(claim.get("expires_at")) <= self._now():
            raise TranslationClaimError(
                f"unit {unit_id} translator claim is expired and cannot authorize acceptance"
            )
        snapshot = claim.get("shared_state_revisions")
        if snapshot is not None:
            try:
                require_current_shared_state(self.repository.storage, snapshot)
            except SharedStateStale as exc:
                raise TranslationClaimError(
                    f"unit {unit_id} translator result is stale: {exc}"
                ) from exc
            except SharedStateError as exc:
                raise TranslationClaimError(
                    f"cannot validate shared state for translator claim {unit_id}: {exc}"
                ) from exc
        return loaded

    def _verify_existing(
        self,
        progress: Mapping[str, Any],
        metadata: Mapping[str, Any],
        chapter_number: int,
        *,
        progress_revision: str,
    ) -> AcceptTranslationResult:
        chapter = self._chapter(progress, chapter_number)
        evidence = chapter.get("translation_acceptance")
        if not isinstance(evidence, Mapping):
            raise TranslationAcceptanceError(
                f"chapter {chapter_number} is already {chapter.get('status')} without machine translation acceptance evidence"
            )
        unit_id = canonical_unit_id(chapter_number)
        workflow_revision = self._workflow_revision(metadata)
        if evidence.get("unit_id") != unit_id:
            raise TranslationAcceptanceError(
                f"chapter {chapter_number} translation acceptance unit identity is invalid"
            )
        if evidence.get("workflow_revision") != workflow_revision:
            raise TranslationAcceptanceError(
                f"chapter {chapter_number} translation acceptance uses another workflow revision"
            )
        source = self._artifact(chapter.get("source_path"), kind="source")
        translation = self._artifact(chapter.get("translation_path"), kind="translation")
        source_sha256 = _sha256(source)
        translation_sha256 = _sha256(translation)
        if evidence.get("source_sha256") != source_sha256:
            raise TranslationAcceptanceError(
                f"chapter {chapter_number} source changed after translation acceptance"
            )
        if evidence.get("translation_sha256") != translation_sha256:
            raise TranslationAcceptanceError(
                f"chapter {chapter_number} translation changed after translation acceptance"
            )
        return AcceptTranslationResult(
            unit_id=unit_id,
            status=str(chapter.get("status")),
            progress_revision=progress_revision,
            translation_sha256=translation_sha256,
            changed=False,
        )

    def accept(
        self,
        progress: Mapping[str, Any],
        progress_revision: str,
        metadata: Mapping[str, Any],
        chapter_number: int,
        *,
        session_id: str,
    ) -> AcceptTranslationResult:
        if not isinstance(progress_revision, str) or not progress_revision.strip():
            raise TranslationAcceptanceError("progress revision must be a non-empty string")
        if not isinstance(session_id, str) or not session_id.strip():
            raise TranslationAcceptanceError("session_id must be a non-empty string")

        chapter = self._chapter(progress, chapter_number)
        status = chapter.get("status")
        if status == "reviewed":
            return self._verify_existing(
                progress,
                metadata,
                chapter_number,
                progress_revision=progress_revision,
            )
        if status == "translated":
            try:
                return self._verify_existing(
                    progress,
                    metadata,
                    chapter_number,
                    progress_revision=progress_revision,
                )
            except TranslationAcceptanceError:
                # A translated chapter can legitimately re-enter the Translator boundary
                # after CORRECTIONS_REQUIRED. The fresh live claim below must then replace
                # the current acceptance evidence in the same progress CAS.
                pass
        elif status != "extracted":
            raise TranslationAcceptanceError(
                f"chapter {chapter_number} must be extracted or translated before translation can be accepted; status={status!r}"
            )

        try:
            lease = self._coordination.acquire(
                operation="translation_acceptance",
                session_id=session_id,
                lease_seconds=TRANSLATION_ACCEPTANCE_LEASE_SECONDS,
            )
        except CoordinationError as exc:
            raise TranslationAcceptanceConflict(
                f"translation acceptance is blocked by book coordination: {exc}"
            ) from exc

        result: AcceptTranslationResult | None = None
        try:
            try:
                if self._coordination.finalization_active():
                    raise TranslationAcceptanceConflict(
                        "translation acceptance is blocked while finalization is active"
                    )
            except CoordinationError as exc:
                raise TranslationAcceptanceConflict(
                    f"cannot verify finalization admission state: {exc}"
                ) from exc

            try:
                durable = self.repository.read("progress.json", SchemaKind.PROGRESS)
            except (StorageError, RepositoryError, SchemaError) as exc:
                raise TranslationAcceptanceError(
                    f"cannot verify current progress state: {exc}"
                ) from exc
            if durable.version != progress_revision or durable.data != dict(progress):
                raise TranslationAcceptanceConflict(
                    "progress state changed; re-read before accepting translation"
                )

            workflow_revision = self._workflow_revision(metadata)
            unit_id = canonical_unit_id(chapter_number)
            claim_doc = self._claim(
                unit_id,
                session_id=session_id,
                workflow_revision=workflow_revision,
            )
            claim = claim_doc.data
            current_chapter = self._chapter(progress, chapter_number)
            source = self._artifact(current_chapter.get("source_path"), kind="source")
            translation = self._artifact(
                current_chapter.get("translation_path"),
                kind="translation",
            )
            source_sha256 = _sha256(source)
            translation_sha256 = _sha256(translation)
            evidence = {
                "schema_version": 1,
                "unit_id": unit_id,
                "claim_id": claim["claim_id"],
                "claim_revision": claim_doc.version,
                "role": "translator",
                "session_id": claim["session_id"],
                "base_revision": claim["base_revision"],
                "base_commit": claim["base_commit"],
                "workflow_revision": claim["workflow_revision"],
                "accepted_from_progress_revision": progress_revision,
                "shared_state_revisions": copy.deepcopy(
                    claim.get("shared_state_revisions")
                ),
                "source_sha256": source_sha256,
                "translation_sha256": translation_sha256,
                "accepted_at": _format_utc(self._now()),
            }

            updated = copy.deepcopy(dict(progress))
            target = self._chapter(updated, chapter_number)
            if not isinstance(target, dict):
                raise TranslationAcceptanceError("progress chapter must be mutable")
            target["status"] = "translated"
            target["translation_acceptance"] = evidence

            latest_source = self._artifact(target.get("source_path"), kind="source")
            latest_translation = self._artifact(
                target.get("translation_path"),
                kind="translation",
            )
            if _sha256(latest_source) != source_sha256:
                raise TranslationAcceptanceConflict(
                    f"chapter {chapter_number} source changed before acceptance"
                )
            if _sha256(latest_translation) != translation_sha256:
                raise TranslationAcceptanceConflict(
                    f"chapter {chapter_number} translation changed before acceptance"
                )

            snapshot = claim.get("shared_state_revisions")
            if snapshot is not None:
                try:
                    require_current_shared_state(self.repository.storage, snapshot)
                except SharedStateStale as exc:
                    raise TranslationClaimError(
                        f"unit {unit_id} translator result is stale: {exc}"
                    ) from exc
                except SharedStateError as exc:
                    raise TranslationClaimError(
                        f"cannot validate shared state for translator claim {unit_id}: {exc}"
                    ) from exc

            try:
                lease = self._coordination.renew(
                    lease,
                    lease_seconds=TRANSLATION_ACCEPTANCE_LEASE_SECONDS,
                )
            except CoordinationError as exc:
                raise TranslationAcceptanceConflict(
                    f"translation acceptance lost book coordination before progress mutation: {exc}"
                ) from exc

            try:
                new_revision = self.repository.write_if_version(
                    "progress.json",
                    SchemaKind.PROGRESS,
                    updated,
                    progress_revision,
                )
            except StorageVersionConflict as exc:
                raise TranslationAcceptanceConflict(
                    "progress state changed before translation acceptance completed"
                ) from exc
            except (StorageError, RepositoryError, SchemaError) as exc:
                raise TranslationAcceptanceError(
                    f"cannot persist translated lifecycle state: {exc}"
                ) from exc

            result = AcceptTranslationResult(
                unit_id=unit_id,
                status="translated",
                progress_revision=new_revision,
                translation_sha256=translation_sha256,
                changed=True,
            )
        except Exception:
            try:
                self._coordination.release(lease)
            except CoordinationError:
                pass
            raise

        try:
            self._coordination.release(lease)
        except CoordinationError as exc:
            raise TranslationAcceptanceConflict(
                "translation was accepted but coordination mutex could not be released"
            ) from exc
        assert result is not None
        return result

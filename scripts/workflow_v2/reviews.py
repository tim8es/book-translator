"""Machine-verifiable Workflow v2 review evidence."""

from __future__ import annotations

import copy
import hashlib
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .claims import canonical_unit_id
from .coordination import FINALIZATION_PATH
from .repository import LoadedDocument, RepositoryError, WorkflowStateRepository
from .schemas import SCHEMA_VERSION, SchemaError, SchemaKind, parse_document
from .storage import StorageError, StorageNotFound, StorageVersionConflict


LEDGER_PATH = "review-ledger.json"
REVIEW_EVIDENCE_VERSION = "review-ledger-v1"
REVIEW_CONTRACT_PATH = "docs/TRANSLATION.md"
HEX_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class ReviewError(RuntimeError):
    """Base error for review-ledger operations."""


class ReviewConflict(ReviewError):
    """Durable review state changed before a conditional write completed."""


class ReviewClaimError(ReviewError):
    """Review evidence is not authorized by a current reviewer claim."""


class ReviewEvidenceError(ReviewError):
    """Review evidence cannot be interpreted or recorded safely."""


@dataclass(frozen=True)
class ReviewRecordResult:
    record: dict[str, Any]
    ledger_revision: str


@dataclass(frozen=True)
class ReviewResolution:
    unit_id: str
    chapter_number: int
    state: str
    source_sha256: str | None
    translation_sha256: str | None
    current_record: dict[str, Any] | None
    history: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class AcceptReviewResult:
    unit_id: str
    status: str
    progress_revision: str
    changed: bool


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _parse_utc(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except (TypeError, ValueError) as exc:
        raise ReviewEvidenceError(f"invalid UTC timestamp {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ReviewEvidenceError(f"timestamp is not timezone-aware: {value!r}")
    return parsed.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class ReviewLedgerManager:
    """Record and resolve exact-artifact literary review evidence."""

    def __init__(
        self,
        repository: WorkflowStateRepository,
        *,
        artifact_reader: Callable[[str], bytes],
        now: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ):
        self.repository = repository
        self._artifact_reader = artifact_reader
        self._now_factory = now or (lambda: datetime.now(timezone.utc))
        self._id_factory = id_factory or (lambda: uuid4().hex)

    def _now(self) -> datetime:
        value = self._now_factory()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ReviewEvidenceError("review clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc).replace(microsecond=0)

    def _new_id(self) -> str:
        value = self._id_factory()
        if not isinstance(value, str) or not HEX_ID_RE.fullmatch(value):
            raise ReviewEvidenceError(
                "review record id factory must return 32 lowercase hexadecimal characters"
            )
        return value

    @staticmethod
    def _workflow_context(metadata: Mapping[str, Any]) -> tuple[str, str]:
        workflow = metadata.get("workflow") if isinstance(metadata, Mapping) else None
        if not isinstance(workflow, Mapping):
            raise ReviewEvidenceError("metadata workflow is unavailable")
        if workflow.get("review_evidence") != REVIEW_EVIDENCE_VERSION:
            raise ReviewEvidenceError(
                f"review ledger evidence is not enabled; expected {REVIEW_EVIDENCE_VERSION}"
            )
        revision = workflow.get("resolved_revision")
        if not isinstance(revision, str) or not revision.strip():
            raise ReviewEvidenceError(
                "immutable metadata workflow resolved_revision is required for review evidence"
            )
        return revision, f"{REVIEW_CONTRACT_PATH}@{revision}"

    @staticmethod
    def _chapter(progress: Mapping[str, Any], chapter_number: int) -> Mapping[str, Any]:
        if type(chapter_number) is not int or chapter_number < 1:
            raise ReviewEvidenceError("chapter number must be a positive integer")
        chapters = progress.get("chapters") if isinstance(progress, Mapping) else None
        if not isinstance(chapters, list):
            raise ReviewEvidenceError("progress state must contain a chapters array")

        found: Mapping[str, Any] | None = None
        seen: set[int] = set()
        for index, chapter in enumerate(chapters):
            if not isinstance(chapter, Mapping):
                raise ReviewEvidenceError(f"progress chapter {index + 1} must be an object")
            number = chapter.get("number")
            if type(number) is not int or number < 1:
                raise ReviewEvidenceError(f"progress chapter {index + 1} has invalid number {number!r}")
            if number in seen:
                raise ReviewEvidenceError(f"progress contains duplicate chapter number {number}")
            seen.add(number)
            if number == chapter_number:
                found = chapter
        if found is None:
            raise ReviewEvidenceError(f"progress does not contain chapter {chapter_number}")
        return found

    def _read_artifact(self, path: Any, *, kind: str, allow_untranslated: bool) -> bytes | None:
        if not isinstance(path, str) or not path.strip():
            raise ReviewEvidenceError(f"{kind} artifact path is missing")
        try:
            content = self._artifact_reader(path)
        except (FileNotFoundError, OSError) as exc:
            if allow_untranslated and kind == "translation":
                return None
            raise ReviewEvidenceError(f"missing {kind} artifact: {path}") from exc
        if not isinstance(content, bytes):
            raise ReviewEvidenceError(f"artifact reader must return bytes for {path}")
        if kind == "translation" and not content.strip():
            if allow_untranslated:
                return None
            raise ReviewEvidenceError(f"translation artifact is empty: {path}")
        return content

    def _load_ledger(self, progress: Mapping[str, Any]) -> LoadedDocument:
        try:
            loaded = self.repository.read(LEDGER_PATH, SchemaKind.REVIEW_LEDGER)
        except (StorageNotFound, StorageError, RepositoryError, SchemaError) as exc:
            raise ReviewEvidenceError(f"review ledger is unavailable or invalid: {exc}") from exc
        book_slug = progress.get("book_slug") if isinstance(progress, Mapping) else None
        if not isinstance(book_slug, str) or not book_slug.strip():
            raise ReviewEvidenceError("progress book_slug is required")
        if loaded.data.get("book_slug") != book_slug:
            raise ReviewEvidenceError(
                f"review ledger book_slug {loaded.data.get('book_slug')!r} does not match progress {book_slug!r}"
            )
        return loaded

    def _require_reviewer_claim(
        self,
        unit_id: str,
        reviewer_session_id: str,
        workflow_revision: str,
    ) -> None:
        if not isinstance(reviewer_session_id, str) or not reviewer_session_id.strip():
            raise ReviewClaimError("reviewer session id must be a non-empty string")
        path = f".workflow/claims/{unit_id}.json"
        try:
            claim = self.repository.read(path, SchemaKind.CLAIM).data
        except StorageNotFound as exc:
            raise ReviewClaimError(f"unit {unit_id} has no active reviewer claim") from exc
        except (StorageError, RepositoryError, SchemaError) as exc:
            raise ReviewClaimError(f"cannot validate reviewer claim for {unit_id}: {exc}") from exc

        if claim.get("role") != "reviewer":
            raise ReviewClaimError(f"unit {unit_id} is not claimed by a reviewer")
        if claim.get("session_id") != reviewer_session_id:
            raise ReviewClaimError(
                f"unit {unit_id} reviewer claim belongs to session {claim.get('session_id')}"
            )
        if claim.get("workflow_revision") != workflow_revision:
            raise ReviewClaimError(f"unit {unit_id} reviewer claim uses another workflow revision")
        expires_at = _parse_utc(claim.get("expires_at"))
        if expires_at <= self._now():
            raise ReviewClaimError(
                f"unit {unit_id} reviewer claim is expired and cannot authorize new review evidence"
            )

    @staticmethod
    def _history_for(ledger: Mapping[str, Any], unit_id: str) -> list[dict[str, Any]]:
        return [copy.deepcopy(record) for record in ledger["records"] if record["unit_id"] == unit_id]

    def _artifact_identity(
        self,
        progress: Mapping[str, Any],
        chapter_number: int,
        *,
        allow_untranslated: bool,
    ) -> tuple[str, Mapping[str, Any], str, str | None]:
        chapter = self._chapter(progress, chapter_number)
        unit_id = canonical_unit_id(chapter_number)
        source = self._read_artifact(
            chapter.get("source_path"),
            kind="source",
            allow_untranslated=False,
        )
        assert source is not None
        translation = self._read_artifact(
            chapter.get("translation_path"),
            kind="translation",
            allow_untranslated=allow_untranslated,
        )
        return (
            unit_id,
            chapter,
            _sha256(source),
            _sha256(translation) if translation is not None else None,
        )

    def resolve_unit(
        self,
        progress: Mapping[str, Any],
        metadata: Mapping[str, Any],
        chapter_number: int,
    ) -> ReviewResolution:
        workflow_revision, contract_revision = self._workflow_context(metadata)
        unit_id, _, source_sha256, translation_sha256 = self._artifact_identity(
            progress,
            chapter_number,
            allow_untranslated=True,
        )
        ledger = self._load_ledger(progress).data
        history = self._history_for(ledger, unit_id)

        if translation_sha256 is None:
            return ReviewResolution(
                unit_id=unit_id,
                chapter_number=chapter_number,
                state="untranslated",
                source_sha256=source_sha256,
                translation_sha256=None,
                current_record=None,
                history=tuple(history),
            )

        exact = [
            record
            for record in history
            if record["source_sha256"] == source_sha256
            and record["translation_sha256"] == translation_sha256
            and record["workflow_revision"] == workflow_revision
            and record["review_contract_revision"] == contract_revision
        ]
        if exact:
            current = exact[-1]
            state = "pass" if current["outcome"] == "PASS" else "corrections_required"
        elif history:
            current = None
            state = "stale"
        else:
            current = None
            state = "missing"

        return ReviewResolution(
            unit_id=unit_id,
            chapter_number=chapter_number,
            state=state,
            source_sha256=source_sha256,
            translation_sha256=translation_sha256,
            current_record=copy.deepcopy(current) if current is not None else None,
            history=tuple(history),
        )

    def resolve_all(
        self,
        progress: Mapping[str, Any],
        metadata: Mapping[str, Any],
    ) -> list[ReviewResolution]:
        chapters = progress.get("chapters") if isinstance(progress, Mapping) else None
        if not isinstance(chapters, list):
            raise ReviewEvidenceError("progress state must contain a chapters array")
        numbers: list[int] = []
        seen: set[int] = set()
        for index, chapter in enumerate(chapters):
            if not isinstance(chapter, Mapping):
                raise ReviewEvidenceError(f"progress chapter {index + 1} must be an object")
            number = chapter.get("number")
            if type(number) is not int or number < 1:
                raise ReviewEvidenceError(f"progress chapter {index + 1} has invalid number {number!r}")
            if number in seen:
                raise ReviewEvidenceError(f"progress contains duplicate chapter number {number}")
            seen.add(number)
            numbers.append(number)
        return [self.resolve_unit(progress, metadata, number) for number in sorted(numbers)]

    @staticmethod
    def _correction_round(history: list[dict[str, Any]], outcome: str) -> int:
        maximum = max((record["correction_round"] for record in history), default=0)
        return maximum + 1 if outcome == "CORRECTIONS_REQUIRED" else maximum

    def record(
        self,
        progress: Mapping[str, Any],
        progress_revision: str,
        metadata: Mapping[str, Any],
        chapter_number: int,
        *,
        outcome: str,
        reviewer_session_id: str,
        review_commit: str | None = None,
    ) -> ReviewRecordResult:
        if outcome not in {"PASS", "CORRECTIONS_REQUIRED"}:
            raise ReviewEvidenceError("review outcome must be PASS or CORRECTIONS_REQUIRED")
        if not isinstance(progress_revision, str) or not progress_revision.strip():
            raise ReviewEvidenceError("progress revision must be a non-empty string")
        if review_commit is not None and (not isinstance(review_commit, str) or not review_commit.strip()):
            raise ReviewEvidenceError("review commit must be null or a non-empty string")

        try:
            durable_progress = self.repository.read("progress.json", SchemaKind.PROGRESS)
        except (StorageError, RepositoryError, SchemaError) as exc:
            raise ReviewEvidenceError(f"cannot verify current progress state: {exc}") from exc
        if durable_progress.version != progress_revision or durable_progress.data != dict(progress):
            raise ReviewConflict("progress state changed; re-read before recording review evidence")

        workflow_revision, contract_revision = self._workflow_context(metadata)
        unit_id, _, source_sha256, translation_sha256 = self._artifact_identity(
            progress,
            chapter_number,
            allow_untranslated=False,
        )
        assert translation_sha256 is not None
        self._require_reviewer_claim(unit_id, reviewer_session_id, workflow_revision)
        loaded = self._load_ledger(progress)
        history = self._history_for(loaded.data, unit_id)
        record_id = self._new_id()
        sequence = loaded.data["next_sequence"]
        record = {
            "record_id": record_id,
            "sequence": sequence,
            "unit_id": unit_id,
            "outcome": outcome,
            "source_sha256": source_sha256,
            "translation_sha256": translation_sha256,
            "workflow_revision": workflow_revision,
            "review_contract_revision": contract_revision,
            "reviewer_session_id": reviewer_session_id,
            "reviewed_at": _format_utc(self._now()),
            "state_revision": progress_revision,
            "review_commit": review_commit,
            "correction_round": self._correction_round(history, outcome),
            "supersedes_record_id": history[-1]["record_id"] if history else None,
        }
        parse_document(
            SchemaKind.REVIEW_LEDGER,
            {
                **loaded.data,
                "next_sequence": sequence + 1,
                "records": [*loaded.data["records"], record],
            },
        )
        new_ledger = copy.deepcopy(loaded.data)
        new_ledger["records"].append(record)
        new_ledger["next_sequence"] = sequence + 1
        try:
            new_revision = self.repository.write_if_version(
                LEDGER_PATH,
                SchemaKind.REVIEW_LEDGER,
                new_ledger,
                loaded.version,
            )
        except StorageVersionConflict as exc:
            raise ReviewConflict("review ledger changed concurrently; re-read review state") from exc
        except (StorageError, RepositoryError, SchemaError) as exc:
            raise ReviewEvidenceError(f"cannot persist review evidence: {exc}") from exc
        return ReviewRecordResult(record=copy.deepcopy(record), ledger_revision=new_revision)

    def accept_review(
        self,
        progress: Mapping[str, Any],
        progress_revision: str,
        metadata: Mapping[str, Any],
        chapter_number: int,
    ) -> AcceptReviewResult:
        if not isinstance(progress_revision, str) or not progress_revision.strip():
            raise ReviewEvidenceError("progress revision must be a non-empty string")

        try:
            self.repository.read(FINALIZATION_PATH, SchemaKind.FINALIZATION_LOCK)
        except StorageNotFound:
            pass
        except (StorageError, RepositoryError, SchemaError) as exc:
            raise ReviewEvidenceError(
                f"cannot verify finalization admission state: {exc}"
            ) from exc
        else:
            raise ReviewEvidenceError(
                "review promotion is blocked while finalization is active"
            )

        chapter = self._chapter(progress, chapter_number)
        status = chapter.get("status")
        if status not in {"translated", "reviewed"}:
            raise ReviewEvidenceError(
                f"chapter {chapter_number} must be translated before review can be accepted; status={status!r}"
            )

        try:
            durable = self.repository.read("progress.json", SchemaKind.PROGRESS)
        except (StorageError, RepositoryError, SchemaError) as exc:
            raise ReviewEvidenceError(f"cannot verify current progress state: {exc}") from exc
        if durable.version != progress_revision or durable.data != dict(progress):
            raise ReviewConflict("progress state changed; re-read before accepting review")

        resolution = self.resolve_unit(progress, metadata, chapter_number)
        if resolution.state != "pass":
            raise ReviewEvidenceError(
                f"chapter {chapter_number} requires current PASS review evidence; review state={resolution.state}"
            )

        if status == "reviewed":
            return AcceptReviewResult(
                unit_id=resolution.unit_id,
                status="reviewed",
                progress_revision=progress_revision,
                changed=False,
            )

        updated = copy.deepcopy(dict(progress))
        target = self._chapter(updated, chapter_number)
        target["status"] = "reviewed"

        latest_resolution = self.resolve_unit(updated, metadata, chapter_number)
        if latest_resolution.state != "pass":
            raise ReviewEvidenceError(
                f"chapter {chapter_number} review evidence became {latest_resolution.state} before promotion"
            )

        try:
            new_revision = self.repository.write_if_version(
                "progress.json",
                SchemaKind.PROGRESS,
                updated,
                progress_revision,
            )
        except StorageVersionConflict as exc:
            raise ReviewConflict("progress state changed before review promotion completed") from exc
        except (StorageError, RepositoryError, SchemaError) as exc:
            raise ReviewEvidenceError(f"cannot persist reviewed lifecycle state: {exc}") from exc

        return AcceptReviewResult(
            unit_id=resolution.unit_id,
            status="reviewed",
            progress_revision=new_revision,
            changed=True,
        )

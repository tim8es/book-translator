"""Domain operations for Workflow v2 durable claims."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from .repository import LoadedDocument, WorkflowStateRepository
from .schemas import SCHEMA_VERSION, SchemaKind, parse_document
from .storage import StorageAlreadyExists, StorageError, StorageNotFound, StorageVersionConflict


SELECTOR_RE = re.compile(r"^([1-9][0-9]*)(?:-([1-9][0-9]*))?$")
HEX_ID_RE = re.compile(r"^[0-9a-f]{32}$")
MAX_CHAPTER_NUMBER = 999_999
CLAIM_PREFIX = ".workflow/claims"
CLAIM_EVENT_PREFIX = ".workflow/claim-events"


class ClaimError(RuntimeError):
    """Base error for durable claim coordination."""


class InvalidClaimSelector(ClaimError):
    """A chapter selector cannot be mapped to canonical workflow units."""


class ClaimConflict(ClaimError):
    """A requested unit is already claimed or changed during coordination."""

    def __init__(self, unit_id: str, message: str | None = None):
        self.unit_id = unit_id
        super().__init__(message or f"unit {unit_id} is already claimed")


class ClaimOwnershipError(ClaimError):
    """A session attempted to release a claim owned by another session."""

    def __init__(self, unit_id: str, owner_session_id: str):
        self.unit_id = unit_id
        self.owner_session_id = owner_session_id
        super().__init__(f"unit {unit_id} is owned by session {owner_session_id}")


class ClaimRollbackError(ClaimError):
    """A failed range acquisition could not fully roll back its own claims."""

    def __init__(self, unresolved_paths: list[str], cause: Exception):
        self.unresolved_paths = tuple(unresolved_paths)
        self.cause = cause
        joined = ", ".join(unresolved_paths)
        super().__init__(f"claim acquisition rollback failed for: {joined}")


class ClaimAuditError(ClaimError):
    """Claim lifecycle audit evidence could not be persisted safely."""


@dataclass(frozen=True)
class ActiveClaim:
    path: str
    data: dict[str, Any]
    version: str


@dataclass(frozen=True)
class ClaimLifecycleResult:
    unit_id: str
    status: str
    request_event_id: str | None = None
    completion_event_id: str | None = None


def canonical_unit_id(number: int) -> str:
    """Return the stable Workflow v2 unit ID for one chapter number."""

    if type(number) is not int or not 1 <= number <= MAX_CHAPTER_NUMBER:
        raise InvalidClaimSelector(
            f"chapter number must be an integer from 1 to {MAX_CHAPTER_NUMBER}"
        )
    return f"chapter-{number:06d}"


def resolve_selector(progress: Mapping[str, Any], selector: str) -> list[str]:
    """Resolve an inclusive numeric selector against durable progress state."""

    if not isinstance(progress, Mapping):
        raise InvalidClaimSelector("progress state must be an object")
    chapters = progress.get("chapters")
    if not isinstance(chapters, list):
        raise InvalidClaimSelector("progress state must contain a chapters array")

    available: set[int] = set()
    for index, chapter in enumerate(chapters):
        if not isinstance(chapter, Mapping):
            raise InvalidClaimSelector(f"progress chapter {index + 1} must be an object")
        number = chapter.get("number")
        if type(number) is not int or not 1 <= number <= MAX_CHAPTER_NUMBER:
            raise InvalidClaimSelector(
                f"progress chapter {index + 1} has invalid chapter number {number!r}"
            )
        if number in available:
            raise InvalidClaimSelector(f"progress contains duplicate chapter number {number}")
        available.add(number)

    if not isinstance(selector, str):
        raise InvalidClaimSelector("selector must be a string")
    match = SELECTOR_RE.fullmatch(selector)
    if match is None:
        raise InvalidClaimSelector("selector must be a positive chapter number or inclusive range N-M")

    start = int(match.group(1))
    end = int(match.group(2)) if match.group(2) is not None else start
    if start > MAX_CHAPTER_NUMBER or end > MAX_CHAPTER_NUMBER:
        raise InvalidClaimSelector(
            f"chapter number must not exceed {MAX_CHAPTER_NUMBER}"
        )
    if end < start:
        raise InvalidClaimSelector("range end must not precede range start")

    requested = list(range(start, end + 1))
    missing = [number for number in requested if number not in available]
    if missing:
        joined = ", ".join(str(number) for number in missing)
        raise InvalidClaimSelector(f"selector references missing chapter(s): {joined}")

    return [canonical_unit_id(number) for number in requested]


def _claim_path(unit_id: str) -> str:
    return f"{CLAIM_PREFIX}/{unit_id}.json"


def _parse_claim_timestamp(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    return datetime.fromisoformat(normalized).astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    value = value.astimezone(timezone.utc).replace(microsecond=0)
    return value.isoformat().replace("+00:00", "Z")


class ClaimManager:
    """Coordinate durable per-unit claims without depending on a concrete backend."""

    def __init__(
        self,
        repository: WorkflowStateRepository,
        *,
        now: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ):
        self.repository = repository
        self._now_factory = now or (lambda: datetime.now(timezone.utc))
        self._id_factory = id_factory or (lambda: uuid4().hex)

    def _now(self) -> datetime:
        value = self._now_factory()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ClaimError("claim clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc).replace(microsecond=0)

    def _new_id(self) -> str:
        value = self._id_factory()
        if not isinstance(value, str) or not HEX_ID_RE.fullmatch(value):
            raise ClaimError("claim/event id factory must return 32 lowercase hexadecimal characters")
        return value

    @staticmethod
    def _require_nonempty(value: str | None, name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ClaimError(f"{name} must be a non-empty string")
        return value

    def _read_claim(self, unit_id: str) -> ActiveClaim:
        path = _claim_path(unit_id)
        loaded = self.repository.read(path, SchemaKind.CLAIM)
        return ActiveClaim(path=path, data=loaded.data, version=loaded.version)

    def list_active(self) -> list[ActiveClaim]:
        claims: list[ActiveClaim] = []
        for path in self.repository.storage.list(CLAIM_PREFIX):
            if not path.endswith(".json"):
                continue
            loaded = self.repository.read(path, SchemaKind.CLAIM)
            claims.append(ActiveClaim(path=path, data=loaded.data, version=loaded.version))
        return sorted(claims, key=lambda claim: (claim.data["unit_id"], claim.path))

    def acquire(
        self,
        progress: Mapping[str, Any],
        selector: str,
        *,
        role: str,
        session_id: str,
        base_revision: str,
        base_commit: str | None,
        workflow_revision: str,
        lease_seconds: int = 3600,
    ) -> list[ActiveClaim]:
        unit_ids = resolve_selector(progress, selector)
        if role not in {"translator", "reviewer"}:
            raise ClaimError("role must be translator or reviewer")
        session_id = self._require_nonempty(session_id, "session_id")
        base_revision = self._require_nonempty(base_revision, "base_revision")
        workflow_revision = self._require_nonempty(workflow_revision, "workflow_revision")
        if base_commit is not None and (not isinstance(base_commit, str) or not base_commit.strip()):
            raise ClaimError("base_commit must be null or a non-empty string")
        if type(lease_seconds) is not int or lease_seconds <= 0:
            raise ClaimError("lease_seconds must be a positive integer")

        for unit_id in unit_ids:
            try:
                self._read_claim(unit_id)
            except StorageNotFound:
                continue
            raise ClaimConflict(unit_id)

        claimed_at = self._now()
        expires_at = claimed_at + timedelta(seconds=lease_seconds)
        documents: list[tuple[str, dict[str, Any]]] = []
        for unit_id in unit_ids:
            document = {
                "schema_version": SCHEMA_VERSION,
                "claim_id": self._new_id(),
                "unit_id": unit_id,
                "role": role,
                "session_id": session_id,
                "base_revision": base_revision,
                "base_commit": base_commit,
                "workflow_revision": workflow_revision,
                "claimed_at": _format_utc(claimed_at),
                "expires_at": _format_utc(expires_at),
            }
            parse_document(SchemaKind.CLAIM, document)
            documents.append((_claim_path(unit_id), document))

        created: list[ActiveClaim] = []
        for path, document in documents:
            unit_id = document["unit_id"]
            try:
                version = self.repository.create(path, SchemaKind.CLAIM, document)
            except StorageAlreadyExists as exc:
                self._rollback_created(created, exc)
                raise ClaimConflict(unit_id) from exc
            except StorageError as exc:
                self._rollback_created(created, exc)
                raise
            created.append(ActiveClaim(path=path, data=document, version=version))
        return created

    def _rollback_created(self, created: list[ActiveClaim], cause: Exception) -> None:
        unresolved: list[str] = []
        for claim in reversed(created):
            try:
                self.repository.delete_if_version(claim.path, SchemaKind.CLAIM, claim.version)
            except (StorageError, ValueError):
                unresolved.append(claim.path)
        if unresolved:
            raise ClaimRollbackError(sorted(unresolved), cause) from cause

    def _create_event(self, event: dict[str, Any], occurred_at: datetime) -> str:
        parse_document(SchemaKind.CLAIM_EVENT, event)
        stamp = occurred_at.strftime("%Y%m%dT%H%M%SZ")
        path = f"{CLAIM_EVENT_PREFIX}/{stamp}-{event['event_id']}.json"
        try:
            self.repository.create(path, SchemaKind.CLAIM_EVENT, event)
        except (StorageError, ValueError) as exc:
            raise ClaimAuditError(f"cannot persist claim audit event {event['event_id']}: {exc}") from exc
        return path

    def _request_event(
        self,
        *,
        action: str,
        claim: ActiveClaim,
        occurred_at: datetime,
        reason: str,
        detail: str | None = None,
    ) -> str:
        event_id = self._new_id()
        event: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "event_id": event_id,
            "action": action,
            "unit_id": claim.data["unit_id"],
            "claim_revision": claim.version,
            "claim": claim.data,
            "occurred_at": _format_utc(occurred_at),
            "reason": reason,
        }
        if detail is not None:
            event["detail"] = detail
        self._create_event(event, occurred_at)
        return event_id

    def _completion_event(
        self,
        *,
        action: str,
        unit_id: str,
        request_event_id: str,
        occurred_at: datetime,
    ) -> str:
        event_id = self._new_id()
        event = {
            "schema_version": SCHEMA_VERSION,
            "event_id": event_id,
            "action": action,
            "unit_id": unit_id,
            "request_event_id": request_event_id,
            "occurred_at": _format_utc(occurred_at),
        }
        self._create_event(event, occurred_at)
        return event_id

    def release(
        self,
        progress: Mapping[str, Any],
        selector: str,
        *,
        session_id: str,
        detail: str | None = None,
    ) -> list[ClaimLifecycleResult]:
        session_id = self._require_nonempty(session_id, "session_id")
        unit_ids = resolve_selector(progress, selector)
        results: list[ClaimLifecycleResult] = []
        for unit_id in unit_ids:
            try:
                claim = self._read_claim(unit_id)
            except StorageNotFound as exc:
                raise ClaimConflict(unit_id, f"unit {unit_id} has no active claim") from exc
            owner = claim.data["session_id"]
            if owner != session_id:
                raise ClaimOwnershipError(unit_id, owner)

            occurred_at = self._now()
            request_id = self._request_event(
                action="release_requested",
                claim=claim,
                occurred_at=occurred_at,
                reason="owner_release",
                detail=detail,
            )
            try:
                self.repository.delete_if_version(claim.path, SchemaKind.CLAIM, claim.version)
            except (StorageNotFound, StorageVersionConflict) as exc:
                raise ClaimConflict(unit_id, f"unit {unit_id} changed before release completed") from exc
            completion_id = self._completion_event(
                action="released",
                unit_id=unit_id,
                request_event_id=request_id,
                occurred_at=self._now(),
            )
            results.append(
                ClaimLifecycleResult(
                    unit_id=unit_id,
                    status="released",
                    request_event_id=request_id,
                    completion_event_id=completion_id,
                )
            )
        return results

    def cleanup_expired(self, *, detail: str | None = None) -> list[ClaimLifecycleResult]:
        now = self._now()
        results: list[ClaimLifecycleResult] = []
        for claim in self.list_active():
            unit_id = claim.data["unit_id"]
            expires_at = _parse_claim_timestamp(claim.data["expires_at"])
            if expires_at > now:
                results.append(ClaimLifecycleResult(unit_id=unit_id, status="live"))
                continue

            request_id = self._request_event(
                action="cleanup_requested",
                claim=claim,
                occurred_at=now,
                reason="lease_expired",
                detail=detail,
            )
            try:
                self.repository.delete_if_version(claim.path, SchemaKind.CLAIM, claim.version)
            except (StorageNotFound, StorageVersionConflict):
                results.append(
                    ClaimLifecycleResult(
                        unit_id=unit_id,
                        status="conflict",
                        request_event_id=request_id,
                    )
                )
                continue
            completion_id = self._completion_event(
                action="cleaned",
                unit_id=unit_id,
                request_event_id=request_id,
                occurred_at=self._now(),
            )
            results.append(
                ClaimLifecycleResult(
                    unit_id=unit_id,
                    status="cleaned",
                    request_event_id=request_id,
                    completion_event_id=completion_id,
                )
            )
        return results

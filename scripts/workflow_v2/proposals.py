"""Immutable worker proposals and central shared-state reconciliation for Workflow v2."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .coordination import BookCoordinationManager, CoordinationError
from .repository import RepositoryError, WorkflowStateRepository
from .schemas import SchemaError, SchemaKind
from .shared_state import (
    SHARED_STATE_KEYS,
    SHARED_STATE_PATHS,
    SharedStateError,
    current_shared_state_revisions,
    validate_shared_state_snapshot,
)
from .storage import (
    StorageAlreadyExists,
    StorageError,
    StorageNotFound,
    StorageVersionConflict,
)


PROPOSAL_PREFIX = ".workflow/proposals"
HEX_ID_RE = re.compile(r"^[0-9a-f]{32}$")
UNIT_ID_RE = re.compile(r"^chapter-[0-9]{6}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PROPOSAL_KEYS = {
    "schema_version",
    "proposal_id",
    "unit_id",
    "claim_id",
    "role",
    "session_id",
    "base_revision",
    "base_commit",
    "workflow_revision",
    "shared_state_revisions",
    "target",
    "suggestion",
    "rationale",
    "proposed_at",
}
RESOLUTION_KEYS = {
    "schema_version",
    "proposal_id",
    "status",
    "target",
    "resolved_by_session_id",
    "resolved_at",
    "shared_state_revisions",
    "resulting_revision",
    "resulting_sha256",
    "reason",
}


class ProposalError(RuntimeError):
    """A worker proposal cannot be persisted or reconciled safely."""


class ProposalClaimError(ProposalError):
    """A proposal is not authorized by the current durable worker claim."""


class ProposalConflict(ProposalError):
    """Proposal durable state changed concurrently."""


@dataclass(frozen=True)
class ProposalSubmitResult:
    proposal: dict[str, Any]
    path: str
    revision: str


@dataclass(frozen=True)
class ProposalResolutionResult:
    resolution: dict[str, Any]
    path: str
    revision: str
    changed_shared_state: bool


def _nonempty(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProposalError(f"{field} must be a non-empty string")
    return value


def _format_utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ProposalError("proposal clock must return a timezone-aware datetime")
    return (
        value.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _parse_utc(value: object, field: str) -> datetime:
    text = _nonempty(value, field)
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ProposalError(f"{field} must be a valid UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProposalError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _require_exact_keys(data: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(data)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing:
        raise ProposalError(f"{label} missing field(s): {', '.join(missing)}")
    if extra:
        raise ProposalError(f"{label} has unsupported field(s): {', '.join(extra)}")


def validate_proposal(data: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(data, Mapping):
        raise ProposalError("proposal must be a JSON object")
    _require_exact_keys(data, PROPOSAL_KEYS, "proposal")
    if data["schema_version"] != 1 or type(data["schema_version"]) is not int:
        raise ProposalError("proposal schema_version must equal 1")
    proposal_id = _nonempty(data["proposal_id"], "proposal_id")
    if HEX_ID_RE.fullmatch(proposal_id) is None:
        raise ProposalError("proposal_id must be 32 lowercase hexadecimal characters")
    unit_id = _nonempty(data["unit_id"], "unit_id")
    if UNIT_ID_RE.fullmatch(unit_id) is None:
        raise ProposalError("unit_id must match chapter-[0-9]{6}")
    claim_id = _nonempty(data["claim_id"], "claim_id")
    if HEX_ID_RE.fullmatch(claim_id) is None:
        raise ProposalError("claim_id must be 32 lowercase hexadecimal characters")
    if data["role"] not in {"translator", "reviewer"}:
        raise ProposalError("role must be translator or reviewer")
    for key in ("session_id", "base_revision", "base_commit", "workflow_revision"):
        _nonempty(data[key], key)
    try:
        validate_shared_state_snapshot(data["shared_state_revisions"])
    except SharedStateError as exc:
        raise ProposalError(str(exc)) from exc
    if data["target"] not in SHARED_STATE_KEYS:
        raise ProposalError("target must be glossary or style_guide")
    _nonempty(data["suggestion"], "suggestion")
    rationale = data["rationale"]
    if rationale is not None:
        _nonempty(rationale, "rationale")
    _parse_utc(data["proposed_at"], "proposed_at")
    return copy.deepcopy(dict(data))


def validate_resolution(data: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(data, Mapping):
        raise ProposalError("proposal resolution must be a JSON object")
    _require_exact_keys(data, RESOLUTION_KEYS, "proposal resolution")
    if data["schema_version"] != 1 or type(data["schema_version"]) is not int:
        raise ProposalError("proposal resolution schema_version must equal 1")
    proposal_id = _nonempty(data["proposal_id"], "proposal_id")
    if HEX_ID_RE.fullmatch(proposal_id) is None:
        raise ProposalError("proposal_id must be 32 lowercase hexadecimal characters")
    status = data["status"]
    if status not in {"accepted", "rejected", "stale"}:
        raise ProposalError("status must be accepted, rejected, or stale")
    if data["target"] not in SHARED_STATE_KEYS:
        raise ProposalError("target must be glossary or style_guide")
    _nonempty(data["resolved_by_session_id"], "resolved_by_session_id")
    _parse_utc(data["resolved_at"], "resolved_at")
    try:
        validate_shared_state_snapshot(data["shared_state_revisions"])
    except SharedStateError as exc:
        raise ProposalError(str(exc)) from exc

    resulting_revision = data["resulting_revision"]
    resulting_sha256 = data["resulting_sha256"]
    reason = data["reason"]
    if status == "accepted":
        _nonempty(resulting_revision, "resulting_revision")
        digest = _nonempty(resulting_sha256, "resulting_sha256")
        if SHA256_RE.fullmatch(digest) is None:
            raise ProposalError("resulting_sha256 must be a lowercase SHA-256")
        if reason is not None:
            raise ProposalError("accepted resolution reason must be null")
    else:
        if resulting_revision is not None or resulting_sha256 is not None:
            raise ProposalError(
                "rejected/stale resolution must not contain a resulting revision or hash"
            )
        _nonempty(reason, "reason")
    return copy.deepcopy(dict(data))


def _serialize(data: Mapping[str, Any], *, resolution: bool) -> bytes:
    validated = validate_resolution(data) if resolution else validate_proposal(data)
    try:
        text = json.dumps(
            validated,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ) + "\n"
    except (TypeError, ValueError) as exc:
        raise ProposalError(f"proposal document is not JSON-serializable: {exc}") from exc
    return text.encode("utf-8")


def _load(storage, path: str, *, resolution: bool) -> tuple[dict[str, Any], str]:
    try:
        stored = storage.read(path)
    except StorageNotFound:
        raise
    except StorageError as exc:
        raise ProposalError(f"cannot read {path}: {exc}") from exc
    try:
        raw = json.loads(stored.content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProposalError(f"{path} is not valid UTF-8 JSON: {exc}") from exc
    validator = validate_resolution if resolution else validate_proposal
    return validator(raw), stored.version


def _proposal_path(proposal_id: str) -> str:
    return f"{PROPOSAL_PREFIX}/{proposal_id}.json"


def _resolution_path(proposal_id: str) -> str:
    return f"{PROPOSAL_PREFIX}/{proposal_id}.resolution.json"


class ProposalManager:
    """Worker proposal submission plus orchestrator-only reconciliation."""

    def __init__(
        self,
        repository: WorkflowStateRepository,
        *,
        now: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
        coordination: BookCoordinationManager | None = None,
    ):
        self.repository = repository
        self._now_factory = now or (lambda: datetime.now(timezone.utc))
        self._id_factory = id_factory or (lambda: uuid4().hex)
        self._coordination = coordination or BookCoordinationManager(
            repository,
            now=self._now_factory,
        )

    def _now(self) -> datetime:
        value = self._now_factory()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ProposalError("proposal clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc).replace(microsecond=0)

    def _new_id(self) -> str:
        value = self._id_factory()
        if not isinstance(value, str) or HEX_ID_RE.fullmatch(value) is None:
            raise ProposalError(
                "proposal id factory must return 32 lowercase hexadecimal characters"
            )
        return value

    def _claim(self, unit_id: str, session_id: str) -> dict[str, Any]:
        if not isinstance(unit_id, str) or UNIT_ID_RE.fullmatch(unit_id) is None:
            raise ProposalClaimError("unit_id must match chapter-[0-9]{6}")
        session_id = _nonempty(session_id, "session_id")
        path = f".workflow/claims/{unit_id}.json"
        try:
            claim = self.repository.read(path, SchemaKind.CLAIM).data
        except StorageNotFound as exc:
            raise ProposalClaimError(f"unit {unit_id} has no active worker claim") from exc
        except (StorageError, RepositoryError, SchemaError) as exc:
            raise ProposalClaimError(f"cannot validate worker claim for {unit_id}: {exc}") from exc
        if claim.get("session_id") != session_id:
            raise ProposalClaimError(
                f"unit {unit_id} claim belongs to session {claim.get('session_id')}"
            )
        if claim.get("role") not in {"translator", "reviewer"}:
            raise ProposalClaimError(f"unit {unit_id} claim has invalid worker role")
        if _parse_utc(claim.get("expires_at"), "claim.expires_at") <= self._now():
            raise ProposalClaimError(f"unit {unit_id} worker claim is expired")
        if not isinstance(claim.get("base_commit"), str) or not claim["base_commit"].strip():
            raise ProposalClaimError(
                f"unit {unit_id} worker claim must record a base_commit before proposing shared-state changes"
            )
        try:
            validate_shared_state_snapshot(claim.get("shared_state_revisions"))
        except SharedStateError as exc:
            raise ProposalClaimError(
                f"unit {unit_id} worker claim lacks a valid frozen shared-state snapshot: {exc}"
            ) from exc
        return copy.deepcopy(claim)

    def submit(
        self,
        unit_id: str,
        *,
        session_id: str,
        target: str,
        suggestion: str,
        rationale: str | None = None,
    ) -> ProposalSubmitResult:
        """Persist one immutable suggestion authorized by the active worker claim."""

        if target not in SHARED_STATE_KEYS:
            raise ProposalError("target must be glossary or style_guide")
        suggestion = _nonempty(suggestion, "suggestion")
        if rationale is not None:
            rationale = _nonempty(rationale, "rationale")
        claim = self._claim(unit_id, session_id)
        proposal_id = self._new_id()
        proposal = {
            "schema_version": 1,
            "proposal_id": proposal_id,
            "unit_id": unit_id,
            "claim_id": claim["claim_id"],
            "role": claim["role"],
            "session_id": claim["session_id"],
            "base_revision": claim["base_revision"],
            "base_commit": claim["base_commit"],
            "workflow_revision": claim["workflow_revision"],
            "shared_state_revisions": copy.deepcopy(claim["shared_state_revisions"]),
            "target": target,
            "suggestion": suggestion,
            "rationale": rationale,
            "proposed_at": _format_utc(self._now()),
        }
        content = _serialize(proposal, resolution=False)
        path = _proposal_path(proposal_id)
        try:
            revision = self.repository.storage.create_if_absent(path, content)
        except StorageAlreadyExists as exc:
            raise ProposalConflict(f"proposal {proposal_id} already exists") from exc
        except StorageError as exc:
            raise ProposalError(f"cannot persist proposal {proposal_id}: {exc}") from exc
        return ProposalSubmitResult(
            proposal=copy.deepcopy(proposal),
            path=path,
            revision=revision,
        )

    def _persist_resolution(
        self,
        resolution: dict[str, Any],
        *,
        changed_shared_state: bool,
    ) -> ProposalResolutionResult:
        path = _resolution_path(resolution["proposal_id"])
        content = _serialize(resolution, resolution=True)
        try:
            revision = self.repository.storage.create_if_absent(path, content)
        except StorageAlreadyExists:
            existing, existing_revision = _load(
                self.repository.storage, path, resolution=True
            )
            return ProposalResolutionResult(
                resolution=existing,
                path=path,
                revision=existing_revision,
                changed_shared_state=False,
            )
        except StorageError as exc:
            raise ProposalError(
                f"cannot persist proposal resolution {resolution['proposal_id']}: {exc}"
            ) from exc
        return ProposalResolutionResult(
            resolution=copy.deepcopy(resolution),
            path=path,
            revision=revision,
            changed_shared_state=changed_shared_state,
        )

    def _accepted_resolution(
        self,
        proposal: Mapping[str, Any],
        *,
        session_id: str,
        resulting_revision: str,
        replacement: bytes,
        changed_shared_state: bool,
    ) -> ProposalResolutionResult:
        resolution = {
            "schema_version": 1,
            "proposal_id": proposal["proposal_id"],
            "status": "accepted",
            "target": proposal["target"],
            "resolved_by_session_id": session_id,
            "resolved_at": _format_utc(self._now()),
            "shared_state_revisions": copy.deepcopy(proposal["shared_state_revisions"]),
            "resulting_revision": resulting_revision,
            "resulting_sha256": hashlib.sha256(replacement).hexdigest(),
            "reason": None,
        }
        return self._persist_resolution(
            resolution, changed_shared_state=changed_shared_state
        )

    def _terminal_resolution(
        self,
        proposal: Mapping[str, Any],
        *,
        session_id: str,
        status: str,
        reason: str,
    ) -> ProposalResolutionResult:
        resolution = {
            "schema_version": 1,
            "proposal_id": proposal["proposal_id"],
            "status": status,
            "target": proposal["target"],
            "resolved_by_session_id": session_id,
            "resolved_at": _format_utc(self._now()),
            "shared_state_revisions": copy.deepcopy(proposal["shared_state_revisions"]),
            "resulting_revision": None,
            "resulting_sha256": None,
            "reason": _nonempty(reason, "reason"),
        }
        return self._persist_resolution(resolution, changed_shared_state=False)

    def _recovery_match(
        self,
        proposal: Mapping[str, Any],
        replacement: bytes,
        current: Mapping[str, str],
    ) -> str | None:
        """Recognize a prior successful target CAS whose resolution write was interrupted."""

        target = proposal["target"]
        frozen = proposal["shared_state_revisions"]
        other_keys = SHARED_STATE_KEYS - {target}
        if any(current[key] != frozen[key] for key in other_keys):
            return None
        target_value = self.repository.storage.read(SHARED_STATE_PATHS[target])
        if target_value.content != replacement:
            return None
        return target_value.version

    def reconcile(
        self,
        proposal_id: str,
        *,
        session_id: str,
        accept: bool,
        replacement: bytes | None = None,
        reason: str | None = None,
    ) -> ProposalResolutionResult:
        """Resolve one proposal through the serialized single-writer shared-state path."""

        if not isinstance(proposal_id, str) or HEX_ID_RE.fullmatch(proposal_id) is None:
            raise ProposalError(
                "proposal_id must be 32 lowercase hexadecimal characters"
            )
        session_id = _nonempty(session_id, "session_id")
        if type(accept) is not bool:
            raise ProposalError("accept must be a boolean")

        try:
            lease = self._coordination.acquire(
                operation="proposal_reconcile",
                session_id=session_id,
            )
        except CoordinationError as exc:
            raise ProposalConflict(
                f"proposal reconciliation is blocked by book coordination: {exc}"
            ) from exc

        try:
            result = self._reconcile_locked(
                proposal_id,
                session_id=session_id,
                accept=accept,
                replacement=replacement,
                reason=reason,
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
            raise ProposalConflict(
                "proposal reconciliation completed but coordination mutex could not be released"
            ) from exc
        return result

    def _reconcile_locked(
        self,
        proposal_id: str,
        *,
        session_id: str,
        accept: bool,
        replacement: bytes | None,
        reason: str | None,
    ) -> ProposalResolutionResult:
        resolution_path = _resolution_path(proposal_id)
        try:
            existing, revision = _load(
                self.repository.storage, resolution_path, resolution=True
            )
        except StorageNotFound:
            pass
        else:
            return ProposalResolutionResult(
                resolution=existing,
                path=resolution_path,
                revision=revision,
                changed_shared_state=False,
            )

        proposal, _ = _load(
            self.repository.storage, _proposal_path(proposal_id), resolution=False
        )

        if not accept:
            if replacement is not None:
                raise ProposalError("rejected proposal must not provide replacement bytes")
            return self._terminal_resolution(
                proposal,
                session_id=session_id,
                status="rejected",
                reason=reason or "rejected by orchestrator",
            )

        if not isinstance(replacement, bytes) or not replacement.strip():
            raise ProposalError("accepted proposal requires non-empty replacement bytes")
        if reason is not None:
            raise ProposalError("accepted proposal reason must be null")

        try:
            current = current_shared_state_revisions(self.repository.storage)
        except SharedStateError as exc:
            raise ProposalError(str(exc)) from exc
        frozen = proposal["shared_state_revisions"]
        changed = [key for key in sorted(SHARED_STATE_KEYS) if current[key] != frozen[key]]
        if changed:
            try:
                recovered_revision = self._recovery_match(proposal, replacement, current)
            except StorageError as exc:
                raise ProposalError(f"cannot verify reconciliation recovery: {exc}") from exc
            if recovered_revision is not None:
                return self._accepted_resolution(
                    proposal,
                    session_id=session_id,
                    resulting_revision=recovered_revision,
                    replacement=replacement,
                    changed_shared_state=False,
                )
            return self._terminal_resolution(
                proposal,
                session_id=session_id,
                status="stale",
                reason="shared state changed: " + ", ".join(changed),
            )

        target = proposal["target"]
        target_path = SHARED_STATE_PATHS[target]
        try:
            resulting_revision = self.repository.storage.write_if_version(
                target_path,
                replacement,
                frozen[target],
            )
        except StorageVersionConflict:
            try:
                current = current_shared_state_revisions(self.repository.storage)
                recovered_revision = self._recovery_match(proposal, replacement, current)
            except (SharedStateError, StorageError) as exc:
                raise ProposalConflict(
                    f"shared state changed while reconciling proposal {proposal_id}: {exc}"
                ) from exc
            if recovered_revision is not None:
                return self._accepted_resolution(
                    proposal,
                    session_id=session_id,
                    resulting_revision=recovered_revision,
                    replacement=replacement,
                    changed_shared_state=False,
                )
            changed = [
                key for key in sorted(SHARED_STATE_KEYS) if current[key] != frozen[key]
            ]
            return self._terminal_resolution(
                proposal,
                session_id=session_id,
                status="stale",
                reason="shared state changed: " + ", ".join(changed or [target]),
            )
        except StorageError as exc:
            raise ProposalError(f"cannot write {target_path}: {exc}") from exc

        return self._accepted_resolution(
            proposal,
            session_id=session_id,
            resulting_revision=resulting_revision,
            replacement=replacement,
            changed_shared_state=True,
        )

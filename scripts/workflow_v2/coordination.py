"""Short-lived book admission coordination for Workflow v2."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from .repository import RepositoryError, WorkflowStateRepository
from .schemas import SCHEMA_VERSION, SchemaError, SchemaKind
from .storage import (
    StorageAlreadyExists,
    StorageError,
    StorageNotFound,
    StorageVersionConflict,
)


COORDINATION_PATH = ".workflow/coordination-lock.json"
FINALIZATION_PATH = ".workflow/finalization.json"
HEX_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class CoordinationError(RuntimeError):
    """Book-level admission coordination could not be completed safely."""


class CoordinationConflict(CoordinationError):
    """Another live admission operation currently owns the coordination mutex."""


@dataclass(frozen=True)
class CoordinationLease:
    path: str
    data: dict[str, Any]
    version: str


def _parse_utc(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    return datetime.fromisoformat(normalized).astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    value = value.astimezone(timezone.utc).replace(microsecond=0)
    return value.isoformat().replace("+00:00", "Z")


class BookCoordinationManager:
    """Serialize short claim/finalize admission transitions."""

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
            raise CoordinationError("coordination clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc).replace(microsecond=0)

    def _new_id(self) -> str:
        value = self._id_factory()
        if not isinstance(value, str) or not HEX_ID_RE.fullmatch(value):
            raise CoordinationError(
                "coordination id factory must return 32 lowercase hexadecimal characters"
            )
        return value

    def _read(self) -> CoordinationLease:
        loaded = self.repository.read(COORDINATION_PATH, SchemaKind.COORDINATION_LOCK)
        return CoordinationLease(COORDINATION_PATH, loaded.data, loaded.version)

    def acquire(
        self,
        *,
        operation: str,
        session_id: str,
        lease_seconds: int = 60,
    ) -> CoordinationLease:
        if operation not in {"claim_admission", "finalize_admission"}:
            raise CoordinationError("operation must be claim_admission or finalize_admission")
        if not isinstance(session_id, str) or not session_id.strip():
            raise CoordinationError("session_id must be a non-empty string")
        if type(lease_seconds) is not int or lease_seconds <= 0:
            raise CoordinationError("lease_seconds must be a positive integer")

        now = self._now()
        document = {
            "schema_version": SCHEMA_VERSION,
            "lock_id": self._new_id(),
            "operation": operation,
            "session_id": session_id,
            "acquired_at": _format_utc(now),
            "expires_at": _format_utc(now + timedelta(seconds=lease_seconds)),
        }

        try:
            version = self.repository.create(
                COORDINATION_PATH,
                SchemaKind.COORDINATION_LOCK,
                document,
            )
            return CoordinationLease(COORDINATION_PATH, document, version)
        except StorageAlreadyExists:
            pass
        except (StorageError, RepositoryError, SchemaError) as exc:
            raise CoordinationError(f"cannot acquire coordination mutex: {exc}") from exc

        try:
            current = self._read()
        except StorageNotFound:
            try:
                version = self.repository.create(
                    COORDINATION_PATH,
                    SchemaKind.COORDINATION_LOCK,
                    document,
                )
            except StorageAlreadyExists as exc:
                raise CoordinationConflict("coordination mutex changed during acquisition") from exc
            except (StorageError, RepositoryError, SchemaError) as exc:
                raise CoordinationError(f"cannot acquire coordination mutex: {exc}") from exc
            return CoordinationLease(COORDINATION_PATH, document, version)
        except (StorageError, RepositoryError, SchemaError) as exc:
            raise CoordinationError(f"coordination mutex is invalid or unavailable: {exc}") from exc

        if _parse_utc(current.data["expires_at"]) > now:
            raise CoordinationConflict(
                f"coordination mutex is held by {current.data['operation']} session "
                f"{current.data['session_id']} until {current.data['expires_at']}"
            )

        try:
            self.repository.delete_if_version(
                COORDINATION_PATH,
                SchemaKind.COORDINATION_LOCK,
                current.version,
            )
        except (StorageNotFound, StorageVersionConflict) as exc:
            raise CoordinationConflict("expired coordination mutex changed before cleanup") from exc
        except (StorageError, RepositoryError, SchemaError) as exc:
            raise CoordinationError(f"cannot clean expired coordination mutex: {exc}") from exc

        try:
            version = self.repository.create(
                COORDINATION_PATH,
                SchemaKind.COORDINATION_LOCK,
                document,
            )
        except StorageAlreadyExists as exc:
            raise CoordinationConflict("coordination mutex was reacquired concurrently") from exc
        except (StorageError, RepositoryError, SchemaError) as exc:
            raise CoordinationError(f"cannot acquire coordination mutex after cleanup: {exc}") from exc
        return CoordinationLease(COORDINATION_PATH, document, version)

    def release(self, lease: CoordinationLease) -> None:
        if not isinstance(lease, CoordinationLease):
            raise CoordinationError("release requires a CoordinationLease")
        try:
            self.repository.delete_if_version(
                lease.path,
                SchemaKind.COORDINATION_LOCK,
                lease.version,
            )
        except (StorageNotFound, StorageVersionConflict) as exc:
            raise CoordinationConflict("coordination mutex changed before release") from exc
        except (StorageError, RepositoryError, SchemaError) as exc:
            raise CoordinationError(f"cannot release coordination mutex: {exc}") from exc

    def finalization_active(self) -> bool:
        try:
            self.repository.read(FINALIZATION_PATH, SchemaKind.FINALIZATION_LOCK)
        except StorageNotFound:
            return False
        except (StorageError, RepositoryError, SchemaError) as exc:
            raise CoordinationError(f"finalization marker is invalid or unavailable: {exc}") from exc
        return True

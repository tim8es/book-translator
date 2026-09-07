"""Strict validation and serialization for transient workflow migration recovery."""

from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any

from .storage import StorageBackend


MIGRATION_PATH = ".workflow/migration.json"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_KINDS = {"metadata", "progress", "review_ledger", "claim", "source_manifest"}
_TOP_LEVEL_KEYS = {
    "schema_version",
    "operation",
    "book_slug",
    "from_revision",
    "to_revision",
    "phase",
    "documents",
}
_ENTRY_KEYS = {
    "path",
    "kind",
    "original_exists",
    "original_revision",
    "original_sha256",
    "original_bytes_base64",
    "target_sha256",
    "resulting_revision",
}


class MigrationJournalError(RuntimeError):
    """Migration recovery journal is malformed or cannot be decoded safely."""


def _nonempty_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MigrationJournalError(f"{field} must be a non-empty string")
    return value


def _nullable_nonempty_string(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _nonempty_string(value, field)


def _sha256(value: object, field: str) -> str:
    text = _nonempty_string(value, field)
    if _SHA256_RE.fullmatch(text) is None:
        raise MigrationJournalError(
            f"{field} must be a 64-character lowercase hexadecimal SHA-256"
        )
    return text


def _safe_path(value: object, field: str) -> str:
    text = _nonempty_string(value, field)
    if "\\" in text:
        raise MigrationJournalError(f"{field} must be a safe relative POSIX path")
    path = PurePosixPath(text)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise MigrationJournalError(f"{field} must be a safe relative POSIX path")
    return text


def _require_exact_keys(data: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(data)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing:
        raise MigrationJournalError(f"{label} missing field(s): {', '.join(missing)}")
    if extra:
        raise MigrationJournalError(f"{label} has unsupported field(s): {', '.join(extra)}")


def validate_migration_journal(data: Mapping[str, Any]) -> dict[str, Any]:
    """Return a validated deep copy of one migration recovery journal."""

    if not isinstance(data, Mapping):
        raise MigrationJournalError("migration journal must be a JSON object")
    _require_exact_keys(data, _TOP_LEVEL_KEYS, "migration journal")

    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise MigrationJournalError("schema_version must equal 1")
    if data["operation"] != "workflow_upgrade":
        raise MigrationJournalError("operation must equal workflow_upgrade")
    _nonempty_string(data["book_slug"], "book_slug")
    _nullable_nonempty_string(data["from_revision"], "from_revision")
    _nonempty_string(data["to_revision"], "to_revision")

    phase = data["phase"]
    if phase not in {"prepared", "applied"}:
        raise MigrationJournalError("phase must be prepared or applied")

    documents = data["documents"]
    if not isinstance(documents, list) or not documents:
        raise MigrationJournalError("documents must be a non-empty array")

    seen_paths: set[str] = set()
    for index, raw_entry in enumerate(documents):
        label = f"documents[{index}]"
        if not isinstance(raw_entry, Mapping):
            raise MigrationJournalError(f"{label} must be an object")
        _require_exact_keys(raw_entry, _ENTRY_KEYS, label)

        path = _safe_path(raw_entry["path"], f"{label}.path")
        if path in seen_paths:
            raise MigrationJournalError(f"{label}.path must be unique")
        seen_paths.add(path)

        kind = _nonempty_string(raw_entry["kind"], f"{label}.kind")
        if kind not in _ALLOWED_KINDS:
            raise MigrationJournalError(
                f"{label}.kind must be one of {', '.join(sorted(_ALLOWED_KINDS))}"
            )

        original_exists = raw_entry["original_exists"]
        if type(original_exists) is not bool:
            raise MigrationJournalError(f"{label}.original_exists must be a boolean")

        original_revision = raw_entry["original_revision"]
        original_sha256 = raw_entry["original_sha256"]
        original_base64 = raw_entry["original_bytes_base64"]
        if original_exists:
            _nonempty_string(original_revision, f"{label}.original_revision")
            expected_hash = _sha256(original_sha256, f"{label}.original_sha256")
            encoded = _nonempty_string(original_base64, f"{label}.original_bytes_base64")
            try:
                decoded = base64.b64decode(encoded.encode("ascii"), validate=True)
            except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
                raise MigrationJournalError(
                    f"{label}.original_bytes_base64 must be strict base64"
                ) from exc
            actual_hash = hashlib.sha256(decoded).hexdigest()
            if actual_hash != expected_hash:
                raise MigrationJournalError(
                    f"{label}.original_bytes_base64 does not match original_sha256"
                )
        elif any(value is not None for value in (original_revision, original_sha256, original_base64)):
            raise MigrationJournalError(
                f"{label} original revision/hash/bytes must all be null when original_exists is false"
            )

        _sha256(raw_entry["target_sha256"], f"{label}.target_sha256")
        resulting = _nullable_nonempty_string(
            raw_entry["resulting_revision"], f"{label}.resulting_revision"
        )
        if phase == "applied" and resulting is None:
            raise MigrationJournalError(
                f"{label}.resulting_revision is required while phase is applied"
            )

    return copy.deepcopy(dict(data))


def serialize_migration_journal(data: Mapping[str, Any]) -> bytes:
    """Serialize a validated journal to deterministic UTF-8 JSON bytes."""

    validated = validate_migration_journal(data)
    try:
        text = json.dumps(
            validated,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ) + "\n"
    except (TypeError, ValueError) as exc:
        raise MigrationJournalError(f"migration journal is not JSON-serializable: {exc}") from exc
    return text.encode("utf-8")


def load_migration_journal(storage: StorageBackend) -> tuple[dict[str, Any], str]:
    """Load and strictly validate the durable migration journal.

    StorageNotFound intentionally propagates so callers can distinguish an absent journal
    from a malformed one.
    """

    stored = storage.read(MIGRATION_PATH)
    try:
        text = stored.content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MigrationJournalError(f"{MIGRATION_PATH}: invalid UTF-8: {exc}") from exc
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MigrationJournalError(f"{MIGRATION_PATH}: invalid JSON: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise MigrationJournalError(f"{MIGRATION_PATH}: journal must be a JSON object")
    return validate_migration_journal(raw), stored.version

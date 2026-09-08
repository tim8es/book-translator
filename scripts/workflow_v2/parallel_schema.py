"""Package-level schema extension for explicit parallel workflow evidence."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from . import schemas
from .schemas import SchemaKind


_SHARED_STATE_KEYS = {"glossary", "style_guide"}
_TRANSLATION_ACCEPTANCE_KEYS = {
    "schema_version",
    "unit_id",
    "claim_id",
    "claim_revision",
    "role",
    "session_id",
    "base_revision",
    "base_commit",
    "workflow_revision",
    "accepted_from_progress_revision",
    "shared_state_revisions",
    "source_sha256",
    "translation_sha256",
    "accepted_at",
}


def _validate_shared_state_snapshot(
    snapshot: object,
    schema: SchemaKind,
    *,
    path: str,
    allow_none: bool = False,
) -> None:
    if snapshot is None and allow_none:
        return
    if not isinstance(snapshot, Mapping):
        raise schemas._field(schema, path, "must be an object")

    keys = set(snapshot)
    if keys != _SHARED_STATE_KEYS:
        raise schemas._field(
            schema,
            path,
            "must contain exactly glossary and style_guide",
        )

    for key in sorted(_SHARED_STATE_KEYS):
        schemas._require_nonempty_string(
            snapshot,
            key,
            schema,
            path=f"{path}.{key}",
        )


def _validate_claim_shared_state(data: Mapping[str, Any], schema: SchemaKind) -> None:
    if "shared_state_revisions" not in data:
        return

    _validate_shared_state_snapshot(
        data.get("shared_state_revisions"),
        schema,
        path="shared_state_revisions",
    )

    base_commit = data.get("base_commit")
    if not isinstance(base_commit, str) or not base_commit.strip():
        raise schemas._field(
            schema,
            "base_commit",
            "must be a non-empty string when shared_state_revisions is present",
        )


def _validate_translation_acceptance(
    chapter: Mapping[str, Any],
    schema: SchemaKind,
    *,
    index: int,
) -> None:
    if "translation_acceptance" not in chapter:
        return
    prefix = f"chapters[{index}].translation_acceptance"
    evidence = chapter.get("translation_acceptance")
    if not isinstance(evidence, Mapping):
        raise schemas._field(schema, prefix, "must be an object")

    actual = set(evidence)
    missing = sorted(_TRANSLATION_ACCEPTANCE_KEYS - actual)
    extra = sorted(actual - _TRANSLATION_ACCEPTANCE_KEYS)
    if missing:
        raise schemas._field(schema, prefix, f"missing field(s): {', '.join(missing)}")
    if extra:
        raise schemas._field(schema, prefix, f"has unsupported field(s): {', '.join(extra)}")

    version = evidence.get("schema_version")
    if type(version) is not int or version != 1:
        raise schemas._field(schema, f"{prefix}.schema_version", "must equal 1")

    number = chapter.get("number")
    unit_id = schemas._require_nonempty_string(
        evidence,
        "unit_id",
        schema,
        path=f"{prefix}.unit_id",
    )
    expected_unit_id = f"chapter-{number:06d}" if type(number) is int else None
    if unit_id != expected_unit_id:
        raise schemas._field(
            schema,
            f"{prefix}.unit_id",
            f"must equal {expected_unit_id}",
        )

    claim_id = schemas._require_nonempty_string(
        evidence,
        "claim_id",
        schema,
        path=f"{prefix}.claim_id",
    )
    schemas._validate_hex_id(claim_id, schema, f"{prefix}.claim_id")
    for key in (
        "claim_revision",
        "session_id",
        "base_revision",
        "workflow_revision",
        "accepted_from_progress_revision",
    ):
        schemas._require_nonempty_string(
            evidence,
            key,
            schema,
            path=f"{prefix}.{key}",
        )

    if evidence.get("role") != "translator":
        raise schemas._field(schema, f"{prefix}.role", "must equal translator")

    base_commit = evidence.get("base_commit")
    if base_commit is not None and (
        not isinstance(base_commit, str) or not base_commit.strip()
    ):
        raise schemas._field(
            schema,
            f"{prefix}.base_commit",
            "must be null or a non-empty string",
        )

    snapshot = evidence.get("shared_state_revisions")
    _validate_shared_state_snapshot(
        snapshot,
        schema,
        path=f"{prefix}.shared_state_revisions",
        allow_none=True,
    )
    if snapshot is not None and (
        not isinstance(base_commit, str) or not base_commit.strip()
    ):
        raise schemas._field(
            schema,
            f"{prefix}.base_commit",
            "must be a non-empty string when shared_state_revisions is present",
        )

    for key in ("source_sha256", "translation_sha256"):
        digest = schemas._require_nonempty_string(
            evidence,
            key,
            schema,
            path=f"{prefix}.{key}",
        )
        schemas._validate_sha256(digest, schema, f"{prefix}.{key}")

    accepted_at = schemas._require_nonempty_string(
        evidence,
        "accepted_at",
        schema,
        path=f"{prefix}.accepted_at",
    )
    schemas._parse_utc_timestamp(accepted_at, schema, f"{prefix}.accepted_at")

    if chapter.get("status") not in {"translated", "reviewed"}:
        raise schemas._field(
            schema,
            prefix,
            "is only valid for translated or reviewed chapters",
        )


def install_parallel_schema_extensions() -> None:
    """Install explicit-parallel claim/coordination/progress validation exactly once."""

    if getattr(schemas, "_explicit_parallel_v1_installed", False):
        return

    claim_validator = schemas._VALIDATORS[SchemaKind.CLAIM]
    progress_validator = schemas._VALIDATORS[SchemaKind.PROGRESS]
    coordination_validator = schemas._VALIDATORS[SchemaKind.COORDINATION_LOCK]

    def validate_claim(data: Mapping[str, Any], schema: SchemaKind) -> None:
        claim_validator(data, schema)
        _validate_claim_shared_state(data, schema)

    def validate_progress(data: Mapping[str, Any], schema: SchemaKind) -> None:
        progress_validator(data, schema)
        chapters = data.get("chapters")
        if not isinstance(chapters, list):
            return
        for index, chapter in enumerate(chapters):
            if isinstance(chapter, Mapping):
                _validate_translation_acceptance(chapter, schema, index=index)

    def validate_coordination(data: Mapping[str, Any], schema: SchemaKind) -> None:
        if data.get("operation") in {"proposal_reconcile", "translation_acceptance"}:
            surrogate = dict(data)
            surrogate["operation"] = "claim_admission"
            coordination_validator(surrogate, schema)
            return
        coordination_validator(data, schema)

    schemas._VALIDATORS[SchemaKind.CLAIM] = validate_claim
    schemas._VALIDATORS[SchemaKind.PROGRESS] = validate_progress
    schemas._VALIDATORS[SchemaKind.COORDINATION_LOCK] = validate_coordination
    setattr(schemas, "_explicit_parallel_v1_installed", True)

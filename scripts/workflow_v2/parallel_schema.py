"""Package-level schema extension for explicit parallel claim snapshots."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from . import schemas
from .schemas import SchemaKind


_SHARED_STATE_KEYS = {"glossary", "style_guide"}


def _validate_claim_shared_state(data: Mapping[str, Any], schema: SchemaKind) -> None:
    if "shared_state_revisions" not in data:
        return

    snapshot = data.get("shared_state_revisions")
    if not isinstance(snapshot, Mapping):
        raise schemas._field(schema, "shared_state_revisions", "must be an object")

    keys = set(snapshot)
    if keys != _SHARED_STATE_KEYS:
        raise schemas._field(
            schema,
            "shared_state_revisions",
            "must contain exactly glossary and style_guide",
        )

    for key in sorted(_SHARED_STATE_KEYS):
        schemas._require_nonempty_string(
            snapshot,
            key,
            schema,
            path=f"shared_state_revisions.{key}",
        )

    base_commit = data.get("base_commit")
    if not isinstance(base_commit, str) or not base_commit.strip():
        raise schemas._field(
            schema,
            "base_commit",
            "must be a non-empty string when shared_state_revisions is present",
        )


def install_parallel_schema_extensions() -> None:
    """Install explicit-parallel claim/coordination validation exactly once."""

    if getattr(schemas, "_explicit_parallel_v1_installed", False):
        return

    claim_validator = schemas._VALIDATORS[SchemaKind.CLAIM]
    coordination_validator = schemas._VALIDATORS[SchemaKind.COORDINATION_LOCK]

    def validate_claim(data: Mapping[str, Any], schema: SchemaKind) -> None:
        claim_validator(data, schema)
        _validate_claim_shared_state(data, schema)

    def validate_coordination(data: Mapping[str, Any], schema: SchemaKind) -> None:
        if data.get("operation") == "proposal_reconcile":
            surrogate = dict(data)
            surrogate["operation"] = "claim_admission"
            coordination_validator(surrogate, schema)
            return
        coordination_validator(data, schema)

    schemas._VALIDATORS[SchemaKind.CLAIM] = validate_claim
    schemas._VALIDATORS[SchemaKind.COORDINATION_LOCK] = validate_coordination
    setattr(schemas, "_explicit_parallel_v1_installed", True)

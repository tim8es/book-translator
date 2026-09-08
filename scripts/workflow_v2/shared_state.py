"""Shared glossary/style revision guards for explicit parallel Workflow v2 work."""

from __future__ import annotations

from collections.abc import Mapping

from .storage import StorageBackend, StorageError


SHARED_STATE_PATHS = {
    "glossary": "glossary.md",
    "style_guide": "style-guide.md",
}
SHARED_STATE_KEYS = frozenset(SHARED_STATE_PATHS)


class SharedStateError(RuntimeError):
    """Shared glossary/style state cannot be validated safely."""


class SharedStateStale(SharedStateError):
    """Frozen worker shared-state revisions no longer match durable state."""

    def __init__(self, changed_keys: list[str]):
        self.changed_keys = tuple(sorted(changed_keys))
        super().__init__("shared state changed: " + ", ".join(self.changed_keys))


def validate_shared_state_snapshot(snapshot: object) -> dict[str, str]:
    """Return a strict copy of a frozen glossary/style revision snapshot."""

    if not isinstance(snapshot, Mapping):
        raise SharedStateError("shared_state_revisions must be an object")
    if set(snapshot) != SHARED_STATE_KEYS:
        raise SharedStateError(
            "shared_state_revisions must contain exactly glossary and style_guide"
        )
    result: dict[str, str] = {}
    for key in sorted(SHARED_STATE_KEYS):
        value = snapshot.get(key)
        if not isinstance(value, str) or not value.strip():
            raise SharedStateError(
                f"shared_state_revisions.{key} must be a non-empty string"
            )
        result[key] = value
    return result


def current_shared_state_revisions(storage: StorageBackend) -> dict[str, str]:
    """Read current backend revisions for both shared text files."""

    result: dict[str, str] = {}
    try:
        for key, path in SHARED_STATE_PATHS.items():
            result[key] = storage.read(path).version
    except StorageError as exc:
        raise SharedStateError(f"cannot read shared state: {exc}") from exc
    return result


def require_current_shared_state(
    storage: StorageBackend,
    snapshot: object,
) -> dict[str, str]:
    """Fail closed when any current shared-state revision differs from a worker snapshot."""

    expected = validate_shared_state_snapshot(snapshot)
    current = current_shared_state_revisions(storage)
    changed = [key for key in sorted(SHARED_STATE_KEYS) if current[key] != expected[key]]
    if changed:
        raise SharedStateStale(changed)
    return current

"""Resolve role instruction paths from the selected installation's manifest.

This module never reads chapters or authorizes durable state transitions.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path


class ContextRoutingError(ValueError):
    """The selected agent manifest cannot provide a safe role context."""


def read_manifest(root: Path) -> dict:
    """Read routing once from the workflow installation, not a book workspace."""
    try:
        manifest = json.loads((root / "agent-manifest.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ContextRoutingError(f"cannot read agent manifest: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 3:
        raise ContextRoutingError("unsupported agent manifest")
    return manifest


def contract_paths(
    manifest: Mapping[str, object], role: str, *, overlays: Sequence[str] = (),
) -> list[str]:
    """Select deduplicated role contracts and explicitly requested overlays."""
    contracts = manifest.get("contracts")
    profiles = manifest.get("context_profiles")
    available = manifest.get("context_overlays")
    if not all(isinstance(value, Mapping) for value in (contracts, profiles, available)):
        raise ContextRoutingError("agent manifest lacks role-context routing")

    keys = profiles.get(role)
    if not isinstance(keys, list) or not keys:
        raise ContextRoutingError(f"role {role!r} is not configured")
    selected = list(keys)
    for name in overlays:
        group = available.get(name)
        extra = group.get(role) if isinstance(group, Mapping) else None
        if not isinstance(extra, list) or not extra:
            raise ContextRoutingError(f"{name} contracts for {role} are not configured")
        selected.extend(extra)

    paths: list[str] = []
    for key in selected:
        path = contracts.get(key) if isinstance(key, str) else None
        if (not isinstance(path, str)
                or not (path == "AGENTS.md" or (path.startswith("docs/") and path.endswith(".md")))
                or ".." in path.split("/")):
            raise ContextRoutingError(f"invalid contract path for {key!r}")
        if path not in paths:
            paths.append(path)
    return paths

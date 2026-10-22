"""Read-only live Reviewer queue from durable status, not a second work database."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .operations import next_unit_operation


def next_review(
    status: Mapping[str, Any],
    session_id: str,
    *,
    wave: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a proposed claim or already-owned work; claim CAS remains mandatory."""
    if not isinstance(session_id, str) or not 1 <= len(session_id.strip()) <= 128:
        raise ValueError("reviewer session_id must be a nonempty string up to 128 chars")
    output: dict[str, Any] = {
        "schema_version": 1, "book_slug": status.get("book_slug"),
        "session_id": session_id, "role": "reviewer", "advisory_only": True,
    }

    def emit(action: str, **details: Any) -> dict[str, Any]:
        return {**output, "action": action, **details}

    if not status.get("valid"):
        return emit("blocked", reason="preflight_failed", errors=status.get("errors", []))
    if status.get("finalization", {}).get("active"):
        return emit("blocked", reason="finalization_active")
    revs = status.get("state_revisions")
    corpus = status.get("corpus")
    if not isinstance(revs, Mapping) or not isinstance(corpus, Mapping):
        return emit("blocked", reason="missing_current_revisions")
    shared = {key: revs.get(key) for key in ("glossary", "style_guide")}
    if any(not isinstance(value, str) or not value for value in shared.values()):
        return emit("blocked", reason="missing_current_revisions")
    if wave is not None and (
        wave.get("book_slug") != status.get("book_slug")
        or wave.get("workflow_revision") != status.get("workflow_revision")
        or wave.get("source_sha256") != corpus.get("source_sha256")
        or wave.get("shared_state_revisions") != shared
    ):
        return emit("blocked", reason="wave_revision_changed")

    allowed = set(wave["unit_ids"]) if wave else None
    claims = {claim["unit_id"]: claim for claim in status.get("claims", [])}
    units = sorted(status.get("units", []), key=lambda u: u["chapter_number"])
    available = []
    for unit in units:
        uid = unit["unit_id"]
        if allowed is not None and uid not in allowed:
            continue
        if next_unit_operation(unit["lifecycle"], unit["review"]) != "review":
            continue
        claim = claims.get(uid)
        if claim and claim.get("session_id") == session_id and claim.get("role") == "reviewer":
            return emit("work", unit_id=uid, chapter_number=unit["chapter_number"],
                        operation="review")
        if claim is None:
            available.append(unit)
    if available:
        first = available[0]
        return emit("claim", unit_id=first["unit_id"],
                    chapter_number=first["chapter_number"], operation="review",
                    ready_count=len(available))
    scoped = [u for u in units if allowed is None or u["unit_id"] in allowed]
    if scoped and all(u["lifecycle"] == "reviewed" for u in scoped):
        return emit("complete")
    return emit("wait", reason="no_unclaimed_accepted_translation")

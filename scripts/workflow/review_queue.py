"""Read-only rolling Reviewer queue, derived only from authoritative status.

No ownership is asserted here. A worker must still acquire the exact reviewer
claim, compare original and accepted translation and record hash-bound evidence.
"""
from __future__ import annotations

from collections.abc import Mapping


def next_review(status: Mapping, session_id: str, *, wave_units=None) -> dict:
    base = {"schema_version": 1, "book_slug": status.get("book_slug"),
            "session_id": session_id, "role": "reviewer"}
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("reviewer session id is required")
    if not status.get("valid"):
        return {**base, "action": "blocked", "reason": "preflight_failed",
                "errors": list(status.get("errors") or [])}
    if status.get("finalization", {}).get("active"):
        return {**base, "action": "blocked", "reason": "finalization_active"}
    units = status.get("units")
    if not isinstance(units, list):
        raise ValueError("verified unit list required")
    active_claims = {item["unit_id"]: item for item in status.get("claims", ())}
    waiting = False
    for unit in sorted(units, key=lambda x: x["chapter_number"]):
        if wave_units is not None and unit["unit_id"] not in wave_units:
            continue
        if unit["lifecycle"] == "reviewed":
            continue
        if unit["lifecycle"] == "translated" and unit["review"] in {
            "missing", "corrections_required", "stale", "unreviewed",
        }:
            claim = active_claims.get(unit["unit_id"])
            if claim:
                if claim.get("session_id") == session_id and claim.get("role") == "reviewer":
                    return {**base, "action": "work", "operation": "review",
                            "unit_id": unit["unit_id"], "chapter_number": unit["chapter_number"],
                            "claim_expires_at": claim.get("expires_at")}
                waiting = True
                continue
            return {**base, "action": "claim", "operation": "review",
                    "unit_id": unit["unit_id"], "chapter_number": unit["chapter_number"]}
        waiting = True
    return {**base, "action": "wait" if waiting else "complete",
            "reason": "awaiting_accepted_translations" if waiting else "all_units_reviewed"}

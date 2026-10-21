"""Read-only, deterministic workpacks for manually opened chat sessions.

Assignments are advisory, not claims. Workers must acquire each unit's durable
role claim before work; old plans cannot authorize writes.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any


class SessionPlanError(ValueError):
    """The requested session plan is invalid or the book cannot be dispatched."""


def plan_sessions(
    status: Mapping[str, Any],
    *,
    sessions: int,
    source_sizes: Mapping[str, int],
    base_commit: str,
) -> dict[str, Any]:
    if type(sessions) is not int or sessions < 1:
        raise SessionPlanError("sessions must be a positive integer")
    if not status.get("valid"):
        raise SessionPlanError("cannot plan sessions without a valid preflight")
    if status.get("finalization", {}).get("active"):
        raise SessionPlanError("cannot plan during finalization")
    if not isinstance(base_commit, str) or not base_commit.strip():
        raise SessionPlanError("base_commit is required; resolve the repository HEAD")
    revisions = status.get("state_revisions")
    if not isinstance(revisions, Mapping):
        raise SessionPlanError("state revisions are unavailable")
    frozen = {key: revisions.get(key) for key in ("glossary", "style_guide")}
    if not all(isinstance(value, str) and value for value in frozen.values()):
        raise SessionPlanError("glossary and style guide revisions are required")

    claimed = {item["unit_id"] for item in status.get("claims", ())}
    work: list[dict[str, Any]] = []
    for unit in sorted(status.get("units", ()), key=lambda entry: entry["chapter_number"]):
        if unit["lifecycle"] == "reviewed" or unit["unit_id"] in claimed:
            continue
        state = unit["lifecycle"]
        review = unit["review"]
        if state == "extracted":
            operation = "translate"
        elif state == "translated" and review == "corrections_required":
            operation = "correct_translation"
        elif state == "translated" and review in {"missing", "stale"}:
            operation = "review"
        elif state == "translated" and review == "pass":
            # This promotion does not need a dedicated worker session.
            continue
        else:
            raise SessionPlanError(
                f"unit {unit['unit_id']} is not dispatchable: {state}/{review}"
            )
        weight = source_sizes.get(unit["unit_id"])
        if type(weight) is not int or weight < 0:
            raise SessionPlanError(f"source byte count unavailable for {unit['unit_id']}")
        work.append({
            "unit_id": unit["unit_id"],
            "chapter_number": unit["chapter_number"],
            "operation": operation,
            "role": "reviewer" if operation == "review" else "translator",
            "source_bytes": weight,
        })

    if not work:
        return {"schema_version": 1, "book_slug": status.get("book_slug"),
                "sessions": [], "remaining_units": 0, "base_commit": base_commit}
    count = min(sessions, len(work))
    # Greedy contiguous partitioning; adapt target to the remaining total.
    groups: list[list[dict[str, Any]]] = []
    index = 0
    remaining_weight = sum(max(1, item["source_bytes"]) for item in work)
    for group_number in range(count):
        remaining_groups = count - group_number
        target = remaining_weight / remaining_groups
        group: list[dict[str, Any]] = []
        group_weight = 0
        while index < len(work):
            remaining_units = len(work) - index
            if group and remaining_units <= remaining_groups - 1:
                break
            item = work[index]
            unit_weight = max(1, item["source_bytes"])
            if group and group_weight >= target and remaining_units > remaining_groups - 1:
                break
            group.append(item)
            group_weight += unit_weight
            index += 1
        groups.append(group)
        remaining_weight -= group_weight

    assignments = []
    for number, group in enumerate(groups, start=1):
        units = [item["unit_id"] for item in group]
        identity = json.dumps({
            "book": status.get("book_slug"), "base_commit": base_commit,
            "units": units, "session": number,
        }, sort_keys=True).encode("utf-8")
        session_id = "bt-" + hashlib.sha256(identity).hexdigest()[:16]
        assignments.append({
            "session_id": session_id,
            "chapter_numbers": [item["chapter_number"] for item in group],
            "units": group,
            "source_bytes": sum(item["source_bytes"] for item in group),
            "context": {
                "workflow_revision": status.get("workflow_revision"),
                "base_commit": base_commit,
                "shared_state_revisions": dict(frozen),
            },
            "instructions": (
                "Use the pinned Book Translator workflow and the shared book repository. "
                f"Session {session_id} owns only the listed units for this plan. "
                "Read current repository state before each unit; acquire the matching "
                "role claim before working, accept and release it after completion. "
                "Claims, not this plan, authorize changes. Never overwrite newer state; "
                "propose glossary/style updates instead of mutating shared files."
            ),
        })
    return {
        "schema_version": 1,
        "book_slug": status.get("book_slug"),
        "base_commit": base_commit,
        "remaining_units": len(work),
        "sessions": assignments,
    }

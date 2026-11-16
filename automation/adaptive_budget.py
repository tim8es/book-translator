"""Opt-in, evidence-aware *bounded* correction model-call planning.

The plan never increases the provider invocation limit, waives unresolved
findings, relaxes literary review, or assigns a chapter PASS. It only varies
how many anchored findings one call may correct and how many calls a single
Actions checkpoint starts. No source or translation excerpts are persisted.
"""
from __future__ import annotations

import math
import os

MAX_GROUP = 3
MAX_CALLS_PER_CHECKPOINT = 2
SEMANTIC_CATEGORIES = {
    "omission", "addition", "meaning", "modality",
    "referent", "structure", "voice",
}


def enabled(config: dict | None = None) -> bool:
    env = os.environ if config is None else config
    mode = env.get("BOOK_TRANSLATOR_ADAPTIVE_BUDGET", "off")
    if mode not in {"on", "off"}:
        raise ValueError("BOOK_TRANSLATOR_ADAPTIVE_BUDGET must be on or off")
    return mode == "on"


def _risk(item: dict, retries: set[str], failure_count: int) -> int:
    if item["id"] in retries or failure_count:
        return 2
    if item.get("severity") == "critical":
        return 2
    if (item.get("category") in {"omission", "structure", "referent"}
            and item.get("severity") in {"major", "critical"}):
        return 2
    if (item.get("category") in SEMANTIC_CATEGORIES
            or item.get("severity") == "major"):
        return 1
    return 0


def plan(findings: list[dict], *, rejected: list[dict] | None = None,
         unit_id: str, record_id: str, failures: int = 0) -> dict:
    """Ordered selection: isolate difficult edits, batch simple grounded ones.

    Conservative editing limits reduce blast radius of malformed model output.
    This is a *pacing* optimization; it does not promise fewer total calls.
    """
    if (not isinstance(findings, list)
            or not isinstance(unit_id, str) or not unit_id
            or not isinstance(record_id, str) or not record_id
            or type(failures) is not int or not 0 <= failures <= 100_000
            or not findings or len(findings) > 100
            or any(not isinstance(item, dict) or not isinstance(item.get("id"), str)
                   or not item["id"] for item in findings)
            or len({item["id"] for item in findings}) != len(findings)):
        raise ValueError("invalid adaptive correction worklist")
    audit = rejected if rejected is not None else []
    if (not isinstance(audit, list) or len(audit) > 512
            or any(not isinstance(item, dict)
                   or not isinstance(item.get("unit_id"), str)
                   or not isinstance(item.get("review_record_id"), str)
                   or not isinstance(item.get("finding_id"), str)
                   for item in audit)):
        raise ValueError("invalid adaptive rejection audit")
    retries = {item["finding_id"] for item in audit
               if item["unit_id"] == unit_id and item["review_record_id"] == record_id}
    ranks = [_risk(item, retries, failures) for item in findings]
    highest = max(ranks)
    first = ranks[0]
    if first == 2:
        group_size = 1
    elif first == 1:
        group_size = 2 if len(ranks) > 1 and ranks[1] < 2 else 1
    else:
        group_size = 1
        while group_size < min(MAX_GROUP, len(ranks)) and ranks[group_size] == 0:
            group_size += 1
    calls_per_checkpoint = 1 if highest == 2 or failures else 2
    return {
        "schema_version": 1,
        "group_size": group_size,
        "max_calls_per_checkpoint": min(calls_per_checkpoint, MAX_CALLS_PER_CHECKPOINT),
        "pending_count": len(findings),
        "risk_class": ("high" if highest == 2 else
                       "mixed" if highest == 1 else "routine"),
        "lower_bound_remaining_calls": math.ceil(len(findings) / MAX_GROUP),
        "current_selection_size": group_size,
    }

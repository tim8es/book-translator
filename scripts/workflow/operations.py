"""Shared, read-only selection of the next chapter operation.

Admission, claim ownership, and state mutation remain the callers' responsibility.
"""

from __future__ import annotations


def next_unit_operation(lifecycle: str, review: str) -> str | None:
    """Classify a chapter without granting permission to perform the operation."""
    if lifecycle == "extracted":
        return "translate"
    if lifecycle == "translated":
        if review == "pass":
            return "accept_review"
        if review == "corrections_required":
            return "correct_translation"
        if review in {"missing", "stale"}:
            return "review"
    return None

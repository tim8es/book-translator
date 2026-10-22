"""Read-only, conservative diff plan for independent correction re-review.

Only a byte-identical baseline from a prior ledger record can authorize a
scoped review *proposal*. This never skips correction lineage or emits PASS.
"""
from __future__ import annotations

import difflib
import hashlib


def plan(before: bytes, after: bytes, *, previous_sha256: str, current_sha256: str) -> dict:
    def digest(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    if digest(before) != previous_sha256 or digest(after) != current_sha256:
        raise ValueError("diff inputs do not match pinned historical/current translation SHA")
    old = before.decode("utf-8").splitlines()
    new = after.decode("utf-8").splitlines()
    changes = []
    changed = 0
    for tag, a, b, c, d in difflib.SequenceMatcher(
        None, old, new, autojunk=False
    ).get_opcodes():
        if tag == "equal":
            continue
        changed += max(b - a, d - c)
        changes.append({
            "kind": tag, "old_lines": [a + 1, b], "new_lines": [c + 1, d],
            "before_excerpt": "\n".join(old[max(0, a - 2):min(len(old), b + 2)])[:1800],
            "after_excerpt": "\n".join(new[max(0, c - 2):min(len(new), d + 2)])[:1800],
        })
    total = max(len(old), len(new), 1)
    fraction = changed / total
    # Fail closed: a large rewrite or many edits means no safe bounded scope.
    eligible = bool(changes) and fraction <= 0.2 and len(changes) <= 8
    return {
        "schema_version": 1, "advisory_only": True,
        "previous_translation_sha256": previous_sha256,
        "current_translation_sha256": current_sha256,
        "changed_line_fraction": round(fraction, 4),
        "changed_regions": changes[:16],
        "scoped_review_candidate": eligible,
        "reason": ("small_localized_change" if eligible else
                   "no_change" if not changes else "broad_or_fragmented_change"),
        "requirements": [
            "independent reviewer must verify changed source-aligned passages and surrounding context",
            "recheck every unresolved anchored prior finding using v2 correction lineage",
            "check full-chapter continuity and coverage; escalate to complete review if confidence is insufficient",
            "record a new hash-bound PASS only after actual literary comparison",
        ],
    }

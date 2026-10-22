"""Advisory, SHA-bound paragraph diff for independent Reviewer follow-up."""
from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher

MAX_SNIPPETS = 12
MAX_BYTES = 1024 * 1024


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def baseline_path(translation_sha256: str) -> str:
    if len(translation_sha256) != 64 or any(c not in "0123456789abcdef" for c in translation_sha256):
        raise ValueError("invalid translation sha256")
    return f".workflow/review-baselines/{translation_sha256}.md"


def compare(previous: bytes, current: bytes) -> dict:
    """Describe changed paragraph windows; never assert semantic correctness."""
    if len(previous) > MAX_BYTES or len(current) > MAX_BYTES:
        return {"mode": "full", "reason": "text_too_large"}
    before = re.split(r"\n\s*\n", previous.decode("utf-8"))
    after = re.split(r"\n\s*\n", current.decode("utf-8"))
    operations = [op for op in SequenceMatcher(
        a=before, b=after, autojunk=False,
    ).get_opcodes() if op[0] != "equal"]
    touched = sum(sum(len(p) for p in before[i:j]) for _, i, j, _, _ in operations)
    inserted = sum(sum(len(p) for p in after[i:j]) for _, _, _, i, j in operations)
    fraction = max(touched / max(len(previous), 1), inserted / max(len(current), 1))
    focused = 0 < len(operations) <= MAX_SNIPPETS and fraction <= 0.25
    sections = [{
        "old_paragraph_range": [a + 1, b],
        "new_paragraph_range": [c + 1, d],
        "previous_excerpt": " ".join(before[a:b])[:250],
        "current_excerpt": " ".join(after[c:d])[:250],
        "preceding_context": (after[c-1][:160] if c else ""),
        "following_context": (after[d][:160] if d < len(after) else ""),
    } for _, a, b, c, d in operations[:MAX_SNIPPETS]]
    return {
        "mode": "focused_candidate" if focused else "full",
        "reason": "bounded_changes" if focused else "large_or_missing_changes",
        "changed_fraction": round(fraction, 4),
        "change_groups": len(operations),
        "sections": sections,
    }

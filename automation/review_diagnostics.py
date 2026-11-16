"""Pure, non-authoritative diagnostics for automatic literary review.

No literary correctness decision is made here. Warnings are grounded in
paired text, bounded in number, and always require independent human/model
source comparison before a finding or PASS can be recorded.
"""
from __future__ import annotations

import hashlib
import re

_BLOCK = re.compile(r"<!--\s*bt-block:(\d+)\s*-->")
_GLOSSARY_LINE = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|")
_FINDING = re.compile(r"findings\[(\d+)\]\.(source_anchor|translation_anchor)")
MAX_ADVISORIES = 12


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _blocks(text: str) -> list[tuple[str, str]]:
    matches = list(_BLOCK.finditer(text))
    return [(match.group(1), text[match.end():matches[i + 1].start()]
             if i + 1 < len(matches) else text[match.end():])
            for i, match in enumerate(matches)]


def _terms(glossary: str) -> list[tuple[str, str]]:
    result = []
    seen = set()
    for line in glossary.splitlines():
        match = _GLOSSARY_LINE.match(line)
        if not match:
            continue
        term, rendering = match.group(1).strip(), match.group(2).strip()
        key = term.casefold()
        if (key in {"original", "term", "source", "оригинал"}
                or not term or not rendering or term.startswith("---")
                or key in seen):
            continue
        seen.add(key)
        result.append((term, rendering))
    return result


def review_advisories(source: str, translation: str, glossary: str,
                      *, limit: int = MAX_ADVISORIES) -> dict:
    """Report source/target block term mismatches; never repair or judge them."""
    if not 0 <= limit <= MAX_ADVISORIES:
        raise ValueError("invalid bounded review advisory limit")
    source_blocks, translated_blocks = _blocks(source), _blocks(translation)
    warnings = []
    if source_blocks or translated_blocks:
        source_ids = [x[0] for x in source_blocks]
        target_ids = [x[0] for x in translated_blocks]
        if source_ids != target_ids:
            warnings.append({"kind": "block_alignment",
                             "source_blocks": len(source_ids),
                             "translation_blocks": len(target_ids)})
        else:
            for (block_id, original), (_, target) in zip(source_blocks, translated_blocks):
                if len(warnings) >= limit:
                    break
                for term, rendering in _terms(glossary):
                    if len(warnings) >= limit:
                        break
                    # The target may be inflected, abbreviated or intentionally
                    # adapted. Literal mismatch is NOT a literary error.
                    if (len(term) >= 3 and len(rendering) >= 3
                            and re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)",
                                          original, re.IGNORECASE)
                            and rendering.casefold() not in target.casefold()):
                        warnings.append({
                            "kind": "glossary_rendering_candidate",
                            "block_id": block_id,
                            "original": term,
                            "glossary_rendering": rendering,
                        })
    return {"schema_version": 1, "advisory_only": True,
            "source_sha256": _sha(source),
            "translation_sha256": _sha(translation),
            "warnings": warnings[:limit],
            "warning_count": len(warnings[:limit])}


def reviewer_failure_detail(error: Exception) -> dict:
    """Classify safe protocol diagnostics without copying literary text to logs.

    This is *not* a decision that an exception can safely be retried or
    deferred. Existing model-vs-provider classification remains authoritative.
    """
    message = str(error)
    detail = {"kind": "other_review_evidence"}
    if "reviewer must return JSON" in message:
        detail["kind"] = "invalid_json"
    elif "literary memory schema" in message or "unsupported reviewer evidence version" in message:
        detail["kind"] = "invalid_schema"
    elif "every literary check must have been performed" in message:
        detail["kind"] = "incomplete_checks"
    elif "prior unresolved findings lack explicit rechecks" in message:
        detail["kind"] = "missing_prior_rechecks"
    elif "cannot mark a prior correction resolved" in message:
        detail["kind"] = "invalid_correction_lineage"
    elif "quoted passage occurrence" in message and "absent" in message:
        detail["kind"] = "anchor_absent"
    elif "quote is repeated" in message:
        detail["kind"] = "anchor_ambiguous"
    elif "chunk did not independently recheck prior findings" in message:
        detail["kind"] = "missing_chunk_rechecks"
    elif "reviewer evidence is invalid" in message:
        detail["kind"] = "other_review_evidence"
    if detail["kind"] in {"anchor_absent", "anchor_ambiguous"}:
        match = _FINDING.search(message)
        if match:
            detail["finding_index"] = int(match.group(1))
            detail["field"] = match.group(2)
    return detail


def should_chunk_review(source: str, translation: str, failures: int,
                        *, minimum_chars: int = 4000) -> bool:
    """Switch only after repeated role-scoped protocol failures, if safe."""
    if type(failures) is not int or failures < 2:
        return False
    if min(len(source), len(translation)) < minimum_chars:
        return False
    # No claims, prompts or existing artifacts are changed by this probe.
    import review_chunks
    try:
        review_chunks.plan(source, translation)
    except ValueError:
        return False
    return True

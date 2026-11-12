"""Deterministic, advisory checks on one accepted source/translation pair.

The report is never PASS evidence. Linguistic judgments remain the independent
Reviewer's responsibility; uncertain patterns are always reported as hints.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter

from .glossary_guard import glossary_mismatch_candidates

NUMBERS = re.compile(r"(?<!\w)\d+(?:[.,]\d+)*(?!\w)")
HEADINGS = re.compile(r"(?m)^#{1,6}\s+\S")
BREAKS = re.compile(r"(?m)^\s*(?:\* ?\* ?\*|- ?- ?-|_{3,})\s*$")


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _glossary_pairs(markdown: str) -> list[tuple[str, str]]:
    pairs = []
    for row in markdown.splitlines():
        if not row.startswith("|"):
            continue
        cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
        if len(cells) < 2 or not cells[0] or not cells[1]:
            continue
        if cells[0].lower() in {"original", "source", "term", "оригинал"}:
            continue
        if re.fullmatch(r"[-:\s]+", cells[0]):
            continue
        pairs.append((cells[0], cells[1]))
    return pairs


def inspect(source: bytes, translation: bytes, glossary: str = "") -> dict:
    """Return bounded source-linked warnings with exact artifact SHA references."""
    original = source.decode("utf-8")
    target = translation.decode("utf-8")
    warnings = []

    def flag(kind: str, detail: str) -> None:
        if len(warnings) < 80:
            warnings.append({"kind": kind, "detail": detail})

    src_numbers = Counter(NUMBERS.findall(original))
    dst_numbers = Counter(NUMBERS.findall(target))
    for value, count in sorted((src_numbers - dst_numbers).items()):
        flag("number_missing", f"{value}: {count} occurrence(s) in source not matched in translation")
    for value, count in sorted((dst_numbers - src_numbers).items()):
        flag("number_added", f"{value}: {count} unmatched occurrence(s) in translation")

    for kind, pattern in (("heading", HEADINGS), ("scene_break", BREAKS)):
        src_count = len(pattern.findall(original))
        dst_count = len(pattern.findall(target))
        if src_count != dst_count:
            flag("structure", f"{kind}: source={src_count}, translation={dst_count}")

    # Glossary comparisons are language-neutral advisory signals; neither exact
    # matching nor the glossary itself proves that the translation is incorrect.
    for entry in glossary_mismatch_candidates(original, target, glossary):
        source_term, target_term = entry["original"], entry["translation"]
        if entry["kind"] == "glossary_decision_review":
            flag("glossary_decision_review",
                 f"{source_term} -> {target_term}: source spelling is absent "
                 "from the translation; inspect whether the glossary decision "
                 "or a target-language adaptation is correct. Do not blindly "
                 "replace a translated form with the glossary literal")
        else:
            flag("glossary_candidate",
                 f"{source_term} -> {target_term}: exact rendering absent; "
                 "may be an inflected form or a glossary mismatch (advisory only)")

    return {
        "schema_version": 1,
        "source_sha256": _sha(source),
        "translation_sha256": _sha(translation),
        "advisory_only": True,
        "warnings": warnings,
        "warning_count": len(warnings),
    }

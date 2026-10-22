"""Deterministic, advisory checks on one accepted source/translation pair.

The report is never PASS evidence. Linguistic judgments remain the independent
Reviewer's responsibility; uncertain patterns are always reported as hints.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter

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

    for source_term, target_term in _glossary_pairs(glossary):
        # Case-folded substring matching is a diagnostic, not a language-aware
        # validator; Russian inflection and synonyms can create false positives.
        if len(source_term) < 3 or len(target_term) < 3:
            continue
        if source_term.casefold() in original.casefold() and target_term.casefold() not in target.casefold():
            flag("glossary_candidate", f"{source_term} -> {target_term}: expected rendering not found exactly")

    return {
        "schema_version": 1,
        "source_sha256": _sha(source),
        "translation_sha256": _sha(translation),
        "advisory_only": True,
        "warnings": warnings,
        "warning_count": len(warnings),
    }

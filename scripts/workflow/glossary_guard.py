"""Language-independent glossary parsing and conservative consistency signals.

These checks never transliterate or decide literary correctness.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict

_ROW_SPLIT = re.compile(r"(?<!\\)\|")
_HEADER = {"original", "source", "term", "оригинал"}


def glossary_entries(markdown: str) -> list[dict[str, str]]:
    """Parse standard four-column Markdown glossary into typed model context."""
    entries: list[dict[str, str]] = []
    for line in markdown.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip().replace(r"\|", "|") for cell in _ROW_SPLIT.split(line.strip().strip("|"))]
        if len(cells) < 2 or not all(cells[:2]):
            continue
        original, translation = cells[:2]
        kind = cells[2] if len(cells) > 2 and cells[2] else "term"
        notes = cells[3] if len(cells) > 3 else ""
        if original.casefold() in _HEADER or re.fullmatch(r"[-:\s]+", original):
            continue
        entries.append({"original": original, "translation": translation,
                        "type": kind, "notes": notes})
    return entries


def conflicting_glossary_entries(markdown: str) -> list[str]:
    """Only structurally contradictory entries are forbidden; any script is valid."""
    groups: dict[tuple[str, str], set[str]] = defaultdict(set)
    for entry in glossary_entries(markdown):
        groups[(entry["original"].casefold(), entry["type"].casefold())].add(
            entry["translation"].casefold()
        )
    return sorted(set(original for (original, _kind), values in groups.items()
                      if len(values) > 1))


def glossary_context(markdown: str, source_language: str, target_language: str) -> str:
    """JSON context with explicit language pair, types, translations and notes."""
    return json.dumps({
        "source_language": source_language,
        "target_language": target_language,
        "entries": glossary_entries(markdown),
        "guidance": (
            "Glossary values are lexical decisions, not immutable surface strings. "
            "Apply target-language morphology, grammatical agreement and context. "
            "Intentionally unchanged proper names are permitted in any language. "
            "If a decision contradicts the source or natural target-language usage, "
            "propose a glossary correction with evidence instead of forcing a literal "
            "replacement in correct translated prose."
        ),
    }, ensure_ascii=False, separators=(",", ":"))


def glossary_mismatch_candidates(source: str, translation: str,
                                 markdown: str) -> list[dict[str, str]]:
    """Nonliteral mismatches for reviewer attention only (no automatic edits)."""
    result = []
    src, dst = source.casefold(), translation.casefold()
    for entry in glossary_entries(markdown):
        original, rendering = entry["original"], entry["translation"]
        if len(original) < 3 or len(rendering) < 3:
            continue
        if original.casefold() not in src or rendering.casefold() in dst:
            continue
        kind = ("glossary_decision_review" if original.casefold() == rendering.casefold()
                else "glossary_candidate")
        result.append({**entry, "kind": kind})
    return result

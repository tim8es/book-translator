"""Conservative checks for accidental untransliterated character names.

This is a language-specific integrity guard, NOT a machine transliterator or
a substitute for source-grounded literary review.
"""
from __future__ import annotations

import re

_LATIN = re.compile(r"[A-Za-z]")
_CYRILLIC = re.compile(r"[А-Яа-яЁё]")
_EXCEPTION = "[latin-script-exception]"


def suspicious_untranslated_character_names(markdown: str, target_language: str) -> list[str]:
    """Return names copied in Latin script in a Russian character glossary.

    An explicitly reviewed Latin-script exception can be recorded in notes.
    Other types (brands, links, book titles) are intentionally unaffected.
    """
    if not isinstance(target_language, str) or target_language.lower().split("-")[0] != "ru":
        return []
    names: list[str] = []
    for line in markdown.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        original, translation, kind, notes = cells[:4]
        if kind.casefold() not in {"character", "персонаж"}:
            continue
        if _EXCEPTION in notes.casefold():
            continue
        if (_LATIN.search(original) and not _CYRILLIC.search(original)
                and original.casefold() == translation.casefold()):
            names.append(original)
    return list(dict.fromkeys(names))

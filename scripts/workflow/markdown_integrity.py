"""Reject unsupported raw HTML accidentally emitted in chapter Markdown.

The EPUB renderer deliberately escapes prose instead of interpreting HTML.
Consequently a model-produced <br> appears as visible text to readers. Catch
this at the canonical translation boundary, not by silently fixing output.
"""
from __future__ import annotations

import re


class TranslationMarkupError(ValueError):
    """Unrenderable markup in a translated chapter."""


_TAG = re.compile(r"<\s*/?\s*([A-Za-z][A-Za-z0-9:-]*)(?:\s+[^<>]*?)?\s*/?>")
_INLINE_CODE = re.compile(r"(?<!\\)(\x60+)[^\x60\n]*\1")
# Require recognized HTML names to avoid mistaking mathematical <x> for markup.
_HTML_NAMES = frozenset(
    """
    a abbr article aside b big blockquote body br button caption center cite code
    col colgroup dd del details div dl dt em figcaption figure font footer form
    h1 h2 h3 h4 h5 h6 head header hr html i iframe img input ins kbd label li
    link main mark meta nav ol option p picture pre q rp rt ruby s samp script
    section select small source span strong style sub summary sup table tbody td
    textarea tfoot th thead time title tr u ul video wbr
    """.split()
)


def reject_raw_html(markdown: str) -> None:
    """Fail closed on HTML tags in prose; preserve code and workflow comments.

    Native Markdown paragraphs, blank lines, emphasis, and workflow control
    markers are still accepted. HTML is not a supported formatting input
    to the plain-Markdown EPUB renderer.
    """
    if not isinstance(markdown, str):
        raise TranslationMarkupError("translated chapter Markdown must be text")
    fenced = False
    for line_no, line in enumerate(markdown.splitlines(), 1):
        if line.strip().startswith(chr(96) * 3):
            fenced = not fenced
            continue
        if fenced:
            continue
        prose = _INLINE_CODE.sub("", line)
        for tag in _TAG.finditer(prose):
            name = tag.group(1).lower()
            if name in _HTML_NAMES:
                raise TranslationMarkupError(
                    f"unsupported raw HTML <{name}> in chapter prose at line {line_no}; "
                    "use Markdown paragraphs/formatting, not HTML tags"
                )

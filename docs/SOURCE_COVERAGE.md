# EPUB source coverage

The original EPUB is independently inventoried before a book can be admitted to
translation. The audit reads XHTML body nodes with XML ElementTree and compares
the exact ordered sequence of normalized word and punctuation tokens with the
HTMLParser-produced Markdown chapters. It also checks all non-navigation XHTML
listed in the EPUB manifest, including documents omitted from the spine.
Undeclared XHTML members in the ZIP also block certification.

The supported subset comprises text blocks, headings, lists without numbering,
and inline bold/emphasis/code. Links, footnotes, tables, images, pagebreaks,
complex semantic elements, malformed XHTML, off-spine prose and ambiguous
literal Markdown formatting characters are **blocked**, not silently skipped.
The audit does not claim pixel-level layout fidelity or universal EPUB support.

## New books

Run normal extraction:

    python scripts/book.py extract novel.epub --slug novel --target-language ru

A successful extraction records a deterministic, text-free
books/novel/source-coverage.json report containing the original SHA-256,
per-document hashes, token/block counts and per-chapter artifact hashes.
For --private-source, the original EPUB can then be removed from the working
repository; the audit report remains without embedding book prose.

All normal status/resume/translation-admission and build preflights reject EPUB
books whose report is missing, incompatible with metadata, or no longer matches
extracted source bytes. Markdown and TXT books retain the existing structural
and SHA-sealed corpus checks.

## Older EPUB workspaces

Never infer that an older extracted corpus is complete from matching hashes.
To test an existing book without modifying it:

    python scripts/book.py source-coverage novel --source novel.epub --json

To create missing evidence **only after a successful independent audit**:

    python scripts/book.py source-coverage novel --source novel.epub --record

The command checks that the original EPUB matches the preexisting source SHA-256
and byte length, and that every extracted chapter is exactly reproducible.
It does not reset or edit translation state, current chapters, review evidence,
glossary or style guide. Existing conflicting evidence for the same source is never overwritten.
When a new source edition was promoted, re-certify the new original with
`--record --replace-stale`: the old report is archived under
`source-coverage-history/<source-sha256>.json` before a conditional replacement
of the current report. Neither progress nor translations are modified.

A new original requires renewed coverage certification. If its provenance,
extracted text or current edition cannot be independently verified, the command
fails without changing the current report. JSON mode produces a machine-readable
BLOCKED reason with a nonzero process exit status.

## Boundaries

Token coverage is proof of text preservation for the explicitly supported
XHTML subset, not proof of typography, reading order defined by CSS, visual
layout, illustration semantics, or rendering correctness. A real-world EPUB
may be rejected until its structure is supported rather than be given an
unjustified 100% score.

A navigation XHTML is excluded only when its body has no prose outside the
navigation elements. Duplicate ZIP entry names also block certification.
The `corpus.py verify` command checks EPUB coverage alongside its existing
cryptographic source-corpus integrity checks.

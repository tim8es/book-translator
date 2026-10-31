# EPUB source coverage

The original EPUB is independently inventoried before a book can be admitted to
translation. The audit reads XHTML body nodes with XML ElementTree and compares
the exact ordered sequence of normalized word and punctuation tokens with the
HTMLParser-produced Markdown chapters. It also checks all non-navigation XHTML
listed in the EPUB manifest, including documents omitted from the spine.
Undeclared XHTML members in the ZIP also block certification.

The supported subset comprises text blocks, headings, lists without numbering,
inline bold/emphasis/code, unambiguous external links, and same-document XHTML
fragment links whose unique target ID is present in the original document.
These links are normalized to `#fragment` in extracted Markdown, and the target
location is marked by a `<!-- bt-anchor:fragment -->` line.
External HTTP(S)/mailto links with literal parentheses remain supported: the
extractor percent-encodes `(` and `)` as `%28` and `%29` only in the derived
Markdown destination. The original EPUB is never rewritten. Unsupported URL
schemes, whitespace, brackets and cross-document relative links remain blocked. Translators must
preserve the link destination and the target marker exactly. Literal Markdown
punctuation in XHTML text is escaped and independently audited against the
original, so source prose such as `d*bcon` is not truncated.

For EPUBs with an in-spine `<nav epub:type="toc">`, its navigation links are
not sent to the translator as prose. The audit requires one nonempty link per
list entry, rejects duplicate targets and missing spine documents/fragment IDs,
and verifies prose *outside* the navigation remains in extracted units. EPUB
output regenerates an ordered navigation document from accepted translated
units. Relative links between different XHTML documents remain blocked until
source-unit remapping is implemented; same-document fragments are supported.

A cover consisting of a single SVG `<image>` wrapper pointing to a manifest-listed
JPEG/PNG/GIF asset is independently checked. Its raster bytes are preserved
under the book workspace, referenced by `metadata.json.cover_path`, and hash-bound
to `source-coverage.json`. SVG graphics, scripts, multiple images, embedded SVG
text and other complex visual material still block certification.

Decorative `aria-hidden="true"` spans are included in text coverage (they
remain visible prose), not skipped. Footnotes, tables, unrelated images,
pagebreaks, complex semantic elements, malformed XHTML, off-spine prose
and non-TOC cross-document relative links remain **blocked**, not silently skipped.
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

## Image-rich EPUB / source layout preservation

EPUBs containing XHTML image elements use the bounded `original_xhtml_v1`
path. The original archive is retained for deterministic source-template
assembly; readable prose blocks are independently checked against exact XHTML
text and have immutable `bt-block` markers. Nonprose images and a verified
legacy TOC are inventoried and retained rather than silently dropped.
The `epub-preserved-xhtml-v1` validator is bound to source SHA-256 and to
extracted paragraph hashes, and the corpus preflight enforces this specific
mode. See [EPUB layout preservation](EPUB_LAYOUT_PRESERVATION.md) for supported
semantics and exclusions. This mode does not translate text drawn in images.

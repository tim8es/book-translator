# Source-layout EPUB extraction (image-rich XHTML)

This document specifies the opt-in-by-source `original_xhtml_v1` mode introduced
for EPUB archives with XHTML `<img>` elements in `.xhtml` or `.html` members (including self-closing image tags). SVG-only covers continue through the existing EPUB coverage path. It is a bounded,
fail-closed compatibility path, not a general HTML-to-EPUB converter.

## Purpose and provenance

Do **not** flatten an image-rich source into freely rebuilt Markdown XHTML.
The original EPUB ZIP package remains immutable and SHA-256 bound. The extracted
units are ordered prose-bearing **source XHTML documents**. Image-only spine
items and a verified simple legacy XHTML TOC are retained as package resources
but are not sent to a literary translator as text-only units. There can therefore
be fewer translation units than spine entries. Every referenced raster asset is
verified against the EPUB manifest, recorded with SHA-256 in
`source-coverage.json`, and preserved byte-for-byte. Manifest-declared SVG image
assets are also supported when an independent XML inspection proves they consist
only of static, text-free vector shapes and local gradient references. SVG
scripts, embedded text, foreign content, remote resources and external references
remain blocking. Approved SVG bytes are hash-bound and copied unchanged.

Each translated prose block is anchored to its original paragraph or heading:

    <!-- bt-block:000001 -->
    Translated paragraph with optional *italic* emphasis.

    <!-- bt-block:000002 -->
    Next translated paragraph.

The 6-digit markers are **mandatory**. A Translator must preserve the count,
identity, order, and boundaries of all markers. Translate the text between them;
do not join or split paragraphs, remove markers, invent replacements for graphic
resources, or change markup intended for the layout engine. The acceptance gate
checks marker alignment before persisting a translated unit, and the EPUB build
rechecks it before replacing XHTML text.

The replacement engine copies the original archive members in order, preserving
original artwork, raster assets, stylesheets, source page structure, image
positions, paragraph classes and first-letter drop-cap classes. Existing simple
italic spans become explicit Markdown `*emphasis*` for the translator, and are
reconstructed as XHTML `<em>` after translation. Link targets are preserved
only if each original inline link label survives verbatim in its original order;
otherwise assembly fails closed. Safe `<wbr>` word-break hints inside linked
labels are preserved with the original link subtree, including when a prose
block has multiple independent links. Word-breaks outside links remain blocked. The output OPF and translated XHTML declare the target language.

## Bounds and limitations

- EPUB 3 navigation (`<nav epub:type="toc">`) is supported only when the
  entire document body consists of one verified TOC containing labelled,
  relative links to existing EPUB spine resources (including valid fragments).
  The TOC may be in the reading-order spine even without an OPF `nav` property,
  or an off-spine manifest item with the `nav` property. Both are retained
  byte-for-byte, not treated as literary chapters. Navigation labels remain
  original-language; translating/regenerating them requires a separate,
  independently validated navigation-localization workflow. Unmapped text,
  malformed links, executable elements, hidden markup and non-TOC navigation
  fail closed instead of being silently discarded.
- A Vellum-generated table of contents inside a uniquely identified `<div role="doc-toc" epub:type="toc">` is preserved as original XHTML only after auditing all internal spine links and fragment targets. A sole external publisher-attribution footer may remain unchanged when it contains no other prose. Unmapped text, missing targets, duplicate links, unsafe HTML, or extra footer content block extraction. The navigation document is not silently translated as a chapter.
- Standalone prose-only `<div>` elements are translated as their own
  source-bound text blocks while preserving the original `div` tag, class and
  ID. Only plain text and unstyled inline `<span>`, `<em>` or `<i>` markup are
  admitted. A `div` containing child block elements, images, hard line breaks,
  interactive links or styled/identified inline elements is not flattened or
  presumed safe; its unmapped prose remains blocking.
- Standard XHTML `<section>` wrappers are preserved at their original
  nesting level, with `id`, `class`, and namespace-qualified attributes intact.
  Only their supported child paragraph/heading blocks are translated. Text
  directly in a section or in unmapped tails remains a hard error, as do hidden
  content, executable elements and unsupported nested structures. Independent
  source coverage continues to verify the original text and reading order.
- The source-preserving mode currently supports a simple, unambiguous structure
  with paragraph/heading blocks, inline text, raster art, a single-image SVG
  wrapper, and a simple legacy hyperlink TOC. Complex EPUB 3 navigation,
  footnotes, tables, scripting, hidden prose, relative links in body prose,
  and artwork nested inside prose elements remain blocked.
- Publisher-credit and social-profile paragraphs containing exactly one external linked icon and its label are treated as protected non-literary XHTML. The original link, icon, and label are retained untranslated; EPUB validation compares the entire source and output subtrees. Independent prose outside the linked icon is not exempt and still blocks extraction if mixed with artwork.
- Artwork can contain **English text baked into pixels** (e.g., a chapter-name
  illustration). The pixels are not translated by this mode, and titles in
  image-only source documents remain original-language. A separate image/text
  localization pipeline is required for fully localized visual typography.
- Body paragraph styles and raster placement are preserved, but arbitrary
  complex inline CSS cannot be reconstructed automatically for freely translated
  sentences. Only the explicitly supported emphasis and drop-cap cases are
  currently reproduced. This does **not** certify pixel-level similarity.
- Source-preserving final EPUB assembly requires the exact original EPUB bytes
  still available in `source/`, even if a source identity is otherwise sealed.
- Existing books retain their pinned runtime and extraction mode; upgrading a
  book requires the explicit workflow revision transition, not a silent switch.

## Acceptance tests

A supported image-rich EPUB must pass:

    python scripts/book.py extract ORIGINAL.epub --slug new-book --target-language ru
    python scripts/book.py validate new-book
    python scripts/corpus.py seal new-book
    python scripts/corpus.py verify new-book

The extracted content and independent coverage report are bound to the original
SHA-256. After translations and their review pass the normal gates, `book.py build <slug> --format epub`
uses the original package as a layout template. Regression tests create a
copyright-free synthetic EPUB with image-only source documents, a legacy TOC,
embedded raster illustrations, CSS, italic spans, and a drop-cap paragraph;
negative cases test broken images, unsafe content, missing markers and source
identity mismatches.

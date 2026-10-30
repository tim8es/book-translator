# Source-layout EPUB extraction (image-rich XHTML)

This document specifies the opt-in-by-source `original_xhtml_v1` mode introduced
for EPUB archives with XHTML `<img>` elements. SVG-only covers continue through the existing EPUB coverage path. It is a bounded,
fail-closed compatibility path, not a general HTML-to-EPUB converter.

## Purpose and provenance

Do **not** flatten an image-rich source into freely rebuilt Markdown XHTML.
The original EPUB ZIP package remains immutable and SHA-256 bound. The extracted
units are ordered prose-bearing **source XHTML documents**. Image-only spine
items and a verified simple legacy XHTML TOC are retained as package resources
but are not sent to a literary translator as text-only units. There can therefore
be fewer translation units than spine entries. Every referenced raster asset is
verified against the EPUB manifest, recorded with SHA-256 in
`source-coverage.json`, and preserved byte-for-byte.

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
only if the original inline link label survives verbatim; otherwise assembly
fails closed. The output OPF and translated XHTML declare the target language.

## Bounds and limitations

- The source-preserving mode currently supports a simple, unambiguous structure
  with paragraph/heading blocks, inline text, raster art, a single-image SVG
  wrapper, and a simple legacy hyperlink TOC. Complex EPUB 3 navigation,
  footnotes, tables, scripting, hidden prose, relative links in body prose,
  and artwork nested inside prose elements remain blocked.
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

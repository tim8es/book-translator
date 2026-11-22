# Source-layout EPUB extraction (image-rich XHTML)

This document specifies the opt-in-by-source `original_xhtml_v1` mode introduced
for EPUB archives with XHTML `<img>` elements in `.xhtml` or `.html` members (including self-closing image tags). SVG-only covers continue through the existing EPUB coverage path. It is a bounded,
fail-closed compatibility path, not a general HTML-to-EPUB converter.

## Purpose and provenance

The same source-bound `original_xhtml_v1` path now admits a narrow subset of
HTML disclosure controls (`<details>`) even when the EPUB contains no images.
A disclosure must have exactly one nonempty `<summary>` as its first child,
with independently mapped prose blocks inside. Nested disclosures, executable
attributes, malformed summaries, and unmapped direct prose still fail closed.
The summary label and every contained paragraph are separate translatable
blocks; the original `<details>` tag, `open` state, attributes, and child order
are retained in EPUB output. Do not flatten hidden text into always-visible
Markdown, or treat a successful ZIP extraction as source-coverage evidence.


Do **not** flatten an image-rich source into freely rebuilt Markdown XHTML.
The original EPUB ZIP package remains immutable and SHA-256 bound. The extracted
units are ordered prose-bearing **source XHTML documents**. Image-only spine
items and a verified simple legacy XHTML TOC are retained as package resources
but are not sent to a literary translator as text-only units. There can therefore
be fewer translation units than spine entries. Every local referenced raster asset is
verified against the EPUB manifest, recorded with SHA-256 in
`source-coverage.json`, and preserved byte-for-byte. A passive XHTML `img` with
a bounded HTTPS URL may instead remain an external reference, without network
access, downloading, or a claim to verify its pixels. Coverage explicitly records
the original URL, its reference-only SHA-256 and unverified availability. Build
preserves the element and its attributes; validation rejects changed references.
The image is not embedded and may be unavailable or request the remote service
when opened in a reader. Unsafe URL schemes/authorities, remote SVG resources
and broken local images remain blocking. Manifest-declared SVG image
assets are also supported when an independent XML inspection proves they consist
only of static, text-free vector shapes and local gradient references. SVG
scripts, embedded text, foreign content, remote resources and external references
remain blocking. Approved SVG bytes are hash-bound and copied unchanged. The independent\nsource-coverage preflight accepts the `image/svg+xml` image identity only in\n`original_xhtml_v1` mode after source-preserving validation; malformed paths,\nmissing hashes and other media types remain blocked.

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
reconstructed as XHTML `<em>` after translation. Inline whitespace at emphasis
and span boundaries stays outside Markdown markers to avoid joining source
words. Independent source coverage verifies those word boundaries. Escaped literal
Markdown punctuation inside italic text, including `*word*`, stays literal
through source coverage and XHTML rendering. Inline hyperlinks are represented by protected `bt-inline` start/end
markers, allowing visible labels to be translated while the original URL,
`rel`, and source attributes are copied back unchanged and in order. Missing,
duplicated, or reordered link markers block assembly. Safe `<wbr>`
word-break hints inside linked labels use source-bound media markers to keep
their original positions, including in multi-link paragraphs. Word-breaks
outside links remain blocked. The output OPF and translated XHTML declare the target language.

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
  and semantically ambiguous artwork inside links remain blocked.
- Publisher-credit and social-profile paragraphs containing exactly one external linked icon and its label are treated as protected non-literary XHTML. The original link, icon, and label are retained untranslated; EPUB validation compares the entire source and output subtrees. Independent prose outside the linked icon is not exempt and still blocks extraction if mixed with artwork.
- Artwork can contain **English text baked into pixels** (e.g., a chapter-name
  illustration). The pixels are not translated by this mode, and titles in
  image-only source documents remain original-language. A separate image/text
  localization pipeline is required for fully localized visual typography.
- Prose containers such as `<blockquote><p>...</p></blockquote>` and
  `<li><p>...</p></li>` preserve their structural wrappers while the
  innermost text-bearing paragraphs are translated as separate blocks.
  Direct text or tails in a container with nested blocks still fail closed:
  every visible word must belong to exactly one mapped translation unit.
- A text-bearing paragraph may interleave original raster `<img>`, `<br>`,
  and `<hr>` elements. Each inert element is represented by an ordered
  `<!-- bt-media:NNNNNN -->` marker in the translation input. The marker
  must remain unchanged; EPUB assembly restores the exact original element,
  position, attributes, and image resource without asking the translator to
  recreate artwork. A `<strong>`, `<em>`, or span that crosses protected
  media uses paired `bt-inline` markers to preserve its original scope.
  Missing, duplicated, or reordered markers block assembly and Auto acceptance.
  Artwork inside hyperlinks and inline SVG requiring semantic reflow remain
  blocked unless verified independently.
- Some imported EPUBs contain unqualified author-site `href` values like
  `flawlessassholes.tumblr.com`. A narrow hostname-only allowlist of common
  public domain suffixes preserves these inert source attributes exactly,
  without guessing `https://`, making external requests, or certifying
  the link is usable. URLs with paths, query strings, arbitrary schemes or
  unknown document-type suffixes still fail closed.
- Body-prose hyperlinks to another XHTML document in the original EPUB spine
  are supported as source-bound inline links. The extractor verifies their
  relative document path against the package spine, and any fragment against
  exactly one target ID in the original XHTML. The translated label may change
  inside existing `bt-inline` markers, but the original href and attributes
  are copied unchanged. Missing/duplicate anchors, links outside the spine,
  unsafe paths, foreign schemes, and unsupported document types remain blocked.
  This is not a general link-rewriting or off-spine-notes capability.
- A prose-only `div` containing one inert HTTP(S), mailto or same-document
  hyperlink may be translated as a whole block, including any direct text
  preceding or following the link. Its `href`, `rel`, classes and original
  link element remain source-bound; a changed inline label still needs an
  unambiguous binding at final assembly. Multi-link, image-bearing or
  structurally ambiguous `div` elements remain blocked.
- XHTML inline `<big>`, `<small>`, `<sub>`, and `<sup>` are represented by
  protected, ordered `<!-- bt-inline:NNNNNN:start -->` and
  `<!-- bt-inline:NNNNNN:end -->` boundaries inside the prose block.
  Translate the visible words *between* each pair, but keep every boundary
  verbatim and in the same order. The renderer reconstructs the original
  tag and attributes (including CSS classes) around translated content.
  The independent text audit ignores the technical markers but compares all
  original text tokens. Nested styled inline elements and those embedded in
  hyperlinks or drop-cap paragraphs remain blocking unless separately mapped.
- A legacy XHTML `<big>` may wrap the complete text content of a prose
  paragraph, heading or simple leaf `div`. That single wrapper and its
  attributes are preserved around the translated content. A partial inline
  `<big>` uses the same protected boundaries as other styled inline text.
  Nested styled wrappers and ambiguous mixed semantic markup still block,
  rather than silently flattening source typography.
- Body paragraph styles and raster placement are preserved, but arbitrary
  complex inline CSS cannot be reconstructed automatically for freely translated
  sentences. Only the explicitly supported emphasis and drop-cap cases are
  currently reproduced. This does **not** certify pixel-level similarity.
- Source-preserving final EPUB assembly requires the exact original EPUB bytes
  still available in `source/`, even if a source identity is otherwise sealed.
- Existing books retain their pinned runtime and extraction mode; upgrading a
  book requires the explicit workflow revision transition, not a silent switch.

## Automatic translation batching

The Auto adapter loads a separately pinned, pure structural helper to withhold
XHTML block, inline, media and anchor markers and original hyperlinks from
language-model inputs. For marker-dense source documents it may request up to
24 source-ID-identified fragments in one response, capped at 3,500 original
source characters per call. Default smaller helper calls remain compatible.
Every fragment, literal separator, ordering marker and original source SHA
stays bound to the exact corpus; the model cannot create its own markers.

A prior Auto stage can adopt the newer batching-only helper **only** when its
saved unit, source identity, literary workflow revision and full segment plan
hash are unchanged, and every persisted translation still passes strict segment
validation. The migration updates helper provenance, not accepted literary
state. Unknown helper transitions remain blocked. This improves call efficiency
but does not increase the per-job model-call spending limit.

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

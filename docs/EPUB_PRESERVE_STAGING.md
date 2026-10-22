# Source-bound XHTML/Markdown staging (experimental)

This is an **opt-in preview-only** interoperability path for EPUB. It does not
replace normal Translator/Reviewer artifacts, independently attest source
coverage, certify language quality, or change a book's canonical durable state.
Do not use its output as a final reviewed book.

## Read-only source identity and Markdown block exchange

\`\`\`sh
python scripts/book.py epub-preserve-export original.epub --output-dir /tmp/my-epub-blocks
\`\`\`

The output directory must not exist. This emits
\`xhtml-translation-map.json\` (metadata and SHA-256 hashes only) and
\`0001.md\`, \`0002.md\`, etc., one for each XHTML spine document.

Every Markdown file is **protected marked text**, not ordinary Markdown
formatting. It contains a source-bound document header and ordered, individually
SHA-256-bound text blocks:

\`\`\`text
<!-- BT:DOC <source-document-sha256> -->

<!-- BT:BLOCK b000001 <source-block-sha256> -->
Whole prose block with [[BT:OPEN:0001]]protected inline XHTML[[BT:CLOSE:0001]].
<!-- BT:END -->
\`\`\`

Translate the prose *inside* each block, preserving its structural markers.
Protected markers represent existing tags, links, and attributes; they can
move with grammar but must be balanced and appear exactly once. Do **not**
translate, remove, add, or duplicate document/block boundary lines or
protected \`[[BT:...]]\` markers. Do not add Markdown heading/bold/list syntax
as a substitute for protected structure: the preview assembler inserts the
literal text and escapes XML metacharacters.

## Preview assembly

\`\`\`sh
python scripts/book.py epub-preserve-preview original.epub \
  --translations-dir /tmp/my-epub-blocks --language ru \
  --title "Translated Title" --output /tmp/book_PREVIEW.epub
\`\`\`

The output basename must end with \`_PREVIEW.epub\`. The output file must not
already exist. Before writing, the assembler checks the original EPUB SHA-256,
the entire expected document/block map, all marked Markdown files and
protected inline markers, XHTML safety, package resources and internal link
targets. It does **not** modify \`metadata.json\`, \`progress.json\`,
\`review-ledger.json\`, \`source-coverage.json\`, or output provenance manifests.

## Deliberate limits before general release

- Existing, free-form canonical \`translated/*.md\` files are **not**
  automatically convertible into block-marked documents. Reuse needs
  explicit source-text alignment and independent checking, not guesswork.
- The source EPUB must remain available verbatim for assembly; the generated
  JSON file is not its replacement. Never overwrite a historical source.
- Complex XHTML such as tables, SVG, executable content, off-spine prose and
  translatable image alternatives remains blocked until dedicated handlers
  and independent coverage tests are implemented.
- Internal links and source package assets are checked; EPUBCheck,
  cross-reader visual tests, localized NAV/NCX and authorial fidelity still
  require release-gate work.
- The existing \`book.py build --format epub\` remains the only canonical
  reviewed-state build path. This staging preview is not review evidence.
- Books remain pinned to their existing workflow revision until an explicit
  versioned upgrade, regardless of what this experimental command can parse.

See [source coverage](SOURCE_COVERAGE.md) and the lifecycle rules in
[orchestration](ORCHESTRATION.md). The staged XHTML/Markdown workflow belongs
to epic #87.

# EPUB structure-preserving translation (default)

For newly extracted EPUBs, Book Translator now preserves original XHTML and CSS as a template rather than re-creating formatting from Markdown. This is the primary EPUB mode; Markdown and TXT inputs continue to use the Markdown renderer. Existing pinned book workflows are not silently upgraded.

## Flow

1. Run `python scripts/book.py extract source.epub --target-language ru`. Source OPF spine and exact XHTML resource paths are recorded in `layout/structure.json`, SHA-bound to the immutable EPUB.
2. Each extracted text unit is one spine XHTML. Prose appears as complete paragraphs/blocks with ordered markers `⟦BT:SEG:000001⟧`.
3. Inline protected markers `⟦BT:O:0001⟧` and `⟦BT:C:0001⟧` bracket spans with emphasis, links, or CSS; `⟦BT:V:0002⟧` is a void element such as a hard break, image or empty anchor.
4. The translator translates normal prose. The agent must keep every segment marker and every protected inline marker intact, balanced, and in the correct block. The agent does not translate HTML or edit links, styles, image paths or IDs. It may freely reorder words around protected spans.
5. Literary acceptance and independent review use exact source and translation hashes as before.
6. Run source coverage from the original EPUB and seal/verify the corpus. The structural coverage validator checks that the flattened protected text equals the original XHTML text.
7. `python scripts/book.py build slug --format epub` automatically detects the structural layout, reinserts translated text into original XHTML, and preserves stylesheet files, images, internal anchors, notes, links and the original EPUB structure. Broken links, missing markers, malformed XML and missing resources block the build. `build-status` includes the SHA-256 of the original and layout manifest in the fingerprint.

## Example

Source:

```
⟦BT:SEG:000002⟧
She ⟦BT:O:0001⟧really⟦BT:C:0001⟧ loved him.⟦BT:V:0002⟧
```

Translated:

```
⟦BT:SEG:000002⟧
Она ⟦BT:O:0001⟧по-настоящему⟦BT:C:0001⟧ любила его.⟦BT:V:0002⟧
```

The last token could represent an original hard line break; the translator does not need to know HTML.

## Safety and limits

- XHTML outside the represented translation blocks must not contain untranslated prose. Non-representable interleaving of block and inline text fails closed.
- XML, spine, protected markers, local link targets and anchors are validated before publishing.
- Original EPUB content is untrusted data, not executable instructions; scripts are never executed.
- Complex unsupported markup needs an explicit structural adapter. Never drop it silently.
- Migrating a previously reviewed book from flat Markdown requires explicit permission and proven correspondence to the original blocks. It must not inherit an unrelated book's provenance.

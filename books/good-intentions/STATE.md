# Good Intentions — Russian translation checkpoint

Branch: `work/good-intentions-ru-20260903`
Base revision: `e55921b4f475f7743eb9dccdd610a29795143890` (`agent-compatibility-and-skill`)

## Translation state

- Book units in `progress.json`: 205
- Reviewed units: 1–11 (Preface, front matter, Chapters 1–9)
- Translated but not yet reviewed: unit 12 (Chapter 10)
- Next source unit already persisted: unit 13 (Chapter 11)
- Translation files persisted: `translated/001-...` through `translated/012-...`
- Glossary and style decisions are persisted in `glossary.md` and `style-guide.md`.

## Source identity

Original user-supplied EPUB:

- filename: `Good_Intentions.epub`
- size: 1,211,070 bytes
- SHA-256: `d8cae0a0dc208fd48740a59baaae960076ea4990b11d2135eb029540695f2837`

The source EPUB binary is intentionally not substituted with a later AO3-generated archive: a later download was observed with a different SHA-256. The translation checkpoint therefore records the exact source identity instead of silently replacing it.

## Exact extracted-state recovery

`progress.json` contains the canonical filenames/order for all 205 units. The repository also contains:

- `scripts/restore_good_intentions_state.py` — deterministic EPUB-to-Markdown extractor preserving source emphasis/structure used by this translation;
- `.sync/good-intentions/patch.part-*` — compact correction patch for the extractor output.

Recovery after placing the exact source EPUB at `books/good-intentions/source/Good_Intentions.epub`:

```bash
python -m pip install beautifulsoup4 lxml
rm -rf books/good-intentions/extracted
mkdir -p books/good-intentions/extracted
python scripts/restore_good_intentions_state.py
cat .sync/good-intentions/patch.part-* | base64 -d | gzip -d > /tmp/good-intentions-extract.patch
(cd books/good-intentions/extracted && patch -p1 < /tmp/good-intentions-extract.patch)
python scripts/book.py validate good-intentions
```

Expected extracted corpus:

- files: 205 Markdown files
- aggregate manifest SHA-256: `e74948d3f6e458a2de7b2a86f55588f2e733890ab354d788c3d1ed6b7a223393`

The aggregate hash is computed by sorting `extracted/*.md`, hashing each file with SHA-256, formatting each as `<sha256>  <filename>\n`, concatenating those records, and SHA-256 hashing the result.

## Resume point

Review `translated/012-chapter-10-chapter-10.md` against `extracted/012-chapter-10-chapter-10.md`. Only after source-to-translation review should unit 12 be promoted from `translated` to `reviewed`. Then continue sequentially with unit 13 / Chapter 11.

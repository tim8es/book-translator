# Good Intentions — Russian translation checkpoint

Branch: `work/good-intentions-ru-20260903`
Base revision: `e55921b4f475f7743eb9dccdd610a29795143890` (`agent-compatibility-and-skill`)
Workflow revision used for the current audit: `70afef29caf31f1fe58501ddd61a75f153366ae0`

## Translation state

- Book units in `progress.json`: 205
- Translation files persisted: `translated/001-...` through `translated/012-...`.
- A fresh full-quality audit was requested on 2026-09-03. See `REVIEW_AUDIT.md`.
- Units 1–8 and unit 12 were freshly compared with their exact extracted source artifacts. Fidelity corrections were applied where required; unit 5 / Chapter 3 required no blocking semantic correction.
- Units 9–11 / Chapters 7–9 **cannot currently receive a fresh reviewer PASS** because their declared extracted source files are absent from this branch.
- `progress.json` still contains historical `reviewed` values for units 9–11. Those values must not be treated as fresh reproducible review evidence until the exact source is restored and those chapters are re-compared.
- Unit 12 / Chapter 10 has been corrected and re-checked against its source at literary-review level, but it remains `translated` in `progress.json`; durable state promotion should wait until repository structural/source availability is reconciled.
- Next untranslated source unit after the current translated range is unit 13 / Chapter 11.
- Glossary and style decisions are persisted in `glossary.md` and `style-guide.md`. The style guide now explicitly preserves the semantic intensity ladder between `interested / like / fancy / crush / fall for / love`.

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

1. Restore the exact EPUB matching the recorded SHA-256 and reconstruct the full extracted corpus.
2. Freshly compare units 9–11 / Chapters 7–9 against their restored source artifacts, paying special attention to relationship-intensity inflation (`like/fancy/crush` versus `влюблён/любить`).
3. Reconcile `progress.json` only after source availability and structural validation are restored; do not trust historical reviewed flags for units 9–11 as evidence of the current audit.
4. After the audited translated range is structurally consistent, continue with unit 13 / Chapter 11.

# Good Intentions — Russian translation checkpoint

Branch: `work/good-intentions-ru-20260903`
Base revision: `e55921b4f475f7743eb9dccdd610a29795143890` (`agent-compatibility-and-skill`)
Workflow revision used for the current audit: `70afef29caf31f1fe58501ddd61a75f153366ae0`

## Translation state

- Book units in `progress.json`: 205.
- Translation files persisted: `translated/001-...` through `translated/012-...`.
- A fresh full-quality audit was requested on 2026-09-03. See `REVIEW_AUDIT.md`.
- Units 1–8 and unit 12 were freshly compared with source artifacts during that audit; fidelity corrections were applied where required.
- Units 9–11 / Chapters 7–9 were initially blocked because their extracted source artifacts were absent from the branch.
- On 2026-09-03 the exact original `Good_Intentions.epub` was supplied again. Its size and SHA-256 matched the recorded source identity exactly.
- The missing source artifacts were regenerated from that verified EPUB with the repository's deterministic extraction logic and restored to:
  - `extracted/009-chapter-7-chapter-7.md`
  - `extracted/010-chapter-8-chapter-8.md`
  - `extracted/011-chapter-9-chapter-9.md`
- Those three units are now source-reviewable again. Their historical `reviewed` flags must still not substitute for the requested fresh audit; re-compare them before treating the current audit as complete.
- Unit 12 / Chapter 10 has already been corrected and re-checked at literary-review level, but remains `translated` in `progress.json` pending state reconciliation.
- Next untranslated source unit after the current translated range is unit 13 / Chapter 11.
- Glossary and style decisions are persisted in `glossary.md` and `style-guide.md`. The style guide explicitly preserves the semantic intensity ladder between `interested / like / fancy / crush / fall for / love`.

## Source identity

Original user-supplied EPUB:

- filename: `Good_Intentions.epub`
- size: 1,211,070 bytes
- SHA-256: `d8cae0a0dc208fd48740a59baaae960076ea4990b11d2135eb029540695f2837`

The EPUB supplied again on 2026-09-03 matched both the recorded size and SHA-256 exactly. It was used only to restore required source artifacts; the binary EPUB itself is not published into the repository.

## Exact extracted-state recovery

`progress.json` contains the canonical filenames/order for all 205 units. The repository also contains:

- `scripts/restore_good_intentions_state.py` — deterministic EPUB-to-Markdown extractor preserving source emphasis/structure used by this translation;
- `.sync/good-intentions/patch.part-*` — compact correction patch for the extractor output.

Full recovery after placing the exact source EPUB at `books/good-intentions/source/Good_Intentions.epub`:

```bash
python -m pip install beautifulsoup4 lxml
rm -rf books/good-intentions/extracted
mkdir -p books/good-intentions/extracted
python scripts/restore_good_intentions_state.py
cat .sync/good-intentions/patch.part-* | base64 -d | gzip -d > /tmp/good-intentions-extract.patch
(cd books/good-intentions/extracted && patch -p1 < /tmp/good-intentions-extract.patch)
python scripts/book.py validate good-intentions
```

Expected full extracted corpus:

- files: 205 Markdown files
- aggregate manifest SHA-256: `e74948d3f6e458a2de7b2a86f55588f2e733890ab354d788c3d1ed6b7a223393`

## Resume point

1. Freshly compare restored units 9–11 / Chapters 7–9 against their source artifacts, with special attention to relationship-intensity inflation (`like/fancy/crush` versus `влюблён/любить`).
2. Reconcile `progress.json` only after the fresh review is complete; historical reviewed flags for units 9–11 are not evidence for the current audit.
3. Once the audited translated range is consistent, continue with unit 13 / Chapter 11.

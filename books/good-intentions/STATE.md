# Good Intentions — Russian translation checkpoint

Branch: `work/good-intentions-ru-20260903`
Base revision: `e55921b4f475f7743eb9dccdd610a29795143890` (`agent-compatibility-and-skill`)
Workflow revision used for the current audit: `70afef29caf31f1fe58501ddd61a75f153366ae0`

## Translation state

- Book units in `progress.json`: 205.
- Translation files physically persisted: `translated/001-...` through `translated/013-...`.
- `progress.json` still records unit 12 / Chapter 10 as `translated` and unit 13 / Chapter 11 as `extracted`; the existing unit-13 translation artifact therefore requires state/review reconciliation before it can be accepted as canonical reviewed work.
- A fresh full-quality audit was requested on 2026-09-03. See `REVIEW_AUDIT.md`.
- Units 1–8 and unit 12 were freshly compared with source artifacts during that audit; fidelity corrections were applied where required.
- Units 9–11 / Chapters 7–9 were initially blocked because their extracted source artifacts were absent from the branch.
- On 2026-09-03 the exact original `Good_Intentions.epub` was supplied again. Its size and SHA-256 matched the recorded source identity exactly.
- The complete canonical extracted corpus has now been restored: 205/205 Markdown source units are present in reading order.
- The restored corpus aggregate manifest SHA-256 is `e74948d3f6e458a2de7b2a86f55588f2e733890ab354d788c3d1ed6b7a223393`, matching the canonical recovery checkpoint derived from the verified original source.
- Units 9–11 are source-reviewable again. Their historical `reviewed` flags must still not substitute for the requested fresh audit; re-compare them before treating the current audit as complete.
- Unit 12 / Chapter 10 has already been corrected and re-checked at literary-review level, but remains `translated` in `progress.json` pending state reconciliation.
- Glossary and style decisions are persisted in `glossary.md` and `style-guide.md`. The style guide explicitly preserves the semantic intensity ladder between `interested / like / fancy / crush / fall for / love`.

## Source identity

Original user-supplied EPUB:

- filename: `Good_Intentions.epub`
- size: 1,211,070 bytes
- SHA-256: `d8cae0a0dc208fd48740a59baaae960076ea4990b11d2135eb029540695f2837`

The EPUB supplied again on 2026-09-03 matched both the recorded size and SHA-256 exactly. It was used to verify and reconstruct the complete canonical extracted corpus. The copyrighted/private binary EPUB itself is intentionally not committed to the repository.

## Extracted source corpus

`progress.json` contains the canonical filenames/order for all 205 units. The working branch now contains all 205 corresponding files under `books/good-intentions/extracted/`.

Verified canonical corpus:

- files: 205 Markdown files
- aggregate manifest SHA-256: `e74948d3f6e458a2de7b2a86f55588f2e733890ab354d788c3d1ed6b7a223393`
- restoration commit: `245193236537902f332b37b61d76c19c5ef7b937`

The repository also retains:

- `scripts/restore_good_intentions_state.py` — deterministic EPUB-to-Markdown extractor preserving source emphasis/structure used by this translation;
- `.sync/good-intentions/patch.part-*` — compact correction patch for the extractor output.

The book remains pinned to workflow revision `70afef29caf31f1fe58501ddd61a75f153366ae0`. The newer source-corpus preflight contract was used to diagnose and verify this repair, but book workflow provenance has not been silently upgraded.

## Resume point

1. Freshly compare units 9–11 / Chapters 7–9 against their restored source artifacts, with special attention to relationship-intensity inflation (`like/fancy/crush` versus `влюблён/любить`).
2. Reconcile `progress.json` only after the fresh review is complete; historical reviewed flags for units 9–11 are not evidence for the current audit.
3. Reconcile the existing unit-13 / Chapter-11 translation artifact with its current `extracted` state before accepting or continuing it.
4. Once the audited translated range is consistent, continue sequential literary work from the first genuinely non-reviewed unit.

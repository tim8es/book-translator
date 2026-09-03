# Good Intentions — Russian translation checkpoint

Branch: `work/good-intentions-ru-20260903`
Base revision: `e55921b4f475f7743eb9dccdd610a29795143890` (`agent-compatibility-and-skill`)
Workflow revision used for the current audit: `70afef29caf31f1fe58501ddd61a75f153366ae0`

## Translation state

- Book units in `progress.json`: 205.
- Translation files physically persisted: `translated/001-...` through `translated/014-...`.
- A fresh full-quality audit was requested on 2026-09-03. See `REVIEW_AUDIT.md`.
- Units 1–14 have now been freshly source-compared at literary-review level; all blocking fidelity defects found in that range were corrected and the current literary outcome is PASS.
- Units 9–11 / Chapters 7–9 were freshly re-reviewed after restoration of their exact canonical extracted source artifacts. Relationship-intensity inflation and several local fidelity defects were corrected; the historical PASS flags are no longer being relied on as evidence.
- Unit 12 / Chapter 10 had already been corrected and source-rechecked during the audit and is accepted at literary-review level.
- Unit 13 / Chapter 11 had an existing translation artifact while `progress.json` still said `extracted`; it has now been freshly compared to source and accepted without blocking corrections.
- Unit 14 / Chapter 12 was newly translated in the current continuation pass, independently re-reviewed against source, corrected for several overly free renderings, and accepted at literary-review level.
- The complete canonical extracted corpus remains present: 205/205 Markdown source units in reading order.
- The restored corpus aggregate manifest SHA-256 is `e74948d3f6e458a2de7b2a86f55588f2e733890ab354d788c3d1ed6b7a223393`, matching the canonical recovery checkpoint derived from the verified original source.
- Glossary and style decisions are persisted in `glossary.md` and `style-guide.md`. The style guide explicitly preserves the semantic intensity ladder between `interested / like / fancy / crush / fall for / love`.

## Source identity

Original user-supplied EPUB:

- filename: `Good_Intentions.epub`
- size: 1,211,070 bytes
- SHA-256: `d8cae0a0dc208fd48740a59baaae960076ea4990b11d2135eb029540695f2837`

The EPUB supplied again on 2026-09-03 matched both the recorded size and SHA-256 exactly. It was used to verify and reconstruct the complete canonical extracted corpus. The copyrighted/private binary EPUB itself is intentionally not committed to the repository.

## Extracted source corpus

`progress.json` contains the canonical filenames/order for all 205 units. The working branch contains all 205 corresponding files under `books/good-intentions/extracted/`.

Verified canonical corpus:

- files: 205 Markdown files
- aggregate manifest SHA-256: `e74948d3f6e458a2de7b2a86f55588f2e733890ab354d788c3d1ed6b7a223393`
- restoration commit: `245193236537902f332b37b61d76c19c5ef7b937`

The repository also retains:

- `scripts/restore_good_intentions_state.py` — deterministic EPUB-to-Markdown extractor preserving source emphasis/structure used by this translation;
- `.sync/good-intentions/patch.part-*` — compact correction patch for the extractor output.

The book remains pinned to workflow revision `70afef29caf31f1fe58501ddd61a75f153366ae0`. The newer source-corpus preflight contract was used to diagnose and verify the repair, but book workflow provenance has not been silently upgraded.

## Current audit corrections

Fresh source review of Chapters 7–9 removed recurring semantic inflation such as `like/fancy` being translated as settled `любить/влюблён`, restored a lost emphasis boundary, and corrected local meaning defects such as `Voldemort’s possession` and tentative `mates` wording.

Fresh review of Chapter 12 corrected several first-draft freedoms, including `withdrawn`, repeated `want him / want me` phrasing, and Draco's final boundary that he will not offer himself merely as a way for Harry to get off.

## Resume point

1. Treat units 1–14 as the accepted translated/audited range once the mechanical `progress.json` reconciliation is committed.
2. Continue sequential literary work from unit 15 / Chapter 13 (`extracted/015-chapter-13-chapter-13.md`).
3. Keep the relationship-intensity ladder, consent/boundary cues, POV attribution, source emphasis, and explicit register as mandatory review checks.

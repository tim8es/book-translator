# Translation review audit — 2026-09-03

Workflow revision: `70afef29caf31f1fe58501ddd61a75f153366ae0`
Branch: `work/good-intentions-ru-20260903`
Scope: all currently translated units 1–12.

## Overall reviewer outcome

`CORRECTIONS_REQUIRED`

The source-backed portion of the translated corpus has been re-reviewed and corrected, but a fresh PASS cannot be issued for the full translated scope because the exact extracted source artifacts for units 9–11 (Chapters 7–9) are absent from the current branch. The preserved source EPUB is also intentionally absent from the repository, so those units cannot be independently source-compared until the exact source is restored.

## Unit results

| Unit | Artifact | Fresh source comparison | Result |
| --- | --- | --- | --- |
| 1 | Preface | Yes | Corrected; source-backed literary PASS |
| 2 | Front matter / Summary | Yes | Corrected; source-backed literary PASS |
| 3 | Chapter 1 | Yes | Corrected; source-backed literary PASS |
| 4 | Chapter 2 | Yes | Corrected; source-backed literary PASS |
| 5 | Chapter 3 | Yes | No blocking fidelity defect found; literary PASS |
| 6 | Chapter 4 | Yes | Corrected; source-backed literary PASS |
| 7 | Chapter 5 | Yes | Corrected; source-backed literary PASS |
| 8 | Chapter 6 | Yes | Corrected; source-backed literary PASS |
| 9 | Chapter 7 | No — `extracted/009-chapter-7-chapter-7.md` absent | BLOCKED; old PASS not accepted as fresh evidence |
| 10 | Chapter 8 | No — `extracted/010-chapter-8-chapter-8.md` absent | BLOCKED; old PASS not accepted as fresh evidence |
| 11 | Chapter 9 | No — `extracted/011-chapter-9-chapter-9.md` absent | BLOCKED; old PASS not accepted as fresh evidence |
| 12 | Chapter 10 | Yes | Corrected and re-checked against source; source-backed literary PASS |

`PASS` above refers only to the literary reviewer result for the artifact that was actually compared. It does not by itself mutate `progress.json`.

## Main fidelity defect found

The dominant systematic error in the prior translation/review pass was **relationship-intensity inflation**. English terms such as `interested`, `like`, `fancy`, and `crush` were repeatedly promoted to stronger Russian formulations such as `влюблён` or `любить`. In this story that changes the pacing of Harry's realization and can make attraction or interest sound emotionally settled earlier than the author writes it.

The book style guide now contains an explicit relationship-intensity ladder and review rule so later translation/review should distinguish `interested / like / fancy / crush / fall for / love` rather than flattening them.

Examples corrected during this audit include:

- Chapter 1: `Malfoy fancies me` no longer becomes `Малфой в меня влюблён`.
- Chapter 2: `if he'd really fancied me the whole time` no longer becomes a claim that Draco was already explicitly `влюблён` in Harry at that point in Harry's POV.
- Chapter 4: Ginny's `interest` is no longer upgraded to `она всё это время была в меня влюблена`.
- Chapter 6: `a guy had a crush on him` is no longer automatically rendered as full `влюблённость`.

## Other material corrections

- Preface: AO3 `Explicit` rating and `references of abuse` tag were restored more precisely.
- Chapter 1: `upset` was corrected from `нервничал` to `расстраивался`; Hermione's grip on Harry's biceps was restored more precisely.
- Chapter 4: `I came in four liberal bursts across my chest` was corrected from the physically incorrect `четырьмя ... толчками` to ejaculation in four bursts/streams.
- Chapter 10: Draco's distinction between not knowing how to *act* when he likes someone and still knowing how to *like* Harry was restored.
- Chapter 10: `I'd like him as a friend` was restored as a platonic-feeling distinction rather than merely `хочу дружить с ним`.
- Chapter 10: Harry's immobility after the near-kiss is now explicitly allowed to have been shock (`he could've just been in shock`) instead of vaguely saying the situation itself `могла быть шоком`.
- Chapter 10: `casual fixation for you to pick and choose when it suits you` no longer becomes a generic `случайная прихоть`; the accusation that Harry must not treat Draco's feelings as an unserious fixation available only when convenient is preserved.
- Chapter 10: several literal calques and explicit-scene action errors were corrected without sanitizing the source.

## Repository-state defect discovered

The durable state currently claims units 9–11 are `reviewed`, while their declared `source_path` files are absent from the branch. That means the repository cannot presently reproduce the source-comparison that `reviewed` is supposed to represent.

The exact original source is identified as:

- `Good_Intentions.epub`
- size: 1,211,070 bytes
- SHA-256: `d8cae0a0dc208fd48740a59baaae960076ea4990b11d2135eb029540695f2837`

The repository contains deterministic recovery instructions and correction patches, but the exact EPUB must be made available to the recovery process before units 9–11 can be freshly reviewed.

## Dogfooding findings for the agent workflow

### 1. Review reproducibility conflicts with source-retention policy

The reviewer contract correctly requires reopening the source, and orchestration validation expects extracted/source artifacts to exist. At the same time, repository hygiene correctly avoids publishing a copyrighted source book. The current checkpoint solves privacy by omitting the EPUB, but it also omits some extracted source units. There is no first-class state representing `reviewed historically, but currently unverifiable until private source restoration`.

**Recommended contract change:** model source availability explicitly. Record verified private-source identity separately from repository publication state, and introduce a resumable `source_unavailable` / `review_unverifiable` condition rather than leaving `reviewed` looking fully reproducible.

### 2. No explicit audit / review-all mode

The normal workflow is sequential and appropriately discourages pointless re-review of already reviewed units. A user-requested quality audit, however, needs a defined mode that can invalidate previous PASS results when a systematic defect is discovered.

**Recommended contract change:** add an `audit` reviewer/orchestrator workflow: select a scope, establish the reason for re-review, invalidate stale review receipts for that scope, compare source again, apply corrections, and only then restore reviewed state.

### 3. Semantic-fidelity rules are correct but too abstract for recurring lexical ladders

The contract says to preserve certainty, implication, and emotional intensity, but that did not prevent repeated `fancy / like / interest / crush` → `влюблён / любить` inflation.

**Recommended contract change:** require book-specific high-risk semantic ladders in the style guide and require reviewers to check them explicitly. Relationship intensity, certainty/modality, consent/boundary cues, and speaker/POV attribution are good default categories.

### 4. `reviewed` has no artifact-bound review receipt

`progress.json` stores a state but not the source SHA, translation SHA, glossary/style-guide revisions, workflow revision, reviewer outcome, and review timestamp that produced that state. After a translation file changes, it is hard to prove which artifact the old PASS covered.

**Recommended contract change:** persist a per-unit review receipt containing at least source blob/hash, translation blob/hash, workflow revision, glossary/style-guide hashes, outcome, and timestamp. A hash mismatch should automatically invalidate `reviewed`.

### 5. Checkpoint/export can preserve an internally unreproducible state

The current branch contains `reviewed` entries whose `source_path` targets are missing. Either structural validation was not run after packaging the checkpoint, or source omission was not represented in the state model.

**Recommended contract change:** every checkpoint/export must run structural validation. If private-source omission is intentional, the exported state must explicitly record that review cannot be reproduced until the private source is restored.

## Workflow strengths confirmed by dogfooding

- Pinning `metadata.json.workflow.resolved_revision` worked: the audit did not silently switch to a newer workflow contract.
- Recording the exact source filename, size, and SHA-256 is valuable and prevented substitution of a different later archive.
- The contract's rule that a previous PASS is not evidence for a changed or questioned artifact is correct and was essential in finding the repeated fidelity drift.
- `glossary.md` and `style-guide.md` are useful durable literary memory; adding the discovered relationship-intensity rule there immediately improves future chapters.

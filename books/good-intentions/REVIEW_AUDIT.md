# Translation review audit — 2026-09-03

Workflow revision: `70afef29caf31f1fe58501ddd61a75f153366ae0`
Branch: `work/good-intentions-ru-20260903`
Scope: all currently translated units 1–14.

## Overall reviewer outcome

`PASS` for the currently translated range (units 1–14) at literary source-comparison level.

The earlier `CORRECTIONS_REQUIRED` outcome was caused by missing extracted source artifacts for units 9–11. The exact original EPUB was subsequently supplied again, verified against the recorded size and SHA-256, and used to restore the canonical 205/205 extracted source corpus. Units 9–11 were then freshly compared rather than inheriting their historical review flags. Unit 13's orphan translation was likewise freshly reconciled, and unit 14 was newly translated and independently re-reviewed.

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
| 9 | Chapter 7 | Yes, after canonical source restoration | Corrected; source-backed literary PASS |
| 10 | Chapter 8 | Yes, after canonical source restoration | Corrected; source-backed literary PASS |
| 11 | Chapter 9 | Yes, after canonical source restoration | Corrected; source-backed literary PASS |
| 12 | Chapter 10 | Yes | Corrected and re-checked; source-backed literary PASS |
| 13 | Chapter 11 | Yes | Existing translation reconciled; no blocking fidelity defect found; literary PASS |
| 14 | Chapter 12 | Yes | Newly translated, corrected after independent comparison; source-backed literary PASS |

`PASS` above is the literary reviewer outcome. Shared orchestration state is updated separately.

## Main fidelity defect found and resolved

The dominant systematic error in the prior translation/review pass was **relationship-intensity inflation**. English terms such as `interested`, `like`, `fancy`, and `crush` were repeatedly promoted to stronger Russian formulations such as `влюблён` or `любить`. In this story that changes the pacing of Harry's realization and can make attraction or interest sound emotionally settled earlier than the author writes it.

The book style guide now contains an explicit relationship-intensity ladder and review rule so later translation/review distinguishes `interested / like / fancy / crush / fall for / love` rather than flattening them.

Examples corrected during this audit include:

- Chapter 1: `Malfoy fancies me` no longer becomes `Малфой в меня влюблён`.
- Chapter 2: `if he'd really fancied me the whole time` no longer becomes a claim that Draco was already explicitly `влюблён` in Harry at that point in Harry's POV.
- Chapter 4: Ginny's `interest` is no longer upgraded to `она всё это время была в меня влюблена`.
- Chapter 6: `a guy had a crush on him` is no longer automatically rendered as full `влюблённость`.
- Chapter 7: `better at liking someone`, `when they fancied someone`, and `what liking men might be like` no longer get promoted to settled love; the source emphasis boundary around `liking men` is also preserved.
- Chapter 8: Harry's note `I'm shit at liking people too` now remains at the `нравится`/attraction level rather than becoming `умею влюбляться`.
- Chapter 9: `not like I'm gonna stop fancying you` now remains `ты ведь не перестанешь мне нравиться` rather than escalating to `перестану в тебя влюбляться`.

## Other material corrections

- Preface: AO3 `Explicit` rating and `references of abuse` tag were restored more precisely.
- Chapter 1: `upset` was corrected from `нервничал` to `расстраивался`; Hermione's grip on Harry's biceps was restored more precisely.
- Chapter 4: `I came in four liberal bursts across my chest` was corrected from the physically incorrect `четырьмя ... толчками` to ejaculation in four bursts/streams.
- Chapter 7: Draco's father `would volunteer me to torture people` is rendered with Draco as the person volunteered/forced to torture, not as an action by the father himself.
- Chapter 9: `Voldemort's possession` is rendered as Voldemort taking possession/control of Ginny rather than an ambiguous `одержимость Волдемортом`; tentative `mates` wording is kept tentative.
- Chapter 10: Draco's distinction between not knowing how to *act* when he likes someone and still knowing how to *like* Harry was restored.
- Chapter 10: `I'd like him as a friend` was restored as a platonic-feeling distinction rather than merely `хочу дружить с ним`.
- Chapter 10: Harry's immobility after the near-kiss is now explicitly allowed to have been shock (`he could've just been in shock`) instead of vaguely saying the situation itself `могла быть шоком`.
- Chapter 10: `casual fixation for you to pick and choose when it suits you` no longer becomes a generic `случайная прихоть`; the accusation that Harry must not treat Draco's feelings as an unserious fixation available only when convenient is preserved.
- Chapter 10: several literal calques and explicit-scene action errors were corrected without sanitizing the source.
- Chapter 12: `smiley and withdrawn` is kept as `улыбчивым и ушедшим в себя`, not weakened/misread as simple distraction.
- Chapter 12: the repeated emotional/sexual line `I want him to want me` / `when he wanted me` remains explicit rather than being softened into merely wanting company.
- Chapter 12: Draco's final boundary `I'm not offering myself up just to get you off, as if that's all I want from you` remains a refusal to be used merely for Harry's release and preserves the fact that Draco wants more than sex.

## Source/repository repair completed

The exact original source identity is:

- `Good_Intentions.epub`
- size: 1,211,070 bytes
- SHA-256: `d8cae0a0dc208fd48740a59baaae960076ea4990b11d2135eb029540695f2837`

The EPUB supplied again on 2026-09-03 matched both recorded values. It was used to restore and verify the complete canonical extracted corpus. The repository now contains all 205 expected Markdown source units; the copyrighted/private EPUB binary itself remains intentionally uncommitted.

Canonical extracted-corpus aggregate manifest SHA-256:

`e74948d3f6e458a2de7b2a86f55588f2e733890ab354d788c3d1ed6b7a223393`

Restoration commit:

`245193236537902f332b37b61d76c19c5ef7b937`

## Dogfooding findings for the agent workflow

### 1. Review reproducibility conflicts with source-retention policy

The reviewer contract correctly requires reopening the source, and orchestration validation expects source artifacts to exist. Repository hygiene correctly avoids publishing the copyrighted binary source. The successful recovery shows the distinction that the workflow needs: private source identity can be durable without committing the original book, while derived reviewable source artifacts can still be restored deterministically.

**Recommended contract change:** model source availability explicitly. Record verified private-source identity separately from repository publication state, and introduce a resumable `source_unavailable` / `review_unverifiable` condition rather than leaving `reviewed` looking fully reproducible when source artifacts are absent.

### 2. No explicit audit / review-all mode

The normal workflow is sequential and appropriately discourages pointless re-review of already reviewed units. A user-requested quality audit, however, needs a defined mode that can invalidate previous PASS results when a systematic defect is discovered.

**Recommended contract change:** add an `audit` reviewer/orchestrator workflow: select a scope, establish the reason for re-review, invalidate stale review receipts for that scope, compare source again, apply corrections, and only then restore reviewed state.

### 3. Semantic-fidelity rules are correct but too abstract for recurring lexical ladders

The contract says to preserve certainty, implication, and emotional intensity, but that did not prevent repeated `fancy / like / interest / crush` → `влюблён / любить` inflation.

**Recommended contract change:** require book-specific high-risk semantic ladders in the style guide and require reviewers to check them explicitly. Relationship intensity, certainty/modality, consent/boundary cues, and speaker/POV attribution are good default categories.

### 4. `reviewed` has no artifact-bound review receipt

`progress.json` stores a state but not the source SHA, translation SHA, glossary/style-guide revisions, workflow revision, reviewer outcome, and review timestamp that produced that state. After a translation file changes, it is hard to prove which artifact the old PASS covered.

**Recommended contract change:** persist a per-unit review receipt containing at least source blob/hash, translation blob/hash, workflow revision, glossary/style-guide hashes, outcome, and timestamp. A hash mismatch should automatically invalidate `reviewed`.

### 5. Full-file state mutation is unnecessarily brittle

`progress.json` is stored as a single minified line with all 205 unit records. The connected GitHub contents API only supports whole-file replacement, so changing three statuses requires safely round-tripping the complete large blob. This is operationally fragile for agents even though the state model itself is simple.

**Recommended contract change:** pretty-print the state file or split mutable per-unit state into a format that supports bounded edits; alternatively provide a repository helper command that updates statuses deterministically and validates the result.

## Workflow strengths confirmed by dogfooding

- Pinning `metadata.json.workflow.resolved_revision` worked: the audit did not silently switch to a newer workflow contract.
- Recording the exact source filename, size, and SHA-256 is valuable and prevented substitution of a different later archive.
- The contract's rule that a previous PASS is not evidence for a changed or questioned artifact is correct and was essential in finding the repeated fidelity drift.
- `glossary.md` and `style-guide.md` are useful durable literary memory; adding the discovered relationship-intensity rule there immediately improves future chapters.
- Restoring the exact source corpus before accepting historical review flags worked as intended: the previously blocked units were independently source-compared and corrected before being accepted.

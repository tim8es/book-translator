# Orchestration protocol

This file is authoritative for Book Translator execution topology, durable state transitions, claims, review evidence, source integrity, resume behavior, finalization, and output sequencing. The repository supports one current workflow contract; older workspace formats must be converted outside the production runtime before use.

The Orchestrator loads the current `agent-manifest.json`, follows its `context_profiles`, and uses `metadata.json.workflow` as durable provenance. `contract_read_order` is not a supported routing mechanism. Do not silently reinterpret an incompatible workspace as current state.

Literary translation and review quality belong to `docs/TRANSLATION.md`. Commit boundaries and recovery-friendly history are defined in `docs/COMMIT_DISCIPLINE.md`.

## Execution topology

Preferred execution uses isolated workers:

```text
Orchestrator
  -> translator role
  -> durable translation acceptance
  -> reviewer role
  -> durable review evidence
  -> orchestrator promotion
  -> next chapter
```

When isolated workers are unavailable, use `single_agent_bounded_context`: construct the Translator context, finish that role, then reconstruct an independent Reviewer context from durable files. Do not pass hidden Translator reasoning to the Reviewer and do not collapse translation and review into one pass.

Only the orchestrator may update global mutable state such as `progress.json`, `glossary.md`, `style-guide.md`, source integrity state, workflow provenance, and other book-wide decisions. Workers may return artifacts and proposals; shared-state proposals are reconciled through the central compare-and-swap path.

## Supported workspace

A supported book lives at `books/<book-slug>/` and has current-schema durable state:

```text
books/<book-slug>/
├── source/
├── extracted/
├── translated/
├── output/
├── metadata.json
├── progress.json
├── review-ledger.json
├── source-manifest.json
├── glossary.md
└── style-guide.md
```

The current workflow requires explicit workflow provenance, source identity, a sealed source manifest, and machine review evidence. Missing schema versions, lifecycle-only review state, and unsealed historical workspace shapes are unsupported rather than alternate execution modes.

For a new book, prefer:

```bash
python scripts/book.py extract <source-file> --slug <book-slug> --target-language <language>
python scripts/corpus.py seal <book-slug>
```

Initialization preserves the supplied source, real reading order, stable chapter paths, `metadata.json.workflow`, an empty review ledger, glossary/style state, and source identity.

## Corpus preflight

Run a book-wide corpus preflight before dispatching literary work and again whenever source/repository state may have changed:

```bash
python scripts/book.py validate <book-slug>
python scripts/corpus.py verify <book-slug>
```

The preflight verifies chapter counts and paths, preserved source identity, every extracted artifact, and the SHA-256 values in `source-manifest.json`. A mismatch is blocking; never regenerate a manifest merely to bless changed bytes.

If the exact trusted source is available but the extracted tree is damaged, restore the complete source corpus in one batch:

```bash
python scripts/corpus.py restore <book-slug> <source-file>
```

Do not repair missing extracted chapters one at a time. Do not substitute a later edition or same-named source whose identity differs. After restore, run structural validation and `python scripts/corpus.py verify <book-slug>` before dispatching literary work.

## Lifecycle

The current lifecycle is:

```text
pending -> extracted -> translated -> reviewed
```

- `pending`: usable source unit is not ready.
- `extracted`: source unit exists and may be translated.
- `translated`: canonical translation has passed machine translation acceptance but lacks accepted current review.
- `reviewed`: the exact canonical translation has current PASS evidence and has been promoted by the Orchestrator.

Do not translate multiple chapters concurrently by default. Sequential execution preserves terminology, voice, ambiguity, and continuity decisions:

```text
T1 -> R1 -> durable state -> T2 -> R2 -> durable state
```

Explicit parallel execution is invocation-scoped only through `resume --parallel N` with `N > 1`.

## Durable claims

A worker may not start literary work without owning a durable claim for the exact unit and role:

```bash
python scripts/book.py claim <book-slug> <chapter-or-range> \
  --role <translator|reviewer> \
  --session-id <session-id>
```

Inspect ownership with:

```bash
python scripts/book.py claims <book-slug>
```

Expired claims remain occupied until auditable cleanup:

```bash
python scripts/book.py cleanup-claims <book-slug>
```

Release only from the owning session after its result is accepted or explicitly abandoned:

```bash
python scripts/book.py release <book-slug> <chapter-or-range> --session-id <session-id>
```

### Parallel snapshot binding

For explicit parallel work, obtain the planning snapshot from `resume --parallel N` and pass the exact returned values into claim acquisition:

```bash
python scripts/book.py claim <book-slug> <chapter> \
  --role <translator|reviewer> \
  --session-id <session-id> \
  --base-commit <context.base_commit> \
  --glossary-revision <context.shared_state_revisions.glossary> \
  --style-guide-revision <context.shared_state_revisions.style_guide>
```

Human output exposes the same `--base-commit`, `--glossary-revision`, and `--style-guide-revision` values. If the frozen shared-state snapshot drifts, reject stale work rather than overwriting newer state.

## Translator boundary

Translator context contains only the current Translator `context_profiles` contracts plus bounded durable inputs: metadata, glossary, style guide, source unit, necessary continuity context, and target artifact path.

A written translation file is not a durable state transition. While the matching Translator claim is live, accept the canonical artifact with:

```bash
python scripts/book.py accept-translation <book-slug> <chapter> \
  --session-id <translator-session>
```

`accept-translation` verifies the current workflow revision, owning live Translator claim, canonical source/translation bytes, artifact SHA-256 identity, and any frozen shared-state snapshot. Only a successful compare-and-swap may advance `extracted -> translated`. Release the Translator claim afterward.

## Reviewer boundary

Reviewer context contains the exact source, canonical translation, current glossary/style decisions, bounded continuity context, and `docs/TRANSLATION.md`. It does not receive hidden Translator reasoning.

The Reviewer outcome is `PASS` or `CORRECTIONS_REQUIRED`. Chat output alone is not authoritative review coverage. Record the outcome while the Reviewer claim is live:

```bash
python scripts/book.py review-record <book-slug> <chapter> \
  --outcome PASS|CORRECTIONS_REQUIRED \
  --session-id <reviewer-session>
```

Inspect machine review state with:

```bash
python scripts/book.py reviews <book-slug>
```

A current PASS is bound to the exact source hash, translation hash, workflow revision, and review contract revision. Changing the artifact makes prior evidence stale. Markdown review notes may be useful context, but they are not authoritative review coverage.

`CORRECTIONS_REQUIRED` keeps the unit translated. Apply corrections through the Translator boundary and run an independent review again. A current PASS is necessary but does not itself mutate lifecycle state. Promote only through:

```bash
python scripts/book.py accept-review <book-slug> <chapter>
```

`accept-review` re-resolves current PASS evidence and compare-and-swaps `progress.json`; missing, stale, mismatched, or `CORRECTIONS_REQUIRED` evidence cannot produce `reviewed`.

## Resume

Repository state is authoritative, not chat history. For every resumed run:

1. identify the book deterministically;
2. read current-schema `metadata.json` and `progress.json`;
3. run corpus preflight;
4. inspect claims and machine review evidence;
5. continue from the first valid non-reviewed operation unless the user requested another bounded scope.

Do not silently infer an old workspace into a supported shape. Invalid or incomplete durable state blocks mutation and must be converted or repaired explicitly outside the runtime.

## Failure and recovery

- Translation failure or failed `accept-translation` leaves the unit unadvanced.
- `CORRECTIONS_REQUIRED` or missing/stale review evidence leaves the unit `translated`.
- Corpus or structural failure blocks literary work.
- A source identity mismatch is never repaired by substituting different bytes.
- A rejected compare-and-swap is replanned from a fresh read; never blindly retry a mutation.
- Finalization and build operations must be idempotent or fail closed around ambiguous partial state.

## Finalization and output

Before declaring a book complete, verify every intended unit is present in reading order, every unit is `reviewed` with current PASS evidence, structural validation succeeds, corpus SHA-256 verification succeeds, shared literary decisions are consistent, and requested output is actually built and checked.

Default Markdown build:

```bash
python scripts/book.py build <book-slug>
```

An unreviewed preview is allowed only when explicitly requested and clearly identified as such. EPUB/final output must be built only from canonical durable state and verified before claiming completion.

`STATE.md`, `FINAL_QUALITY_GATES.md`, and `REVIEW_REPORT.md` are generated projections; authoritative state remains the machine-readable records and current artifact bytes.

## GitHub API storage

GitHub API storage is a supported durable backend for environments without a local checkout. It preserves the same create-if-absent and compare-and-swap semantics as filesystem storage. GitHub Actions are not required for runtime orchestration.

Transport failures or ambiguous mutations must not be blindly retried. Re-read authoritative GitHub state, classify what actually happened, and replan. GitHub-specific mechanics remain at the storage boundary; Translator and Reviewer contracts stay backend-agnostic.

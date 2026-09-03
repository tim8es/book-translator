# Orchestration protocol

This file is authoritative for Book Translator execution topology, book initialization/resume, bounded role context, chapter-state transitions, single-writer persistence, failure handling, validation ordering, source-corpus integrity, and output/completion sequencing.

It is loaded by the `orchestrator` context profile together with `AGENTS.md`.

Literary translation and review quality belong exclusively to `docs/TRANSLATION.md`. This file consumes Translator artifacts and Reviewer outcomes without restating the literary checklist.

## Orchestrator responsibility

The Orchestrator coordinates durable book work without carrying an ever-growing copy of the book or setup instructions in conversational context.

At the start of a run:

1. identify the Book Translator installation root;
2. identify the active book or determine that a new book must be initialized;
3. determine the workflow revision associated with that book;
4. read `agent-manifest.json` from that revision;
5. load the `orchestrator` entry from `context_profiles` from the same revision;
6. read only the durable book state needed to choose the next operation.

For an existing book, `metadata.json.workflow` is the provenance source for its execution contract.

## Execution modes

### Preferred: `isolated_workers`

Use independent worker sessions/subagents when the active environment supports them.

For each chapter:

```text
Orchestrator
  -> translator role (fresh bounded context)
  -> reviewer role (fresh independent bounded context)
  -> Orchestrator accepts/rejects proposals and persists valid state
  -> next chapter
```

The reviewer must not inherit hidden reasoning from the translator. It receives the source, translation artifact, and required durable literary context.

### Fallback: `single_agent_bounded_context`

When isolated workers are unavailable, preserve the same logical role boundaries in one physical agent session:

1. build and load only the translator context pack;
2. complete the translation role and persist/return its artifact;
3. end that role context;
4. rebuild the reviewer context pack from durable files;
5. independently perform review under `docs/TRANSLATION.md`;
6. return the Reviewer outcome to the Orchestrator role;
7. perform the state transition only from the Orchestrator role.

Do not collapse translation and review into one pass merely because only one physical agent is available.

## Single-writer rule

Only the orchestrator may update global mutable state during an orchestrated run.

This strict rule applies to:

- `progress.json`;
- `glossary.md`;
- `style-guide.md`;
- `source-manifest.json`;
- book-level metadata;
- workflow provenance;
- other shared book-wide decisions.

Translator and Reviewer roles may return artifacts, findings, warnings, corrections, and proposed glossary/style decisions. They do not independently race to persist shared state.

A translation artifact may be written directly by a worker only when the active environment provides a non-conflicting target. The Orchestrator remains responsible for accepting that artifact as canonical book state.

## Book selection

On a new session:

1. enumerate valid book workspaces under the active installation root;
2. if the user explicitly identifies a book, select it;
3. otherwise, if exactly one incomplete book exists, select it automatically;
4. if multiple incomplete books exist and user intent does not identify one, ask only which book to resume;
5. never choose arbitrarily between multiple plausible active books.

## New-book initialization

If the source book has not yet been initialized, create durable book state before translation begins.

Prefer `scripts/book.py extract` when Python is available and the source format is supported by the helper. Otherwise reproduce the same state directly and conservatively.

A valid initialized book has:

```text
books/<book-slug>/
├── source/
├── extracted/
├── translated/
├── output/
├── metadata.json
├── progress.json
├── source-manifest.json
├── glossary.md
└── style-guide.md
```

Initialization requirements:

- preserve the supplied source unchanged under `source/`;
- preserve real reading order during extraction;
- use stable, unique chapter numbering/slugs and aligned source/translation paths;
- create per-book metadata and progress state;
- copy the selected workflow repository/requested ref/resolved revision into `metadata.json.workflow`;
- initialize glossary and style-guide durable memory;
- perform structural validation before sustained translation;
- seal the verified source corpus after successful extraction.

When Python is available, seal the initialized corpus once:

```bash
python scripts/corpus.py seal <book-slug>
```

`source-manifest.json` records the SHA-256 identity of the preserved source and every extracted source artifact. Treat it as integrity/provenance state, not literary state. It must not be regenerated from an unverified replacement source merely to make validation look clean.

For EPUB manual extraction, use the package/spine reading order rather than treating every XHTML resource as a chapter. For other structured text sources, respect explicit document structure and preserve a larger unit when boundaries are ambiguous.

## Source corpus preflight

Before dispatching literary work for an existing book, perform a corpus preflight once per resumed run or whenever repository/source state may have changed.

The preflight is book-wide, not chapter-by-chapter:

1. read `metadata.json` and `progress.json`;
2. compare `metadata.chapter_count` with the number of progress entries;
3. verify that every `source_path` required by `progress.json` exists, not merely the next chapter;
4. verify the preserved source declared by metadata is available when the active workspace is expected to be self-contained;
5. when `source-manifest.json` exists, retain its source SHA-256 as the source identity for recovery;
6. run structural validation before selecting the next literary task.

When Python is available:

```bash
python scripts/book.py validate <book-slug>
```

A workspace with 205 progress entries and only 13 extracted artifacts is not a partially valid 13-chapter source corpus. It is an incomplete corpus and must be repaired before translation/review continues.

If the original source is available but the extracted tree is incomplete, restore the complete source corpus in one batch. Do not repair missing extracted chapters one at a time.

For a sealed workspace:

```bash
python scripts/corpus.py restore <book-slug> <source-file>
```

For a legacy workspace without `source-manifest.json`, recovery requires a trusted SHA-256 from durable provenance or the user:

```bash
python scripts/corpus.py restore <book-slug> <source-file> --expected-sha256 <sha256>
```

The restore operation must verify source identity and complete extraction before replacing canonical source artifacts. It must preserve `progress.json`, translation files, review states, glossary, and style guide. After recovery, run `python scripts/book.py validate <book-slug>` again before dispatching literary work.

Do not substitute a later online edition, archive export, or same-named file when the recorded SHA-256 does not match. If no trusted source identity is available, report the source-reproducibility block instead of guessing.

A checkpoint that intentionally omits a private/copyrighted source binary may still preserve translation work, but it is not self-contained for source-dependent review. Record that limitation explicitly. Once the exact private source is reattached, recover the whole corpus in one pass rather than repeatedly asking for individual chapters.

## Chapter states

The workflow uses the states declared by `agent-manifest.json.chapter_states`:

```text
pending -> extracted -> translated -> reviewed
```

Operational meanings:

- `pending`: the chapter is known but its usable extracted source is not yet ready;
- `extracted`: the source chapter artifact exists and is ready for translation;
- `translated`: a complete translation artifact exists, but the required independent literary review has not yet produced an accepted PASS for that artifact;
- `reviewed`: the current canonical translation artifact has passed review under `docs/TRANSLATION.md` and the Orchestrator has completed the required state acceptance/validation transition.

Never use `reviewed` as a convenience label for a translation that merely looks fluent or complete.

## Sequential chapter policy

Translate chapters sequentially by default:

```text
T1 -> R1 -> state commit -> T2 -> R2 -> state commit -> T3 ...
```

Do not translate multiple chapters concurrently by default.

Later chapters can depend on terminology, character voice, ambiguity, and continuity decisions established during earlier reviewed chapters. Parallel chapter translation requires a separate explicit future mode with conflict handling and state-version checks; it is not part of this contract.

## Selecting the next chapter

Unless the user explicitly requests another scope:

1. complete the source corpus preflight for the active book;
2. inspect `progress.json` in chapter order;
3. choose the first chapter whose state is not `reviewed`;
4. verify the source artifact referenced for that chapter exists;
5. repair invalid extraction/state before dispatching literary work.

Do not retranslate a reviewed chapter without a concrete reason. If a reviewed translation changes materially, move it back to the appropriate non-reviewed state until the changed artifact passes review again.

## Translator context pack

To dispatch the `translator` role:

1. read `agent-manifest.json` from the book's workflow revision;
2. load only the contracts named by the `translator` context profile;
3. add the task-specific durable inputs:
   - metadata relevant to language/book identity and workflow provenance;
   - current `glossary.md`;
   - current `style-guide.md`;
   - current chapter source;
   - the smallest prior excerpt/context needed for continuity;
   - expected translation artifact/path;
   - exact task/output request.

Do not include `docs/AGENT_SETUP.md` or this orchestration contract in the Translator context merely because the Orchestrator has them loaded.

If the chapter directly continues a scene and prior text is materially necessary, include the necessary bounded context. Otherwise prefer durable glossary/style decisions plus a small continuity excerpt over entire prior chapters.

## Accepting a Translator result

The Translator returns a complete chapter artifact plus any proposals/warnings defined by `docs/TRANSLATION.md`.

Before changing state to `translated`, the Orchestrator verifies that:

- the expected translation artifact exists or was returned completely;
- it is non-empty;
- it corresponds to the selected chapter;
- proposed global decisions are handled explicitly rather than silently committed by the worker.

After accepting the artifact, persist `translated` and any accepted global decisions through the single-writer path.

## Reviewer context pack

To dispatch the `reviewer` role:

1. load only the contracts named by the `reviewer` context profile from the same book workflow revision;
2. include the current source chapter;
3. include the current canonical translation artifact;
4. include current glossary and style-guide decisions;
5. include only bounded continuity context required to judge the passage;
6. request one of the outcome categories defined by `docs/TRANSLATION.md`.

Do not pass the Translator's hidden reasoning or justification.

## Review/state boundary

The Reviewer returns either `PASS` or `CORRECTIONS_REQUIRED` under `docs/TRANSLATION.md`.

```text
translated artifact
  -> Reviewer under docs/TRANSLATION.md
  -> CORRECTIONS_REQUIRED: remain translated
       -> apply/obtain corrections
       -> review corrected artifact again
  -> PASS: Orchestrator accepts valid proposals
       -> structural validation while still translated
       -> persist reviewed
       -> validate the resulting state
```

If the outcome is `CORRECTIONS_REQUIRED`, do not mark the chapter reviewed. Apply or obtain the corrections through the appropriate role boundary and re-run independent review on the corrected artifact until a Reviewer returns `PASS`.

A `PASS` is necessary but not sufficient for the durable state transition. Before persisting `reviewed`, the Orchestrator must also ensure the reviewed artifact is the canonical artifact, required files exist, accepted global decisions have been applied consistently, and structural state validates.

## Context freshness

A worker result is valid only for the durable state it was given.

When hashes/revisions are available, associate a dispatch with:

- `metadata.json.workflow.resolved_revision`;
- current chapter/progress state;
- current glossary state;
- current style-guide state;
- the current source/translation artifacts relevant to that role.

If shared state changes materially before a result is accepted, rebuild or re-check the result against the new state rather than accepting it blindly.

## Resume behavior

Repository state is more authoritative than chat history.

For an existing book:

1. select the installation root and book deterministically;
2. read the book's `metadata.json.workflow` before selecting the execution contract;
3. use its `resolved_revision` when available, otherwise the most specific recorded requested ref;
4. read `agent-manifest.json` from that recorded workflow revision;
5. load the `orchestrator` context profile from that same revision;
6. read `progress.json`, `glossary.md`, `style-guide.md`, and `source-manifest.json` when present;
7. run the source corpus preflight and repair the complete corpus if required;
8. inspect only the bounded source/translation context required for the next operation;
9. continue from the first non-`reviewed` chapter unless the user explicitly requests another scope.

Do not use a successful lookup of the next chapter as a substitute for corpus preflight. A later missing source artifact is a repository-integrity defect even when the immediate chapter happens to exist.

`<installation-root>/.book-translator-install.json` describes the workflow currently installed at that root, but it does not override an existing book's recorded workflow provenance.

If the installed revision differs from the book revision, do not silently rewrite the book's provenance or execute it under the newer contract. Load the recorded workflow revision when possible; otherwise state the exact reproducibility limitation before making state-changing claims.

Different books in one workspace may legitimately retain different workflow revisions.

## Explicit workflow upgrade

A workflow upgrade for an existing book is an explicit state transition, never an incidental side effect of installation changes.

Before upgrading:

1. record the current book provenance and validate current structure;
2. resolve the requested new workflow revision;
3. inspect compatibility relevant to the book's durable schema/state;
4. update the book provenance only as part of the explicit upgrade;
5. reload the `orchestrator` profile from the new revision;
6. validate the book again before continuing chapter work.

If compatibility cannot be established safely, keep the existing book revision instead of guessing.

## Structural validation

Structural validation verifies repository consistency, not literary fidelity.

When Python is available, prefer:

```bash
python scripts/book.py validate <book-slug>
```

Use the equivalent helper path for a namespaced installation.

At minimum, validation must protect these invariants:

- the preserved source declared by metadata exists when the workspace is expected to be self-contained;
- chapter numbers/slugs are unique and ordered;
- chapter count matches progress entries;
- every extracted-or-later chapter has its source artifact;
- the actual extracted corpus is complete relative to `progress.json`, not just complete through the next chapter;
- translated-or-reviewed chapters have non-empty translation artifacts;
- glossary and style guide exist for active books;
- workflow provenance is present for new books;
- source identity/integrity state is retained when `source-manifest.json` exists;
- no translation artifact replaced the preserved source.

Structural validation cannot substitute for a Reviewer `PASS` under `docs/TRANSLATION.md`.

## Failure handling

- If translation fails or is incomplete, do not advance the chapter to `translated`.
- If review fails to run, errors, or returns `CORRECTIONS_REQUIRED`, keep the chapter `translated`.
- If corpus preflight or validation fails, stop state advancement, repair durable state in one batch when possible, and validate again before starting the next chapter.
- If the exact source is unavailable, do not silently use a same-title/same-name replacement.
- If the required workflow revision cannot be loaded, do not silently substitute another revision and claim exact reproducibility.
- If isolated workers are unavailable, use `single_agent_bounded_context` rather than asking the user to manually orchestrate roles.

## Building output

Build output only from canonical translation artifacts in chapter order.

Markdown is the transparent default. When Python is available:

```bash
python scripts/book.py build <book-slug>
```

The default build path requires chapters to be `reviewed`. A preview containing merely translated chapters should be produced only when the user explicitly requests an unreviewed preview and the output is clearly identified as such.

Only claim EPUB, DOCX, PDF, or another deliverable when the active environment actually created and checked that artifact.

## Book completion

Before declaring a book complete, the Orchestrator verifies that:

1. every intended chapter is present and in real reading order;
2. every intended chapter is `reviewed` through the review/state boundary above;
3. structural validation succeeds;
4. glossary and style-guide decisions are consistent across the book;
5. selected difficult, ambiguous, emotionally important, or plot-critical passages are re-checked under `docs/TRANSLATION.md` when a book-level consistency check warrants it;
6. requested output artifacts are ordered/checked if output was requested;
7. the preserved source remains unchanged;
8. workflow provenance remains intact;
9. source-corpus integrity/provenance remains reproducible or any intentional private-source limitation is explicitly recorded.

A built output file alone is not evidence that the book is complete.

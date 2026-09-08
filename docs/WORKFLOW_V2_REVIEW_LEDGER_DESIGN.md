# Workflow v2 — Review ledger, hashes and stale-review detection

Issue: #9
Branch: `feature/workflow-v2-review-ledger`
Base while #8 is pending: `feature/workflow-v2-claims-cas`
Final PR target: `refactor/workflow-engine-v2`
Date: 2026-09-06

## Purpose

Make literary review evidence machine-verifiable and bind every Reviewer outcome to the exact source artifact, translation artifact, workflow revision, and durable state context that were reviewed.

Repository state remains authoritative. `progress.json` continues to represent lifecycle only; review evidence is stored separately and is resolved against current artifact hashes before a chapter may be treated as currently reviewed.

## Scope

In scope:

- authoritative `review-ledger.json` machine state;
- immutable append-only review records inside the versioned ledger;
- SHA-256 identity for source and translation artifacts;
- workflow/review-contract provenance;
- `PASS` and `CORRECTIONS_REQUIRED` evidence;
- deterministic correction-round history;
- deterministic duplicate/supersession validation;
- current/stale/missing review resolution;
- compare-and-swap ledger updates using #8 storage primitives;
- a promotion gate that permits `progress.json.status=reviewed` only with current PASS evidence;
- structural validation of ledger-enabled reviewed state;
- stable domain and CLI surfaces for #10, #12, and #21.

Out of scope:

- generated Markdown review reports (#21);
- deterministic status/resume/context descriptors (#10);
- atomic book finalization (#12);
- private-source portability policy (#11);
- explicit parallel translation scheduling (#15);
- workflow migration tooling (#16);
- GitHub-specific storage/backend behavior (#17).

## Core invariants

1. A PASS applies only to the exact source and translation bytes that were reviewed.
2. Changing either artifact makes prior evidence stale without rewriting or deleting history.
3. `progress.json` never serves as proof that review occurred.
4. A chapter cannot be promoted to `reviewed` unless current PASS evidence exists at the moment of the CAS state transition.
5. Review records are append-only. Existing records are never edited in place.
6. One deterministic current outcome is resolvable for every unit/artifact version.
7. Correction history remains auditable across translation revisions.
8. Concurrent ledger writers cannot silently overwrite each other.
9. Ledger-enabled books fail closed when authoritative review evidence is missing or malformed.
10. Handwritten Markdown audit files are never authoritative completion evidence.

## Durable layout

The ledger is book-local shared workflow state:

```text
books/<book-slug>/review-ledger.json
```

The ledger is a single CAS-protected document rather than one mutable file per unit. The current workflow is sequential by default, so one document keeps ordering, duplicate detection, reporting, and finalization deterministic without introducing a second coordination index.

The logical record history is append-only even though the JSON document itself is replaced through compare-and-swap when a record is appended.

## Ledger enablement and backward compatibility

New books created by the #9-capable workflow opt into authoritative review evidence explicitly in metadata:

```json
{
  "workflow": {
    "review_evidence": "review-ledger-v1"
  }
}
```

For such books, `review-ledger.json` is created during initialization with an empty record set.

This marker prevents the current runtime from projecting #9 requirements backward onto books pinned to workflow revisions that predate the review ledger. Existing books without `workflow.review_evidence` retain the review semantics of their recorded workflow revision until an explicit migration/upgrade establishes ledger evidence.

For a ledger-enabled book, deleting `review-ledger.json` is a validation error; absence is not interpreted as legacy mode.

## Ledger schema

Version 1 ledger shape:

```json
{
  "schema_version": 1,
  "book_slug": "example-book",
  "next_sequence": 4,
  "records": [
    {
      "record_id": "0123456789abcdef0123456789abcdef",
      "sequence": 1,
      "unit_id": "chapter-000001",
      "outcome": "CORRECTIONS_REQUIRED",
      "source_sha256": "<64 lowercase hex>",
      "translation_sha256": "<64 lowercase hex>",
      "workflow_revision": "<immutable workflow revision>",
      "review_contract_revision": "docs/TRANSLATION.md@<workflow revision>",
      "reviewer_session_id": "review-session-a",
      "reviewed_at": "2026-09-06T00:00:00Z",
      "state_revision": "<progress.json revision observed for review>",
      "review_commit": null,
      "correction_round": 1,
      "supersedes_record_id": null
    }
  ]
}
```

Top-level rules:

- `schema_version` is required and must be supported explicitly;
- `book_slug` must match the active book;
- `next_sequence` is a positive integer;
- `records` is an array;
- record IDs are unique;
- sequence numbers are unique and strictly increasing in stored order;
- for an empty ledger, `next_sequence == 1`;
- otherwise `next_sequence == max(sequence) + 1`.

Record rules:

- `record_id`: 32 lowercase hexadecimal characters;
- `sequence`: positive integer assigned by the successful CAS append;
- `unit_id`: canonical `chapter-[0-9]{6}` identity from validated progress state;
- `outcome`: exactly `PASS` or `CORRECTIONS_REQUIRED`;
- source/translation hashes: lowercase SHA-256;
- `workflow_revision`: non-empty immutable workflow provenance for the book;
- `review_contract_revision`: deterministic identifier for the exact literary review contract selected by that workflow revision;
- `reviewer_session_id`: non-empty session identity associated with the reviewer claim;
- `reviewed_at`: UTC RFC 3339 timestamp;
- `state_revision`: exact `progress.json` storage revision observed when the review evidence was recorded;
- `review_commit`: non-empty string or null;
- `correction_round`: non-negative integer;
- `supersedes_record_id`: prior record ID for the same unit or null when no earlier record exists.

Unknown additive fields remain preserved under the existing Workflow v2 schema policy.

## Workflow and review-contract identity

`workflow_revision` is the immutable workflow revision recorded for the book. Ledger-enabled recording refuses to fabricate it when the book lacks immutable resolved workflow provenance.

For the current v3 literary contract, `review_contract_revision` is:

```text
docs/TRANSLATION.md@<workflow_revision>
```

The workflow commit makes this identifier content-addressable through Git history without requiring a second runtime hash protocol. A future manifest may select another literary contract path; the identifier then uses the actual selected contract path plus the same immutable workflow revision.

The review contract identifier is evidence metadata. Current-review resolution requires it to match the contract expected by the book's recorded workflow revision.

## Artifact identity

The review domain computes hashes from canonical files; callers do not provide trusted artifact hashes.

For one chapter, resolution uses the `source_path` and `translation_path` from validated `progress.json` and computes SHA-256 over exact file bytes.

Rules:

- missing source is a structural error;
- missing or empty translation cannot receive review evidence;
- source and translation paths must already satisfy Workflow v2 safe relative-path rules;
- text normalization, newline normalization, Unicode normalization, or Markdown parsing is not performed before hashing;
- any byte change produces a new artifact identity and invalidates prior current evidence.

This deliberately makes stale-review detection mechanical rather than interpretive.

## Reviewer claim boundary

Recording a review outcome requires an active claim for the same unit with:

- `role=reviewer`;
- matching `reviewer_session_id` / claim `session_id`;
- matching book workflow revision.

The review writer reads and validates the claim immediately before appending evidence. An absent, expired-but-not-cleaned, foreign-session, wrong-role, or changed claim blocks recording.

Lease expiry alone does not transfer ownership; #8 cleanup rules remain authoritative.

The Orchestrator may persist the Reviewer result on behalf of the worker, but it must present the reviewer's session identity and current reviewer claim. This preserves the logical Reviewer/Orchestrator boundary without allowing unclaimed PASS fabrication.

## Append and compare-and-swap flow

`ReviewLedger` / `ReviewLedgerManager` uses `WorkflowStateRepository` and #8 CAS primitives.

For each record append:

1. load and validate metadata, progress, and canonical unit identity;
2. verify the active reviewer claim and session ownership;
3. read source/translation bytes and compute current hashes;
4. read `review-ledger.json` and retain its storage revision;
5. validate the complete ledger, including sequence/ID/supersession invariants;
6. derive the next record, correction round, and supersession link;
7. append exactly one immutable record in memory using `next_sequence`;
8. write the whole ledger using `write_if_version(expected_revision)`;
9. if the ledger changed concurrently, fail with a conflict; do not silently retry with the stale Reviewer result.

For a correctly initialized ledger-enabled book, the ledger already exists. A legacy/migration path that creates a ledger is outside ordinary review recording and belongs to explicit upgrade/migration work.

The caller may retry only after re-reading current repository state and re-validating that the Reviewer result still applies.

## Deterministic supersession

Each unit has one linear review history.

For a newly appended record:

- `supersedes_record_id` is null only when the unit has no prior record;
- otherwise it must point to the immediately preceding record for that same unit by sequence;
- the target must exist, belong to the same unit, and have a smaller sequence;
- records may not fork the supersession chain;
- a record may not supersede itself or a record from another unit.

The ledger validator rejects duplicate record IDs, duplicate sequences, broken supersession targets, forks, non-monotonic stored ordering, and inconsistent `next_sequence`.

Because the current record is always the highest-sequence record for the relevant artifact identity, duplicate/superseded history cannot yield two ambiguous current PASS states.

## Current review resolution

Resolution is computed; it is not stored as a second mutable summary.

For each canonical unit, the resolver computes:

- current source SHA-256;
- current translation SHA-256, or translation absence;
- expected workflow revision;
- expected review-contract revision;
- unit review history ordered by sequence.

A record is an exact current-artifact match only when all of these match current state:

- `unit_id`;
- `source_sha256`;
- `translation_sha256`;
- `workflow_revision`;
- `review_contract_revision`.

Among exact matches, the highest `sequence` determines the current outcome.

Resolution states:

- `pass`: exact current-artifact record exists and latest exact outcome is `PASS`;
- `corrections_required`: exact current-artifact record exists and latest exact outcome is `CORRECTIONS_REQUIRED`;
- `stale`: the unit has review history but no exact record matches current source/translation/workflow/contract identity;
- `missing`: the unit has no review history;
- `untranslated`: no canonical non-empty translation artifact exists, so review coverage cannot exist.

Older exact-match records remain history. A later `CORRECTIONS_REQUIRED` for the same artifact supersedes an earlier PASS and removes current PASS coverage. A later PASS restores coverage for that exact artifact.

## Stale review semantics

No watcher or state rewrite is required when an artifact changes.

Example:

1. translation hash `A` receives PASS;
2. `progress.json` may be promoted to `reviewed` after the PASS gate;
3. translation file changes to hash `B`;
4. resolver finds no exact PASS for `B`;
5. current review state is `stale`;
6. structural validation rejects `status=reviewed` until the new artifact is reviewed and promoted again.

The historical PASS for hash `A` remains in the ledger as audit evidence but contributes zero current coverage.

The same rule applies when the source hash or review contract identity changes.

## Correction-round semantics

`correction_round` counts correction cycles entered for one unit.

Deterministic rules:

- a first PASS with no prior `CORRECTIONS_REQUIRED` uses round `0`;
- the first `CORRECTIONS_REQUIRED` record uses round `1`;
- subsequent review records after that correction request remain round `1` until another `CORRECTIONS_REQUIRED` begins a new correction cycle;
- each later `CORRECTIONS_REQUIRED` increments the previous maximum correction round by one;
- a PASS never increments the round.

Thus a typical sequence is:

```text
CORRECTIONS_REQUIRED round=1 hash=A
PASS                 round=1 hash=B
CORRECTIONS_REQUIRED round=2 hash=B
PASS                 round=2 hash=C
```

All records remain linked through `supersedes_record_id`.

## Review commit and state revision

`state_revision` records the exact `progress.json` storage revision observed when evidence was appended. It is audit context, not by itself a validity key: unrelated progress changes must not make an otherwise exact source/translation PASS stale.

`review_commit` records the relevant Git commit when available. CLI resolution is explicit `--review-commit` first, then best-effort repository `HEAD`; absence is stored as null.

Artifact hashes and workflow/contract identity determine current review validity. State revision and commit provide traceability.

## Promotion to `reviewed`

`progress.json` remains lifecycle state only.

The only supported #9 promotion path is a domain operation exposed by `book.py accept-review`.

For one unit it:

1. reads metadata, progress, ledger, source, and translation from current repository state;
2. requires the chapter lifecycle state to be `translated` or already `reviewed` for an idempotent no-op check;
3. resolves current review evidence;
4. requires resolution state `pass`;
5. verifies the PASS still matches current artifact/workflow/contract identity;
6. changes only that chapter status to `reviewed` in memory;
7. writes `progress.json` with `write_if_version` against the exact revision read in step 1;
8. on conflict, leaves current state unchanged and requires a fresh retry.

The operation does not modify the ledger.

If the chapter is already `reviewed` and current PASS remains valid, `accept-review` is idempotent. If it is `reviewed` but evidence is stale/missing, the command fails rather than claiming success.

A `CORRECTIONS_REQUIRED` record never promotes lifecycle state.

## Validation behavior

For a book with `metadata.workflow.review_evidence == review-ledger-v1`, `book.py validate` additionally requires:

- `review-ledger.json` exists and passes schema/domain validation;
- ledger `book_slug` matches metadata/progress book identity;
- every `status=reviewed` chapter resolves to current `pass` evidence;
- a stale, missing, corrections-required, untranslated, malformed, or ambiguous ledger state blocks structural validity;
- handwritten Markdown review/audit files are ignored for authoritative coverage.

Validation does not automatically rewrite a stale reviewed chapter to `translated`. It reports the inconsistency and stops state advancement. The Orchestrator can then explicitly restore the correct lifecycle state and re-review as required.

Books without the review-evidence marker continue to use the semantics of their pinned legacy workflow and are not silently migrated by current `book.py validate`.

## Domain API

A focused module is added under `scripts/workflow_v2/reviews.py`.

Primary domain types/functions:

```text
ReviewLedgerManager.record(...)
ReviewLedgerManager.resolve_unit(...)
ReviewLedgerManager.resolve_all(...)
ReviewLedgerManager.accept_review(...)
ReviewResolution
ReviewRecordResult
ReviewError
ReviewConflict
ReviewClaimError
ReviewEvidenceError
```

The manager depends on repository/storage abstractions and filesystem artifact reads supplied by the book/runtime adapter. It does not depend on GitHub APIs or chat state.

`resolve_all()` is the stable machine-backed input for #10, #12, and #21. Consumers do not parse Markdown audit files or duplicate review-resolution rules.

## CLI surface

`book.py` exposes:

```bash
python scripts/book.py review-record <book-slug> <chapter> \
  --outcome PASS|CORRECTIONS_REQUIRED \
  --session-id <reviewer-session> \
  [--review-commit <commit>] \
  [--json]

python scripts/book.py reviews <book-slug> [--json]

python scripts/book.py accept-review <book-slug> <chapter> [--json]
```

`review-record`:

- resolves exactly one chapter selector in #9;
- requires active matching reviewer claim;
- hashes canonical source/translation artifacts itself;
- appends one CAS-protected record;
- emits record identity, sequence, hashes, outcome, correction round, and ledger revision.

`reviews`:

- performs no mutation;
- returns deterministic canonical-unit order;
- reports current resolution plus sufficient record history/identifiers for generated reporting;
- JSON output is stable and machine-readable.

`accept-review`:

- performs the guarded lifecycle promotion described above;
- uses CAS on `progress.json`;
- returns the accepted unit and resulting progress revision;
- is idempotent only when existing `reviewed` state still has current PASS evidence.

## Orchestration flow

The normal sequential chapter path becomes:

```text
translator claim
  -> translation artifact
  -> progress translated
  -> release translator claim
  -> reviewer claim
  -> source-comparison review
  -> review-record CORRECTIONS_REQUIRED
       -> release reviewer claim
       -> correction flow
       -> reviewer claim again
       -> fresh review
  -> review-record PASS
  -> accept-review
  -> release reviewer claim
  -> next chapter
```

The exact role transitions remain governed by `docs/ORCHESTRATION.md` and literary criteria by `docs/TRANSLATION.md`.

A reviewer PASS returned in chat is not authoritative until it is recorded in the ledger and accepted through the promotion gate.

## Error handling

Fail closed on:

- missing/malformed ledger for ledger-enabled books;
- missing immutable workflow provenance;
- invalid/missing artifact paths;
- empty/missing translation;
- foreign/missing/wrong-role reviewer claim;
- malformed review record/history;
- duplicate record IDs/sequences;
- broken/forked supersession chains;
- ledger CAS conflict;
- progress CAS conflict during promotion;
- stale/missing/current `CORRECTIONS_REQUIRED` evidence during promotion.

No failure path silently rewrites another session's ledger/progress state.

## Concurrency boundary

The ledger is shared mutable state and uses the strong CAS semantics established by #8.

Two writers may read the same ledger revision, but only one may commit a replacement for that revision. The loser receives `StorageVersionConflict` / a review-domain conflict and must re-read state before deciding whether the Reviewer result is still valid.

No automatic blind retry is performed because a concurrent review record may change the authoritative current outcome or correction round.

The single-ledger design intentionally serializes review-record commits. This is acceptable under the current sequential policy and remains correct if future parallel review is enabled; throughput optimization can later replace the storage layout behind the same resolver API if measurements justify it.

## Initialization changes

For books initialized by the #9-capable workflow:

- metadata adds `workflow.review_evidence = review-ledger-v1`;
- `review-ledger.json` is created with:

```json
{
  "schema_version": 1,
  "book_slug": "<book-slug>",
  "next_sequence": 1,
  "records": []
}
```

Initialization remains deterministic and does not create handwritten audit files.

## Testing strategy

TDD coverage must include:

- strict ledger/record schema validation;
- unique IDs/sequences and `next_sequence` invariants;
- valid linear supersession and rejection of broken/forked chains;
- deterministic correction-round derivation;
- PASS for exact source/translation hashes;
- translation change -> stale;
- source change -> stale;
- workflow/review-contract change -> stale;
- later `CORRECTIONS_REQUIRED` supersedes an earlier PASS for the same artifact;
- later PASS restores current coverage;
- missing translation -> untranslated / no record allowed;
- foreign, missing, expired-but-not-cleaned, and wrong-role reviewer claims block recording;
- concurrent ledger writers from one expected revision -> exactly one succeeds;
- CAS conflict is surfaced without blind retry;
- missing/stale/mismatched PASS blocks `accept-review`;
- current PASS permits CAS promotion;
- progress CAS conflict leaves state unmodified;
- already-reviewed + current PASS is idempotent;
- already-reviewed + stale evidence fails;
- ledger-enabled `validate` rejects reviewed state without current PASS;
- legacy books without the marker preserve prior validation behavior;
- CLI record/list/accept JSON is deterministic;
- review coverage requires no Markdown audit parsing;
- full Python 3.10 and 3.12 regression suites remain green.

## Acceptance mapping

Issue #9 acceptance criteria map directly:

- **Changing reviewed translation invalidates review:** exact-byte hash resolution becomes `stale`.
- **Coverage without Markdown:** `resolve_all()` computes machine coverage from ledger + current artifacts.
- **No reviewed promotion without PASS:** `accept-review` requires exact current PASS and CAS-updates progress.
- **Correction rounds auditable:** immutable records preserve round, hashes, sequence, state revision, commit, and supersession.
- **No ambiguous current PASS:** unique sequence plus linear supersession and highest-sequence exact-match resolution.
- **Sufficient for #21:** resolver exposes current/stale/missing outcome, hashes, workflow/contract provenance, review revision/commit, and history identifiers.

## Branching and integration

Development occurs on `feature/workflow-v2-review-ledger` stacked on `feature/workflow-v2-claims-cas` while #24/#8 is pending.

The initial PR should target `feature/workflow-v2-claims-cas` so its diff is #9-only. After #23 and #24 are integrated into `refactor/workflow-engine-v2`, retarget #9 to the integration branch and rerun the complete CI matrix before merge.

Do not merge #9 before its #7/#8 dependencies are integrated and the PR has been retargeted/reverified.
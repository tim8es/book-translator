# Workflow v2 Atomic Finalize — Design

Issue: #12
Branch: `feature/workflow-v2-finalize`
Base: `refactor/workflow-engine-v2` at `b5388b89108e37f63ec76869662a57632a0afa68`
Target: `refactor/workflow-engine-v2`

## Goal

Provide one idempotent `book.py finalize <slug>` completion gate that mechanically proves the book is ready, promotes all eligible chapters to `reviewed` in one durable CAS, generates deterministic completion projections from authoritative state, and can recover safely after process interruption.

## Non-goals

- Do not build EPUB output (#14).
- Do not introduce schema migrations (#16), GitHub API transport (#17), or parallel proposal reconciliation (#15).
- Do not make generated Markdown authoritative.
- Do not require GitHub Actions.
- Do not merge or otherwise change `main`.

## Architectural choice

Use a transient durable finalization marker plus a short-lived book coordination mutex. Final lifecycle promotion is one CAS write of `progress.json`; generated reports are deterministic projections written only after promotion.

Rejected alternatives:

1. Sequentially call `accept_review()` for each chapter and roll back on failure. This can expose partially promoted lifecycle state and cannot guarantee rollback after a crash.
2. Introduce a permanent finalization state machine as a new authoritative database. This adds unnecessary durable state and complicates future migration/backend work.

## Durable coordination

### Coordination mutex

Path: `.workflow/coordination-lock.json`.

Purpose: serialize only the short admission transition between ordinary unit-claim acquisition and entering finalization. It is not held for the full finalization operation.

Schema fields:

- `schema_version`: 1
- `lock_id`: 32 lowercase hex characters
- `operation`: `claim_admission` or `finalize_admission`
- `session_id`: non-empty string
- `acquired_at`: UTC timestamp
- `expires_at`: UTC timestamp, strictly later than `acquired_at`

The mutex is obtained with `create_if_absent`. If present and expired, a contender may remove it only with `delete_if_version` and retry. A live mutex is a deterministic conflict. The default lease is short (60 seconds); tests use an injected clock.

### Claim admission

`ClaimManager.acquire()` changes as follows:

1. Acquire the coordination mutex as `claim_admission`.
2. While holding it, reject acquisition if `.workflow/finalization.json` exists.
3. Run the existing deterministic unit-conflict preflight and create the requested unit claims.
4. Release the coordination mutex with version-checked delete in a `finally` path.
5. Existing range rollback semantics remain unchanged for ordinary create conflicts/errors.

Because finalization admission uses the same mutex, a claim cannot appear after finalization has installed its marker.

### Finalization marker

Path: `.workflow/finalization.json`.

Schema fields:

- `schema_version`: 1
- `lock_id`: 32 lowercase hex characters
- `book_slug`: exact progress `book_slug`
- `workflow_revision`: immutable metadata `workflow.resolved_revision`
- `base_progress_revision`: backend storage revision observed before promotion
- `candidate_progress_sha256`: SHA-256 of the deterministic serialized all-reviewed candidate progress bytes
- `phase`: `preparing` or `promoted`
- `promoted_progress_revision`: null in `preparing`; actual backend storage revision after successful progress CAS in `promoted`
- `session_id`: session that first created the marker (audit only; recovery is not owner-bound)
- `started_at`: UTC timestamp

The marker is transient authoritative coordination state only while finalization is unfinished. It is removed after successful post-validation and report writes.

`candidate_progress_sha256` is deliberately content-based rather than a predicted storage version. `StorageBackend` revision tokens are opaque and future backends (#17) need not use content hashes.

## Deterministic document identity

Expose one repository serialization helper used by both ordinary repository writes and finalize candidate hashing. Finalize must not duplicate JSON formatting rules or infer backend revision tokens.

The helper validates through the declared schema and returns the exact canonical UTF-8 bytes that `create`/`write_if_version` would persist. Candidate SHA-256 is computed from those bytes.

For recovery, the current raw `progress.json` storage bytes are hashed and compared with `candidate_progress_sha256` after schema validation.

## Finalization state transition

### Initial preflight

Before acquiring locks, finalize computes a read-only candidate from current state and verifies:

- structural validation succeeds;
- source corpus state is exactly `verified`;
- metadata uses immutable `workflow.resolved_revision` and ledger review evidence;
- every chapter has a non-empty translation artifact;
- every unit resolves to current `PASS` for exact source/translation/workflow/review-contract identity;
- no unsupported lifecycle state exists.

The candidate progress document is a deterministic deep copy of current progress with every chapter status set to `reviewed`. No durable mutation occurs during initial preflight.

Filesystem CLI pre/postflight reuses the same structural + corpus normalization path already used by `status`/`resume`, including explicit `private_external` source behavior. Finalize domain logic receives verified corpus/preflight data rather than reimplementing filesystem-specific corpus rules.

### Admission and revalidation

Finalize then:

1. Acquires the coordination mutex as `finalize_admission`.
2. Creates `.workflow/finalization.json` with `create_if_absent`, or adopts an existing compatible marker for recovery.
3. While still holding the mutex, verifies that there are zero active unit claims.
4. Releases the coordination mutex.

After the marker exists, new unit claims are rejected by claim admission.

Finalize immediately re-reads metadata, progress, ledger, artifacts and corpus status. Any failed business precondition before progress promotion releases the finalization marker (version-checked) so translation/review work can continue.

### One-CAS lifecycle promotion

Recovery cases are resolved from marker phase, backend revision, and candidate content hash:

- `phase=preparing` and current progress revision == `base_progress_revision`: revalidate exact PASS/artifact identities, then CAS-write the full candidate document once;
- `phase=preparing` and current raw progress SHA-256 == `candidate_progress_sha256`: a previous process committed progress but crashed before updating the marker; promote marker phase with CAS using the current actual backend revision;
- `phase=promoted` and current progress revision == `promoted_progress_revision` and content hash == `candidate_progress_sha256`: promotion is already complete; do not write progress again;
- any other progress revision/content combination: fail closed as a concurrent/unexpected state change. Do not claim completion.

After a successful progress CAS, finalize CAS-updates the marker to `phase=promoted` and records the actual backend revision returned by storage. Crash between those two writes is covered by the content-hash recovery case above.

The candidate write changes all remaining `translated` chapters to `reviewed` together. Already-`reviewed` chapters remain `reviewed`. No sequential per-chapter promotion is used.

Immediately before the progress CAS, finalize rechecks zero active claims and current PASS identities while the finalization marker blocks new claim admission.

## Crash and retry semantics

- Crash before the finalization marker: no finalize mutation exists; retry starts normally.
- Crash after marker creation but before progress CAS: retry adopts the compatible `preparing` marker, sees current progress at `base_progress_revision`, revalidates, and performs the CAS.
- Crash after progress CAS but before marker phase update: retry matches candidate content SHA-256, records the actual current backend revision in the marker, and continues.
- Crash after marker phase update: retry verifies `promoted_progress_revision` + candidate content hash and continues without another lifecycle write.
- Crash while writing generated reports: progress may already be fully reviewed, but reports are non-authoritative; retry regenerates all reports from current authoritative state.
- Crash after all reports but before marker deletion: retry reproduces the same report bytes, verifies postconditions, then removes the marker.

A successful rerun with unchanged state performs no substantive progress or report changes.

## Orchestration visibility and mutation admission

An unfinished finalization must be visible to a fresh session.

`StatusResolver.status()` reads `.workflow/finalization.json` when present and exposes a deterministic `finalization` field. Inactive status is `{ "active": false }`. Active status includes `active=true`, marker `phase`, `workflow_revision`, and `started_at`. A malformed marker or workflow/book mismatch makes status invalid.

`StatusResolver.resume()` gives an active, valid finalization marker priority over ordinary unit work and returns `operation="finalize"` with orchestrator context. This ensures a fresh session resumes completion instead of trying per-chapter `accept_review` after a crash.

`ReviewLedgerManager.accept_review()` rejects lifecycle promotion while a finalization marker exists. Review recording already requires a live reviewer claim, and no new reviewer claim can be admitted once finalization starts.

Out-of-band direct artifact edits are not made impossible by the storage abstraction; instead, exact source/translation hashes are revalidated immediately before the progress CAS and again during post-validation. Any such edit invalidates completion and prevents successful marker removal.

## Completion snapshot

Add backend-neutral `workflow_v2.finalize` logic that builds one completion snapshot from:

- current metadata/progress revisions;
- verified corpus status;
- current claim count;
- `build_review_report_snapshot()` from #21;
- current lifecycle counts;
- exact workflow revision.

Snapshot fields include:

- schema identifier (`completion-report-v1`);
- `book_slug`;
- `workflow_revision`;
- state revisions (`metadata`, `progress`, `review_ledger`);
- lifecycle counts;
- corpus reproducibility state/mode;
- review summary and PASS coverage;
- quality-gate booleans.

No wall-clock generation timestamp is included.

## Generated reports

All three files are projections of the same post-promotion authoritative state:

1. `REVIEW_REPORT.md` — use #21 `render_review_report_markdown()` from the same review snapshot.
2. `STATE.md` — concise deterministic lifecycle/review/corpus/revision summary.
3. `FINAL_QUALITY_GATES.md` — deterministic checklist showing structural validity, verified corpus, zero claims, translation completeness, 100% current PASS review coverage, and all-reviewed lifecycle.

Generated reports are written through existing versioned storage (`create_if_absent` or `write_if_version`). Identical bytes produce no rewrite. A concurrent report edit causes a deterministic conflict rather than blind overwrite.

The report files may be temporarily incomplete as a set if the process crashes between writes. This is acceptable because they are non-authoritative; finalize does not report success or remove its marker until all three canonical files match the current snapshot.

## Post-validation and completion

After progress promotion and report writes, finalize performs a fresh post-validation:

- structural validation remains clean;
- corpus remains verified;
- zero active unit claims;
- every chapter status is `reviewed`;
- every review resolution is current `pass`;
- generated report bytes equal rendering of the fresh snapshot.

Only then is the finalization marker deleted with `delete_if_version` and the command returns success.

## Error handling

### Clean precondition failure

Before lifecycle promotion, expected failures (missing/stale/corrections review, untranslated artifact, invalid corpus/structure, active claims) produce a concise CLI error and leave progress/reports unchanged. If a marker was created during admission, it is released before returning the business failure.

### Conflict / uncertain mutation

Storage CAS conflicts, incompatible recovery marker, unexpected progress revision/content hash, marker phase conflict, or report write conflict fail closed. If progress may already have been promoted, retain the finalization marker so a later `finalize` retry performs recovery rather than admitting new claims.

### Stale coordination mutex

An expired coordination mutex can be removed only by version-checked delete. A live mutex is never stolen.

## CLI

Add `book.py finalize <slug>` via a focused `workflow_v2.finalize_cli` adapter.

Optional flags:

- `--session-id`: explicit non-empty session identifier for deterministic/auditable operation; default is a generated UUID when omitted.
- `--json`: emit deterministic completion snapshot/result after success. JSON does not change finalization semantics; finalize still performs the operation.

Expected workflow errors are surfaced as `ERROR: ...` without traceback and exit code 1.

## Schema changes

Extend `SchemaKind` with:

- `COORDINATION_LOCK`
- `FINALIZATION_LOCK`

Both are strict version-1 durable transient documents. No existing metadata/progress schema version is bumped; #16 owns migrations.

The existing `GENERATED_STATE` schema is not used as a new authoritative completion document. `STATE.md` remains a generated projection.

## Testing strategy

Strict TDD slices:

1. Coordination RED/GREEN:
   - claim admission blocked by finalization marker;
   - claim/finalize admission race is serialized by coordination mutex;
   - expired mutex cleanup is version-safe;
   - existing claim rollback/ownership behavior remains green.
2. Finalization preflight RED/GREEN:
   - rejects active claims, invalid/unsealed corpus, untranslated chapters, missing/stale/corrections reviews;
   - no mutation on precondition failure.
3. Atomic promotion RED/GREEN:
   - all chapters promoted by one progress CAS;
   - stale progress conflict cannot partially promote;
   - exact PASS identity rechecked immediately before CAS;
   - backend-neutral candidate hash identity, including crash after progress CAS before marker phase update.
4. Crash recovery RED/GREEN:
   - retry before CAS;
   - retry after CAS;
   - retry after partial report generation;
   - successful rerun is idempotent;
   - `status/resume` exposes and resumes active finalization;
   - direct `accept_review` is blocked while finalization is active.
5. Reports/CLI RED/GREEN:
   - deterministic `STATE.md`, `FINAL_QUALITY_GATES.md`, and regenerated `REVIEW_REPORT.md`;
   - JSON success output;
   - malformed state fails closed without traceback.
6. Extend #18 reliability coverage for interrupted finalize/idempotence in this same PR or a directly adjacent #18 slice before Phase 2 exit.

Every production change requires full Python 3.10/3.12 CI. Final PR audit requires `behind_by=0`, no unresolved review threads, integration target unchanged, feature branch preserved, and `main` unchanged.

## Acceptance mapping

- `book.py finalize <book>`: CLI adapter.
- Book-level lock and claim rejection: finalization marker + serialized coordination admission.
- No active claims / corpus / translation / PASS/hash verification: initial and immediate pre-CAS revalidation.
- Validate before and after promotion: explicit pre/post phases.
- Fresh-session recovery: active marker is visible in status and `resume` selects `finalize`.
- Competing lifecycle promotion: `accept_review` rejects while finalization is active.
- Generated `STATE.md`: deterministic completion snapshot projection.
- Generated final quality report: deterministic completion snapshot projection.
- No partial lifecycle promotion: one CAS of complete `progress.json` candidate.
- Idempotence: base revision + candidate content hash + promoted backend revision recovery, plus byte-identical report writes.
- Failure-path coverage: dedicated TDD and #18 reliability extension.

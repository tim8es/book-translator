# Workflow v2 — Claims, leases and compare-and-swap concurrency

Issue: #8
Branch: `feature/workflow-v2-claims-cas`
Base while #7 is pending: `feature/workflow-v2-state-core`
Final PR target: `refactor/workflow-engine-v2`
Date: 2026-09-05

## Purpose

Make multi-session coordination mechanically safe so the user is not required to schedule translator/reviewer work manually. This design adds durable per-unit claims, lease expiry, auditable release/cleanup, deterministic range-conflict handling, and stronger compare-and-swap semantics for shared mutable state.

The repository remains the authority. Claims are workflow state, not chat/session memory.

## Scope

In scope:

- durable per-unit claims under `.workflow/claims/`;
- translator/reviewer roles;
- session identity, unit identity, base state revision/commit, workflow revision, timestamps, and expiry;
- atomic claim acquisition;
- deterministic overlapping-range rejection;
- release and expired-claim cleanup;
- immutable audit events for release/cleanup;
- version-checked deletion;
- stronger filesystem CAS semantics for shared mutable state;
- CLI operations for claim/list/release/cleanup;
- stable JSON output for later `status`/`resume` integration.

Out of scope:

- review-ledger/hash semantics (#9);
- deterministic resume/context selection (#10);
- private-source mode (#11);
- explicit parallel translation policy (#15);
- GitHub API backend (#17);
- migration commands (#16).

## Durable layout

Active claims are one file per unit:

```text
books/<book-slug>/.workflow/claims/<unit-id>.json
```

A unit has one active claim path regardless of role. Therefore Translator and Reviewer cannot concurrently claim the same unit.

Released or cleaned claims are recorded as immutable events:

```text
books/<book-slug>/.workflow/claim-events/<timestamp>-<event-id>.json
```

Audit events are append-only through `create_if_absent`; they are never the source of current ownership. Current ownership is defined only by `.workflow/claims/`.

## Claim schema

The existing Workflow v2 `claim` schema is extended/used with these durable fields:

- `schema_version`;
- `unit_id`;
- `role`: `translator` or `reviewer`;
- `session_id`;
- `base_revision`: revision token of the shared state used to dispatch work;
- `base_commit`: Git commit when available, otherwise `null`;
- `workflow_revision`;
- `claimed_at`;
- `expires_at`.

Times are UTC RFC 3339 timestamps. Expiry comparison is performed against an injected clock in domain logic so tests are deterministic.

Unknown explicit schema versions remain fatal. No implicit claim migration is introduced.

## Claim acquisition

Single-unit acquisition uses storage `create_if_absent` on the unit claim path. Existing active content yields a conflict; it is never overwritten during acquisition.

Expired claims are still conflicts until an explicit cleanup operation removes them. Acquisition does not silently steal expired claims because cleanup must remain auditable.

### Range acquisition

A requested range is normalized into a sorted unique list of unit IDs before any write.

Algorithm:

1. validate that every requested unit exists and that the range itself is valid;
2. inspect current claims and reject any known overlap before writing;
3. acquire unit claims in deterministic unit order with `create_if_absent`;
4. if any acquisition conflicts, roll back claims already created by this operation using their exact returned revision tokens;
5. report the conflicting unit and create no successful range result unless the entire range is owned.

Rollback failures are surfaced as blocking coordination errors and identify any remaining claim paths. They are never silently ignored.

This provides all-or-nothing behavior for ordinary range acquisition failures without introducing a book-wide permanent mutex.

## Storage contract changes

Add to `StorageBackend`:

```python
def delete_if_version(path: str, expected_version: str) -> None: ...
```

`delete_if_version` must:

- fail with `StorageNotFound` when the path is absent;
- fail with `StorageVersionConflict` when durable content no longer matches the expected revision;
- remove only the revision that was actually read/acquired.

`WorkflowStateRepository` receives the corresponding schema-aware helper where useful, while low-level claim cleanup may operate on the exact stored revision after the document has already been validated.

## Filesystem CAS semantics

Issue #7 introduced SHA-256 revision tokens and stale-write detection but left a small cross-process time-of-check/time-of-use window between the final revision check and `os.replace`.

Issue #8 closes that gap for filesystem mutation operations.

The filesystem backend uses a short-lived internal lock scoped to the logical target path while executing `write_if_version` and `delete_if_version`. The lock is an implementation detail; domain code depends only on the `StorageBackend` protocol.

Required properties:

- lock acquisition is bounded to the mutation operation;
- no persistent lease state is encoded in the lock file;
- stale writers fail with `StorageVersionConflict`;
- temporary/lock artifacts are cleaned after successful operations and normal failures;
- locking is deterministic across processes targeting the same resolved logical path.

The future GitHub backend (#17) will implement the same storage contract using GitHub SHA/conditional mutation semantics rather than filesystem locks.

## Release

Release is ownership-sensitive.

The caller supplies the unit and session identity. Domain logic reads and validates the current claim, verifies the expected owner/session, records an immutable `release` audit event, and removes the active claim using `delete_if_version` with the exact revision that was read.

A stale session cannot release a newer claim because the version-checked delete fails.

Release of a claim owned by another session fails without mutation.

## Expired-claim cleanup

Cleanup is explicit. It does not happen implicitly during acquisition.

For each candidate claim:

1. read and validate the claim;
2. compare `expires_at` with the supplied/injected current time;
3. skip live claims;
4. create an immutable cleanup audit event containing the claim snapshot/revision, cleanup timestamp, and auditable reason;
5. delete the claim using `delete_if_version` with the revision that was inspected.

If the claim changes between inspection and deletion, cleanup fails for that claim rather than deleting a replacement claim.

Cleanup reasons use stable machine-readable values, initially `lease_expired`, with optional human detail kept separate.

## Audit event schema

Claim events include:

- `schema_version`;
- `event_id`;
- `action`: `released` or `cleaned`;
- `unit_id`;
- `claim_revision`;
- `claim`: the validated claim snapshot;
- `occurred_at`;
- `reason`;
- optional `detail`.

Event filenames contain a sortable UTC timestamp plus unique ID; correctness does not depend on filename parsing.

## Domain component

Add `scripts/workflow_v2/claims.py` containing claim-domain operations independent of CLI and filesystem details.

Responsibilities:

- construct/validate claims;
- normalize and validate unit/range requests;
- acquire one or many claims;
- list active claims;
- release owned claims;
- cleanup expired claims;
- produce audit events;
- return structured domain results/errors.

Dependencies are `WorkflowStateRepository`/`StorageBackend` abstractions plus an injected clock. The module does not call GitHub, shell commands, or argparse.

## CLI

Extend `scripts/book.py` with:

```text
book.py claim <book> <unit-or-range> --role <translator|reviewer> --session-id <id> --lease-seconds <n> [...]
book.py claims <book> [--json]
book.py release <book> <unit-or-range> --session-id <id> [--json]
book.py cleanup-claims <book> [--json]
```

Exact flags may be adjusted during implementation when needed for compatibility, but these invariants are fixed:

- machine-readable output is deterministic;
- acquisition reports no success until the whole requested range is acquired;
- conflicts return non-zero exit status;
- cleanup removes only expired claims;
- release requires matching ownership/session;
- CLI delegates coordination semantics to `claims.py` rather than duplicating them.

## Error model

Domain errors distinguish at least:

- invalid unit/range;
- claim conflict;
- ownership mismatch;
- live-claim cleanup attempt/no-op;
- storage version conflict;
- rollback failure;
- invalid claim document.

Storage exceptions remain specific where their identity matters. CLI converts domain/storage failures to stable non-zero errors without stack traces during expected conflict cases.

## Testing strategy

Development is test-first.

### Storage tests

- `delete_if_version` deletes only the expected revision;
- stale version cannot delete replacement content;
- simultaneous stale writers cannot both commit successfully;
- mutation lock artifacts do not remain after ordinary success/failure;
- path safety remains enforced.

### Claim-domain tests

- two sessions cannot acquire the same unit;
- Translator and Reviewer conflict on the same unit;
- expired claim is not silently stolen;
- overlapping ranges are rejected deterministically;
- partial range acquisition rolls back claims created by the failed attempt;
- foreign session cannot release a claim;
- stale release cannot delete a replacement claim;
- cleanup removes expired but not live claims;
- cleanup writes an audit event with reason and exact claim revision;
- injected clock makes expiry boundaries deterministic.

### CLI tests

- claim/list/release/cleanup happy paths;
- JSON output stability;
- non-zero conflicts;
- range behavior;
- existing extract/validate/build/corpus behavior remains green.

Full suite runs on Python 3.10 and 3.12.

## Acceptance mapping

Issue #8 acceptance criteria map as follows:

1. **Two sessions cannot claim the same unit concurrently** — one unit path + atomic `create_if_absent`, with concurrency tests.
2. **Overlapping ranges are rejected deterministically** — normalized sorted range acquisition plus rollback and conflict tests.
3. **Lost updates fail with conflict** — strengthened filesystem `write_if_version` critical section and existing revision tokens.
4. **Expired claims can be cleaned up with an auditable reason** — explicit cleanup plus immutable claim event before version-checked deletion.
5. **CLI supports claim/list/release/cleanup** — `book.py` subcommands backed by domain operations.

## Dependency and PR strategy

Until #23/#7 is merged, `feature/workflow-v2-claims-cas` is stacked on `feature/workflow-v2-state-core` because #8 directly depends on its schema/storage abstractions.

The #8 implementation PR may initially target `feature/workflow-v2-state-core` so its diff contains only #8 work. After #23 merges into `refactor/workflow-engine-v2`, the branch will be rebased/updated as needed and the PR retargeted to `refactor/workflow-engine-v2`, preserving a clean integration diff.

No merge into the integration branch or `main` is performed without a separate integration decision.

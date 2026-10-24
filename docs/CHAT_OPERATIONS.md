# Chat → GitHub Actions operations (opt-in)

This bridge is **not** a second Workflow engine. It submits allowlisted, immutable JSON requests to a queue in the book repository, executes the book's exact pinned Python revision in a trusted Actions runner, and publishes an immutable machine result together with the corresponding book mutation **in one Git commit**. All literary decisions remain in Chat; no action translates or reviews a book.

## Deployment

Install the separate `.github/workflows/chat-operations.yml` workflow in the **book repository**, not the public workflow repository. Keep `scripts/chat_ops_bridge.py` and its tests in the canonical public repository. The Actions job checks out the book repository on `main`, then the current public bridge, and checks out the **exact** `metadata.json.workflow.resolved_revision` for the existing book's `scripts/book.py` and `scripts/corpus.py`. Never use the current main engine as a substitute for the book's selected revision.

The private-side Actions file must have `contents:write` on the book repository, only accept `push` to `main` for `.chat-ops/requests/*.json`, and use `concurrency: chat-operations-main` with `cancel-in-progress: false`. Use a scheduled replay (e.g. every 15 minutes) because GitHub concurrency may replace pending runs. All requests not yet carrying results are re-evaluated from fresh `origin/main` on each run; never assume each push receives its own run.

A Chat session submits an immutable file:

```text
.chat-ops/requests/<random-32-lowercase-hex>.json
```

It then reads the *exact* result file:

```text
.chat-ops/results/<same-id>.json
```

The result is either `ok` or `failed`. Absence is **not** a success. Failed requests never commit partial chapter mutations. Do not immediately resubmit on a timeout: read the receipt first. Do not reuse request IDs. An unsuccessful Git push forces fresh read/revalidation before another attempt.

## Request envelope

Required for all operations: `schema_version: 1`, `request_id`, `book_slug`, `workflow_revision` (40-hex pinned commit), `operation`, `chapter` (integer), `session_id`.

Allowlisted operations:

| Operation | Additional fields | Execution |
|---|---|---|
| `claim` | `role`; optional `base_commit`, `shared_state` | `book.py claim` |
| `renew-claim` | none | `book.py renew-claim` |
| `release` | none | `book.py release` |
| `accept-translation` | `translation`, `translation_sha256`, `source_sha256`, `claim_sha256` | write chapter artifact and run `book.py accept-translation` |
| `review-record` | `outcome`, `quality_report`, `source_sha256`, `translation_sha256`, `claim_sha256` | `book.py review-record` |
| `accept-review` | none | `book.py accept-review` |
| `cleanup-claims` | none | `book.py cleanup-claims` |

`shared_state` is `{"glossary": "<40-hex-Git-blob-SHA>", "style_guide": "<40-hex-Git-blob-SHA>"}`; it is required only for a parallel snapshot-bound claim and must be paired with `base_commit`. The executor checks the Git blob identities **before** converting to the filesystem backend's internal SHA-256 revision values. Never compare a Git blob SHA with a filesystem SHA-256 directly.

`claim_sha256` is the SHA-256 of **exact claim JSON bytes** in `books/<slug>/.workflow/claims/<unit-id>.json`, not the Git blob SHA. The source/translation hashes are also SHA-256 of exact UTF-8 artifact bytes. `translation` is the entire final text for that unit as a JSON string, including its exact newlines. The request is durable and immutable; ensure repository visibility is appropriate for the book.

Example claim for a new session (fill in a unique request ID, pinned workflow revision and actual book):

```json
{
  "schema_version": 1,
  "request_id": "0123456789abcdef0123456789abcdef",
  "book_slug": "example-book",
  "workflow_revision": "1111111111111111111111111111111111111111",
  "operation": "claim",
  "chapter": 4,
  "session_id": "example-translator-session",
  "role": "translator"
}
```

After receiving an `ok` claim receipt, fetch the exact current source, glossary, style, claims and bounded context; translate independently. Submit `accept-translation` while the claim is live, wait for `ok` acceptance, then `release`. A separate Reviewer session performs actual source comparison and produces a quality report; the bridge never generates a PASS on its own.

## Safety properties and limitations

- Only fixed, schema-validated operations are accepted. No user-supplied shell, Python code, extra command flags, paths or arbitrary repository.
- The worker runs `book.py validate` and `corpus.py verify` before each mutation. It verifies the book's pinned revision, session claim ownership, expiry and artifact hashes before acceptance.
- The job serializes writers **from this queue**, and Git's non-force `push` ensures remote changes are not silently overwritten. On a rejected push, the full request is re-evaluated from a fresh checkout. External direct writers must themselves respect canonical CAS; this queue cannot make arbitrary outside writers cooperative.
- The executor never silently changes a book's pinned workflow revision. Existing translations, progress, and reviewed units are not migrated.
- A successful `claim` is not translation; a successful `review-record` is not automatically `reviewed`; `accept-review` remains a separate gate.
- GitHub Actions must be permitted to push to `main` (or use a separately tested branch-gated publication policy). If branch protection denies the push, results remain absent and operations **must not** be reported successful.
- Runner `GITHUB_TOKEN` pushes do not trigger additional runs; scheduled replay and manual dispatch are recovery paths. A large backlog is processed in bounded batches.
- This is a GitHub Actions transport for the existing **filesystem CLI**; the authoritative transition code remains the chosen workflow revision. Full interoperability with simultaneously used direct GitHubStorage workers is not claimed. Do not mix storage backends during an active claim.

## Testing before activation

Run `python -m unittest discover -s tests -v` on the public PR. On a disposable test book and an isolated branch/repository, test claim/translation/review/release, two concurrent claims for the same unit, a stale claim/hash, a deliberately rejected push, duplicate delivery, a missed workflow event recovered by schedule, and a protected branch denying pushes. Confirm no tests mutate real book state. Only then activate the book repository workflow on `main`.

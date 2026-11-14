# Public Auto runtime

This directory owns the reusable automated Book Translator executor. The
**canonical literary workflow remains a separately pinned revision** selected
for each book through `metadata.json.workflow.resolved_revision`. Updating the
Auto runtime never rewrites a book's workflow pin.

Modules:

- `book_runner.py`: bounded Auto orchestration, state/preflight checks,
  translation, independent literary review, corrections, recovery, delivery.
- `literary_context.py`: role-specific prompts, strict evidence parsing,
  claim-bound proposals and reconciliations.
- `omniroute.py`: OpenAI-compatible provider transport, JSON response
  validation and privacy-preserving diagnostics.
- `job_monitor.py`: idempotent, signed delivery monitoring and book status
  integrity checks.
- `actions_failure_notifier.py`: safe startup-failure reporting.
- `verify_changed_books.py`: read-only book artifact checks using each
  book's *exact* pinned literary workflow, never a stale private installation.

The runtime has no default book storage, user key, or provider credentials.
Its caller supplies a state checkout root via `BOOK_TRANSLATOR_STATE_ROOT`.
When unset it uses the current working directory (useful for unit tests).

```bash
export BOOK_TRANSLATOR_STATE_ROOT=/path/to/book-state-checkout
python /path/to/book-translator/automation/book_runner.py --list-ready-json
```

The state checkout must be a real writable Git checkout and contain the
appropriate `books/`, `intake/` and `automation/status/` durable data.
The public runner clones each book's **exact** immutable literary revision
into its own temporary checkout. The out-of-tree `scripts/pinned_cli.py`
binds that checkout to state through a temporary `books/` symlink; it keeps
the canonical installation marker, scripts, docs, and contracts in the
**public engine checkout**, never in the state tree. Old book pins are not
silently upgraded or rewritten. The Git HEAD used by canonical claims and
parallel dispatch belongs to the book-state repository, not the public clone.
All book sources, translated texts, manifests, per-job checkpoints,
notifications, external callbacks and API credentials belong **only**
in the caller's private state repository. Do not copy or
upload any of them into this public engine.

The private repository deploys this code at a verified immutable **runtime
commit**, separate from each book's frozen workflow revision. An Actions
checkout provides the runtime under `.public-auto-runtime/`, and a tiny
compatibility entrypoint keeps existing job commands and test imports valid.
Do not use mutable `main` for execution. Upgrade the runtime only by
changing the reviewed private lock and validating private integration tests.

There are two independent modes:

- ChatOps uses the public Chat executor and its own pinned execution revision.
- Auto uses this public executor with provider credentials supplied only at
  run time by the private workflow.

Both operate on the same canonical, hash-bound book state. Neither fabricates
PASS evidence, bypasses source coverage checks, or modifies the book workflow
pin. Shared workflow bugs belong in the public implementation and its tests;
the private repository only owns launcher configuration, adapter-specific
integrations, and private data.

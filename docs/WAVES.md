# Literary Memory & Waves (v1)

The implementation extends Book Translator, without introducing a second chapter progress or review database. One wave per book. Python commands are registered in scripts/book.py and use the existing conditional StorageBackend.

## Operational flow

1. Run current source/corpus preflight; preview with `wave-plan BOOK --count 6`.
2. Start with `wave-start BOOK --count 6`.
3. Obtain session workpacks with `wave-dispatch BOOK --sessions 3 --base-commit HEAD`.
4. Each user-opened chat calls `wave-next BOOK --dispatch-id ID --session-id SESSION`; normal durable claim and translation/review acceptance rules remain mandatory.
5. Read bounded `memory-context BOOK --unit-id chapter-000001`.
6. A claimed worker submits source-grounded `memory-propose BOOK --entry entry.json --session-id SESSION`.
7. Finish translations, record independent PASS and release claims.
8. Prepare decisions JSON mapping each proposal ID to accepted or rejected. Preview `wave-reconcile BOOK --decisions decisions.json`, then freeze via `--prepare`.
9. Publish `wave-close BOOK`. If interrupted after preparing, execute `wave-recover BOOK` using frozen data.
10. Check `memory-impact BOOK`; affected reviewed chapters require explicit independent re-review. Final output and finalization remain blocked until impact checks are resolved.

## Durable paths

Under books/BOOK/.workflow/: waves/active.json, waves/WAVE/manifest.json, waves/WAVE/state.json, waves/WAVE/reconciliation.json, waves/WAVE/proposals/; literary-memory/active.json, literary-memory/snapshots/, literary-memory/impacts/, literary-memory/resolutions/, and existing session-dispatches/.

Snapshots and manifest are immutable. State and active pointers are version-guarded. A multi-file GitHub transition is not atomic, so RECONCILING remains a visible recovery barrier. Previous accepted translations are never rewritten by wave closure.

## Evidence

Literary memory entries have memory_id, kind, subject, statement, translation_decision, evidence (unit_id and verbatim original quote), source_revision_id, status and supersedes. Source evidence must be verified against the chapter before acceptance. An accepted change can produce conservative text-match impact candidates. This is not semantic proof of every affected passage.

Glossary and style-guide remain authoritative; proposals to those existing documents are not automatically merged in the wave. New entries never override established glossary terms. Keep proposed/shared literary changes separate until reconciled. A changed shared-state revision blocks stale workpacks.

## GitHub-native ChatGPT limitation

ChatGPT GitHub sessions can inspect authoritatively stored files, workpacks, claims and artifacts. They cannot assume Python ran or manufacture SHA-256 memory revisions. Before any mutation, validate the contract, verify current state, use create-if-absent or compare-and-swap, then read back results. If the connector cannot execute a required deterministic transition or perform its cryptographic checks, stop and request execution by a verified runtime. Full GitHub-only feature parity requires a dedicated execution adapter and equivalence tests; it is not yet implemented by instructions alone.

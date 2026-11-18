# Transactional Auto quality

Experimental opt-in flag BOOK_TRANSLATOR_TRANSACTIONAL_QUALITY=on.
Reject destructive changes before staging and canonical acceptance. Review the
actual combined after-candidate by source-owned blocks; hold accepted bytes
unchanged on veto. Record candidate-bound approvals across checkpoints.
Canonical independent review and EPUB validation still required.

This is the first increment, not a throughput claim. Length checks are risk
alarms, not semantic proof. Large unaligned units fail closed. Next: full-block
repair pathway, dependency-aware incremental verification and recovery instead of
replaying failed patches. Test candidate gates including veto/resume and stale
hashes before enabling in production.

Acceptance: at least two independently reviewed substantive 20k–40k-character
chapters per wall-clock hour, one sequential book process, all source coverage,
all previous findings resolved, valid non-preview EPUB. Record model calls,
elapsed time, tokens, found and introduced defects. Use more than one complete
book fixture and real provider runs, not just unit tests. Do not waive actual
literary errors or claim zero errors from a validator alone.

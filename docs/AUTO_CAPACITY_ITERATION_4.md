# Fourth capacity iteration: productive correction feedback

Third test 44419355669: 57,259 source characters, 2 translated, 0 independently
reviewed, 41 reserved calls (7 translator claim, 6 reviewer claim, 27 corrector
claim including independent preflight, 1 orchestrator), 3519.9s; one provider
503. Both chapters deferred after repeated candidate vetoes. A valid negative
preflight was treated as a global model failure, with no concrete rejection
reason communicated to the next corrector.

New opt-in BOOK_TRANSLATOR_CORRECTION_FEEDBACK=on: independent reviewer makes
a comparative before/after judgment, provides the specific risk and reason,
and rejects every new regression, no-progress edit and uncertain case.
Partial verified improvements may proceed through canonical acceptance but
all old findings remain subject to mandatory independent review. Per-finding
veto budget is two distinct rejected candidates per unchanged record; exhausted
findings remain open and other verified patches can advance to re-review.
Feedback-bound plain paragraph reconstruction preserves bytes outside the
reviewer anchor. No automatic PASS and no reuse of preflight as PASS. Stable
case identity improves exact cache reuse. Metrics separate claimed role from
actual requested model role. No production change before real capacity proof.

The current pinned contract requires review of accepted translation bytes;
do not pretend a pre-acceptance veto is the canonical review. A fully combined
single independent transaction requires an explicit compatible versioned
workflow contract; preserve existing book pins.

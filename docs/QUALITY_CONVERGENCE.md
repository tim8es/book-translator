# Quality convergence contract (revision-scoped)

This contract supplements REVIEWER and ORCHESTRATION in revisions containing
the canonical adjudication validator. Never migrate a historical book implicitly.

The independent reviewer checks the source and target comprehensively. Report
concrete defects, not equally valid alternative phrasings. No PASS with an
unresolved actual defect, regardless of severity. Minor is not synonymous with
optional. Only minor style/register/voice disagreements can be adjudicated
as acceptable variants: an independent invocation must positively establish
that the current text is faithful, grammatical, idiomatic and consistent
with the book's literary decisions. Uncertainty, actual awkwardness, structural
and fidelity problems remain mandatory. The adjudicator may not edit or
grant chapter PASS. The orchestrator assembles the complete review result.

Store adjudication evidence with the original finding detail and exact current
anchors, source/target SHA-256, separate reviewer/adjudicator session IDs and
concrete rationale. This closes a disputed finding, not a claim that its wording
changed. Previous disputes may close on unchanged text only with identical
source and target anchors and validated evidence. Never relabel a substantive
finding to obtain closure. Changed bytes require independent new review.

Project stable defect identities from original finding and canonical resolves
links. Do not conflate distinct findings on the same passage. On repeated
rejections use source-first clause reconstruction, then compare distinct
source-faithful renderings inside the authorized anchor. A fresh independent
candidate check must establish that the correction fixes the cited defect
without introducing regression. It is a veto, not PASS. Source coverage,
corpus validation, review promotion and EPUB gates remain mandatory.

The overall model-call cap, provider backoff, claim rules, and hard chapter
review/correction budgets still apply. No identical known-rejected patch may
be reapplied. If evidence is missing, exhausted or contradictory, preserve the
last accepted artifact and report a blocked result rather than certify a defective
translation. For a recurring defect, a single source-owned EPUB paragraph may
be reconstructed from the original under the active corrector claim. Both
source and target anchors must belong to the same unique preserved block;
no cross-block finding, embedded protected marker or link may be altered.
Each stable defect gets at most one such recovery attempt per source revision.
Persist the exact plan and candidate separately before the independent veto.
Never infer PASS from candidate approval; re-review the accepted artifact.
For recurrent defects at the highest recovery level, use a distinct configured
role fallback model where available. Bind the escalation to one exact model
call, role, unit and workflow revision. Without a distinct configured fallback,
continue with the normal route. Persistent unrestricted tool-using sessions
are not part of this contract.

Before default rollout, compare identical source books against ChatOps and
measure cost, completion and independent literary quality. A test-suite pass
alone is not evidence of autonomous literary completion.

## Evidence-earned recovery budget

Do not extend a chapter just because a reviewer rejected it. The automatic
runner grants at most four additional cycles, each earned by a distinct
current-revision artifact with a verified prior defect closure, lower total
unresolved severity, and no newly reported serious defect. Adjudication alone,
identical/recycled artifacts and new serious defects earn no credit. The total
model budget still bounds all calls. A genuinely different recovery strategy
may run before stagnation deferral, but cannot override the hard cap.

## Candidate veto recovery

An independent veto is a quality decision, not a provider error. Remove only
vetoed patches from a mixed group, discard all old candidate approvals and
unbound literary-memory proposals, and independently verify the reduced group.
No remaining finding is resolved by this operation. A completely unsafe or
vetoed group is retired without mutating the accepted translation, so the next
checkpoint may use source-first reconstruction. At most three completely
unusable groups per exact source/target/review identity are admitted. If that
bound is exhausted, defer through the canonical attention queue, without PASS.
Partial groups shrink strictly; the overall model budget remains mandatory.

## Full review after substantive correction

Focused review is restricted to minor anchored style, register and voice
findings. Any other unresolved category or severity requires a new full-source
comparison (partitioned when necessary), not inherited coverage from an earlier
review. This is a conservative response to extended qualification finding a
missed fidelity defect after internal acceptance; it does not establish the
root cause or guarantee a successful live qualification.

Extended synthetic qualification requires both the existing whole-book audit
and six unit-local audits. Each unit verdict is bound to exact source and
target hashes; any failed check keeps qualification failed. Audit model calls
are reported separately from the bounded runner calls. No source or translated
passages are added to the qualification report. A configured backup identical
to the primary route is not a fallback.

## Bounded agent review planning (opt-in)

With BOOK_TRANSLATOR_AGENT_REVIEW_PLANNING=on and incremental review enabled,
the orchestration model may choose between an admissible direct complete review
and a prevalidated two-to-four-part aligned complete review. The canonical
claim, source and target hashes, immutable literary revision and prior review
record still determine the exact work. Mandatory partitioning, small units,
existing stages and focused editorial-only checks are not discretionary.
The runner reserves one counted advice call before invocation, records a
hash-bound decision, and reuses it after interruption without a second paid
call. Malformed or unavailable advice falls back to direct review. Existing
review, correction, attention and promotion gates remain authoritative.
The agent sees only technical sizes and budgets, never literary passages.
This capability does not make a model an unrestricted repository operator.
Keep the option off in production until live qualification on long units.

## Independent confirmation of short PASS (experimental)

The second extended run had a whole-book 11/11 audit but only two of six
unit-local passes. A new opt-in BOOK_TRANSLATOR_CONFIRM_SHORT_PASS gate
requires a fresh full-source reviewer invocation for an otherwise accepted
short unit (at most 12,000 characters in either artifact). It uses the pinned
role, exact current artifact and prior canonical lineage, not the first
reviewer's answer. Rejection enters the ordinary correction path, while
invalid/unavailable responses fail closed. Both invocations consume the
durable global cap; an interrupted call is not free. A same-model invocation
is independent context, not independent underlying provider.

Extended qualification enables the gate and verifies final per-unit hash-bound
confirmation coverage. For diagnosis only, failed unit-local checks now retain
a bounded redacted target excerpt from the internally authored synthetic
fixture in the private qualification report. No production book text is
exported by this change. This intentionally changes the earlier synthetic
report content policy to permit debugging that fixture. Production remains
default-off; long-unit confirmation and live quality must be qualified separately.


## Candidate preflight context budget

The previous combined 38,000-character limit left insufficient evidence
capacity alongside the growing pinned reviewer contract. Preserve the full
role contract; bound evidence/user prompt to 38,000 characters and the
combined request to 80,000 characters. Existing batch reduction remains
mandatory. Never truncate evidence, skip the veto or grant PASS.

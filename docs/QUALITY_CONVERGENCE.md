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
translation. This first stage does not yet implement broader paragraph
replacement, persistent tool-using agent sessions or cross-model escalation.

Before default rollout, compare identical source books against ChatOps and
measure cost, completion and independent literary quality. A test-suite pass
alone is not evidence of autonomous literary completion.

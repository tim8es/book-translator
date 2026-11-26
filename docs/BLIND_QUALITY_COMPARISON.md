# Blind Auto versus ChatOps literary comparison

This optional read-only benchmark is not a replacement for the canonical
chapter review or EPUB gates. It never promotes a unit, mutates a book,
or changes its workflow revision.

## Preparation

Use the same authored or permissioned source in two isolated workspaces.
Pin the same literary workflow revision, target language and source edition.
Execute one through Auto/Pi and the other through ChatOps, without sharing
translations or literary-worker feedback. Validate and verify both corpora
using the corresponding pinned CLI. Record completion, runner calls, provider
usage and elapsed time separately; ChatOps call/cost data may be unavailable
and must not be fabricated. Export matching source and target units with
the actual SHA-256 values of their UTF-8 text, not guessed hashes.

The explicit input manifest is a local, operator-prepared JSON object:
schema_version (1), target_language (language tag), units (1–30).
Each unit has unit_id (chapter-NNNNNN), source ({text,sha256}), auto
({text,sha256,source_sha256}) and chatops ({text,sha256,source_sha256}).
The harness checks text hashes and the common source hash before paid calls.
It does not authenticate the operator's labels or prove either workflow ran;
retain canonical completion and source provenance separately. Do not commit
the manifest or copyrighted source/translation artifacts to the public repo.

## Execution

From the canonical executor checkout:

    python automation/blind_quality_comparison.py --manifest /secure/pairs.json --output /secure/report.json

Use --judge-role REVIEWER_FALLBACK when a distinct independently configured
review route is available. Two tool-free model invocations per unit evaluate
each variant in reversed A/B positions; the prompt includes no mode labels,
model names, prior judgments or provenance metadata. The input is bounded to
16,000 characters per text and at most 60 calls total. Larger sources must
be sampled at explicit source-aligned unit boundaries, with sampling declared.

The report stores only hashes, criterion verdicts, number of calls started,
and consensus counts, not source/target text or model output. A criterion
passes only if both judgments agree it passes; disagreement remains
disagreement. A complete report means the comparison protocol finished,
not that either translation passed literary review. Independently inspect
disagreements and critical failures before asserting quality parity.
Do not rerun selectively to obtain a favorable result.

## Rollout

Run a synthetic paired benchmark first, then a representative permissioned
sample including dialogue, negation, agency, omissions and long chapters.
Do not substitute hand-authored reference translations for actual ChatOps
execution. The benchmark is deliberately opt-in and does not add a new queue,
a per-book Action or any production model calls.

## Canonical export of completed paired workspaces

Use the read-only exporter instead of manually constructing source hashes:

    python automation/blind_quality_export.py --auto-root /state/auto --chatops-root /state/chatops --auto-slug fixture --chatops-slug fixture --output /secure/pairs.json

The two state checkouts must be separate, clean, committed, and contain
current reviewed units with matching source edition, workflow revision,
target language and exact extracted source bytes. The local public Git checkout
must contain that literary revision. Each snapshot is checked using its own
disposable pinned CLI, corpus verification and canonical status. The exporter
rejects active claims, stale review, changed bytes and source mismatches.
It records both state commits in its stdout; preserve that provenance separately.
The input labels still must be verified by the operator: the exporter cannot
prove the history of literary decisions or the true execution mode. It does not
create a completed translation or review evidence. Keep the generated manifest
outside all repositories; only the sanitized comparison report may be published.

## Live paired benchmark from an actual ChatOps snapshot

`paired_quality_qualification.py` requires a completed, clean, committed
ChatOps state checkout, an explicit state commit, intake job ID and source
SHA-256. The source must be a two-unit authored synthetic EPUB. The current
canonical PASS and final translation hashes must match successful ChatOps
accept-translation and review-record receipts with distinct worker sessions.
An unfinished ChatOps book is rejected before any paid Auto or judge call.

Use a complete (not shallow) checkout of the public executor object store and
run from a trusted private environment:

    python automation/paired_quality_qualification.py --executor-revision <executor-sha> --chatops-root /state/chatops --chatops-commit <state-sha> --chatops-job <32-hex-id> --source-sha256 <source-sha256> --output /secure/paired-report.json

The Auto side uses the exact original EPUB bytes, same literary workflow pin
and target language as the ChatOps side, in a new temporary Git repository
with a local bare remote. No seeded defects, target text or reviewer feedback
are copied across. The Auto cap is 36 counted runner calls; the blinded
comparison uses at most four additional calls and runs only after both sides
are canonically reviewed. The output stores sanitized provenance, counts,
hashes and verdicts, not the underlying literary texts. ChatOps model-call
count is explicitly unavailable; successful queue receipts are not model calls.
The judge uses a fresh reviewer invocation, not a distinct underlying provider
unless separately verified. Results from this two-unit pilot are not sufficient
for production-wide quality equivalence. The CLI does not dispatch an Action;
a separate explicit private invocation is required after ChatOps completion.


The paired qualification additionally requires the latest canonical review
report and matching successful bridge request to contain the same
independent_session context. The documented same-chat bounded-context
fallback is valid for ordinary translation but not this independent-review
benchmark. Session identifiers and structured reports attest context; they
cannot prove that the operator actually used a separate conversation.

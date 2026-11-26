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

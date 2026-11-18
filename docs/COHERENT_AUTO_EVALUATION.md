# Experimental coherent Auto scheduling

Set BOOK_TRANSLATOR_COHERENT_BATCHES=on in an isolated evaluation environment.
Default off; no private runtime deployment or book workflow revision changes.
Structural requests use existing source IDs, deterministic marker reconstruction,
and existing partial/coverage validation. Expand only the orchestration selection
to 96 spans / 12000 source characters; preserve provider-pressure and single-span
fallbacks. Correction requests may group up to eight exact nonoverlapping
replacement anchors, preserving canonical review evidence and patch validators.
Critical, structural, omitted, rejected, overlapping or ambiguous work stops
expansion. Existing persisted reviewer partitions, provider-pressure and
protocol fallbacks retain precedence; otherwise prefer whole-unit review within
existing size budgets. No model-created coordinates or PASS are added.

Run python -m unittest discover -s tests -p test_auto_coherent_batches.py -v.
The synthetic A/B counts scheduler selections, not paid calls or review quality.
For real A/B, use two separate private workspaces with identical permissioned
source, pinned literary workflow, provider role models, glossary/style starting
state and runtime revision, varying only the coherent flag. Record cumulative
calls, prompt/completion tokens, elapsed wall time, PASS coverage, deferred
units and independent acceptance on final hashes. Do not compare an incomplete
run to a complete one or deploy based on synthetic counts alone.

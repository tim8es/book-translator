# Fifth capacity iteration

Fourth run 44430711334: 60 minutes, 2 translated, 2 accepted correction rounds,
0 reviewed, 42 reserved calls (41 completed), zero reported provider errors.
First chapter improved from 19 open findings to 9 after one correction round,
then needed another. Preflight plus repeated complete re-review consumed the
budget; the second chapter also encountered invalid compact null-target evidence.

Experimental flags (off by default): single review gate preserves independent
preflight for high-risk edits, while routine mechanically safe corrections
proceed through machine-only acceptance and mandatory independent review of
the current bytes. No PASS evidence reused from preflight. This is an explicit
quality/speed tradeoff: routine semantic regressions can reach the intermediate
translated state but cannot be promoted without independent review.

Preserve paragraph counts per source-owned translation span; plan stable
ordinal paragraph windows when source and target counts match, allowing wider
bounded cores within 42k combined context. Ordinal pairing remains a hypothesis,
not semantic evidence. Existing hash-bound clean-window reuse is available
only with exact unchanged reviewed windows/memory and no prior open finding.
Address overlap explicitly and favor self-contained correction anchors.
No production changes or literary pin upgrade before measured success.

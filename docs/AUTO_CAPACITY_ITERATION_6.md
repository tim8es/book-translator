# Sixth capacity iteration

Fifth run 44498455714: 3600.03 s, 1 translated, 1 correction accepted,
0 reviewed, 14 reserved invocations (13 completed). Two 503 responses after
approximately 300 seconds each, followed by 5 and 15 min waits. Correction
took 2 min, with 7 cases and only 1 high-risk case needing preflight.
Structural translation required 7 calls and hit optional literary memory cap.

Opt-in resilient recovery: retain exact previously reviewed windows and split
only the next failed core, using deterministic partition replay against the
same source/accepted target and bound reviewer contract/memory. Full evidence
and prior finding lineage remain required. Use ordinary 12k initial cores
instead of 16k; ordinal pairing remains advisory, with legacy fallback.
Shorter bounded retry after slow gateway/timeouts only, never bypass provider
Retry-After or 429 backoff. No new provider route is invented.
Cap optional validated memory proposals without rejecting completed content;
count deferred proposals for observability. Preserve exact declared paragraph
layout in prompts; when only paragraph count differs do not repeat an otherwise
coverage-safe span solely for formatting. Actual independent review still checks
all content/structure. Existing coverage floor and marker/link checks remain.

No production changes until capacity target proven.

# Third capacity iteration: marker-free EPUB

The second live run 44402042237 completed with 1 translated unit, 0 reviewed,
22 paid invocations and model-blocked after 2314.78 seconds. No capacity claim.
The previous candidate required bt-block markers to activate compact review.
Pinned workflow a973f29 uses original_xhtml_v1 only for raster-XHTML EPUB;
ordinary EPUB extraction is marker-free. Long marker-free chapters were also
ineligible for the transactional whole-artifact veto (18k combined chars).
This explains the observed legacy reviewer routing and identifies a
deterministic candidate-gate blocker; the exact last error from the previous
disposable run was not captured, so no assertion of sole cause.

Marker-free review presents independently addressed exact source/target
paragraph ranges; these are NOT assumed aligned. Existing canonical chunk
coverage and v2 anchor/lineage checks still apply. Marker-free correction veto
checks actual combined candidate windows, mapped by immutable patch coordinates,
with original paragraphs selected from each independent source anchor.
Refuse oversized/ambiguous scopes rather than silently truncating.
All existing independent promotion rules, pinned literary rules and provider
backoff remain unchanged. No production rollout before actual benchmark.

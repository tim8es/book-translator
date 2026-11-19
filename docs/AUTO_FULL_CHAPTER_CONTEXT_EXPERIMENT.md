# Experimental chapter-context translation

**Goal:** restore enough context for literary translation across Auto requests, keeping source-identified fragments and canonical independent review unchanged.

- Opt-in: \`BOOK_TRANSLATOR_FULL_CHAPTER_CONTEXT=on\`.
- Typical coherent Auto batch: up to ~12,000 original characters. Internal spans may be smaller for protected EPUB markup, but the model translates a *group*, not necessarily each individual segment as a separate call.
- Short/medium chapters (up to 65,000 original characters): transmit the complete original chapter alongside the selected source-IDs. Include prior translated text in source order when it fits the bounded prompt. If the context exceeds the prompt character budget, discard older prior target excerpts first.
- Longer chapters: translate contiguous source-ID batches with an overlapping contextual source window (target 56,000 source characters) surrounding the current batch, and up to 15,000 characters of nearby completed target text. The window shifts deterministically as the translation progresses. This is *not* a chapter summary and does not rewrite previous segments.
- Hard ceiling: 145,000 characters across system and user prompts including serialized context. If needed, shrink *only the contextual window*, not the assigned source batch; fail closed if even the selected batch cannot be represented. No silent bare-segment fallback. The budget is a heuristic, **not** a guarantee of model-specific token capacity.
- Provider pressure or rejected output may still invoke existing smaller rescue calls; all their prompts receive context through the same source-bound adapter. Protocol and accepted state remain unchanged.
- Audit records store only mode, character counts, selection counts, and original-source offsets, not literary text or API secrets.

**Verification:** deterministic tests exercise the complete chapter, the large sliding window, a final-window resume, model-prompt reduction and source identity. A real-model comparison must measure first-review quality, accepted chapters/hour, model calls, tokens and verified EPUB. Current existing test #11 tests only chapters of about 27k and 30k characters; it does not exercise this large-chapter mode.

**Release:** draft PR only; do not merge or modify existing pinned book revisions without separate authorization.

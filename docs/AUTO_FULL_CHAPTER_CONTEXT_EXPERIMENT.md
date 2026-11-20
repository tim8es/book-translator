# Experimental chapter-context translation

**Goal:** restore enough context for literary translation across Auto requests, keeping source-identified fragments and canonical independent review unchanged.

- Opt-in: \`BOOK_TRANSLATOR_FULL_CHAPTER_CONTEXT=on\`.
- With the chapter-context flag on, translation batches begin at **up to 5,000 source characters and 8 distinct source IDs** per output, independently of the coherent-batching flag. This is a conservative output-size heuristic informed by #12's invalid IDs and oversized responses, **not a measured provider token limit**. After each invalid model-output protocol response, the durable source-hash-bound budget shrinks to 3,000/4 IDs, then 1,800/2 IDs, then 1,600/1 ID. A persistent minimum-size failure activates the original single-span safety fallback. Previously accepted segments are never overwritten. Provider pressure can also force smaller batches.
- Short/medium chapters (up to 65,000 original characters): transmit the complete original chapter alongside the selected source-IDs. Include prior translated text in source order when it fits the bounded prompt. If the context exceeds the prompt character budget, discard older prior target excerpts first.
- Longer chapters: translate contiguous source-ID batches with an overlapping contextual source window (target 56,000 source characters) surrounding the current batch, and up to 15,000 characters of nearby completed target text. The window shifts deterministically as the translation progresses. This is *not* a chapter summary and does not rewrite previous segments.
- Hard ceiling: 145,000 characters across system and user prompts including serialized context. If needed, shrink *only the contextual window*, not the assigned source batch; fail closed if even the selected batch cannot be represented. No silent bare-segment fallback. The budget is a heuristic, **not** a guarantee of model-specific token capacity.
- Provider pressure or rejected output may still invoke existing smaller rescue calls; all their prompts receive context through the same source-bound adapter. Protocol and accepted state remain unchanged.
- Audit records store only mode, character counts, selection counts, and original-source offsets, not literary text or API secrets.

**Verification:** deterministic tests exercise complete chapter, sliding windows, final-window resume, exact source identity, moderate output batching, resumption and gradual failure downgrades. A real-model comparison must measure first-review quality, accepted chapters/hour, model calls, tokens and verified EPUB. Current existing test #11 tests only chapters of about 27k and 30k characters; it does not exercise this large-chapter mode.

**Release:** draft PR only; do not merge or modify existing pinned book revisions without separate authorization.

**Next measured probe:** `automation/chapter_first_review_benchmark.py` uses the same public-domain Dickens chapter 18 as #10/#12 and stops immediately after its **first independent review** (or at a strict 1,800-second wall budget). It logs aggregate model calls and tokens, protocol rejection counts, adaptive batch caps and first-review findings. A successful probe exit means the first review ran; it is not literary approval and does not build an EPUB. This does not start a 60-minute correction loop.

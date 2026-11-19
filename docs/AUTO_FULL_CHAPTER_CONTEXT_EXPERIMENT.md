# Experimental full-chapter Auto translation context

**Purpose:** distinguish poor first-pass fidelity due to missing chapter context from reviewer/corrector non-convergence. A/B requires the exact same fixture, models and independent review contracts; compare first-review new/major findings, translated/reviewed counts, provider prompt/completion tokens, wall time and EPUB verification.

**Opt-in:** \`BOOK_TRANSLATOR_FULL_CHAPTER_CONTEXT=on\` for structural Auto Translator calls. Each call reads exact source-hash-bound full original, includes all previously checkpointed translated spans in source order, and constrains generation to only selected source span IDs. This is **not** actual conversational model session memory; it explicitly reconstructs literary context from durable data. Unbounded chapters or oversized prompts use legacy bounded segments with a content-free reason in \`prompt_context.chapter_context\`. No content, tokens, book data or secrets are put into diagnostic records. No modification to canonical state/claims/review/EPUB acceptance or pinned book revision.

**Limitations:** repeated full source and accumulated translated spans consume extra input tokens. Expanded context can trigger provider context pressure, output truncation or timeout. A/B must show a *lower independent first-pass error rate*, not just higher token usage. One live run cannot establish a general quality conclusion.

**Prior baseline:** run #9 (b45d17) produced 2 translated, 0 independently reviewed, 22 calls and 383,399 measured model tokens in 3600 seconds, 7 new first-review findings. Run #10 tests the same fixture with complete mapped review but without full chapter translation context; use it as the closer control once finalized.

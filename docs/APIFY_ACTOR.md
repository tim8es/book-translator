# Managed-LLM Apify Actor

This branch is an isolated commercial runtime experiment. It does not replace the default agent-driven Book Translator workflow.

## Product boundary

The Actor owns the LLM credentials and pays the model provider. End users upload a book and choose a target language. They never provide an LLM API key and cannot select the owner-side model.

Runtime flow:

1. receive one uploaded book;
2. initialize the existing Book Translator durable workspace and sealed source manifest;
3. reject books above the owner source-size limit;
4. reserve the fixed PPE start event before any LLM call;
5. verify the user's max-charge budget can cover the whole planned book before spending on the model;
6. for each chapter, verify remaining word-based PPE capacity before spending on the model;
7. acquire a Translator claim;
8. generate the translation using the owner-funded LLM;
9. accept the translation through the existing hash-bound state machine;
10. acquire an independent Reviewer claim;
11. record PASS or CORRECTIONS_REQUIRED evidence;
12. if needed, re-enter the Translator boundary, correct, and review again;
13. accept only current PASS evidence;
14. persist the reviewed chapter as a run artifact, then charge its word-based PPE event;
15. validate the whole workspace;
16. build EPUB and/or Markdown from reviewed durable state;
17. store final artifacts in the run key-value store and usage/cost metadata in SUMMARY.

## Owner-only configuration

Set these as Actor environment variables in Apify Console. The API key must be marked Secret.

Required:

- BOOK_TRANSLATOR_LLM_API_KEY
- BOOK_TRANSLATOR_TRANSLATION_MODEL
- BOOK_TRANSLATOR_TRANSLATION_INPUT_USD_PER_1M
- BOOK_TRANSLATOR_TRANSLATION_OUTPUT_USD_PER_1M
- BOOK_TRANSLATOR_REVIEW_INPUT_USD_PER_1M
- BOOK_TRANSLATOR_REVIEW_OUTPUT_USD_PER_1M

Optional:

- BOOK_TRANSLATOR_LLM_BASE_URL, default https://api.openai.com/v1
- BOOK_TRANSLATOR_REVIEW_MODEL, default same as translation model
- BOOK_TRANSLATOR_MAX_TOKENS_PARAMETER, default max_completion_tokens; set max_tokens for older OpenAI-compatible gateways
- BOOK_TRANSLATOR_REASONING_EFFORT, optional; forwarded as reasoning_effort when configured
- BOOK_TRANSLATOR_MAX_LLM_COST_USD_PER_RUN, default 20
- BOOK_TRANSLATOR_MAX_SOURCE_WORDS, default 500000
- BOOK_TRANSLATOR_MAX_SOURCE_BYTES, default 50000000
- BOOK_TRANSLATOR_MAX_CHAPTER_CHARS, default 60000
- BOOK_TRANSLATOR_TRANSLATION_MAX_TOKENS, default 32000
- BOOK_TRANSLATOR_REVIEW_MAX_TOKENS, default 8000
- BOOK_TRANSLATOR_MAX_REVIEW_ROUNDS, default 2
- BOOK_TRANSLATOR_LLM_TIMEOUT_SECONDS, default 300
- BOOK_TRANSLATOR_WORKFLOW_REVISION, default apify-managed-llm-mvp
- BOOK_TRANSLATOR_SKIP_CHARGING=true for local/development tests only

The runtime currently targets an OpenAI-compatible chat-completions endpoint so the model provider can be changed without exposing that choice to the user. It does not send temperature by default, which keeps it compatible with modern reasoning models. The output-token parameter is owner-configurable because current providers differ between max_completion_tokens and legacy max_tokens.

## Apify monetization contract

The code emits two PPE event names:

- book-started: one event after source validation and full-run budget preflight, before the first LLM call;
- translation-1k-words: one event per 1,000 source words (rounded up per chapter), charged only after that chapter has current PASS evidence and its reviewed translation has been saved as a run artifact.

Prices are intentionally not hard-coded. They must be configured in Apify Console after unit economics are finalized.

Before any LLM call, the Actor verifies that the run's maximum charge can cover the complete planned book at the configured PPE prices. It also rechecks capacity chapter-by-chapter. This avoids spending owner-funded LLM budget on a run that cannot pay for the planned work. A failed later chapter leaves earlier reviewed chapter artifacts accessible and does not charge the failed chapter.

## Cost protection

There are two independent limits:

1. user-side Apify max charge, enforced through PPE charge results;
2. owner-side LLM dollar ceiling, enforced by the runtime.

The LLM budget check estimates the next request before it is sent. After the response, provider-reported token usage is converted to cost. If a compatible gateway omits token usage, the conservative pre-request estimate is booked instead of assuming zero cost. Any non-success finish reason (for example an output-length truncation) fails closed and is never blindly retried, so an incomplete chapter cannot enter durable translation state and the same doomed request is not paid for repeatedly.

The summary records model, role, input/output tokens when available, and estimated/measured LLM cost. It never records the API key.

## Current deliberate constraints

- one book per Actor run;
- remote file inputs are size-limited and private/link-local network destinations are rejected;
- EPUB, HTML/XHTML, Markdown, and TXT use the existing automatic extractor;
- DOCX and PDF are not yet accepted by the automatic Actor path;
- Translator and Reviewer are sequential and logically independent;
- after a PASS, bounded reviewer-proposed glossary and style observations are sanitized and persisted by the Actor orchestrator for subsequent chapters;
- chapters above the owner context limit are rejected before charging; explicit chunking is still needed before Store release;
- no resume-from-previous-Apify-run contract yet;
- pricing values are not final;
- no production deployment has been smoke-tested from this branch yet.

## Before first paid public test

1. add bounded chapter chunking with continuity at chunk boundaries;
2. test and tune glossary/style proposal quality on real books;
3. run real books through at least two candidate model combinations and record actual token cost;
4. set PPE prices from measured p50/p90 cost, not guesses;
5. deploy privately on Apify and test file upload, PPE limits, partial chapter delivery, output retrieval, retries, and secret redaction;
6. only then prepare the Store listing and move the commercial runtime into its own repository.

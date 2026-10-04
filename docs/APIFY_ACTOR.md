# Managed-LLM Apify Actor

This branch is an isolated commercial runtime experiment. It does not replace the default agent-driven Book Translator workflow.

## Product boundary

The Actor owns the LLM credentials and pays the model provider. End users upload a book and choose a target language. They never provide an LLM API key and cannot select the owner-side model.

Runtime flow:

1. receive one uploaded book;
2. initialize the existing Book Translator durable workspace and sealed source manifest;
3. reject books above the owner source-size limit;
4. reserve the fixed PPE start event before any LLM call;
5. for each chapter, reserve word-based PPE capacity before spending on the model;
6. acquire a Translator claim;
7. generate the translation using the owner-funded LLM;
8. accept the translation through the existing hash-bound state machine;
9. acquire an independent Reviewer claim;
10. record PASS or CORRECTIONS_REQUIRED evidence;
11. if needed, re-enter the Translator boundary, correct, and review again;
12. accept only current PASS evidence;
13. validate the whole workspace;
14. build EPUB and/or Markdown from reviewed durable state;
15. store final artifacts in the run key-value store and usage/cost metadata in SUMMARY.

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
- BOOK_TRANSLATOR_MAX_LLM_COST_USD_PER_RUN, default 20
- BOOK_TRANSLATOR_MAX_SOURCE_WORDS, default 500000
- BOOK_TRANSLATOR_MAX_CHAPTER_CHARS, default 60000
- BOOK_TRANSLATOR_MAX_REVIEW_ROUNDS, default 2
- BOOK_TRANSLATOR_LLM_TIMEOUT_SECONDS, default 300
- BOOK_TRANSLATOR_WORKFLOW_REVISION, default apify-managed-llm-mvp
- BOOK_TRANSLATOR_SKIP_CHARGING=true for local/development tests only

The runtime currently targets an OpenAI-compatible chat-completions endpoint so the model provider can be changed without exposing that choice to the user.

## Apify monetization contract

The code emits two PPE event names:

- book-started: one event per accepted run, before the first LLM call;
- translation-1k-words: one event per started 1,000 source words, charged chapter-by-chapter before the LLM work for that chapter.

Prices are intentionally not hard-coded. They must be configured in Apify Console after unit economics are finalized.

The Actor checks the result of every charge. If the run's maximum charge cannot cover the next unit of work, it stops before the corresponding LLM call.

## Cost protection

There are two independent limits:

1. user-side Apify max charge, enforced through PPE charge results;
2. owner-side LLM dollar ceiling, enforced by the runtime.

The LLM budget check estimates the next request before it is sent. After the response, provider-reported token usage is converted to cost. If a compatible gateway omits token usage, the conservative pre-request estimate is booked instead of assuming zero cost.

The summary records model, role, input/output tokens when available, and estimated/measured LLM cost. It never records the API key.

## Current deliberate constraints

- one book per Actor run;
- EPUB, HTML/XHTML, Markdown, and TXT use the existing automatic extractor;
- DOCX and PDF are not yet accepted by the automatic Actor path;
- Translator and Reviewer are sequential and logically independent;
- glossary/style files are read as shared context but automatic durable literary-memory updates are not implemented yet;
- chapters above the owner context limit are rejected before charging; explicit chunking is still needed before Store release;
- no resume-from-previous-Apify-run contract yet;
- pricing values are not final;
- no production deployment has been smoke-tested from this branch yet.

## Before first paid public test

1. add bounded chapter chunking with continuity at chunk boundaries;
2. add automated glossary/style proposal and orchestrator-owned merge rules;
3. add deterministic failure records and a clear charging policy for failed chapters;
4. run real books through at least two candidate model combinations and record actual token cost;
5. set PPE prices from measured p50/p90 cost, not guesses;
6. deploy privately on Apify and test file upload, PPE limits, output retrieval, retries, and secret redaction;
7. only then prepare the Store listing and move the commercial runtime into its own repository.

# Private Apify smoke test

This runbook is for the `apify-managed-llm-mvp` branch. It keeps the Actor private and disables end-user charging while measuring real LLM cost and validating the full workflow.

## Source

Use this Git repository source URL in Apify:

```text
https://github.com/tim8es/book-translator#apify-managed-llm-mvp
```

Apify supports selecting a branch by adding it as the URL fragment.

## Owner environment

Create the following Actor environment variables. Mark `BOOK_TRANSLATOR_LLM_API_KEY` as Secret.

```text
BOOK_TRANSLATOR_LLM_API_KEY=<secret>
BOOK_TRANSLATOR_LLM_BASE_URL=https://api.openai.com/v1

BOOK_TRANSLATOR_TRANSLATION_MODEL=gpt-5.4-mini
BOOK_TRANSLATOR_REVIEW_MODEL=gpt-5.4
BOOK_TRANSLATOR_MAX_TOKENS_PARAMETER=max_completion_tokens

BOOK_TRANSLATOR_TRANSLATION_INPUT_USD_PER_1M=0.75
BOOK_TRANSLATOR_TRANSLATION_OUTPUT_USD_PER_1M=4.50
BOOK_TRANSLATOR_REVIEW_INPUT_USD_PER_1M=2.50
BOOK_TRANSLATOR_REVIEW_OUTPUT_USD_PER_1M=15.00

BOOK_TRANSLATOR_MAX_LLM_COST_USD_PER_RUN=5
BOOK_TRANSLATOR_MAX_SOURCE_WORDS=500000
BOOK_TRANSLATOR_MAX_SOURCE_BYTES=50000000
BOOK_TRANSLATOR_MAX_CHAPTER_CHARS=60000
BOOK_TRANSLATOR_TRANSLATION_MAX_TOKENS=32000
BOOK_TRANSLATOR_REVIEW_MAX_TOKENS=8000
BOOK_TRANSLATOR_MAX_REVIEW_ROUNDS=2
BOOK_TRANSLATOR_LLM_TIMEOUT_SECONDS=300
BOOK_TRANSLATOR_WORKFLOW_REVISION=apify-managed-llm-mvp

BOOK_TRANSLATOR_SKIP_CHARGING=true
```

Do not enable a reasoning effort for the first smoke test. The models default to `none`, which keeps latency and cost easier to interpret.

The token prices above are owner-side metering inputs, not customer pricing. Update them whenever provider pricing changes.

## First input

Upload `examples/apify-smoke-book.txt`.

Use:

```json
{
  "bookFiles": ["<uploaded-file-url>"],
  "targetLanguage": "Russian",
  "sourceLanguage": "English",
  "title": "The Lantern Window",
  "author": "Book Translator smoke fixture",
  "outputFormat": "both"
}
```

The Apify input form will produce the uploaded-file URL automatically.

## Expected result

The run should:

1. extract two chapters;
2. translate chapter 1;
3. persist Translator acceptance evidence;
4. independently review it;
5. correct it only if required;
6. persist current PASS evidence;
7. save `CHAPTER_000001`;
8. repeat for chapter 2;
9. validate the durable workspace;
10. produce `OUTPUT_MARKDOWN`, `OUTPUT_EPUB`, and `SUMMARY`.

`SUMMARY` should include actual provider token usage and the computed owner LLM cost.

No LLM credential should appear in the dataset, key-value-store records, logs, output files, or error summary.

## Smoke-test acceptance gate

Do not enable charging or publish to Store until all of these are true:

- build succeeds from the Git branch;
- the sample run succeeds end-to-end;
- both chapter recovery keys are present;
- Markdown and EPUB are present;
- final validation passes;
- source and translated content are not leaked into logs;
- the API key is absent from all visible outputs/logs;
- LLM cost in `SUMMARY` is plausible against provider usage;
- a deliberately low owner LLM ceiling fails before an over-budget request;
- a deliberately low chapter limit fails before the first LLM call;
- a failed review round does not charge a chapter;
- a completed reviewed chapter remains downloadable after a later chapter failure.

## After the smoke test

Run at least one representative short book and one representative novel. Record:

- source words;
- translation input/output tokens;
- review input/output tokens;
- number of correction rounds;
- total LLM cost;
- run time;
- quality failures found manually.

Use those measurements to set PPE prices. Do not derive public pricing from the synthetic smoke fixture.

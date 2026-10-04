# Private Apify smoke test

This runbook is for the `apify-managed-llm-mvp` branch. It keeps the Actor private and disables end-user charging while measuring real LLM cost and validating the full workflow.

## Source

Use this Git repository source URL in Apify:

```text
https://github.com/tim8es/book-translator#apify-managed-llm-mvp
```

Apify supports selecting a branch by adding it as the URL fragment.

## Owner environment

For the private smoke test, the runtime is already configured with these defaults:

- Translator: `gpt-6-luna`
- Reviewer: `gpt-6.1-sol`
- reasoning effort: `high` for both
- Chat Completions token parameter: `max_completion_tokens`
- Translator max completion budget: 64,000 tokens
- Reviewer max completion budget: 16,000 tokens
- end-user charging: disabled by default on this private MVP branch

The only required owner secret is:

```text
BOOK_TRANSLATOR_LLM_API_KEY=<secret>
```

Mark it as **Secret** in Apify. Do not place it in GitHub, Actor input, logs, or source files.

Current owner-side metering defaults for the selected OpenAI models are:

```text
gpt-6-luna:
  input=$0.10/M
  cached_input=$0.01/M
  cache_write=$0.125/M
  output=$0.50/M

gpt-6.1-sol:
  input=$2.00/M
  cached_input=$0.10/M
  cache_write=$2.50/M
  output=$10.00/M
```

These rates apply to the short-context requests used by the current chapter limit. They are runtime defaults, can be overridden with owner environment variables, and must be reviewed whenever provider pricing changes.

Optional overrides remain available for model IDs, reasoning effort, token prices, source limits, token budgets, timeout, workflow revision, and the owner LLM cost ceiling.

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

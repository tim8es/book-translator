# Apify Store release gate — Book Translator

Status on 2026-10-12: **source prepared; NOT publicly published; live Apify run unverified**.

## 1. Create or locate the private Actor in Apify Console

- [ ] In [Apify Console](https://console.apify.com/actors), use **Development > My Actors**, create/select `book-translator-managed-llm`.
- [ ] Set **Source type: Git repository**, Git URL: `https://github.com/tim8es/book-translator#apify-managed-llm-mvp`.
- [ ] Build manually, or enable GitHub auto-build after the first green build. Verify `.actor/actor.json` and Dockerfile are used and the build **SUCCEEDED**.
- [ ] Select **limited permissions**; this Actor needs only its own run storage, output and charging.
- [ ] Configure minimum memory 256 MB, default 512 MB, maximum 2048 MB unless cloud measurements demonstrate a need to change.
- [ ] Keep Actor **private** until all gates below pass.

## 2. Configure provider and secrets

- [ ] In Actor settings, set `BOOK_TRANSLATOR_LLM_API_KEY` as **Secret**, never input, repository code, or log text.
- [ ] Verify `BOOK_TRANSLATOR_LLM_BASE_URL` routes to the real, authorized OpenAI-compatible endpoint supporting **both selected model IDs**. The default is `https://api.openai.com/v1`; do not assume the configured model IDs are available there without a real provider test.
- [ ] For a Cloudflare Access-protected gateway, set both `BOOK_TRANSLATOR_LLM_CLOUDFLARE_ACCESS_CLIENT_ID` and `BOOK_TRANSLATOR_LLM_CLOUDFLARE_ACCESS_CLIENT_SECRET` as **Secrets**; keep base URL HTTPS.
- [ ] Verify `gpt-6-luna` and `gpt-6.1-sol` names, `reasoning_effort=high`, `max_completion_tokens`, and provider token usage. Model and gateway names are not proof of actual availability.
- [ ] Keep `BOOK_TRANSLATOR_SKIP_CHARGING=true` **only for owner-run private smoke tests**.
- [ ] Confirm rate variables reflect current provider charges; runtime defaults are historical assumptions, not externally verified prices.

## 3. Mandatory private runs

Run `docs/APIFY_PRIVATE_SMOKE.md` using the bundled two-chapter fixture:

- [ ] Private build and 2-chapter run succeed with **actual LLM**.
- [ ] Both `CHAPTER_000001` and `CHAPTER_000002` are recoverable.
- [ ] `OUTPUT_EPUB`, `OUTPUT_MARKDOWN`, and `SUMMARY` exist and download correctly.
- [ ] EPUB opens in a real reader; content/language checked manually for both chapters.
- [ ] Store run ID, actual input/output/review tokens, total model cost, compute units, and duration in a private record; do not put text of the book or secrets in public issues.
- [ ] Confirm no API key or Cloudflare token in logs, output, failure summaries, or dataset items.
- [ ] Verify early rejection of unsupported formats, overly-long chapters, and insufficient LLM budget.
- [ ] Test one short representative book and one representative novel (with realistic chapter size) before presenting it as a reliable book-translator service.
- [ ] Separate smoke fixture quality from actual literary quality; get manual review of representative paragraphs.

## 4. Monetization (never publish owner-funded LLM work at zero revenue)

- [ ] Use **Pay per event** and configure the custom events **exactly** `book-started` and `translation-1k-words` in the Publishing > Monetization screen.
- [ ] Select `translation-1k-words` as the primary event.
- [ ] Remove or set the synthetic `apify-default-dataset-item` fee to zero; it must not double-charge chapter-progress rows.
- [ ] Decide separately whether to retain the synthetic `apify-actor-start` price; if retained, include it explicitly in full-book capacity checks and all published price examples. The current code checks only the two **custom** event prices, so **leave the synthetic start price at zero for this release** unless that code is extended and tested.
- [ ] Determine actual custom event prices from costs of real cloud runs using `docs/APIFY_PRICE_MODEL.md`.
- [ ] Set an appropriate minimum user maximum run charge; code still preflights full-book affordability before model work.
- [ ] Set `BOOK_TRANSLATOR_SKIP_CHARGING=false` **after** events are configured and tested with a paid account.
- [ ] Verify a paid Apify test account is charged correctly and a free plan exits before any remote file download or LLM expense, with a clear status.
- [ ] Verify a failed review round doesn't bill an unreviewed chapter; previously completed chapters remain accessible.
- [ ] Complete developer payout, tax, and identity-verification steps required by Apify.

## 5. Store listing and review

- [x] Public-facing README in `.actor/README.md` covering input, output, privacy, limitations, recovery, and pricing logic.
- [x] Human-readable title, description and tested input schema.
- [x] Machine-readable output schema, dataset table and key-value-store collections.
- [ ] Upload a polished **Actor logo** through the Apify Publishing tab.
- [ ] Add a **real sample output from a successful test run**; do not publish an invented result as a real sample.
- [ ] Check published permissions, description, support link, category and name in Console.
- [ ] Confirm platform privacy/retention and copyright disclaimers match actual deployment.
- [ ] Inspect each publishing section until Apify marks it **complete**, then select **Publish on Store**.
- [ ] Find the public Store page and verify a live paid run, output downloads, and run analytics.

## Deployment authorization limit

The connected Apify tools in this ChatGPT session can search the public Store and inspect/run Actors **when an exact Actor ID is known**, but cannot create or configure a private Actor, its secrets, payout profile, listing/logo, or monetization. Those parts **have not been executed** and require the account owner or an authenticated browser session with the necessary privileges.

No Store publish claim is permitted until the Store URL and successful run ID have actually been verified.

Official references:
- https://docs.apify.com/actors/publishing/publish
- https://docs.apify.com/actors/publishing/monetize/pay-per-event
- https://docs.apify.com/actors/development/deployment/source-types
- https://docs.apify.com/actors/development/permissions

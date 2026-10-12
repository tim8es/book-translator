# Apify Book Translator — unit economics and PPE calibration

**Never set public prices from the synthetic 2-chapter fixture alone.** The operator pays the LLM provider, while Apify collects its own share and platform costs.

## Billable events

The current implementation uses two explicit custom events:

| Event | When charged | Units |
| --- | --- | --- |
| `book-started` | Once after source, chapter and full-book capacity validation, before LLM calls | 1 per run |
| `translation-1k-words` | Only after a chapter has current PASS evidence and its reviewed text is saved | `max(1, ceil(chapter_words/1000))` per chapter |

The word-unit event count is the **sum of each chapter rounded up separately**. A run can charge for completed chapters even if a later chapter fails. No refunds are represented in this implementation.

**Important:** Disable synthetic `apify-default-dataset-item` pricing and leave synthetic `apify-actor-start` priced at zero for this version, to avoid double charging and disagreement with `ensure_book_charge_capacity()`. Set the max-total-charge minimum based on a measured realistic book.

## Revenue formula

For each paying-user run:

```text
C = number of chapters
U = sum(max(1, ceil(chapter_source_words / 1000)))
S = price of custom book-started event, USD
W = price of custom translation-1k-words event, USD
R = S + U*W                       # gross billed events
Apify developer revenue ≈ 0.80*R # Apify's documented 20% share
Net = 0.80*R - LLMCost - PlatformCost - OtherCost
```

The 80% value and platform-cost subtraction are from Apify's current pay-per-event documentation. **Provider LLM cost is additional** and must be subtracted separately. Use actual prices by tier, not a synthetic model estimate. If you pass platform usage to users, document the extra charge instead of hiding it.

### Pricing safeguards

1. Calculate representative p50, p90, and worst observed provider costs per 1,000 source words, separated by translation vs. review/correction. Review costs may dominate.
2. Record actual per-run Apify compute/traffic/storage costs, including failed attempts and retained output.
3. Choose `S` and `W` so that `0.80*(S+U*W)` comfortably exceeds observed LLM + platform + failure overhead, **including small books with many short chapters**.
4. Estimate user charges for a 2-chapter smoke, a small ebook, and a novel with varied chapter lengths; add exactly those examples to Store README after prices are configured.
5. Disable public monetization until over-budget and partially failed runs are tested with live pay-per-event prices.

## Free-plan risk

According to Apify's current documentation, free-plan runs do not produce developer revenue, while external LLM costs still accrue to the developer. This Actor now exits early for free-plan users when `BOOK_TRANSLATOR_SKIP_CHARGING=false`, before downloading input or calling the model. The Store README and input form disclose this restriction. Introduce a genuinely cost-capped free tier only after measuring actual provider costs and fraud risk.

## Required real-run measurement table

| Metric | Two-chapter fixture | Short book | Novel |
| --- | --- | --- | --- |
| Source words | Not measured live | Not measured | Not measured |
| Translation input/output tokens | Not measured | Not measured | Not measured |
| Review input/output tokens | Not measured | Not measured | Not measured |
| Correction rounds | Not measured live | Not measured | Not measured |
| Owner LLM USD | Not measured | Not measured | Not measured |
| Apify platform USD | Not measured | Not measured | Not measured |
| Run time | Not measured | Not measured | Not measured |
| Final quality manual review | Pending | Pending | Pending |

Official references:
- https://docs.apify.com/actors/publishing/monetize/pay-per-event
- https://docs.apify.com/actors/publishing/monetize/pricing-and-costs
- https://docs.apify.com/actors/monetize/set-up-monetization

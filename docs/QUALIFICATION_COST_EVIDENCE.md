# Qualification cost evidence

The paired and long-agent synthetic reports expose runner-only reserved calls,
recorded completions, per-role usage observation counts and complete token
totals where actually available. Judge and audit calls remain separately
counted. A reservation without a recorded completion is not assumed free.
Legacy aggregates without observation coverage and partial/missing provider
usage cannot establish complete token totals.

Pi's current print transport does not provide provider token usage; do not
estimate it from characters. The report deliberately emits null billed cost:
tariffs, cache billing, upstream routing and provider invoices are not yet
verified. The next qualification step is to instrument the exact pinned Pi
transport and compare measured usage with OmniRoute/provider billing. Only
then compare cost per accepted source unit on matched quality fixtures,
including retries and judging separately. No production config or literary
revision is changed by this qualification report.

## Experimental Pi JSONL transport

With BOOK_TRANSLATOR_PI_USAGE_EVENTS=on, the adapter consumes the pinned Pi
v1.6.0 JSON event protocol rather than plain print output. It accepts only one
settled tool-free assistant turn, extracts its final text, and exposes consistent
positive-output usage counters (prompt includes cache reads/writes). Missing,
zero-filled or inconsistent usage remains unknown. SDK cost is not an invoice.
The default remains off; qualify on a synthetic fixture before enabling it.
See https://github.com/earendil-works/pi/blob/v1.6.0/packages/coding-agent/docs/json.md.

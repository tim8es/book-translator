"""Opt-in coherent batching; scheduling only, never acceptance or review evidence."""
from __future__ import annotations
import os

MAX_SPANS = 96
MAX_SOURCE_CHARS = 12_000
MAX_CORRECTIONS = 8

def enabled():
    value = os.environ.get("BOOK_TRANSLATOR_COHERENT_BATCHES", "off")
    if value not in {"on", "off"}:
        raise ValueError("BOOK_TRANSLATOR_COHERENT_BATCHES must be on or off")
    return value == "on"

def structural_selection(helper, plan, completed, legacy_limit, pressure=0, fallback=False):
    # The pinned helper validates both the immutable plan and the durable stage.
    baseline = helper.next_spans(plan, completed, limit=legacy_limit)
    if not enabled() or pressure or fallback or not baseline:
        return baseline
    chosen = []
    chars = 0
    for item in helper.spans(plan):
        if item["id"] in completed:
            continue
        size = len(item["source"])
        if size > MAX_SOURCE_CHARS:
            return baseline
        if chosen and (len(chosen) >= MAX_SPANS or chars + size > MAX_SOURCE_CHARS):
            break
        chosen.append(item)
        chars += size
    return chosen

def correction_selection(pending, translation, baseline, rejected, unit_id, record_id,
                         failures=0, pressure=0, isolated=False):
    # Only distinct, exact, non-overlapping replacement anchors may expand a batch.
    # Insertion, structural, critical and previously rejected work retains the old path.
    if not enabled() or failures or pressure or isolated or not pending:
        return baseline
    retry_ids = {x["finding_id"] for x in rejected
                 if x.get("unit_id") == unit_id and x.get("review_record_id") == record_id}
    chosen = []
    ranges = []
    for item in pending[:MAX_CORRECTIONS]:
        anchor = item.get("translation_anchor")
        if (item["id"] in retry_ids or item.get("severity") == "critical"
                or item.get("category") in {"structure", "omission"}
                or not isinstance(anchor, dict) or anchor.get("occurrence") != 0):
            break
        quote = anchor.get("quote")
        if (not isinstance(quote, str) or not quote or translation.count(quote) != 1
                or "<!--" in quote or "](" in quote):
            break
        start = translation.index(quote)
        end = start + len(quote)
        if any(start < b and a < end for a, b in ranges):
            break
        chosen.append(item)
        ranges.append((start, end))
    # Never expand across a risk boundary; if first item is unsafe, isolate it.
    return chosen or pending[:1]

def prefer_direct_review(system_chars, user_chars, source_chars, direct_limit, prompt_limit):
    # Existing saved partition, provider pressure and protocol fallback still take priority.
    return enabled() and 0 < source_chars <= direct_limit and 0 < system_chars + user_chars <= prompt_limit

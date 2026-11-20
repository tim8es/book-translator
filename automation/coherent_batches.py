"""Opt-in coherent batching; scheduling only, never acceptance or review evidence."""
from __future__ import annotations
import os
import json
import re

MAX_SPANS = 96
MAX_SOURCE_CHARS = 12_000
MAX_CORRECTIONS = 8

# An output with many separate IDs is fragile even when the source fits a
# model's input context. Start at a moderate output budget, and halve the
# number of IDs after each protocol error; never drop to one ID immediately.
CONTEXT_INITIAL_SOURCE_CHARS = 5000
CONTEXT_INITIAL_SPANS = 8
CONTEXT_MIN_SOURCE_CHARS = 1600
CONTEXT_MIN_SPANS = 1
CONTEXT_PROGRESS_KEY = "structural_full_context_batch_profiles"


def context_batch_caps(status, unit_id, source_sha256):
    """Durable chapter-local output size, bound to the exact source identity."""
    table = status.get(CONTEXT_PROGRESS_KEY, {})
    if not isinstance(table, dict):
        raise ValueError("invalid chapter context batch checkpoint")
    row = table.get(unit_id)
    if row is None or (isinstance(row, dict) and row.get("source_sha256") != source_sha256):
        return CONTEXT_INITIAL_SOURCE_CHARS, CONTEXT_INITIAL_SPANS
    if (not isinstance(row, dict) or
            type(row.get("max_source_chars")) is not int or
            not CONTEXT_MIN_SOURCE_CHARS <= row["max_source_chars"] <= CONTEXT_INITIAL_SOURCE_CHARS or
            type(row.get("max_spans")) is not int or
            not CONTEXT_MIN_SPANS <= row["max_spans"] <= CONTEXT_INITIAL_SPANS):
        raise ValueError("invalid durable chapter context output budget")
    return row["max_source_chars"], row["max_spans"]


def shrink_context_batch(status, unit_id, source_sha256):
    """Record a protocol failure; return True only after reaching single-span minimum."""
    chars, spans = context_batch_caps(status, unit_id, source_sha256)
    if chars == CONTEXT_MIN_SOURCE_CHARS and spans == CONTEXT_MIN_SPANS:
        return True
    smaller_chars = max(CONTEXT_MIN_SOURCE_CHARS, chars * 3 // 5)
    smaller_spans = max(CONTEXT_MIN_SPANS, spans // 2)
    status.setdefault(CONTEXT_PROGRESS_KEY, {})[unit_id] = {
        "source_sha256": source_sha256,
        "max_source_chars": smaller_chars,
        "max_spans": smaller_spans,
    }
    return False

def adjust_after_sparse_response(status, unit_id, source_sha256, requested, accepted):
    """Bound repeated incomplete *valid* output without rejecting accepted IDs.

    A model may legally return only part of a requested ID set. This signal
    decreases future batch sizes, while every accepted source ID is checkpointed.
    """
    if (type(requested) is not int or type(accepted) is not int
            or not 1 <= accepted <= requested):
        raise ValueError("invalid structural response coverage counters")
    if requested >= 4 and accepted * 2 <= requested:
        shrink_context_batch(status, unit_id, source_sha256)
        return True
    return False


def enabled():
    value = os.environ.get("BOOK_TRANSLATOR_COHERENT_BATCHES", "off")
    if value not in {"on", "off"}:
        raise ValueError("BOOK_TRANSLATOR_COHERENT_BATCHES must be on or off")
    return value == "on"

def structural_selection(helper, plan, completed, legacy_limit, pressure=0, fallback=False, *,
                         full_chapter=False, source_char_limit=None, span_limit=None):
    # The pinned helper validates both the immutable plan and the durable stage.
    baseline = helper.next_spans(plan, completed, limit=legacy_limit)
    if (not enabled() and not full_chapter) or pressure or fallback or not baseline:
        return baseline
    if full_chapter:
        char_budget = CONTEXT_INITIAL_SOURCE_CHARS if source_char_limit is None else source_char_limit
        span_budget = CONTEXT_INITIAL_SPANS if span_limit is None else span_limit
        if (type(char_budget) is not int or
                not CONTEXT_MIN_SOURCE_CHARS <= char_budget <= CONTEXT_INITIAL_SOURCE_CHARS or
                type(span_budget) is not int or
                not CONTEXT_MIN_SPANS <= span_budget <= CONTEXT_INITIAL_SPANS):
            raise ValueError("invalid full-context translator output budget")
    else:
        char_budget,span_budget=MAX_SOURCE_CHARS,MAX_SPANS
    chosen = []
    chars = 0
    for item in helper.spans(plan):
        if item["id"] in completed:
            continue
        size = len(item["source"])
        if size > char_budget:
            return baseline
        if chosen and (len(chosen) >= span_budget or chars + size > char_budget):
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
    for item in (pending if os.environ.get("BOOK_TRANSLATOR_TRANSACTIONAL_QUALITY") == "on" else pending[:MAX_CORRECTIONS]):
        if len(chosen)>=MAX_CORRECTIONS:
            break
        anchor = item.get("translation_anchor")
        if (item["id"] in retry_ids or item.get("severity") == "critical"
                or item.get("category") in {"structure", "omission"}
                or (item.get("category") == "referent" and item.get("severity") == "major")
                or not isinstance(anchor, dict) or anchor.get("occurrence") != 0):
            if os.environ.get("BOOK_TRANSLATOR_TRANSACTIONAL_QUALITY") == "on" and chosen:
                continue
            break
        quote = anchor.get("quote")
        if (not isinstance(quote, str) or not quote or translation.count(quote) != 1
                or "<!--" in quote or "](" in quote):
            if os.environ.get("BOOK_TRANSLATOR_TRANSACTIONAL_QUALITY") == "on" and chosen:
                continue
            break
        start = translation.index(quote)
        end = start + len(quote)
        if any(start < b and a < end for a, b in ranges):
            if os.environ.get("BOOK_TRANSLATOR_TRANSACTIONAL_QUALITY") == "on" and chosen:
                continue
            break
        chosen.append(item)
        ranges.append((start, end))
    # Never expand across a risk boundary; if first item is unsafe, isolate it.
    return chosen or pending[:1]

def prefer_direct_review(system_chars, user_chars, source_chars, direct_limit, prompt_limit):
    # Existing saved partition, provider pressure and protocol fallback still take priority.
    return enabled() and 0 < source_chars <= direct_limit and 0 < system_chars + user_chars <= prompt_limit

_BLOCK = re.compile(r"<!--\s*bt-block:(\d+)\s*-->")

def block_ranges(text):
    markers = list(_BLOCK.finditer(text))
    ids = [m.group(1) for m in markers]
    if not ids or len(ids) != len(set(ids)):
        return {}
    return {m.group(1): (m.end(), markers[i+1].start() if i+1 < len(markers) else len(text))
            for i, m in enumerate(markers)}

def block_address_prompt(source, target):
    left, right = block_ranges(source), block_ranges(target)
    if not enabled() or not left or list(left) != list(right):
        return ""
    return (
        "\nOPTIONAL BLOCK ADDRESS: For each exact anchor you may additionally include "
        "block_id (the digits as printed in its preceding bt-block marker, as a string). "
        "Copy the exact quote within that block; occurrence must be 0. "
        "The program resolves that exact block/quote to the canonical anchor. "
        "If a quote appears twice inside the block or is the first of several identical "
        "quotes across the unit, use a longer exact unique excerpt instead. "
        "Do not include markers in quotes, guess block identifiers, or change prior recheck references. "
        "Normal canonical anchors remain supported.\n")

def normalize_block_addresses(answer, source, target):
    # Adapter only: never edit an allegation, exact quote, resolves or outcome.
    # A block address that cannot be represented in the pinned V2 contract fails closed.
    if not enabled():
        return answer
    try:
        data = json.loads(answer)
    except (TypeError, ValueError):
        return answer  # Existing JSON validation / repair owns malformed responses.
    if not isinstance(data, dict) or not isinstance(data.get("findings"), list):
        return answer
    selected = [(f, field) for f in data["findings"] if isinstance(f, dict)
                for field in ("source_anchor", "translation_anchor")
                if isinstance(f.get(field), dict) and "block_id" in f[field]]
    if not selected:
        return answer
    left, right = block_ranges(source), block_ranges(target)
    if not left or list(left) != list(right):
        raise ValueError("block addressing requires identical unique source-owned block IDs")
    for f, field in selected:
        a = f[field]
        text, ranges = (source, left) if field == "source_anchor" else (target, right)
        q, bid = a.get("quote"), a.get("block_id")
        if (set(a) != {"quote", "occurrence", "block_id"}
                or type(a.get("occurrence")) is not int or a["occurrence"] != 0
                or not isinstance(q, str) or not q.strip() or len(q) > 300
                or not isinstance(bid, str) or bid not in ranges):
            raise ValueError("invalid block-local exact quote")
        lo, hi = ranges[bid]
        local = text[lo:hi]
        if local.count(q) != 1:
            raise ValueError("block-local quote is absent or ambiguous")
        position = lo + local.index(q)
        matches = []
        offset = 0
        while True:
            index = text.find(q, offset)
            if index < 0:
                break
            matches.append(index)
            offset = index + len(q)
        if position not in matches:
            raise ValueError("block-local quote cannot be represented canonically")
        occurrence = matches.index(position)
        if (occurrence == 0 and len(matches) != 1) or occurrence >= 10000:
            raise ValueError("block-local quote needs a longer exact unique excerpt")
        f[field] = {"quote": q, "occurrence": occurrence}
    return json.dumps(data, ensure_ascii=False)

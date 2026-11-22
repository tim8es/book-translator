"""Opt-in, source/hash-bound feedback for repeated literary correction attempts.

This is an execution hint, not a new literary role or permission to bypass
pinned anchored patches, independent review, source checks or review limits.
Only bounded diagnostics go into durable status; excerpts go to the active
Corrector prompt and are never persisted as execution metadata.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections import Counter

SEVERITY = {"minor": 1, "major": 3, "critical": 5}
CATEGORIES = {"referent", "meaning", "modality", "omission", "addition",
              "structure", "terminology", "style", "voice", "register"}


def enabled(env: dict | None = None) -> bool:
    config = os.environ if env is None else env
    choice = config.get("BOOK_TRANSLATOR_CORRECTION_FEEDBACK", "off")
    if choice not in {"on", "off"}:
        raise ValueError("BOOK_TRANSLATOR_CORRECTION_FEEDBACK must be on or off")
    return choice == "on"


def _open(record: dict) -> list[dict]:
    report = record.get("quality_report")
    if not isinstance(report, dict) or report.get("evidence_version") != 2:
        return []
    findings = report.get("findings")
    if not isinstance(findings, list):
        return []
    return [f for f in findings
            if isinstance(f, dict) and f.get("resolved") is False
            and isinstance(f.get("category"), str)]


def _identity(finding: dict) -> str | None:
    anchor = finding.get("source_anchor")
    quote = anchor.get("quote") if isinstance(anchor, dict) else None
    occurrence = anchor.get("occurrence") if isinstance(anchor, dict) else None
    if not isinstance(quote, str) or not quote.strip() or type(occurrence) is not int:
        return None
    category = finding.get("category")
    if not isinstance(category, str):
        return None
    key = json.dumps([category, " ".join(quote.casefold().split()), occurrence],
                     ensure_ascii=False)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _score(items: list[dict]) -> int:
    return sum(SEVERITY.get(item.get("severity"), 1) for item in items)


def plan(ledger: dict, chapter: dict, record: dict, selected: list[dict],
         no_effect: dict | None = None) -> dict:
    """Return a bounded strategy using only verified historical rejected reviews.

    Previous translation anchors are *never* recycled. Current reviewer
    anchors are the only legal edit coordinates.
    """
    acceptance = chapter.get("translation_acceptance")
    if (not isinstance(acceptance, dict)
            or record.get("outcome") != "CORRECTIONS_REQUIRED"
            or acceptance.get("translation_sha256") != record.get("translation_sha256")
            or acceptance.get("source_sha256") != record.get("source_sha256")
            or not isinstance(ledger.get("records"), list)
            or not isinstance(selected, list) or not selected):
        raise ValueError("correction feedback requires current accepted review")
    ids = {f.get("id") for f in _open(record)}
    if any(not isinstance(f, dict) or f.get("id") not in ids for f in selected):
        raise ValueError("correction feedback has unrecognized finding")
    older = sorted(
        (r for r in ledger["records"]
         if isinstance(r, dict)
         and r.get("unit_id") == chapter.get("unit_id")
         and r.get("outcome") == "CORRECTIONS_REQUIRED"
         and r.get("source_sha256") == record["source_sha256"]
         and type(r.get("sequence")) is int
         and r["sequence"] < record["sequence"]),
        key=lambda r: r["sequence"], reverse=True,
    )
    # Distinct accepted translation revisions only; avoid overcounting a review
    # repeated without changing the artifact.
    previous = []
    known_sha = {record["translation_sha256"]}
    for r in older:
        target = r.get("translation_sha256")
        if not isinstance(target, str) or target in known_sha:
            continue
        known_sha.add(target)
        previous.append(r)
        if len(previous) == 4:
            break
    earlier = Counter(
        identity
        for r in previous
        for finding in _open(r)
        if (identity := _identity(finding)) is not None
    )
    recurring = [f for f in selected if (key := _identity(f))
                 and earlier[key] >= 1]
    categories = Counter(f.get("category", "unknown") for f in selected)
    no_effect_count = (
        no_effect.get("count", 0)
        if isinstance(no_effect, dict)
        and no_effect.get("unit_id") == chapter.get("unit_id")
        and no_effect.get("review_record_id") == record.get("record_id")
        and no_effect.get("translation_sha256") == record["translation_sha256"]
        else 0
    )
    scores = [_score(_open(r)) for r in reversed(previous)] + [_score(_open(record))]
    # Compare net quality over two different accepted translations rather
    # than requiring a monotone score. Oscillation (8 -> 16 -> 13) still
    # means the latest attempt is worse than the first observed one.
    nonimproving = (len(scores) >= 3 and scores[-1] >= scores[-3])
    # Whole-chapter rewrites are intentionally NOT a strategy: the pinned
    # correction contract permits only the reviewer's exact target anchors.
    strategy = ("replan_anchored_clause"
                if recurring or nonimproving or no_effect_count else "focused_patch")
    details = []
    selected_keys = {_identity(f) for f in selected}
    for r in previous[:2]:
        for f in _open(r):
            if _identity(f) not in selected_keys:
                continue
            detail = f.get("detail")
            if isinstance(detail, str) and detail.strip():
                details.append({"category": f["category"],
                                "prior_review_feedback": detail[:260]})
            if len(details) >= 4:
                break
        if len(details) >= 4:
            break
    evidence = {
        "schema_version": 1,
        "unit_id": chapter["unit_id"],
        "review_record_id": record["record_id"],
        "translation_sha256": record["translation_sha256"],
        "strategy": strategy,
        "prior_distinct_rejected_reviews": len(previous),
        "recurring_anchored_findings": len(recurring),
        "nonimproving_reviews": nonimproving,
        "no_effect_attempts": min(no_effect_count, 9),
        "selected_categories": dict(sorted(categories.items())),
        "severity_scores_old_to_new": scores[-5:],
    }
    instruction = (
        "\nCORRECTION FEEDBACK (historical reviews are diagnostic, NEVER "
        "editable coordinates): " +
        json.dumps({"history": evidence, "previous_review_explanations": details},
                   ensure_ascii=False) +
        "\nThe reviewer-approved CURRENT translation anchors in this request "
        "are the ONLY allowed replacement locations. Previous review anchors "
        "and old translation text are not edit targets. "
    )
    if strategy == "replan_anchored_clause":
        instruction += (
            "PRIOR STRATEGY DID NOT CONVERGE. Do not repeat a prior ineffective "
            "word substitution. Redraft the full meaningful clause WITHIN each "
            "current reviewer's exact quoted target span where that span permits "
            "it; never enlarge the target or edit adjacent unanchored text. "
            "If the anchor is too narrow for a safe repair, do not invent a "
            "broader anchor. Recheck who speaks, who acts, pronoun and verb gender, "
            "source meaning, omissions and repetition against supplied context. "
        )
    else:
        instruction += (
            "Use a minimal, source-faithful rewrite of the current anchored "
            "passage, checking referents, dialogue attribution and grammar. "
        )
    instruction += (
        "Before answering, compare the proposed replacement against the "
        "current anchor: it must actually change the translation, preserve "
        "meaning and all protected markers. Return the existing patch JSON "
        "schema only; do not return commentary or a PASS verdict."
    )
    return {"audit": evidence, "prompt": instruction}

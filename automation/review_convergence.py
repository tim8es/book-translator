"""Source-/artifact-bound diagnostics for correction/review convergence.

The active literary workflow remains authoritative for review acceptance.
This helper can only identify a stalled *rejected* review sequence for
operator attention; it never resolves findings, waives requirements or PASSes.
"""
from __future__ import annotations

from collections import Counter

SEVERITY_WEIGHTS = {"minor": 1, "major": 3, "critical": 5}
MIN_REJECTED_REVIEWS = 3


def _summary(record: dict) -> dict | None:
    report = record.get("quality_report")
    if not isinstance(report, dict) or report.get("evidence_version") != 2:
        return None
    findings = report.get("findings")
    if not isinstance(findings, list):
        return None
    counts: Counter = Counter()
    score = 0
    for item in findings:
        if not isinstance(item, dict) or type(item.get("resolved")) is not bool:
            return None
        if item["resolved"]:
            continue
        weight = SEVERITY_WEIGHTS.get(item.get("severity"))
        if weight is None:
            return None
        score += weight
        counts[item["severity"]] += 1
    return {
        "review_record_id": record["record_id"],
        "translation_sha256": record["translation_sha256"],
        "score": score,
        "unresolved": sum(counts.values()),
        "major_or_critical": counts["major"] + counts["critical"],
    }


def stalled_rejected_reviews(ledger: dict, chapter: dict,
                             *, min_reviews: int = MIN_REJECTED_REVIEWS) -> dict | None:
    """Identify 2+ successive accepted corrections without net review progress.

    Score is explicitly an *imperfect diagnostic* of independently reviewed
    findings; a newly discovered old error can make the score rise. We stop
    spending model calls to escalate the case, not to assert lack of quality.
    Only a sequence of different, canonical, hash-bound translation artifacts
    and accepted anchored v2 reviews is considered.
    """
    if type(min_reviews) is not int or min_reviews < 3:
        raise ValueError("stagnation window must cover at least three reviews")
    if not isinstance(ledger, dict) or not isinstance(ledger.get("records"), list):
        return None
    if not isinstance(chapter, dict):
        return None
    acceptance = chapter.get("translation_acceptance")
    if not isinstance(acceptance, dict):
        return None
    source_hash = acceptance.get("source_sha256")
    target_hash = acceptance.get("translation_sha256")
    unit_id = chapter.get("unit_id")
    if not isinstance(source_hash, str) or not isinstance(target_hash, str):
        return None
    eligible = [item for item in ledger["records"]
                if isinstance(item, dict)
                and item.get("unit_id") == unit_id
                and item.get("source_sha256") == source_hash
                and item.get("outcome") in {"PASS", "CORRECTIONS_REQUIRED"}]
    if len(eligible) < min_reviews:
        return None
    # Preserve ledger append order: sorting by arbitrary model-supplied fields
    # could mix evidence from separate accepted outcomes or revisions.
    window = eligible[-min_reviews:]
    if any(r.get("outcome") != "CORRECTIONS_REQUIRED" for r in window):
        return None
    if window[-1].get("translation_sha256") != target_hash:
        return None
    if len({r.get("translation_sha256") for r in window}) != min_reviews:
        return None
    if any(not isinstance(r.get("record_id"), str) or
           not isinstance(r.get("translation_sha256"), str)
           for r in window):
        return None
    summaries = [_summary(record) for record in window]
    if any(item is None for item in summaries):
        return None
    scores = [item["score"] for item in summaries]
    if not all(later >= former for former, later in zip(scores, scores[1:])):
        return None
    return {
        "reason": "consecutive-reviews-without-net-severity-improvement",
        "review_record_ids": [item["review_record_id"] for item in summaries],
        "translation_sha256": target_hash,
        "source_sha256": source_hash,
        "severity_scores": scores,
        "unresolved_counts": [item["unresolved"] for item in summaries],
        "major_or_critical_counts": [item["major_or_critical"] for item in summaries],
        "action": "operator-attention-no-pass",
    }


def costly_nonconvergence(ledger: dict, chapter: dict, status: dict) -> dict | None:
    """Two rejected artifacts, measured cost and worsening severity; no PASS.

    Use only observed work for this unit. This is a conservative early
    attention candidate, not a literary judgment or an override of admission.
    """
    acceptance = chapter.get("translation_acceptance", {})
    unit = chapter.get("unit_id")
    if (not isinstance(acceptance, dict) or not isinstance(unit, str)
            or not isinstance(status.get("correction_rounds"), dict)
            or type(status["correction_rounds"].get(unit)) is not int
            or status["correction_rounds"][unit] < 1):
        return None
    records = [r for r in ledger.get("records", [])
               if isinstance(r, dict) and r.get("unit_id") == unit
               and r.get("source_sha256") == acceptance.get("source_sha256")]
    if len(records) < 2:
        return None
    a, b = records[-2:]
    if (a.get("outcome") != "CORRECTIONS_REQUIRED" or b.get("outcome") != "CORRECTIONS_REQUIRED"
            or a.get("translation_sha256") == b.get("translation_sha256")
            or b.get("translation_sha256") != acceptance.get("translation_sha256")):
        return None
    first, last = _summary(a), _summary(b)
    if (not first or not last or last["score"] <= first["score"]
            or last["major_or_critical"] < first["major_or_critical"]):
        return None
    calls = [m for m in status.get("provider_call_metrics", [])
             if isinstance(m, dict) and m.get("unit_id") == unit
             and m.get("role") in {"reviewer", "corrector"}
             and m.get("outcome") == "response"
             and type(m.get("duration_ms")) is int and m["duration_ms"] >= 0]
    elapsed = sum(m["duration_ms"] for m in calls)
    if len(calls) < 10 or elapsed < 1_200_000:
        return None
    return {"reason": "costly-rejected-reviews-without-net-improvement",
            "review_record_ids": [a["record_id"], b["record_id"]],
            "translation_sha256": b["translation_sha256"],
            "source_sha256": b["source_sha256"],
            "severity_scores": [first["score"], last["score"]],
            "unresolved_counts": [first["unresolved"], last["unresolved"]],
            "major_or_critical_counts": [first["major_or_critical"], last["major_or_critical"]],
            "observed_responses": len(calls), "observed_duration_ms": elapsed,
            "action": "operator-attention-no-pass"}

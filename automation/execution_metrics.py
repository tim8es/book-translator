"""Aggregate content-free Auto metrics from this runtime's first observed event."""
from __future__ import annotations
from datetime import datetime, timezone
import os
import re

def _metrics(status: dict) -> dict:
    result = status.setdefault("execution_metrics", {
        "schema_version": 1, "since": datetime.now(timezone.utc).isoformat(),
        "api": {}, "accepted_operations": {},
    })
    if (not isinstance(result, dict) or result.get("schema_version") != 1
            or not isinstance(result.get("api"), dict)
            or not isinstance(result.get("accepted_operations"), dict)):
        raise ValueError("invalid execution metrics")
    return result

def _pi_revision_metrics(status: dict) -> dict | None:
    """Separate completed Pi metrics by exact immutable executor revision.

    The historical aggregate remains untouched. Only the opt-in Pi backend
    with an Actions-pinned full Git SHA acquires a revision-specific bucket;
    unknown/unconfigured revisions must never be attributed to current code.
    """
    if os.environ.get("BOOK_TRANSLATOR_LLM_BACKEND") != "pi":
        return None
    revision = os.environ.get("PI_EXECUTOR_SHA", "")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        return None
    revisions = _metrics(status).setdefault("runtime_revisions", {})
    if not isinstance(revisions, dict):
        raise ValueError("invalid runtime revision metrics")
    bucket = revisions.setdefault(revision, {
        "since": datetime.now(timezone.utc).isoformat(),
        "api": {}, "accepted_operations": {},
    })
    if (not isinstance(bucket, dict) or
            not isinstance(bucket.get("api"), dict) or
            not isinstance(bucket.get("accepted_operations"), dict)):
        raise ValueError("invalid runtime revision bucket")
    return bucket


def provider(status: dict, metric: dict) -> None:
    role = metric.get("role")
    if role not in {"translator", "reviewer", "corrector", "orchestrator"}:
        return
    buckets = (_metrics(status), _pi_revision_metrics(status))
    for bucket in buckets:
        if bucket is None:
            continue
        row = bucket["api"].setdefault(role, {})
        def add(key, amount):
            prior = row.get(key, 0)
            if type(prior) is not int or prior < 0:
                raise ValueError("invalid execution counter")
            row[key] = prior + amount
        add("completed_calls", 1)
        add("responses" if metric.get("outcome") == "response" else "errors", 1)
        for field in ("duration_ms", "request_chars", "prompt_tokens",
                      "completion_tokens", "total_tokens"):
            value = metric.get(field)
            if type(value) is int and value >= 0:
                add(field, value)
                if field in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    add(field + "_observations", 1)


def accepted(status: dict, role: str, outcome: str | None = None) -> None:
    keys = {"translator": "translated", "corrector": "corrected",
            "promote": "reviewed"}
    if role == "reviewer":
        key = "review_pass" if outcome == "PASS" else "review_rejected"
    else:
        key = keys[role]
    for bucket in (_metrics(status), _pi_revision_metrics(status)):
        if bucket is None:
            continue
        row = bucket["accepted_operations"]
        prior = row.get(key, 0)
        if type(prior) is not int or prior < 0:
            raise ValueError("invalid accepted operation counter")
        row[key] = prior + 1
    if role == "reviewer" and outcome == "PASS":
        accepted(status, "promote")


def quality_started(status: dict, unit_id: str, source_sha256: str,
                    model_calls: int, at: str) -> None:
    """Record an immutable baseline at the first *observed* translator acceptance.

    Existing ChatOps work without such a baseline deliberately has no measured
    end-to-end duration; do not infer it from a later correction timestamp.
    """
    if (not isinstance(unit_id, str) or not unit_id
            or not isinstance(source_sha256, str) or len(source_sha256) != 64
            or type(model_calls) is not int or model_calls < 0):
        raise ValueError("invalid quality baseline")
    timestamp = datetime.fromisoformat(at.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError("quality baseline requires timezone")
    starts = status.setdefault("quality_baselines", {})
    if not isinstance(starts, dict):
        raise ValueError("invalid quality baselines")
    old = starts.get(unit_id)
    if isinstance(old, dict) and old.get("source_sha256") == source_sha256:
        return
    starts[unit_id] = {
        "source_sha256": source_sha256,
        "first_translated_at": at,
        "model_calls_at_first_translation": model_calls,
    }


def quality_passed(status: dict, unit_id: str, source_sha256: str,
                   translation_sha256: str, model_calls: int,
                   prior_rejections: int, correction_rounds: int, at: str) -> dict:
    """Count independently accepted results, never manufacture a PASS."""
    if (not isinstance(unit_id, str) or not unit_id
            or any(not isinstance(v, str) or len(v) != 64
                   for v in (source_sha256, translation_sha256))
            or any(type(v) is not int or v < 0 for v in (
                model_calls, prior_rejections, correction_rounds))):
        raise ValueError("invalid quality completion")
    now = datetime.fromisoformat(at.replace("Z", "+00:00"))
    if now.tzinfo is None:
        raise ValueError("quality completion requires timezone")
    baseline = status.get("quality_baselines", {}).get(unit_id)
    seconds = None
    calls = None
    if isinstance(baseline, dict) and baseline.get("source_sha256") == source_sha256:
        before = datetime.fromisoformat(
            baseline["first_translated_at"].replace("Z", "+00:00"))
        start_calls = baseline.get("model_calls_at_first_translation")
        if before.tzinfo is None or type(start_calls) is not int:
            raise ValueError("invalid stored quality baseline")
        if now < before or model_calls < start_calls:
            raise ValueError("quality completion precedes its baseline")
        seconds = round((now - before).total_seconds())
        calls = model_calls - start_calls
    entry = {
        "unit_id": unit_id, "source_sha256": source_sha256,
        "translation_sha256": translation_sha256,
        "independent_pass_at": at,
        "seconds_from_first_observed_translation": seconds,
        "model_calls_after_first_observed_translation": calls,
        "prior_rejected_reviews": prior_rejections,
        "correction_rounds": correction_rounds,
    }
    history = status.setdefault("quality_pass_metrics", [])
    if not isinstance(history, list):
        raise ValueError("invalid quality pass history")
    if not any(isinstance(row, dict)
               and all(row.get(key) == entry[key] for key in (
                   "unit_id", "source_sha256", "translation_sha256"))
               for row in history):
        history.append(entry)
        status["quality_pass_metrics"] = history[-100:]
    return entry

def queue_snapshot(chapters: list[dict], ledger: dict) -> dict:
    """Read-only raw lifecycle/evidence counts; attention is not an acceptance."""
    result = {"extracted": 0, "reviewed": 0, "awaiting_review": 0,
              "correction_candidates": 0, "awaiting_promotion": 0}
    records = {r.get("unit_id"): r for r in ledger.get("records", [])
               if isinstance(r, dict)}
    for chapter in chapters:
        state = chapter.get("status")
        if state in {"extracted", "reviewed"}:
            result[state] += 1
        elif state == "translated":
            current = records.get(chapter.get("unit_id"), {})
            target = chapter.get("translation_acceptance", {}).get("translation_sha256")
            if target and current.get("translation_sha256") == target:
                if current.get("outcome") == "CORRECTIONS_REQUIRED":
                    result["correction_candidates"] += 1
                    continue
                if current.get("outcome") == "PASS":
                    result["awaiting_promotion"] += 1
                    continue
            result["awaiting_review"] += 1
    return result


TOKEN_FIELDS = ("prompt_tokens", "completion_tokens", "total_tokens")
USAGE_ROLES = ("translator", "reviewer", "corrector", "orchestrator")


def qualification_usage(status: dict) -> dict:
    """Read only numeric evidence from a fresh isolated qualification run.

    A reserved call may not have a recorded completion. Historical counters
    without observation counts are not complete usage. Zero tokens are valid
    only when explicitly observed. Never infer a bill from tokens or calls.
    """
    def count(value):
        return value if type(value) is int and value >= 0 else None

    reserved = count(status.get("model_calls_started", 0))
    metrics = status.get("execution_metrics", {})
    api = metrics.get("api", {}) if isinstance(metrics, dict) else {}
    rows = {}
    completed = 0
    valid = isinstance(api, dict) and isinstance(metrics, dict)
    for role in USAGE_ROLES:
        row = api.get(role, {}) if isinstance(api, dict) else {}
        if not isinstance(row, dict):
            valid = False
            row = {}
        calls = count(row.get("completed_calls", 0))
        if calls is None:
            valid = False
            calls = 0
        completed += calls
        if not calls:
            continue
        observed = {}
        tokens = {}
        for field in TOKEN_FIELDS:
            n = count(row.get(field + "_observations", 0))
            total = count(row.get(field))
            if n is None or n > calls or (n and total is None):
                valid = False
                n = 0
            observed[field] = n
            # No partial totals: older aggregates might include uncounted samples.
            tokens[field] = total if n == calls else None
        rows[role] = {"completed_calls": calls, "token_observations": observed,
                      "tokens_if_complete": tokens}
    matched = valid and reserved is not None and completed == reserved
    complete = bool(matched and completed and all(
        row["token_observations"][field] == row["completed_calls"]
        and row["tokens_if_complete"][field] is not None
        for row in rows.values() for field in TOKEN_FIELDS))
    return {
        "schema_version": 1,
        "reserved_calls": reserved,
        "recorded_completions": completed,
        "all_reserved_calls_recorded": matched,
        "token_usage_complete": complete,
        "tokens_if_complete": {
            field: sum(row["tokens_if_complete"][field] for row in rows.values())
            if complete else None for field in TOKEN_FIELDS
        },
        "roles": rows,
        "billed_cost": None,
        "scope": "runner_only_excludes_judges_and_audits",
    }

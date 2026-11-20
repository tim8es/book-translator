"""Aggregate content-free Auto metrics from this runtime's first observed event."""
from __future__ import annotations
from datetime import datetime, timezone

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

def provider(status: dict, metric: dict) -> None:
    role = metric.get("role")
    if role not in {"translator", "reviewer", "corrector", "orchestrator"}:
        return
    result=_metrics(status)
    rows=[result["api"].setdefault(role,{})]
    requested=metric.get("requested_role")
    if requested in {"translator","reviewer","corrector","orchestrator"}:
        rows.append(result.setdefault("requested_api",{}).setdefault(requested,{}))
    for row in rows:
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

def accepted(status: dict, role: str, outcome: str | None = None) -> None:
    keys = ({"translator": "translated", "corrector": "corrected",
             "promote": "reviewed"})
    if role == "reviewer":
        key = "review_pass" if outcome == "PASS" else "review_rejected"
    else:
        key = keys[role]
    row = _metrics(status)["accepted_operations"]
    prior = row.get(key, 0)
    if type(prior) is not int or prior < 0:
        raise ValueError("invalid accepted operation counter")
    row[key] = prior + 1
    if role == "reviewer" and outcome == "PASS":
        accepted(status, "promote")

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

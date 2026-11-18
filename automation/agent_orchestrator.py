"""Optional bounded model advice, never a literary or repository authority."""
from __future__ import annotations
import json

STRATEGIES = {"batch_remaining", "isolate_remaining"}

def prompt(rules: str, pending: list[dict], isolated: set[str]) -> tuple[str, str]:
    # Metadata only. No source text, quotes, hidden role reasoning or credentials.
    counts = {}
    for item in pending:
        key = item.get("severity", "unknown")
        if key not in {"minor", "major", "critical"}:
            key = "unknown"
        counts[key] = counts.get(key, 0) + 1
    system = rules + (
        "\nChoose only a technical execution strategy for remaining independent "
        "corrections. Never waive a finding or grant PASS. Return JSON with exactly "
        'one key "strategy": "batch_remaining" or "isolate_remaining".')
    return system, json.dumps({"pending": len(pending), "severity_counts": counts,
                               "isolated_unsafe": len(isolated),
                               "allowed_strategies": sorted(STRATEGIES)})

QUEUE_STRATEGIES = {"continue_same", "defer_attention"}

def queue_prompt(rules: str, evidence: dict, queue: dict) -> tuple[str, str]:
    system = rules + (
        "\nChoose technical queue pacing only. Both options preserve independent "
        "review and unresolved findings; a deferral is not PASS. Return JSON "
        'with exactly one key "strategy": "continue_same" or "defer_attention".')
    return system, json.dumps({"evidence": evidence, "queue_counts": queue,
                               "allowed_strategies": sorted(QUEUE_STRATEGIES)})

def parse(answer: str, allowed: set[str] = STRATEGIES) -> str:
    value = json.loads(answer)
    if (not isinstance(value, dict) or set(value) != {"strategy"}
            or not isinstance(value["strategy"], str)
            or value["strategy"] not in allowed):
        raise ValueError("unsupported orchestrator advice")
    return value["strategy"]

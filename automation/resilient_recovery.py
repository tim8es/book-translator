"""Optional artifact-preserving recovery. No literary acceptance here."""
from __future__ import annotations
import os
import re

def enabled():
    value=os.environ.get("BOOK_TRANSLATOR_RESILIENT_RECOVERY","off")
    if value not in {"on","off"}:
        raise ValueError("invalid resilient recovery flag")
    return value=="on"

def retry_delay(status,chapter,role,failure,attempt,legacy):
    """Shorter bounded retry only for observed slow gateway/request failures.
    Provider Retry-After is still applied by the caller. Rate limits, instant
    outages and prolonged failures retain the normal policy.
    """
    metric=status.get("provider_call_metric")
    if (enabled() and 1<=attempt<=3 and failure.code in {"http_408","http_502","http_503","http_504","transport"}
            and isinstance(metric,dict) and metric.get("unit_id")==chapter["unit_id"]
            and metric.get("role")==role and metric.get("outcome")==failure.code
            and type(metric.get("duration_ms")) is int and metric["duration_ms"]>=120000):
        return (60,180,300)[attempt-1]
    return legacy

def bounded_memory(existing,proposed,*,limit=40):
    """Optional proposals are not canonical memory. Keep exact entries up to
    canonical cap, deduplicate, count deferred items; never touch text/findings.
    All entries must have passed the existing per-response schema validation.
    """
    unique=[]
    for row in [*existing,*proposed]:
        if row not in unique:
            unique.append(row)
    return unique[:limit],max(0,len(unique)-limit)

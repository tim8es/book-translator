"""Sanitized, bounded readiness check for configured OmniRoute fallback routes."""
from __future__ import annotations
import json

ROLES = ("TRANSLATOR", "REVIEWER", "CORRECTOR")
PRIMARY_ROLES = ROLES + ("ORCHESTRATOR",)


def model(env, role):
    value = env.get("OMNIROUTE_" + role + "_MODEL", "")
    return value.strip() if isinstance(value, str) else ""


def probe(env, complete, report):
    """No route guessing or mutation. At most one call per configured backup role."""
    primaries = {role: model(env, role) for role in PRIMARY_ROLES}
    primary_ids = set(primaries.values()) - {""}
    report.update(primary_routes_configured=sum(bool(v) for v in primaries.values()),
                  distinct_primary_routes=len(primary_ids),
                  model_calls_started=0, max_model_calls=len(ROLES),
                  roles={}, all_fallbacks_ready=False,
                  independent_fallback_provider_verified=False)
    for role in ROLES:
        backup = model(env, role + "_FALLBACK")
        item = {"status": "missing"}
        report["roles"][role.lower()] = item
        if not primaries[role]:
            item["status"] = "primary_missing"
        elif not backup:
            continue
        elif backup == primaries[role]:
            item["status"] = "same_as_primary"
        elif backup in primary_ids:
            # A route used by another primary role can fail together with it.
            item["status"] = "shared_primary_alias"
        else:
            item["status"] = "probe_started"
            report["model_calls_started"] += 1
            try:
                result = json.loads(complete(role + "_FALLBACK",
                    "Return JSON with exactly one key named ready, boolean true. "
                    "This is a synthetic transport compatibility test; do not use tools.",
                    "Return the requested JSON object."))
                if (not isinstance(result, dict) or set(result) != {"ready"}
                        or result["ready"] is not True):
                    item["status"] = "invalid_response"
                else:
                    item["status"] = "ready_distinct_alias"
            except Exception as exc:
                # No provider response text, ID, credential, URL or exception
                # string enters the report.
                item["status"] = "probe_failed"
                item["failure_type"] = type(exc).__name__
    report["all_fallbacks_ready"] = (
        report["primary_routes_configured"] == len(PRIMARY_ROLES)
        and all(v["status"] == "ready_distinct_alias" for v in report["roles"].values()))
    # Distinct aliases and compatible responses do not prove independent
    # physical providers, accounts or underlying model deployments.

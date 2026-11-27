"""Explicit live Pi usage transport qualification; no book state or billing estimates."""
from __future__ import annotations

import json
import os
import time
from unittest.mock import patch

ROLES = ("TRANSLATOR", "REVIEWER", "CORRECTOR", "ORCHESTRATOR")
FIELDS = ("prompt_tokens", "completion_tokens", "total_tokens")
SYSTEM = "This is a synthetic transport test. Return exactly one JSON object with key ready and boolean value true. Do not use tools."
USER = "Return the requested JSON object."
# Closed allowlist: never copy arbitrary exception messages or event contents.
PI_CODES = {"pi_oversize_event_stream", "pi_invalid_event_stream",
            "pi_unexpected_extra_work", "pi_incomplete_assistant_message",
            "pi_stop_length", "pi_stop_error", "pi_stop_aborted", "pi_stop_tool_use",
            "pi_invalid_assistant_content", "pi_empty_or_oversize_output",
            "pi_invalid_configuration", "pi_binary_missing", "pi_invalid_token_limits"}
CONFIG_ERRORS = {"OmniRoute HTTPS base URL, key and role model must be configured",
                 "invalid model identifier", "unsupported model role",
                 "unsupported model backend"}


def safe_failure_code(exc):
    from omniroute import ModelError, TransientModelError
    if isinstance(exc, TransientModelError):
        return "transport"
    if type(exc) is ModelError:
        message = str(exc)
        if message in CONFIG_ERRORS:
            return "configuration"
        prefix = "Pi backend: "
        if message.startswith(prefix) and message[len(prefix):] in PI_CODES:
            return message[len(prefix):]
    return "unclassified"


def probe(complete, report):
    report.update(model_call_limit=len(ROLES), model_calls_started=0, roles={},
                  all_transports_ready=False, token_usage_complete=False,
                  billed_cost=None)
    # Each role gets one explicit, isolated call. No retry, fallback or extra audit.
    with patch.dict(os.environ, {"BOOK_TRANSLATOR_LLM_BACKEND": "pi",
                                 "BOOK_TRANSLATOR_PI_USAGE_EVENTS": "on"}):
        for role in ROLES:
            row = {"transport": "failed", "token_usage": None}
            report["roles"][role.lower()] = row
            report["model_calls_started"] += 1
            try:
                answer = complete(role, SYSTEM, USER)
                parsed = json.loads(answer)
                if not isinstance(parsed, dict) or set(parsed) != {"ready"} or parsed["ready"] is not True:
                    row["transport"] = "invalid_response"
                    continue
                row["transport"] = "ready"
                diag = getattr(answer, "diagnostics", None)
                if isinstance(diag, dict) and all(
                        type(diag.get(k)) is int and 0 <= diag[k] <= 1_000_000_000 for k in FIELDS):
                    if diag["completion_tokens"] > 0 and diag["total_tokens"] == (
                            diag["prompt_tokens"] + diag["completion_tokens"]):
                        row["token_usage"] = {k: diag[k] for k in FIELDS}
            except Exception as exc:
                # No exception message, event, model alias, prompt or response.
                row["failure_type"] = type(exc).__name__
                row["failure_code"] = safe_failure_code(exc)
    report["all_transports_ready"] = all(r["transport"] == "ready" for r in report["roles"].values())
    report["token_usage_complete"] = report["all_transports_ready"] and all(
        r["token_usage"] is not None for r in report["roles"].values())
    report["qualification"] = "passed" if report["token_usage_complete"] else "failed"


def qualify_usage(revision, output):
    from omniroute import complete
    report = {"schema_version": 1, "workflow_revision": revision, "profile": "usage",
              "qualification": "failed"}
    start = time.monotonic()
    try:
        probe(complete, report)
    except Exception as exc:
        report["failure_type"] = type(exc).__name__
    finally:
        report["duration_seconds"] = round(time.monotonic() - start, 1)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n", "utf-8")
        print(json.dumps(report))
    return 0 if report["qualification"] == "passed" else 1

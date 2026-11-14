"""Minimal OpenAI-compatible LLM transport for the Actions worker.

No user-supplied URL or model is accepted. Secrets never enter prompts, logs or Git.
"""
from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class ModelError(RuntimeError):
    pass


class CompletionText(str):
    """String-compatible chat output with non-sensitive transport diagnostics."""

    def __new__(cls, value: str, diagnostics: dict):
        obj = super().__new__(cls, value)
        obj.diagnostics = diagnostics
        return obj


class TransientModelError(ModelError):
    """Recoverable HTTP/transport outage; never an invalid model output."""

    def __init__(self, message: str, *, code: str,
                 retry_after_seconds: int | None = None):
        super().__init__(message)
        self.code = code
        self.retry_after_seconds = retry_after_seconds



def config(role: str) -> tuple[str, str, str]:
    if role not in {"TRANSLATOR", "REVIEWER", "CORRECTOR",
                    "TRANSLATOR_FALLBACK", "REVIEWER_FALLBACK",
                    "CORRECTOR_FALLBACK"}:
        raise ModelError("unsupported model role")
    base = os.environ.get("OMNIROUTE_BASE_URL", "").rstrip("/")
    key = os.environ.get("OMNIROUTE_API_KEY", "")
    model = os.environ.get(f"OMNIROUTE_{role}_MODEL", "")
    parsed = urlsplit(base)
    if (parsed.scheme != "https" or not parsed.netloc or parsed.username or
            parsed.password or parsed.query or parsed.fragment or not key or not model):
        raise ModelError("OmniRoute HTTPS base URL, key and role model must be configured")
    if not model.strip() or len(model) > 150:
        raise ModelError("invalid model identifier")
    return base, key, model


def complete(role: str, system: str, user: str) -> str:
    base, key, model = config(role)
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.4 if role.startswith("TRANSLATOR") else 0.2,
    }, ensure_ascii=False).encode("utf-8")
    request = Request(
        base + "/chat/completions",
        data=body,
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    # Each outgoing provider request is paid/budgeted individually by the
    # calling durable worker. Transient failures are persisted and retried by
    # the scheduled Actions wake, not by uncounted requests inside this client.
    try:
        with urlopen(request, timeout=240) as response:
            payload = json.loads(response.read(8_000_000).decode("utf-8"))
        choice = payload["choices"][0]
        reason = choice.get("finish_reason")
        if reason in {"length", "content_filter"}:
            # Keep the finish reason without recording the rejected response.
            raise ModelError("model output incomplete or filtered: " + reason)
        answer = choice["message"]["content"]
        if isinstance(answer, list):
            answer = "".join(part.get("text", "") for part in answer
                             if isinstance(part, dict))
        if not isinstance(answer, str) or not answer.strip():
            raise ModelError("model returned empty content")
        usage = payload.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        diagnostics = {
            "finish_reason": (reason if reason in {"stop", "length", "content_filter"}
                              else "other"),
            "response_format": "json_object",
            "response_chars": len(answer),
            "provider_refusal": bool(choice["message"].get("refusal")),
        }
        # A provider may omit usage. Persist only bounded counts, never the
        # prompt, response body, provider model ID, URL or authorization data.
        for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = usage.get(field)
            if type(value) is int and 0 <= value <= 1_000_000_000:
                diagnostics[field] = value
        return CompletionText(answer.strip() + "\n", diagnostics)
    except HTTPError as exc:
        if exc.code in {408, 429, 500, 502, 503, 504}:
            delay = exc.headers.get("Retry-After") if exc.headers else None
            seconds = (min(int(delay), 3600)
                       if isinstance(delay, str) and delay.isdecimal() else None)
            raise TransientModelError(
                f"model HTTP status {exc.code}", code=f"http_{exc.code}",
                retry_after_seconds=seconds) from None
        raise ModelError(f"model HTTP status {exc.code}") from None
    except (URLError, TimeoutError) as exc:
        raise TransientModelError(
            "model transport timeout or connection failure",
            code="transport") from exc
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ModelError("model response is not a valid chat completion") from exc

def parse_review(text: str) -> tuple[str, dict]:
    """Require strict, actionable source-comparison evidence before promotion."""
    candidate = text.strip()
    fence = chr(96) * 3
    if candidate.startswith(fence):
        lines = candidate.splitlines()
        if len(lines) < 3 or not lines[-1].strip().startswith(fence):
            raise ModelError("reviewer returned incomplete JSON fence")
        candidate = "\n".join(lines[1:-1])
    try:
        obj = json.loads(candidate)
    except ValueError as exc:
        raise ModelError("reviewer did not return valid JSON") from exc
    if not isinstance(obj, dict) or set(obj) != {"outcome", "checks", "findings"}:
        raise ModelError("reviewer JSON schema mismatch")
    outcome = obj["outcome"]
    checks = obj["checks"]
    if outcome not in {"PASS", "CORRECTIONS_REQUIRED"}:
        raise ModelError("reviewer outcome invalid")
    expected = {"coverage", "meaning", "terminology", "style", "structure"}
    if not isinstance(checks, dict) or set(checks) != expected or any(
        checks[k] is not True for k in expected
    ):
        raise ModelError("reviewer has not confirmed all five checks were performed")
    findings = obj["findings"]
    if not isinstance(findings, list) or len(findings) > 30:
        raise ModelError("reviewer findings are not bounded")
    for item in findings:
        if not isinstance(item, dict) or set(item) != {"severity", "detail", "resolved"}:
            raise ModelError("reviewer finding has invalid fields")
        if item["severity"] not in {"minor", "major", "critical"}:
            raise ModelError("reviewer finding has invalid severity")
        if not isinstance(item["detail"], str) or not 1 <= len(item["detail"].strip()) <= 1000:
            raise ModelError("reviewer finding needs a specific detail")
        if type(item["resolved"]) is not bool:
            raise ModelError("reviewer finding resolved must be boolean")
    unresolved = [f for f in findings if not f["resolved"]]
    if outcome == "CORRECTIONS_REQUIRED" and not unresolved:
        raise ModelError("corrections required without concrete unresolved findings")
    if outcome == "PASS" and unresolved:
        raise ModelError("unresolved reviewer findings cannot be promoted")
    return outcome, {
        "context": "independent_session",
        "checks": checks,
        "findings": findings,
    }

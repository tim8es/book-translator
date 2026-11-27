"""Isolated Pi CLI transport for the existing GitHub Actions Auto executor.

Literary state, claims, review ledger and EPUB are still controlled by the
canonical book_runner, not by the LLM. Pi has no tools or Git credentials.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


MAX_OUTPUT_CHARS = 8_000_000


class PiTransportFailure(RuntimeError):
    """Sanitized, non-content Pi execution failure."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


MAX_EVENT_CHARS = 64_000_000


def parse_json_events(output: str) -> tuple[str, dict]:
    """Extract one authoritative tool-free assistant message (Pi v1.6 JSONL).

    Provider counts may be absent or default to zeros. Do not treat a
    successful nonempty response with zero output tokens as measured usage.
    Never expose raw event bodies, provider identifiers, or SDK cost guesses.
    """
    if len(output) > MAX_EVENT_CHARS:
        raise PiTransportFailure("pi_oversize_event_stream")
    records = []
    starts = turns = 0
    settled = False
    for line in output.split("\n"):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            raise PiTransportFailure("pi_invalid_event_stream") from None
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            raise PiTransportFailure("pi_invalid_event_stream")
        kind = event["type"]
        if kind == "agent_start":
            starts += 1
        if kind == "turn_start":
            turns += 1
        if (kind.startswith(("tool_execution_", "auto_retry_", "compaction_", "summarization_retry_"))
                or (kind == "agent_end" and event.get("willRetry") is True)):
            raise PiTransportFailure("pi_unexpected_extra_work")
        if kind == "message_end":
            message = event.get("message")
            if not isinstance(message, dict):
                raise PiTransportFailure("pi_invalid_event_stream")
            if message.get("role") == "assistant":
                records.append(message)
        if kind == "agent_settled":
            if settled or event.get("aborted") is not False:
                raise PiTransportFailure("pi_invalid_event_stream")
            settled = True
    if not settled or starts != 1 or turns != 1 or len(records) != 1:
        raise PiTransportFailure("pi_unexpected_extra_work")
    message = records[0]
    reason = message.get("stopReason")
    if reason != "stop":
        # Closed enum only: never expose SDK errorMessage, text, URL, or
        # provider details. No partial message is accepted and no new retry
        # is introduced. Unknown stop reasons retain the old generic error.
        code = {"length": "pi_stop_length", "error": "pi_stop_error",
                "aborted": "pi_stop_aborted", "toolUse": "pi_stop_tool_use"}
        raise PiTransportFailure(code.get(reason, "pi_incomplete_assistant_message")
                                 if isinstance(reason, str) else "pi_incomplete_assistant_message")
    content = message.get("content")
    if not isinstance(content, list):
        raise PiTransportFailure("pi_invalid_assistant_content")
    parts = []
    for block in content:
        if not isinstance(block, dict):
            raise PiTransportFailure("pi_invalid_assistant_content")
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
        elif block.get("type") == "thinking" and isinstance(block.get("thinking"), str):
            # Pi print mode discards documented thinking blocks. Keep the
            # same visible response in JSON mode; never retain reasoning.
            continue
        else:
            # In particular, a tool call is never a valid response.
            raise PiTransportFailure("pi_invalid_assistant_content")
    answer = "\n".join(parts).strip()
    if not answer or len(answer) > MAX_OUTPUT_CHARS:
        raise PiTransportFailure("pi_empty_or_oversize_output")
    diagnostics = {"finish_reason": "stop", "response_format": "pi_prompt_json",
                   "response_chars": len(answer), "provider_refusal": False}
    usage = message.get("usage")
    fields = ("input", "output", "cacheRead", "cacheWrite", "totalTokens")
    if isinstance(usage, dict) and all(
            type(usage.get(k)) is int and 0 <= usage[k] <= 1_000_000_000 for k in fields):
        total = usage["totalTokens"]
        prompt = usage["input"] + usage["cacheRead"] + usage["cacheWrite"]
        if usage["output"] > 0 and total == prompt + usage["output"] and total <= 1_000_000_000:
            diagnostics.update(prompt_tokens=prompt, completion_tokens=usage["output"],
                               total_tokens=total)
    return answer + "\n", diagnostics


def _token_limit(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        raise PiTransportFailure("pi_invalid_token_limits") from None
    if not minimum <= value <= maximum:
        raise PiTransportFailure("pi_invalid_token_limits")
    return value


def complete(base: str, model: str, key: str, system: str, user: str, *, role: str) -> tuple[str, dict]:
    """Run one tool-free, independent Pi turn and return text plus diagnostics.

    Automatic retries, compaction and cache warming are disabled: Auto already
    reserves paid provider calls durably. Pi stderr never reaches GitHub logs.
    """
    if not key or not system.strip() or not user.strip():
        raise PiTransportFailure("pi_invalid_configuration")
    pi = shutil.which("pi")
    if not pi:
        raise PiTransportFailure("pi_binary_missing")

    context_window = _token_limit("PI_AGENT_CONTEXT_WINDOW", 131072, 4096, 1_000_000)
    max_tokens = _token_limit("PI_AGENT_MAX_TOKENS", 32768, 256, 131072)
    if max_tokens >= context_window:
        raise PiTransportFailure("pi_invalid_token_limits")
    event_mode = os.environ.get("BOOK_TRANSLATOR_PI_USAGE_EVENTS", "off")
    if event_mode not in {"off", "on"}:
        raise PiTransportFailure("pi_invalid_configuration")
    timeout = 90 if role == "ORCHESTRATOR" else 840
    with tempfile.TemporaryDirectory(prefix="bt-pi-") as workspace:
        root = Path(workspace)
        agent_dir = root / "agent-config"
        agent_dir.mkdir(mode=0o700)
        models = {
            "providers": {
                "book-omniroute": {
                    "baseUrl": base,
                    "api": "openai-completions",
                    "apiKey": "$OMNIROUTE_API_KEY",
                    "authHeader": True,
                    "models": [{
                        "id": model,
                        "name": "Book Translator model",
                        "reasoning": False,
                        "input": ["text"],
                        "contextWindow": context_window,
                        "maxTokens": max_tokens,
                    }],
                },
            },
        }
        (agent_dir / "models.json").write_text(json.dumps(models), encoding="utf-8")
        settings = {
            "defaultThinkingLevel": "off",
            "compaction": {"enabled": False},
            "cacheWarming": "off",
            "retry": {"enabled": False, "maxRetries": 0,
                      "provider": {"maxRetries": 0, "timeoutMs": timeout * 1000}},
        }
        (agent_dir / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
        system_path = root / "system.txt"
        system_path.write_text(
            system + "\n\nReturn only the requested JSON object. No Markdown fences or commentary.\n",
            encoding="utf-8",
        )
        system_path.chmod(0o600)
        # Explicit allowlist prevents provider prompts from accessing GH_TOKEN.
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": workspace,
            "PI_CODING_AGENT_DIR": str(agent_dir),
            "PI_CODING_AGENT_SESSION_DIR": str(root / "sessions"),
            "PI_OFFLINE": "1",
            "PI_SKIP_VERSION_CHECK": "1",
            "OMNIROUTE_API_KEY": key,
        }
        command = [
            pi, "--print", "--no-session", "--no-tools", "--no-approve",
            "--provider", "book-omniroute", "--model", model,
            "--system-prompt", str(system_path),
        ]
        if event_mode == "on":
            command += ["--mode", "json"]
        try:
            result = subprocess.run(
                command, cwd=root, env=env, input=user, capture_output=True,
                text=True, encoding="utf-8", timeout=timeout + 15, check=False,
            )
        except subprocess.TimeoutExpired:
            raise PiTransportFailure("pi_timeout") from None
        except (OSError, UnicodeError):
            raise PiTransportFailure("pi_subprocess_failure") from None
        # Do not print raw stderr: it can contain a prompt, response or URL.
        if result.returncode != 0:
            raise PiTransportFailure("pi_nonzero_exit")
        if event_mode == "on":
            return parse_json_events(result.stdout)
        answer = result.stdout.strip()
        if not answer or len(answer) > MAX_OUTPUT_CHARS:
            raise PiTransportFailure("pi_empty_or_oversize_output")
        return answer + "\n", {
            "finish_reason": "stop",
            "response_format": "pi_prompt_json",
            "response_chars": len(answer),
            "provider_refusal": False,
        }

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
        answer = result.stdout.strip()
        if not answer or len(answer) > MAX_OUTPUT_CHARS:
            raise PiTransportFailure("pi_empty_or_oversize_output")
        return answer + "\n", {
            "finish_reason": "stop",
            "response_format": "pi_prompt_json",
            "response_chars": len(answer),
            "provider_refusal": False,
        }

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import httpx

from .config import Settings


class LlmError(RuntimeError):
    pass


class LlmBudgetExceeded(LlmError):
    pass


class LlmNonRetryableError(LlmError):
    pass


@dataclass
class LlmUsage:
    model: str
    role: str
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    cost_usd: float


class ManagedLlm:
    def __init__(self, settings: Settings, run_limit_usd: float) -> None:
        self.settings = settings
        self.limit_usd = min(settings.max_llm_cost_usd, run_limit_usd)
        self.spent_usd = 0.0
        self.usage: list[LlmUsage] = []
        self.client = httpx.AsyncClient(
            timeout=settings.request_timeout_seconds,
            headers={
                "Authorization": f"Bearer {settings.api_key}",
                "Content-Type": "application/json",
            },
        )

    async def close(self) -> None:
        await self.client.aclose()

    def _prices(self, role: str) -> tuple[float, float, float]:
        if role == "translation":
            return (
                self.settings.translation_input_usd_per_m,
                self.settings.translation_cached_input_usd_per_m,
                self.settings.translation_output_usd_per_m,
            )
        return (
            self.settings.review_input_usd_per_m,
            self.settings.review_cached_input_usd_per_m,
            self.settings.review_output_usd_per_m,
        )

    def _estimate_request_cost(self, messages: list[dict[str, str]], role: str, max_tokens: int) -> float:
        text_chars = sum(len(item.get("content", "")) for item in messages)
        estimated_input_tokens = max(1, text_chars // 4)
        input_price, _, output_price = self._prices(role)
        return (
            estimated_input_tokens * input_price / 1_000_000
            + max_tokens * output_price / 1_000_000
        )

    async def complete(
        self,
        *,
        role: str,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int,
    ) -> str:
        estimated = self._estimate_request_cost(messages, role, max_tokens)
        if self.spent_usd + estimated > self.limit_usd:
            raise LlmBudgetExceeded(
                f"LLM safety ceiling would be exceeded: spent=${self.spent_usd:.4f}, "
                f"estimated next request=${estimated:.4f}, ceiling=${self.limit_usd:.4f}"
            )

        body = {
            "model": model,
            "messages": messages,
            self.settings.max_tokens_parameter: max_tokens,
        }
        if self.settings.reasoning_effort:
            body["reasoning_effort"] = self.settings.reasoning_effort
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await self.client.post(
                    f"{self.settings.base_url}/chat/completions", json=body
                )
                response.raise_for_status()
                payload = response.json()
                choice = payload["choices"][0]
                finish_reason = choice.get("finish_reason")
                if finish_reason not in {None, "stop"}:
                    raise LlmNonRetryableError(
                        f"LLM stopped before a complete artifact: finish_reason={finish_reason}"
                    )
                content = choice["message"]["content"]
                usage = payload.get("usage") or {}
                input_tokens = int(
                    usage.get("prompt_tokens")
                    or usage.get("input_tokens")
                    or 0
                )
                output_tokens = int(
                    usage.get("completion_tokens")
                    or usage.get("output_tokens")
                    or 0
                )
                prompt_details = usage.get("prompt_tokens_details") or {}
                cached_input_tokens = int(prompt_details.get("cached_tokens") or 0)
                cached_input_tokens = min(max(0, cached_input_tokens), input_tokens)
                uncached_input_tokens = input_tokens - cached_input_tokens
                input_price, cached_input_price, output_price = self._prices(role)
                measured_cost = (
                    uncached_input_tokens * input_price / 1_000_000
                    + cached_input_tokens * cached_input_price / 1_000_000
                    + output_tokens * output_price / 1_000_000
                )
                # Some OpenAI-compatible gateways omit usage. Never treat missing
                # metering as free: reserve the conservative pre-request estimate.
                cost = measured_cost if (input_tokens or output_tokens) else estimated
                self.spent_usd += cost
                self.usage.append(
                    LlmUsage(
                        model=model,
                        role=role,
                        input_tokens=input_tokens,
                        cached_input_tokens=cached_input_tokens,
                        output_tokens=output_tokens,
                        cost_usd=cost,
                    )
                )
                if self.spent_usd > self.limit_usd:
                    raise LlmBudgetExceeded(
                        f"Provider-reported usage crossed LLM safety ceiling: "
                        f"${self.spent_usd:.4f} > ${self.limit_usd:.4f}"
                    )
                if not isinstance(content, str) or not content.strip():
                    raise LlmError("LLM returned empty content")
                return content.strip()
            except (LlmBudgetExceeded, LlmNonRetryableError):
                raise
            except (httpx.HTTPError, KeyError, TypeError, ValueError, LlmError) as exc:
                last_error = exc
                if attempt == 2:
                    break
                await asyncio.sleep(2 ** attempt)
        raise LlmError(f"LLM request failed after retries: {last_error}")

    async def translation(self, messages: list[dict[str, str]]) -> str:
        return await self.complete(
            role="translation",
            model=self.settings.translation_model,
            messages=messages,
            max_tokens=self.settings.translation_max_tokens,
        )

    async def review_json(self, messages: list[dict[str, str]]) -> dict:
        raw = await self.complete(
            role="review",
            model=self.settings.review_model,
            messages=messages,
            max_tokens=self.settings.review_max_tokens,
        )
        cleaned = raw.strip()
        fence = chr(96) * 3
        if cleaned.startswith(fence):
            cleaned = cleaned.strip(chr(96))
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:].lstrip()
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise LlmError(f"Reviewer did not return valid JSON: {exc}") from exc
        if parsed.get("outcome") not in {"PASS", "CORRECTIONS_REQUIRED"}:
            raise LlmError("Reviewer JSON must contain outcome PASS or CORRECTIONS_REQUIRED")
        issues = parsed.get("issues", [])
        if not isinstance(issues, list):
            raise LlmError("Reviewer JSON issues must be an array")

        glossary = parsed.get("glossary_additions", [])
        if not isinstance(glossary, list):
            glossary = []
        safe_glossary: list[dict[str, str]] = []
        for item in glossary[:20]:
            if not isinstance(item, dict):
                continue
            original = str(item.get("original") or "").strip()[:300]
            translation = str(item.get("translation") or "").strip()[:300]
            if not original or not translation:
                continue
            safe_glossary.append(
                {
                    "original": original,
                    "translation": translation,
                    "type": str(item.get("type") or "term").strip()[:80],
                    "notes": str(item.get("notes") or "").strip()[:500],
                }
            )

        observations = parsed.get("style_observations", [])
        if not isinstance(observations, list):
            observations = []
        safe_observations = [
            str(item).strip()[:500]
            for item in observations[:10]
            if isinstance(item, str) and item.strip()
        ]

        return {
            "outcome": parsed["outcome"],
            "issues": issues,
            "glossary_additions": safe_glossary,
            "style_observations": safe_observations,
        }

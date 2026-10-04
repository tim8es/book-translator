from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(RuntimeError):
    pass


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(f"Missing required owner configuration: {name}")
    return value


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be numeric") from exc


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc


@dataclass(frozen=True)
class Settings:
    api_key: str
    base_url: str
    translation_model: str
    review_model: str
    max_tokens_parameter: str
    reasoning_effort: str | None
    translation_input_usd_per_m: float
    translation_cached_input_usd_per_m: float
    translation_cache_write_usd_per_m: float
    translation_output_usd_per_m: float
    review_input_usd_per_m: float
    review_cached_input_usd_per_m: float
    review_cache_write_usd_per_m: float
    review_output_usd_per_m: float
    max_llm_cost_usd: float
    max_source_words: int
    max_source_bytes: int
    max_chapter_chars: int
    translation_max_tokens: int
    review_max_tokens: int
    max_review_rounds: int
    request_timeout_seconds: int
    workflow_revision: str
    skip_charging: bool

    @classmethod
    def from_env(cls) -> "Settings":
        translation_model = os.getenv("BOOK_TRANSLATOR_TRANSLATION_MODEL", "gpt-6-luna").strip() or "gpt-6-luna"
        max_tokens_parameter = os.getenv(
            "BOOK_TRANSLATOR_MAX_TOKENS_PARAMETER", "max_completion_tokens"
        ).strip()
        if max_tokens_parameter not in {"max_completion_tokens", "max_tokens"}:
            raise ConfigError(
                "BOOK_TRANSLATOR_MAX_TOKENS_PARAMETER must be max_completion_tokens or max_tokens"
            )
        reasoning_effort = os.getenv("BOOK_TRANSLATOR_REASONING_EFFORT", "high").strip() or "high"
        return cls(
            api_key=_required("BOOK_TRANSLATOR_LLM_API_KEY"),
            base_url=os.getenv("BOOK_TRANSLATOR_LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            translation_model=translation_model,
            review_model=os.getenv("BOOK_TRANSLATOR_REVIEW_MODEL", "gpt-6.1-sol").strip() or "gpt-6.1-sol",
            max_tokens_parameter=max_tokens_parameter,
            reasoning_effort=reasoning_effort,
            translation_input_usd_per_m=_float("BOOK_TRANSLATOR_TRANSLATION_INPUT_USD_PER_1M", 0.10),
            translation_cached_input_usd_per_m=_float("BOOK_TRANSLATOR_TRANSLATION_CACHED_INPUT_USD_PER_1M", 0.01),
            translation_cache_write_usd_per_m=_float("BOOK_TRANSLATOR_TRANSLATION_CACHE_WRITE_USD_PER_1M", 0.125),
            translation_output_usd_per_m=_float("BOOK_TRANSLATOR_TRANSLATION_OUTPUT_USD_PER_1M", 0.50),
            review_input_usd_per_m=_float("BOOK_TRANSLATOR_REVIEW_INPUT_USD_PER_1M", 2.00),
            review_cached_input_usd_per_m=_float("BOOK_TRANSLATOR_REVIEW_CACHED_INPUT_USD_PER_1M", 0.10),
            review_cache_write_usd_per_m=_float("BOOK_TRANSLATOR_REVIEW_CACHE_WRITE_USD_PER_1M", 2.50),
            review_output_usd_per_m=_float("BOOK_TRANSLATOR_REVIEW_OUTPUT_USD_PER_1M", 10.00),
            max_llm_cost_usd=_float("BOOK_TRANSLATOR_MAX_LLM_COST_USD_PER_RUN", 20.0),
            max_source_words=_int("BOOK_TRANSLATOR_MAX_SOURCE_WORDS", 500_000),
            max_source_bytes=_int("BOOK_TRANSLATOR_MAX_SOURCE_BYTES", 50_000_000),
            max_chapter_chars=_int("BOOK_TRANSLATOR_MAX_CHAPTER_CHARS", 60_000),
            translation_max_tokens=max(1000, _int("BOOK_TRANSLATOR_TRANSLATION_MAX_TOKENS", 32_000)),
            review_max_tokens=max(1000, _int("BOOK_TRANSLATOR_REVIEW_MAX_TOKENS", 8_000)),
            max_review_rounds=max(1, _int("BOOK_TRANSLATOR_MAX_REVIEW_ROUNDS", 2)),
            request_timeout_seconds=max(30, _int("BOOK_TRANSLATOR_LLM_TIMEOUT_SECONDS", 300)),
            workflow_revision=os.getenv("BOOK_TRANSLATOR_WORKFLOW_REVISION", "apify-managed-llm-mvp").strip(),
            skip_charging=os.getenv("BOOK_TRANSLATOR_SKIP_CHARGING", "").lower() in {"1", "true", "yes"},
        )

    def validate_pricing(self) -> None:
        values = (
            self.translation_input_usd_per_m,
            self.translation_cached_input_usd_per_m,
            self.translation_cache_write_usd_per_m,
            self.translation_output_usd_per_m,
            self.review_input_usd_per_m,
            self.review_cached_input_usd_per_m,
            self.review_cache_write_usd_per_m,
            self.review_output_usd_per_m,
        )
        if any(value < 0 for value in values):
            raise ConfigError("LLM token prices cannot be negative")
        if all(value == 0 for value in values):
            raise ConfigError(
                "Configure owner-side token prices before production so the LLM cost guard can work"
            )
        if self.max_llm_cost_usd <= 0:
            raise ConfigError("BOOK_TRANSLATOR_MAX_LLM_COST_USD_PER_RUN must be positive")

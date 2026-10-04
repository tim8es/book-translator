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
    translation_input_usd_per_m: float
    translation_output_usd_per_m: float
    review_input_usd_per_m: float
    review_output_usd_per_m: float
    max_llm_cost_usd: float
    max_source_words: int
    max_review_rounds: int
    request_timeout_seconds: int
    workflow_revision: str
    skip_charging: bool

    @classmethod
    def from_env(cls) -> "Settings":
        translation_model = _required("BOOK_TRANSLATOR_TRANSLATION_MODEL")
        return cls(
            api_key=_required("BOOK_TRANSLATOR_LLM_API_KEY"),
            base_url=os.getenv("BOOK_TRANSLATOR_LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            translation_model=translation_model,
            review_model=os.getenv("BOOK_TRANSLATOR_REVIEW_MODEL", translation_model).strip() or translation_model,
            translation_input_usd_per_m=_float("BOOK_TRANSLATOR_TRANSLATION_INPUT_USD_PER_1M", 0.0),
            translation_output_usd_per_m=_float("BOOK_TRANSLATOR_TRANSLATION_OUTPUT_USD_PER_1M", 0.0),
            review_input_usd_per_m=_float("BOOK_TRANSLATOR_REVIEW_INPUT_USD_PER_1M", 0.0),
            review_output_usd_per_m=_float("BOOK_TRANSLATOR_REVIEW_OUTPUT_USD_PER_1M", 0.0),
            max_llm_cost_usd=_float("BOOK_TRANSLATOR_MAX_LLM_COST_USD_PER_RUN", 20.0),
            max_source_words=_int("BOOK_TRANSLATOR_MAX_SOURCE_WORDS", 500_000),
            max_review_rounds=max(1, _int("BOOK_TRANSLATOR_MAX_REVIEW_ROUNDS", 2)),
            request_timeout_seconds=max(30, _int("BOOK_TRANSLATOR_LLM_TIMEOUT_SECONDS", 300)),
            workflow_revision=os.getenv("BOOK_TRANSLATOR_WORKFLOW_REVISION", "apify-managed-llm-mvp").strip(),
            skip_charging=os.getenv("BOOK_TRANSLATOR_SKIP_CHARGING", "").lower() in {"1", "true", "yes"},
        )

    def validate_pricing(self) -> None:
        values = (
            self.translation_input_usd_per_m,
            self.translation_output_usd_per_m,
            self.review_input_usd_per_m,
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

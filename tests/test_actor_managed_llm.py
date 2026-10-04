import json
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

if sys.version_info < (3, 11):
    raise unittest.SkipTest("Apify SDK v4 requires Python 3.11+")

from actor_src.config import Settings
from actor_src.llm import LlmBudgetExceeded, LlmNonRetryableError, ManagedLlm
from actor_src import main as actor_main


def settings(**overrides):
    values = {
        "api_key": "test-secret",
        "base_url": "https://example.invalid/v1",
        "translation_model": "translation-model",
        "review_model": "review-model",
        "max_tokens_parameter": "max_completion_tokens",
        "reasoning_effort": None,
        "translation_input_usd_per_m": 1.0,
        "translation_cached_input_usd_per_m": 0.1,
        "translation_output_usd_per_m": 1000.0,
        "review_input_usd_per_m": 1.0,
        "review_cached_input_usd_per_m": 0.1,
        "review_output_usd_per_m": 1.0,
        "max_llm_cost_usd": 1.0,
        "max_source_words": 500_000,
        "max_source_bytes": 50_000_000,
        "max_chapter_chars": 60_000,
        "translation_max_tokens": 32_000,
        "review_max_tokens": 8_000,
        "max_review_rounds": 2,
        "request_timeout_seconds": 30,
        "workflow_revision": "test",
        "skip_charging": False,
    }
    values.update(overrides)
    return Settings(**values)


class _FakeResponse:
    def __init__(self, finish_reason=None):
        self.finish_reason = finish_reason

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "choices": [{
                "message": {"content": "translated"},
                "finish_reason": self.finish_reason,
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2000},
        }


class _FakeClient:
    def __init__(self, finish_reason=None):
        self.calls = 0
        self.last_json = None
        self.finish_reason = finish_reason

    async def post(self, *args, **kwargs):
        self.calls += 1
        self.last_json = kwargs.get("json")
        return _FakeResponse(self.finish_reason)

    async def aclose(self):
        return None


class ManagedLlmSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_budget_breach_after_response_is_never_retried(self):
        llm = ManagedLlm(settings(), run_limit_usd=1.0)
        await llm.client.aclose()
        fake = _FakeClient()
        llm.client = fake

        with self.assertRaises(LlmBudgetExceeded):
            await llm.complete(
                role="translation",
                model="translation-model",
                messages=[{"role": "user", "content": "short"}],
                max_tokens=1,
            )

        self.assertEqual(fake.calls, 1)
        self.assertIn("max_completion_tokens", fake.last_json)
        self.assertNotIn("max_tokens", fake.last_json)
        self.assertNotIn("temperature", fake.last_json)

    async def test_truncated_output_is_never_retried(self):
        llm = ManagedLlm(
            settings(
                translation_output_usd_per_m=1.0,
                max_llm_cost_usd=10.0,
            ),
            run_limit_usd=10.0,
        )
        await llm.client.aclose()
        fake = _FakeClient(finish_reason="length")
        llm.client = fake

        with self.assertRaises(LlmNonRetryableError):
            await llm.complete(
                role="translation",
                model="translation-model",
                messages=[{"role": "user", "content": "short"}],
                max_tokens=100,
            )

        self.assertEqual(fake.calls, 1)

    async def test_reviewer_memory_is_bounded_and_sanitized(self):
        llm = ManagedLlm(
            settings(
                translation_output_usd_per_m=1.0,
                max_llm_cost_usd=10.0,
            ),
            run_limit_usd=10.0,
        )
        payload = {
            "outcome": "PASS",
            "issues": [],
            "glossary_additions": [
                {
                    "original": "Name",
                    "translation": "Имя",
                    "type": "character",
                    "notes": "Keep stable",
                }
                for _ in range(30)
            ],
            "style_observations": ["Short, restrained narration"] * 20,
        }
        llm.complete = AsyncMock(return_value=json.dumps(payload, ensure_ascii=False))

        result = await llm.review_json([{"role": "user", "content": "x"}])

        self.assertEqual(result["outcome"], "PASS")
        self.assertEqual(len(result["glossary_additions"]), 20)
        self.assertEqual(len(result["style_observations"]), 10)
        await llm.client.aclose()


class BillingGuardTests(unittest.TestCase):
    def _manager(self, max_total):
        pricing = SimpleNamespace(
            is_pay_per_event=True,
            per_event_prices={
                "book-started": Decimal("1.00"),
                "translation-1k-words": Decimal("0.10"),
            },
            max_total_charge_usd=Decimal(max_total),
        )
        return SimpleNamespace(
            get_pricing_info=lambda: pricing,
            calculate_total_charged_amount=lambda: Decimal("0"),
        )

    def test_full_book_budget_is_checked_before_work(self):
        fake = self._manager("1.20")
        with patch.object(actor_main.Actor, "get_charging_manager", return_value=fake):
            with self.assertRaises(actor_main.ActorRunError):
                actor_main.ensure_book_charge_capacity(
                    SimpleNamespace(skip_charging=False),
                    [1000, 1001],
                )

    def test_full_book_budget_accepts_sufficient_limit(self):
        fake = self._manager("1.30")
        with patch.object(actor_main.Actor, "get_charging_manager", return_value=fake):
            actor_main.ensure_book_charge_capacity(
                SimpleNamespace(skip_charging=False),
                [1000, 1001],
            )


class LiteraryMemoryTests(unittest.TestCase):
    def test_memory_is_persisted_and_deduplicated(self):
        with TemporaryDirectory() as directory:
            book_dir = Path(directory)
            (book_dir / "glossary.md").write_text(
                "# Glossary\n\n| Original | Translation | Type | Notes |\n"
                "| --- | --- | --- | --- |\n",
                encoding="utf-8",
            )
            (book_dir / "style-guide.md").write_text(
                "# Style Guide\n",
                encoding="utf-8",
            )
            review = {
                "glossary_additions": [
                    {
                        "original": "John",
                        "translation": "Джон",
                        "type": "character",
                        "notes": "Name",
                    }
                ],
                "style_observations": ["Narration remains terse."],
            }

            actor_main.apply_literary_memory(book_dir, review)
            actor_main.apply_literary_memory(book_dir, review)

            glossary = (book_dir / "glossary.md").read_text(encoding="utf-8")
            style = (book_dir / "style-guide.md").read_text(encoding="utf-8")
            self.assertEqual(glossary.count("| John | Джон | character | Name |"), 1)
            self.assertEqual(style.count("- Narration remains terse."), 1)


if __name__ == "__main__":
    unittest.main()

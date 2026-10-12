"""Offline end-to-end smoke test of the real Actor orchestration and book CLI.

The LLM and Apify SDK are faked; extraction, durable review evidence, validation,
and EPUB/Markdown packaging run through the production code paths.
"""
import io
import os
import shutil
import sys
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

if sys.version_info < (3, 11):
    raise unittest.SkipTest("Apify SDK v4 requires Python 3.11+")

from actor_src import main as actor_main


class FakeActor:
    def __init__(self, source_path):
        self.input = {
            "bookFiles": [str(source_path)],
            "targetLanguage": "Russian",
            "sourceLanguage": "English",
            "title": "The Lantern Window",
            "author": "Book Translator smoke fixture",
            "outputFormat": "both",
        }
        self.records = {}
        self.rows = []
        self.messages = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get_input(self):
        return self.input

    async def set_value(self, key, value, **kwargs):
        self.records[key] = value

    async def push_data(self, value):
        self.rows.append(value)

    async def set_status_message(self, message, **kwargs):
        self.messages.append(message)


class FakeManagedLlm:
    def __init__(self, settings, run_limit_usd):
        self.limit_usd = run_limit_usd
        self.spent_usd = 0.0
        self.usage = []
        self.calls = []

    async def translation(self, messages):
        self.calls.append("translation")
        return (
            "Глава переведена. Мара подошла к старому дому, где светилось окно. "
            "Она остановилась, прислушалась и увидела лестницу.\n"
        )

    async def review_json(self, messages):
        self.calls.append("review")
        return {
            "outcome": "PASS",
            "issues": [],
            "glossary_additions": [],
            "style_observations": [],
        }

    async def close(self):
        pass


class ActorEndToEndSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_chapter_fixture_to_validated_epub_and_markdown(self):
        source_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory(prefix="book-translator-e2e-") as temp:
            root = Path(temp) / "repo"
            shutil.copytree(
                source_root,
                root,
                ignore=shutil.ignore_patterns(
                    ".git", "__pycache__", ".actor-work", "books", "*.pyc"
                ),
            )
            fixture = root / "examples" / "apify-smoke-book.txt"
            self.assertTrue(fixture.is_file(), "Missing checked-in smoke fixture")
            fake_actor = FakeActor(fixture)
            fake_actor.input["bookFiles"] = ["https://example.org/upload/book.txt"]
            created_llms = []

            def create_llm(settings, run_limit_usd):
                llm = FakeManagedLlm(settings, run_limit_usd)
                created_llms.append(llm)
                return llm

            with (
                patch.object(actor_main, "ROOT", root),
                patch.object(actor_main, "Actor", fake_actor),
                patch.object(actor_main, "ManagedLlm", side_effect=create_llm),
                patch.object(actor_main, "materialize_source", new_callable=AsyncMock) as stage_source,
                patch.dict(os.environ, {"BOOK_TRANSLATOR_LLM_API_KEY": "test-only-key"}),
            ):
                stage_source.return_value = fixture
                await actor_main.main()
                stage_source.assert_awaited_once()

            self.assertEqual(fake_actor.records["SUMMARY"]["status"], "complete")
            self.assertEqual(fake_actor.records["SUMMARY"]["chapters"], 2)
            self.assertEqual(
                fake_actor.records["SUMMARY"]["outputKeys"],
                ["OUTPUT_MARKDOWN", "OUTPUT_EPUB"],
            )
            self.assertEqual(len(fake_actor.rows), 3)
            self.assertTrue(all(row["status"] == "reviewed" for row in fake_actor.rows[:2]))
            self.assertIn("CHAPTER_000001", fake_actor.records)
            self.assertIn("CHAPTER_000002", fake_actor.records)
            self.assertIn("OUTPUT_MARKDOWN", fake_actor.records)
            self.assertIn("OUTPUT_EPUB", fake_actor.records)
            self.assertIn("Мара", fake_actor.records["OUTPUT_MARKDOWN"])
            epub = fake_actor.records["OUTPUT_EPUB"]
            with zipfile.ZipFile(io.BytesIO(epub), "r") as archive:
                self.assertEqual(archive.read("mimetype"), b"application/epub+zip")
                self.assertIn("META-INF/container.xml", archive.namelist())
                self.assertTrue(archive.testzip() is None)
            self.assertEqual(created_llms[0].calls, ["translation", "review"] * 2)
            self.assertNotIn("test-only-key", str(fake_actor.records))
            self.assertNotIn("test-only-key", str(fake_actor.rows))

    async def test_local_path_is_rejected_even_when_billing_disabled(self):
        with TemporaryDirectory() as temp:
            with patch.object(actor_main, "ROOT", Path(temp)):
                with self.assertRaisesRegex(actor_main.ActorRunError, "local paths are not supported"):
                    await actor_main.materialize_source("/etc/passwd", object())

    async def test_invalid_output_format_fails_before_model_work(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            fake_actor = FakeActor(root / "placeholder.txt")
            fake_actor.input["bookFiles"] = ["https://example.org/book.txt"]
            fake_actor.input["outputFormat"] = "pdf"

            with (
                patch.object(actor_main, "ROOT", root),
                patch.object(actor_main, "Actor", fake_actor),
                patch.object(actor_main, "ManagedLlm", FakeManagedLlm),
                patch.object(actor_main, "materialize_source", new_callable=AsyncMock) as stage_source,
                patch.dict(os.environ, {"BOOK_TRANSLATOR_LLM_API_KEY": "test-only-key"}),
            ):
                stage_source.return_value = root / "placeholder.txt"
                with self.assertRaisesRegex(actor_main.ActorRunError, "outputFormat"):
                    await actor_main.main()
                self.assertEqual(fake_actor.records["SUMMARY"]["status"], "failed")


if __name__ == "__main__":
    unittest.main()

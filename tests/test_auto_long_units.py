"""Synthetic regressions for oversized EPUB source units in Auto.

The input is generated: no copyrighted book text, network or model calls.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "automation"))
import book_runner as runner


class PlainStructuralHelper:
    def plan(self, source):
        return None

    def spans(self, plan):
        return [{"id": p["id"], "source": p["original"]}
                for p in plan["parts"] if p["kind"] == "text"]


class LongUnitContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.book = self.root / "books" / "synthetic-book"
        (self.book / "extracted").mkdir(parents=True)
        (self.book / "translated").mkdir()
        self.chapter = {
            "unit_id": "chapter-000002",
            "number": 2,
            "status": "extracted",
            "source_path": "extracted/two.md",
            "translation_path": "translated/two.md",
        }
        self.source = "\n\n".join(
            f"Passage {i}. " + "Every sentence retains its meaning. " * 13
            for i in range(170)
        )
        self.assertGreater(len(self.source), runner.MAX_CHAPTER_CHARS)
        self.assertLess(len(self.source), runner.MAX_REVIEW_SOURCE_CHARS)
        (self.book / self.chapter["source_path"]).write_text(self.source, "utf-8")
        (self.book / self.chapter["translation_path"]).write_text(self.source, "utf-8")
        (self.book / "metadata.json").write_text(json.dumps({
            "title": "Synthetic Book", "author": "Test Writer",
            "source_language": "en", "target_language": "ru",
        }), "utf-8")
        (self.book / "glossary.md").write_text("", "utf-8")
        (self.book / "style-guide.md").write_text("", "utf-8")

    def _contexts(self):
        return (
            patch.object(runner, "ROOT", self.root),
            patch.object(runner, "role_contracts", return_value=("Pinned role rules", ["AGENTS.md"])),
            patch.object(runner, "chapter_data", return_value=({}, {}, [self.chapter])),
        )

    def test_long_translator_skips_full_prompt_and_uses_all_bounded_spans(self):
        with self._contexts()[0], self._contexts()[1], self._contexts()[2]:
            system, user, source = runner.preliminary_context(
                self.root, "synthetic-book", self.chapter, "translator")
            self.assertEqual((system, user, source), ("", "", self.source))
            with self.assertRaisesRegex(runner.JobError, "safe single-context"):
                runner.context(self.root, "synthetic-book", self.chapter, "translator")
            helper = PlainStructuralHelper()
            plan = runner.bounded_source_plan(self.source, helper)
            spans = helper.spans(plan)
            self.assertGreater(len(spans), 30)
            self.assertTrue(all(len(span["source"]) <= runner.SOURCE_SPAN_CHAR_LIMIT
                                for span in spans))
            restored = "".join(
                p["before"] + p["original"] + p["after"] if p["kind"] == "text"
                else p["value"] for p in plan["parts"])
            self.assertEqual(restored, self.source)
            self.assertEqual(plan["source_sha256"],
                             hashlib.sha256(self.source.encode()).hexdigest())
            system, prompt, original = runner.context(
                self.root, "synthetic-book", self.chapter, "translator",
                structural_segments=spans[:3])
            self.assertEqual(original, self.source)
            self.assertIn(spans[0]["source"], prompt)
            self.assertNotIn(spans[-1]["source"], prompt)
            self.assertLess(len(system) + len(prompt), runner.MAX_CHAPTER_CHARS)

    def test_long_reviewer_has_complete_but_budgeted_evidence(self):
        with self._contexts()[0], self._contexts()[1], self._contexts()[2]:
            system, user, source = runner.context(
                self.root, "synthetic-book", self.chapter, "reviewer")
            self.assertEqual(source, self.source)
            self.assertIn(self.source, user)
            self.assertIn("CURRENT ACCEPTED TRANSLATION", user)
            self.assertLessEqual(len(system) + len(user), runner.MAX_REVIEW_PROMPT_CHARS)

    def test_reviewer_refuses_oversized_full_prompt_or_source(self):
        with self._contexts()[0], self._contexts()[1], self._contexts()[2]:
            translation = self.source + (" extra translated paragraph." * 4000)
            (self.book / self.chapter["translation_path"]).write_text(translation, "utf-8")
            with self.assertRaisesRegex(runner.JobError, "reviewer prompt exceeds"):
                runner.context(self.root, "synthetic-book", self.chapter, "reviewer")
            too_long = self.source + ("Another paragraph.\n\n" * 3000)
            (self.book / self.chapter["source_path"]).write_text(too_long, "utf-8")
            with self.assertRaisesRegex(runner.JobError, "safe single-context"):
                runner.context(self.root, "synthetic-book", self.chapter, "reviewer")

    def test_normal_short_unit_still_uses_existing_direct_context(self):
        short = "One complete paragraph."
        (self.book / self.chapter["source_path"]).write_text(short, "utf-8")
        with self._contexts()[0], self._contexts()[1], self._contexts()[2]:
            system, prompt, original = runner.preliminary_context(
                self.root, "synthetic-book", self.chapter, "translator")
        self.assertEqual(original, short)
        self.assertIn(short, prompt)
        self.assertIn("Pinned role rules", system)


if __name__ == "__main__":
    unittest.main()

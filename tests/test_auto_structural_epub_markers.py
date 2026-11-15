"""Protect source XHTML marker spans and split lengthy prose losslessly."""
from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "automation"))

import book_runner
from workflow import structural_translation as helper


class StructuralSourceSplitTests(unittest.TestCase):
    def test_all_preserved_epub_tags_are_inert_to_model(self):
        source = (
            "<!-- bt-block:000001 -->\n"
            "Before <!-- bt-inline:000001:start -->styled"
            "<!-- bt-media:000001 --> text<!-- bt-inline:000001:end --> after. "
            "<!-- bt-anchor:note-01 --> [Official](https://example.org/source)"
        )
        plan = book_runner.bounded_source_plan(source, helper)
        spans = helper.spans(plan)
        self.assertTrue(spans)
        self.assertTrue(all("<!-- bt-" not in part["source"] for part in spans))
        self.assertTrue(all("https://example.org/source" not in part["source"] for part in spans))
        protected = [p["value"] for p in plan["parts"]
                     if p["kind"] in {"epub_marker", "link"}]
        self.assertEqual(len(protected), 6)
        reconstructed = helper.render(
            plan, {p["id"]: p["source"] for p in spans})
        self.assertEqual(reconstructed, source + "\n")
        book_runner.check_markers(source, reconstructed)
        unsafe = json.dumps({
            "segments": [{"id": spans[0]["id"],
                          "translation": "Alter <!-- bt-media:000001 -->"}],
            "glossary": [], "style_guide": [],
        })
        with self.assertRaisesRegex(ValueError, "unsafe translated segment"):
            helper.parse_batch(unsafe, [spans[0]["id"]])

    def test_paragraph_longer_than_model_limit_splits_on_source_whitespace(self):
        source = ("A complete sentence with stable meaning. " * 180).strip()
        self.assertGreater(len(source), book_runner.SOURCE_SPAN_CHAR_LIMIT * 3)
        plan = book_runner.bounded_source_plan(source, helper)
        spans = helper.spans(plan)
        self.assertGreater(len(spans), 3)
        self.assertTrue(all(0 < len(item["source"]) <= book_runner.SOURCE_SPAN_CHAR_LIMIT
                            for item in spans))
        self.assertTrue(all(p["value"].isspace() for p in plan["parts"]
                            if p["kind"] == "literal"))
        self.assertEqual(helper.render(plan, {p["id"]: p["source"]
                                              for p in spans}), source + "\n")
        self.assertEqual(plan["source_sha256"],
                         hashlib.sha256(source.encode("utf-8")).hexdigest())

    def test_mixed_long_paragraph_and_block_markers_are_not_spliced(self):
        source = (
            "<!-- bt-block:000001 -->\n"
            + "Long paragraph with punctuation. " * 103
            + "\n\n<!-- bt-block:000002 -->\n"
            + "Second plain sentence. " * 95
        )
        plan = book_runner.bounded_source_plan(source, helper, limit=650)
        spans = helper.spans(plan)
        self.assertTrue(all(len(item["source"]) <= 650 for item in spans))
        self.assertEqual(helper.render(plan, {item["id"]: item["source"] for item in spans}),
                         source.rstrip() + "\n")
        self.assertEqual([p["value"] for p in plan["parts"]
                          if p["kind"] == "epub_marker"],
                         ["<!-- bt-block:000001 -->", "<!-- bt-block:000002 -->"])

    def test_unbreakable_source_token_stays_blocked(self):
        source = "X" * (book_runner.SOURCE_SPAN_CHAR_LIMIT + 100)
        with self.assertRaisesRegex(book_runner.JobError,
                                    "source token exceeds bounded translator span limit"):
            book_runner.bounded_source_plan(source, helper)


if __name__ == "__main__":
    unittest.main()

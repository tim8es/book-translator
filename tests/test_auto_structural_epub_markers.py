"""Protect source XHTML marker spans and split lengthy prose losslessly."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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


class StructuralPreflightRecoveryTests(unittest.TestCase):
    JOB = "05eed6ea814d3b5eec014c6d05b39e3f"

    @classmethod
    def blocked(cls):
        return {
            "schema_version": 1,
            "job_id": cls.JOB,
            "book_slug": "fixture-book",
            "state": "blocked",
            "phase": "model-blocked",
            "failures": 3,
            "active": None,
            "blocked_unit_id": "chapter-000001",
            "source_sha256": "a" * 64,
            "workflow_revision": "b" * 40,
            "last_error": "source paragraph exceeds bounded translator span limit",
            "correction_rounds": {},
        }

    def test_exact_pre_model_error_is_auto_recoverable_once(self):
        status = self.blocked()
        self.assertTrue(book_runner.recoverable_structural_span_blocked(status))
        for changes in (
            {"last_error": "unknown model failure"},
            {"active": {"role": "translator"}},
            {"model_calls_started": 1},
            {"structural_segment_progress": {"completed": 1, "total": 4}},
            {"provider_retry": {"attempts": 1}},
            {"structural_span_retries": 1},
            {"blocked_unit_id": "other"},
            {"workflow_revision": None},
            {"book_slug": None},
            {"state": "running"},
            {"phase": "review-deferred-requires-operator"},
        ):
            with self.subTest(changes=changes):
                self.assertFalse(book_runner.recoverable_structural_span_blocked(
                    {**status, **changes}))
        book_runner.approve_auto_structural_span_resume(status)
        self.assertEqual(status["state"], "running")
        self.assertEqual(status["failures"], 0)
        self.assertEqual(status["structural_span_retries"], 1)
        self.assertEqual(status["recoveries"][0]["action"],
                         "auto-retry-corrected-structural-preflight")
        self.assertEqual(status["recoveries"][0]["helper_revision"],
                         book_runner.PUBLIC_STRUCTURAL_HELPER_REVISION)
        self.assertFalse(book_runner.recoverable_structural_span_blocked(status))
        with self.assertRaisesRegex(book_runner.JobError, "unsafe automatic"):
            book_runner.approve_auto_structural_span_resume(status)

    def test_ready_queue_admits_fixed_preflight_only_until_first_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            intake = root / "intake" / "auto" / self.JOB
            intake.mkdir(parents=True)
            (intake / "request.json").write_text("{}")
            status_dir = root / "automation" / "status"
            status_dir.mkdir(parents=True)
            path = status_dir / (self.JOB + ".json")
            status = self.blocked()
            path.write_text(json.dumps(status))
            with patch.object(book_runner, "ROOT", root), \
                 patch.object(book_runner, "book_key", return_value="fixture-book"):
                self.assertEqual(book_runner.ready_jobs(),
                                 [{"job_id": self.JOB, "book_key": "fixture-book"}])
                status["structural_span_retries"] = 1
                path.write_text(json.dumps(status))
                self.assertEqual(book_runner.ready_jobs(), [])


if __name__ == "__main__":
    unittest.main()

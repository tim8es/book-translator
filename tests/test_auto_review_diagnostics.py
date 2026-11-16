"""Synthetic, no-provider regressions for deterministic review recovery.

Tests cannot pass a chapter, mutate a translation, or waive pinned evidence.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))
sys.path.insert(0, str(ROOT / "scripts"))
import book_runner as runner
from omniroute import ModelError, TransientModelError
from review_diagnostics import review_advisories, reviewer_failure_detail, should_chunk_review


class ReviewAdvisoryTests(unittest.TestCase):
    def test_detects_suspicious_spelling_in_second_block_without_rewriting(self):
        source = ("<!-- bt-block:000001 --> Lance arrives.\n"
                  "<!-- bt-block:000002 --> Lance smiles.")
        target = ("<!-- bt-block:000001 --> Лэнс приходит.\n"
                  "<!-- bt-block:000002 --> Ланс улыбается.")
        glossary = ("| Original | Translation | Type | Notes |\n"
                    "| --- | --- | --- | --- |\n"
                    "| Lance | Лэнс | person | Established name |\n")
        result = review_advisories(source, target, glossary)
        self.assertEqual(result["warning_count"], 1)
        self.assertEqual(result["warnings"], [{
            "kind": "glossary_rendering_candidate",
            "block_id": "000002",
            "original": "Lance",
            "glossary_rendering": "Лэнс",
        }])
        self.assertEqual(result["source_sha256"], hashlib.sha256(source.encode()).hexdigest())
        self.assertEqual(result["translation_sha256"], hashlib.sha256(target.encode()).hexdigest())
        self.assertTrue(result["advisory_only"])
        self.assertEqual(review_advisories(source, target, glossary, limit=0)["warnings"], [])
        self.assertEqual(target.splitlines()[-1], "<!-- bt-block:000002 --> Ланс улыбается.")

    def test_inflection_and_unpaired_blocks_are_advisory_only(self):
        glossary = "| Lance | Лэнс | person | Case forms are allowed |\n"
        source = "<!-- bt-block:000001 --> Lance smiled."
        inflected = "<!-- bt-block:000001 --> Лэнса рассмешили."
        self.assertEqual(review_advisories(source, inflected, glossary)["warnings"], [])
        unrelated = "<!-- bt-block:000002 --> Ланс улыбнулся."
        mismatch = review_advisories(source, unrelated, glossary)
        self.assertEqual(mismatch["warnings"][0]["kind"], "block_alignment")
        self.assertFalse(any(x["kind"] == "glossary_rendering_candidate"
                             for x in mismatch["warnings"]))

    def test_no_markers_does_not_invent_word_alignments(self):
        self.assertEqual(review_advisories("Lance", "Ланс",
                                          "| Lance | Лэнс | person | name |\n")["warnings"], [])


class ReviewRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.book = self.root / "books" / "test-book"
        for path in ("extracted", "translated", ".workflow/claims"):
            (self.book / path).mkdir(parents=True, exist_ok=True)
        self.source = ("English paragraph with significant narrative context. " * 12 + "\n\n") * 15
        self.target = ("Русский перевод с отдельными литературными решениями. " * 12 + "\n\n") * 15
        (self.book / "extracted/one.md").write_text(self.source, "utf-8")
        (self.book / "translated/one.md").write_text(self.target, "utf-8")
        (self.book / "glossary.md").write_text("", "utf-8")
        (self.book / "style-guide.md").write_text("", "utf-8")
        (self.book / ".workflow/claims/chapter-000001.json").write_text(
            json.dumps({"session_id": "test-session"}), "utf-8")
        self.chapter = {"unit_id": "chapter-000001", "number": 1,
                        "status": "translated", "source_path": "extracted/one.md",
                        "translation_path": "translated/one.md"}
        self.status = {"state": "running", "workflow_revision": "a" * 40,
                       "active": {"role": "reviewer", "chapter": 1,
                                  "session_id": "test-session"},
                       "model_calls_started": 0, "failures": 0,
                       "role_protocol_failures": {"chapter-000001:reviewer": 2}}

    def test_safe_repeated_evidence_failure_switches_to_resumable_chunks(self):
        self.assertTrue(should_chunk_review(self.source, self.target, 2))
        with patch.object(runner, "ROOT", self.root), \
             patch.object(runner, "chapter_data",
                          return_value=({}, {"records": []}, [self.chapter])), \
             patch.object(runner, "preliminary_context",
                          return_value=("pinned system", "prompt", self.source)), \
             patch.object(runner, "role_contracts",
                          return_value=("pinned reviewer", ["AGENTS.md"])), \
             patch.object(runner, "checkpoint"), \
             patch.object(runner, "process_review_chunks", return_value=None) as chunks:
            runner.process_claim("c" * 32, "test-book", self.status, self.root)
            self.assertEqual(self.status["review_chunk_fallback_unit"], "chapter-000001")
            self.status["role_protocol_failures"] = {}
            runner.process_claim("c" * 32, "test-book", self.status, self.root)
        self.assertEqual(chunks.call_count, 2)
        self.assertEqual(self.status["model_calls_started"], 0)
        self.assertEqual((self.book / "translated/one.md").read_text("utf-8"), self.target)

    def test_single_failure_does_not_switch_and_unpartitionable_is_safe(self):
        self.assertFalse(should_chunk_review(self.source, self.target, 1))
        self.assertFalse(should_chunk_review("a" * 6000, "б" * 6000, 2))
        self.assertFalse(should_chunk_review(self.source, self.target, -1))
        self.assertFalse(should_chunk_review(self.source, self.target, True))

    def test_typed_evidence_errors_do_not_copy_private_quote(self):
        cases = [
            ("reviewer must return JSON", "invalid_json"),
            ("reviewer evidence is invalid: every literary check must have been performed",
             "incomplete_checks"),
            ("reviewer evidence is invalid: prior unresolved findings lack explicit rechecks: secret",
             "missing_prior_rechecks"),
            ("reviewer evidence is invalid: findings[3].translation_anchor: "
             "quoted passage occurrence 0 is absent from the current artifact",
             "anchor_absent"),
            ("reviewer evidence is invalid: findings[0].source_anchor: quote is repeated; "
             "use an unambiguous longer excerpt", "anchor_ambiguous"),
        ]
        for message, expected in cases:
            with self.subTest(expected=expected):
                detail = reviewer_failure_detail(ModelError(message))
                self.assertEqual(detail["kind"], expected)
                self.assertNotIn("secret", json.dumps(detail))
        detail = reviewer_failure_detail(ModelError(cases[3][0]))
        self.assertEqual(detail["finding_index"], 3)
        self.assertEqual(detail["field"], "translation_anchor")
        self.assertIsNone(runner.reviewer_model_output_code(
            TransientModelError("provider transport", code="transport")))


if __name__ == "__main__":
    unittest.main()

"""Synthetic contract regressions for source-bound correction convergence.

No book content, network, secret or model call is used.
"""
from __future__ import annotations

import hashlib
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "automation"))

import correction_feedback as feedback
import execution_metrics as metrics
import book_runner as runner


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def finding(ident="f1", category="referent", detail="Actor still has wrong gender"):
    return {
        "id": ident, "category": category, "severity": "major",
        "resolved": False, "detail": detail,
        "source_anchor": {"quote": "The traveler entered the garden.", "occurrence": 0},
        "translation_anchor": {"quote": "Путешественница вошла в сад.", "occurrence": 0},
    }


def rejected(sequence, target_hash, items, source_hash="s" * 64):
    return {
        "sequence": sequence, "record_id": f"{sequence:032x}",
        "unit_id": "chapter-000001", "source_sha256": source_hash,
        "translation_sha256": target_hash, "outcome": "CORRECTIONS_REQUIRED",
        "quality_report": {"evidence_version": 2, "findings": items},
    }


class CorrectionFeedbackTests(unittest.TestCase):
    def fixture(self):
        current = rejected(3, "c" * 64, [finding()])
        old = rejected(1, "a" * 64, [finding(detail="Prior edit still used feminine agreement")])
        recent = rejected(2, "b" * 64, [finding(detail="Previous fix changed a word, not the subject")])
        chapter = {"unit_id": "chapter-000001",
                   "translation_acceptance": {
                       "source_sha256": "s" * 64,
                       "translation_sha256": current["translation_sha256"],
                   }}
        return {"records": [old, recent, current]}, chapter, current

    def test_feature_is_explicit_opt_in(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(feedback.enabled())
        self.assertTrue(feedback.enabled({"BOOK_TRANSLATOR_CORRECTION_FEEDBACK": "on"}))
        with self.assertRaises(ValueError):
            feedback.enabled({"BOOK_TRANSLATOR_CORRECTION_FEEDBACK": "yes"})

    def test_recurrent_anchored_issue_replans_without_mutation_or_pass(self):
        ledger, chapter, current = self.fixture()
        result = feedback.plan(ledger, chapter, current, [finding()])
        self.assertEqual(result["audit"]["strategy"], "replan_anchored_clause")
        self.assertEqual(result["audit"]["recurring_anchored_findings"], 1)
        self.assertEqual(result["audit"]["prior_distinct_rejected_reviews"], 2)
        self.assertIn("Previous fix changed a word", result["prompt"])
        self.assertIn("ONLY allowed replacement locations", result["prompt"])
        self.assertNotIn("Previous fix changed", str(result["audit"]))
        self.assertNotIn("Путешественница", str(result["audit"]))
        self.assertNotIn('"PASS"', result["prompt"])

    def test_different_unit_source_or_duplicate_target_never_counts_as_history(self):
        ledger, chapter, current = self.fixture()
        ledger["records"][0]["unit_id"] = "chapter-000002"
        ledger["records"][1]["source_sha256"] = "other"
        out = feedback.plan(ledger, chapter, current, [finding()])
        self.assertEqual(out["audit"]["strategy"], "focused_patch")
        self.assertEqual(out["audit"]["prior_distinct_rejected_reviews"], 0)
        ledger, chapter, current = self.fixture()
        ledger["records"][1]["translation_sha256"] = ledger["records"][0]["translation_sha256"]
        out = feedback.plan(ledger, chapter, current, [finding()])
        self.assertEqual(out["audit"]["prior_distinct_rejected_reviews"], 1)

    def test_no_effect_scoped_to_current_review_and_artifact(self):
        ledger, chapter, current = self.fixture()
        ledger["records"] = [current]
        attempt = {"unit_id": "chapter-000001", "review_record_id": current["record_id"],
                   "translation_sha256": current["translation_sha256"], "count": 1}
        self.assertEqual(feedback.plan(ledger, chapter, current, [finding()], attempt)[
            "audit"]["strategy"], "replan_anchored_clause")
        attempt["review_record_id"] = "obsolete"
        self.assertEqual(feedback.plan(ledger, chapter, current, [finding()], attempt)[
            "audit"]["strategy"], "focused_patch")

    def test_stale_accepted_review_and_unrecognized_finding_refused(self):
        ledger, chapter, current = self.fixture()
        chapter["translation_acceptance"]["translation_sha256"] = "z" * 64
        with self.assertRaisesRegex(ValueError, "current accepted review"):
            feedback.plan(ledger, chapter, current, [finding()])
        chapter["translation_acceptance"]["translation_sha256"] = "c" * 64
        with self.assertRaisesRegex(ValueError, "unrecognized finding"):
            feedback.plan(ledger, chapter, current, [finding("unknown")])

    def test_double_no_op_can_defer_without_pass_or_translation_change(self):
        ledger, chapter, current = self.fixture()
        chapter["number"] = 1
        chapter["status"] = "translated"
        status = {"failures": 2}
        with patch.object(runner, "cli") as call, patch.object(runner, "clock",
                                                              return_value="2026-11-22T00:00:00Z"):
            self.assertFalse(runner.defer_protocol_failed_unit(
                "synthetic", chapter, current, status, "unchanged_correction"))
            self.assertTrue(runner.defer_protocol_failed_unit(
                "synthetic", chapter, current, status, "unchanged_correction",
                minimum_failures=2))
        call.assert_called_once_with("defer-review", "synthetic", "1",
                                     "--reason", "operator-attention")
        self.assertEqual(status["state"], "running")
        self.assertEqual(status["phase"], "model-unit-deferred")
        self.assertEqual(status["failures"], 0)
        self.assertEqual(status["model_unit_deferral_audit"][0]["error_code"],
                         "unchanged_correction")
        self.assertNotIn("PASS", str(status))


    def test_first_observed_translation_to_pass_has_inspectable_duration(self):
        status = {}
        metrics.quality_started(status, "chapter-000001", "s" * 64, 10,
                                "2026-11-22T12:00:00Z")
        metrics.quality_started(status, "chapter-000001", "s" * 64, 99,
                                "2026-11-22T13:00:00Z")
        result = metrics.quality_passed(
            status, "chapter-000001", "s" * 64, "t" * 64, 22, 3, 2,
            "2026-11-22T13:30:00Z")
        self.assertEqual(result["seconds_from_first_observed_translation"], 5400)
        self.assertEqual(result["model_calls_after_first_observed_translation"], 12)
        self.assertEqual(result["prior_rejected_reviews"], 3)
        metrics.quality_passed(status, "chapter-000001", "s" * 64, "t" * 64,
                               22, 3, 2, "2026-11-22T13:30:00Z")
        self.assertEqual(len(status["quality_pass_metrics"]), 1)

    def test_historical_chatops_translation_is_not_given_fake_time_to_pass(self):
        status = {}
        result = metrics.quality_passed(
            status, "chapter-000002", "s" * 64, "t" * 64, 45, 4, 3,
            "2026-11-22T13:30:00Z")
        self.assertIsNone(result["seconds_from_first_observed_translation"])
        self.assertIsNone(result["model_calls_after_first_observed_translation"])
        self.assertEqual(result["prior_rejected_reviews"], 4)



if __name__ == "__main__":
    unittest.main()

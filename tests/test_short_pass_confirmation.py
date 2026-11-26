import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))
import book_runner as runner


class ShortPassConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.source, self.target = "Seven coins.", "Семь монет."
        self.chapter = {"unit_id": "chapter-000001", "translation_acceptance": {
            "source_sha256": runner.review_chunks.sha(self.source),
            "translation_sha256": runner.review_chunks.sha(self.target)}}
        self.status = {"workflow_revision": "a" * 40, "model_calls_started": 3}
        self.owned = {"session_id": "reviewer-test"}
        self.first = ("PASS", {"findings": []}, [], [])
        self.second = ("CORRECTIONS_REQUIRED", {"findings": [{"resolved": False}]}, ["style"], [])
        patchers = [
            patch.dict(os.environ, {"BOOK_TRANSLATOR_CONFIRM_SHORT_PASS": "on"}),
            patch.object(runner.quality_controller, "supported", return_value=True),
            patch.object(runner, "context", return_value=("system", "source and target", self.source)),
            patch.object(runner, "checkpoint"),
            patch.object(runner, "model_call_limit", return_value=10),
            patch.object(runner, "complete_for_job", return_value="answer"),
            patch.object(runner, "checked_reviewer_response", return_value=self.second[:3]),
            patch.object(runner, "reviewer_glossary_proposals", return_value=[]),
        ]
        self.mocks = [p.start() for p in patchers]
        for p in patchers:
            self.addCleanup(p.stop)

    def call(self, first=None):
        return runner.confirm_short_review_pass("a"*32, "fixture", self.status,
            ROOT, self.chapter, self.owned, None, self.source, self.target,
            *(self.first if first is None else first))

    def test_second_rejection_replaces_first_pass(self):
        self.assertEqual(self.call(), self.second)
        self.assertEqual(self.status["model_calls_started"], 4)
        self.assertEqual(self.status["short_pass_confirmation_calls"], 1)
        self.assertEqual(self.status["short_pass_confirmations"]["chapter-000001"]["outcome"],
                         "CORRECTIONS_REQUIRED")
        self.mocks[2].assert_called_once_with(ROOT, "fixture", self.chapter,
                                             "reviewer", force_full_review=True)
        self.assertEqual(self.mocks[3].call_count, 2)

    def test_two_passes_return_second_full_evidence(self):
        self.mocks[6].return_value = self.first[:3]
        self.assertEqual(self.call(), self.first)
        self.assertEqual(self.status["short_pass_confirmations"]["chapter-000001"]["outcome"], "PASS")

    def test_disabled_or_first_rejection_has_no_extra_call(self):
        self.assertEqual(self.call(self.second), self.second)
        with patch.dict(os.environ, {"BOOK_TRANSLATOR_CONFIRM_SHORT_PASS": "off"}):
            self.assertEqual(self.call(), self.first)
        self.mocks[5].assert_not_called()

    def test_budget_and_invalid_response_fail_closed(self):
        self.status["model_calls_started"] = 10
        with self.assertRaises(runner.ModelError):
            self.call()
        self.mocks[5].assert_not_called()
        self.status["model_calls_started"] = 3
        self.mocks[6].side_effect = runner.ModelError("invalid")
        with self.assertRaises(runner.ModelError):
            self.call()
        self.assertEqual(self.status["model_calls_started"], 4)
        self.assertEqual(self.status["short_pass_confirmations"]["chapter-000001"]["outcome"], "reserved")

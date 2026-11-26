"""Review-plan advice is bounded by canonical artifact and model budgets."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))
import book_runner as runner
import agent_orchestrator


class ReviewOrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.source = "source " * 2000
        self.target = "перевод " * 2000
        self.unit = "chapter-000001"
        self.chapter = {"unit_id": self.unit, "translation_acceptance": {
            "source_sha256": runner.review_chunks.sha(self.source),
            "translation_sha256": runner.review_chunks.sha(self.target)}}
        self.status = {"workflow_revision": "a" * 40, "model_calls_started": 3}
        self.env = {"BOOK_TRANSLATOR_AGENT_REVIEW_PLANNING": "on",
                    "BOOK_TRANSLATOR_INCREMENTAL_REVIEW": "on",
                    "OMNIROUTE_ORCHESTRATOR_MODEL": "test"}
        self.patchers = [
            patch.dict(os.environ, self.env),
            patch.object(runner.quality_controller, "supported", return_value=True),
            patch.object(runner, "review_chunk_stage_path", return_value=ROOT / "nonexistent-stage"),
            patch.object(runner, "quality_stage_path", return_value=ROOT / "nonexistent-stage"),
            patch.object(runner.review_chunks, "plan_aligned", return_value={"chunks": [{}, {}]}),
            patch.object(runner.review_chunks, "validate_plan"),
            patch.object(runner, "role_contracts", return_value=("rules", [])),
            patch.object(runner, "model_call_limit", return_value=10),
            patch.object(runner, "checkpoint"),
            patch.object(runner.execution_metrics, "provider"),
            patch.object(runner, "complete", return_value='{"strategy":"aligned_chunks"}'),
        ]
        self.mocks = [p.start() for p in self.patchers]
        for p in self.patchers:
            self.addCleanup(p.stop)

    def choose(self):
        return runner.review_orchestrator_strategy("a" * 32, "fixture", self.status,
            ROOT, self.chapter, None, self.source, self.target)

    def test_valid_choice_is_reserved_and_reused(self):
        self.assertEqual(self.choose(), "aligned_chunks")
        self.assertEqual(self.choose(), "aligned_chunks")
        self.assertEqual(self.status["model_calls_started"], 4)
        self.mocks[-1].assert_called_once()
        self.assertEqual(self.mocks[-3].call_count, 2)
        self.assertNotIn(self.source, self.mocks[-1].call_args.args[2])
        self.assertEqual(self.status["review_orchestrator_decisions"][self.unit]["outcome"], "accepted")

    def test_interrupted_advice_never_repaid(self):
        self.assertEqual(self.choose(), "aligned_chunks")
        decision = self.status["review_orchestrator_decisions"][self.unit]
        decision.update(outcome="reserved", strategy="full_direct")
        self.assertEqual(self.choose(), "full_direct")
        self.mocks[-1].assert_called_once()

    def test_invalid_advice_cannot_promote(self):
        self.mocks[-1].return_value = '{"strategy":"PASS"}'
        self.assertEqual(self.choose(), "full_direct")
        self.assertEqual(self.status["model_calls_started"], 4)
        self.assertEqual(self.status["review_orchestrator_decisions"][self.unit]["outcome"], "fallback")

    def test_insufficient_budget_and_changed_target_do_not_pay(self):
        self.status["model_calls_started"] = 8
        self.assertEqual(self.choose(), "full_direct")
        self.status["model_calls_started"] = 3
        self.target += "changed"
        self.assertEqual(self.choose(), "full_direct")
        self.mocks[-1].assert_not_called()

    def test_disabled_by_default(self):
        with patch.dict(os.environ, {"BOOK_TRANSLATOR_AGENT_REVIEW_PLANNING": "off"}):
            self.assertEqual(self.choose(), "full_direct")
        self.mocks[-1].assert_not_called()

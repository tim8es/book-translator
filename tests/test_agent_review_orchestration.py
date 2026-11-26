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
            patch.object(runner.review_chunks, "plan_aligned", return_value={
                "helper_revision": runner.review_chunks.STABLE_ALIGNMENT_REVISION,
                "chunks": [{}, {}]}),
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


    def use_real_planner(self, source, target):
        # Remove only these two mocks. The actual planner and validator must
        # exercise complete coverage and real alignment, not a fabricated plan.
        self.patchers[4].stop()
        self.patchers[5].stop()
        self.source, self.target = source, target
        self.chapter["translation_acceptance"] = {
            "source_sha256": runner.review_chunks.sha(source),
            "translation_sha256": runner.review_chunks.sha(target)}

    def test_real_long_unmarked_text_never_pays_for_alignment_advice(self):
        source = "\n\n".join(f"Passage {i}: " + "The harbour was quiet. " * 30
                             for i in range(25))
        target = "\n\n".join(f"Отрывок {i}: " + "В гавани было тихо. " * 40
                             for i in range(25))
        self.assertGreater(len(source), runner.review_chunks.TARGET_CHARS)
        self.use_real_planner(source, target)
        plan = runner.review_chunks.plan_aligned(source, target, stable=True)
        self.assertEqual(plan["helper_revision"], runner.review_chunks.HELPER_REVISION)
        self.assertEqual(self.choose(), "full_direct")
        self.assertEqual(self.status["model_calls_started"], 3)
        self.mocks[-1].assert_not_called()

    def test_real_long_marked_artifacts_allow_one_bounded_advice_call(self):
        source = "\n\n".join(f"<!-- bt-block:{i:06d} -->\n" +
                             f"Passage {i}: " + "The harbour was quiet. " * 30
                             for i in range(1, 26))
        target = "\n\n".join(f"<!-- bt-block:{i:06d} -->\n" +
                             f"Отрывок {i}: " + "В гавани было тихо. " * (20 + i)
                             for i in range(1, 26))
        self.use_real_planner(source, target)
        plan = runner.review_chunks.plan_aligned(source, target, stable=True)
        self.assertEqual(plan["helper_revision"], runner.review_chunks.STABLE_ALIGNMENT_REVISION)
        self.assertTrue(2 <= len(plan["chunks"]) <= 4)
        runner.review_chunks.validate_plan(plan, source, target)
        for chunk in plan["chunks"][1:]:
            s = source[chunk["source_core"][0]:]
            t = target[chunk["translation_core"][0]:]
            self.assertEqual(runner.review_chunks.BLOCK_MARKER.match(s).group(1),
                             runner.review_chunks.BLOCK_MARKER.match(t).group(1))
        self.assertEqual(self.choose(), "aligned_chunks")
        self.assertEqual(self.choose(), "aligned_chunks")
        self.assertEqual(self.status["model_calls_started"], 4)
        self.mocks[-1].assert_called_once()
        self.assertEqual(self.status["review_orchestrator_decisions"][self.unit]["chunk_count"],
                         len(plan["chunks"]))

    def test_mismatched_markers_do_not_pay_for_advice(self):
        source = "\n\n".join(f"<!-- bt-block:{i:06d} -->\n" +
                             "The harbour was quiet. " * 30 for i in range(1, 26))
        target = source.replace("bt-block:000012", "bt-block:999999")
        self.use_real_planner(source, target)
        self.assertEqual(self.choose(), "full_direct")
        self.mocks[-1].assert_not_called()

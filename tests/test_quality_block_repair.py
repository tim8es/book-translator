"""Source-owned paragraph recovery regression tests."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "automation"), str(ROOT / "scripts")]
import book_runner as runner
import quality_block_repair as block
import candidate_review
from omniroute import TransientModelError

SOURCE = "<!-- bt-block:1 -->\nThe door was blue.\n\n<!-- bt-block:2 -->\nHe might find the key.\n"
TARGET = "<!-- bt-block:1 -->\nДверь была синей.\n\n<!-- bt-block:2 -->\nОн найдёт ключ.\n"
S = block.sha(SOURCE)
T = block.sha(TARGET)
UNIT = "chapter-000001"
RID = "a" * 32
OLD = "b" * 32


def record(fid="modality", target=T, ref=None):
    return {"record_id": RID if ref else OLD, "unit_id": UNIT,
            "source_sha256": S, "translation_sha256": target,
            "outcome": "CORRECTIONS_REQUIRED", "quality_report": {
                "evidence_version": 2, "findings": [{
                    "id": fid, "category": "modality", "severity": "major",
                    "detail": "Possibility became certainty.",
                    "source_anchor": {"quote": "might find", "occurrence": 0},
                    "translation_anchor": {"quote": "найдёт", "occurrence": 0},
                    "resolved": False, "resolves": ref}]}}


def fixture():
    old = record("first", "e" * 64)
    current = record(ref={"record_id": OLD, "finding_id": "first"})
    return current, {"records": [old, current]}


class BlockPlanTests(unittest.TestCase):
    def test_source_owned_reconstruction_changes_only_authorized_body(self):
        current, _ = fixture()
        plan = block.plan_for(SOURCE, TARGET, current, "2", position=runner._anchor_position)
        self.assertIsNotNone(plan)
        result = block.apply(plan, SOURCE, TARGET, current, "Возможно, он найдёт ключ.",
                             position=runner._anchor_position)
        self.assertEqual(result, TARGET.replace("Он найдёт ключ.", "Возможно, он найдёт ключ."))
        runner.check_markers(SOURCE, result)
        with self.assertRaises(ValueError):
            block.apply(plan, SOURCE, TARGET, current, "Новое<!-- bt-block:3 -->",
                        position=runner._anchor_position)
        with self.assertRaises(ValueError):
            block.apply(plan, SOURCE, TARGET, current, "Он найдёт ключ.",
                        position=runner._anchor_position)

    def test_mismatched_blocks_or_cross_block_findings_not_admitted(self):
        current, _ = fixture()
        self.assertIsNone(block.plan_for(SOURCE, TARGET.replace("bt-block:2", "bt-block:3"),
                                        current, "2", position=runner._anchor_position))
        current["quality_report"]["findings"][0]["translation_anchor"] = {
            "quote": "синей", "occurrence": 0}
        self.assertIsNone(block.plan_for(SOURCE, TARGET, current, "2",
                                        position=runner._anchor_position))

    def test_one_reconstruction_per_stable_issue(self):
        current, _ = fixture()
        plan = {"modality": {"level": 1, "issue_id": "stable"}}
        self.assertIsNotNone(block.select(SOURCE, TARGET, current, plan, [],
                                          position=runner._anchor_position))
        attempts = [{"source_sha256": S, "issue_ids": ["stable"]}]
        self.assertIsNone(block.select(SOURCE, TARGET, current, plan, attempts,
                                        position=runner._anchor_position))


class BlockRuntimeTests(unittest.TestCase):
    def test_staged_candidate_requires_independent_veto_and_survives_resume(self):
        current, ledger = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            book = root / "books" / "test"
            book.mkdir(parents=True)
            (book / "glossary.md").write_text("Glossary", "utf-8")
            (book / "style-guide.md").write_text("Style", "utf-8")
            chapter = {"unit_id": UNIT, "number": 1}
            status = {"workflow_revision": "f" * 40, "active": {"session_id": "corrector"},
                      "model_calls_started": 0}
            unresolved = current["quality_report"]["findings"]
            review = json.dumps({"schema_version": 1,
                "checks": dict.fromkeys(candidate_review.CHECKS, True),
                "decisions": [{"finding_id": "block-reconstruction", "verdict": "approve",
                               "risk": "none"}]})
            with patch.object(runner, "ROOT", root), \
                 patch.object(runner, "checkpoint"), \
                 patch.object(runner, "model_call_limit", return_value=100), \
                 patch.object(runner, "role_contracts", return_value=("Independent contract", ["doc"])), \
                 patch.object(runner, "complete_for_job", side_effect=[
                     json.dumps({"replacement": "Возможно, он найдёт ключ."}), review]) as provider:
                args = ("job", "test", status, ROOT, chapter, current, ledger, SOURCE, TARGET, unresolved)
                self.assertEqual(runner.process_quality_block_repair(*args), (True, None))
                stage = runner.read_json(runner.quality_block_stage_path("test", UNIT))
                self.assertEqual(stage["state"], "candidate")
                handled, result = runner.process_quality_block_repair(*args)
                self.assertTrue(handled)
                self.assertEqual(result[0], TARGET.replace("Он найдёт ключ.", "Возможно, он найдёт ключ."))
                self.assertEqual(runner.process_quality_block_repair(*args), (True, result))
                self.assertEqual(provider.call_count, 2)
                self.assertEqual(status["model_calls_started"], 2)

    def test_independent_veto_never_returns_candidate(self):
        current, ledger = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            book = root / "books" / "test"
            book.mkdir(parents=True)
            (book / "glossary.md").write_text("Glossary", "utf-8")
            (book / "style-guide.md").write_text("Style", "utf-8")
            chapter = {"unit_id": UNIT, "number": 1}
            status = {"workflow_revision": "f" * 40, "active": {"session_id": "corrector"},
                      "model_calls_started": 0}
            veto = json.dumps({"schema_version": 1, "checks": dict.fromkeys(candidate_review.CHECKS, True),
                    "decisions": [{"finding_id": "block-reconstruction", "verdict": "reject", "risk": "meaning"}]})
            with patch.object(runner, "ROOT", root), patch.object(runner, "checkpoint"), \
                 patch.object(runner, "model_call_limit", return_value=100), \
                 patch.object(runner, "role_contracts", return_value=("Independent contract", ["doc"])), \
                 patch.object(runner, "complete_for_job", side_effect=[
                    json.dumps({"replacement": "Возможно, он найдёт ключ."}), veto]) as provider:
                args = ("job", "test", status, ROOT, chapter, current, ledger, SOURCE, TARGET,
                        current["quality_report"]["findings"])
                self.assertEqual(runner.process_quality_block_repair(*args), (True, None))
                self.assertEqual(runner.process_quality_block_repair(*args), (True, None))
                self.assertEqual(runner.process_quality_block_repair(*args), (False, None))
                self.assertEqual(provider.call_count, 2)
                self.assertEqual(status["quality_block_attempts"][0]["outcome"], "independent-veto")



class BlockBatchTests(unittest.TestCase):
    def test_disjoint_reconstruction_preserves_markers_and_checks_combined_context(self):
        current, _ = fixture()
        first = dict(current["quality_report"]["findings"][0], id="door",
                     source_anchor={"quote": "blue", "occurrence": 0},
                     translation_anchor={"quote": "синей", "occurrence": 0})
        current["quality_report"]["findings"].append(first)
        strategy = {"modality": {"level": 1, "issue_id": "key"},
                    "door": {"level": 1, "issue_id": "door"}}
        plan = block.select_batch(SOURCE, TARGET, current, strategy, [],
                                  position=runner._anchor_position)
        self.assertEqual(plan["schema_version"], 2)
        self.assertEqual(set(plan["finding_ids"]), {"door", "modality"})
        replacements = {"1": "Дверь была голубой.", "2": "Возможно, он найдёт ключ."}
        result = block.apply_batch(plan, SOURCE, TARGET, current, replacements,
                                   position=runner._anchor_position)
        runner.check_markers(SOURCE, result)
        self.assertIn(replacements["1"], result)
        self.assertIn(replacements["2"], result)
        cases = block.review_batch_cases(plan, SOURCE, TARGET, current, replacements,
                                         position=runner._anchor_position)
        self.assertEqual(len(cases), 2)
        self.assertIn(replacements["2"], cases[0]["candidate_context_after"])
        self.assertIn(replacements["1"], cases[1]["candidate_context_before"])
        with self.assertRaises(ValueError):
            block.apply_batch(plan, SOURCE, TARGET, current, {"1": replacements["1"]},
                              position=runner._anchor_position)
        with self.assertRaises(ValueError):
            block.apply_batch(plan, SOURCE, TARGET, current,
                              {**replacements, "2": "<!-- bt-block:3 -->"},
                              position=runner._anchor_position)
        with self.assertRaises(ValueError):
            block.apply_batch(plan, SOURCE, TARGET + "changed", current, replacements,
                              position=runner._anchor_position)


if __name__ == "__main__":
    unittest.main()

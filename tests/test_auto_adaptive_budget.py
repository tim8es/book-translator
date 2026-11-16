"""Synthetic evidence-aware pacing with unchanged canonical review requirements."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "automation"))
import adaptive_budget
import book_runner as runner


def finding(n, severity="minor", category="style"):
    return {"id": f"fix-{n}", "severity": severity, "category": category,
            "resolved": False}


class AdaptivePlanTests(unittest.TestCase):
    def test_safe_default_and_strict_mode(self):
        self.assertFalse(adaptive_budget.enabled({}))
        self.assertTrue(adaptive_budget.enabled({
            "BOOK_TRANSLATOR_ADAPTIVE_BUDGET": "on"}))
        with self.assertRaises(ValueError):
            adaptive_budget.enabled({"BOOK_TRANSLATOR_ADAPTIVE_BUDGET": "maybe"})

    def test_routine_issues_group_three_in_one_call(self):
        decision = adaptive_budget.plan(
            [finding(i) for i in range(1, 7)],
            unit_id="chapter-000001", record_id="record-1")
        self.assertEqual(decision["group_size"], 3)
        self.assertEqual(decision["max_calls_per_checkpoint"], 2)
        self.assertEqual(decision["lower_bound_remaining_calls"], 2)

    def test_critical_and_previous_failed_patch_isolate_one(self):
        cases = [finding(1, "critical", "meaning"), finding(2), finding(3)]
        decision = adaptive_budget.plan(
            cases, unit_id="chapter-000001", record_id="record-1")
        self.assertEqual(decision["group_size"], 1)
        self.assertEqual(decision["max_calls_per_checkpoint"], 1)
        decision = adaptive_budget.plan(
            [finding(1), finding(2), finding(3)],
            rejected=[{"unit_id": "chapter-000001",
                       "review_record_id": "record-1",
                       "finding_id": "fix-1"}],
            unit_id="chapter-000001", record_id="record-1")
        self.assertEqual(decision["group_size"], 1)
        self.assertEqual(decision["risk_class"], "high")
        prior_other_review = adaptive_budget.plan(
            [finding(1), finding(2)],
            rejected=[{"unit_id": "chapter-000001",
                       "review_record_id": "another-record",
                       "finding_id": "fix-1"}],
            unit_id="chapter-000001", record_id="record-1")
        self.assertEqual(prior_other_review["group_size"], 2)

    def test_malformed_audit_or_worklist_fails_closed(self):
        with self.assertRaises(ValueError):
            adaptive_budget.plan(
                [finding(1), finding(1)],
                unit_id="chapter-000001", record_id="record-1")
        with self.assertRaises(ValueError):
            adaptive_budget.plan(
                [finding(1)], rejected=[{"oops": 1}],
                unit_id="chapter-000001", record_id="record-1")
        with self.assertRaises(ValueError):
            adaptive_budget.plan(
                [finding(1)], failures=-1,
                unit_id="chapter-000001", record_id="record-1")


class AdaptiveRunnerTests(unittest.TestCase):
    def fixture(self, folder, *, critical=False):
        root = Path(folder)
        book = root / "books/synthetic"
        (book / "extracted").mkdir(parents=True)
        (book / "translated").mkdir()
        (book / "glossary.md").write_text("", "utf-8")
        (book / "style-guide.md").write_text("", "utf-8")
        original = "Первый абзац. Второй абзац. Третий абзац."
        (book / "extracted/unit.md").write_text(original, "utf-8")
        (book / "translated/unit.md").write_text(original, "utf-8")
        sha = hashlib.sha256(original.encode("utf-8")).hexdigest()
        chapter = {"unit_id": "chapter-000001", "number": 1,
                   "source_path": "extracted/unit.md",
                   "translation_path": "translated/unit.md",
                   "translation_acceptance": {
                       "source_sha256": sha, "translation_sha256": sha}}
        review = {
            "record_id": "record-1", "source_sha256": sha,
            "translation_sha256": sha, "outcome": "CORRECTIONS_REQUIRED",
            "quality_report": {
                "evidence_version": 2,
                "findings": [finding(i, "critical" if critical and i == 1 else "minor",
                                     "meaning" if critical and i == 1 else "style")
                             for i in range(1, 4)]}}
        status = {
            "workflow_revision": "a"*40,
            "active": {"unit_id": chapter["unit_id"],
                       "chapter": 1, "session_id": "test"},
            "model_calls_started": 0,
            "failures": 0,
        }
        replacements = [
            ("Первый абзац", "Начальный абзац"),
            ("Второй абзац", "Следующий абзац"),
            ("Третий абзац", "Последний абзац"),
        ]
        patches = {
            f"fix-{i}": {
                "finding_id": f"fix-{i}",
                "operation": "replace",
                "target_anchor": {"quote": old, "occurrence": 0},
                "replacement": new}
            for i, (old, new) in enumerate(replacements, 1)}
        stage_path = book / ".workflow/auto-correction-batches/chapter-000001.json"
        return root, chapter, review, status, patches, stage_path

    def execute_correction(self, fixture, stages=None):
        root, chapter, record, status, patches, stage_path = fixture
        if stages is None:
            stages = {"patches": [], "glossary": [], "style_guide": []}
        calls = []
        def parsed(answer, selected, **kw):
            calls.append([item["id"] for item in selected])
            return ([patches[item["id"]] for item in selected], [], [])
        with patch.dict(os.environ, {"BOOK_TRANSLATOR_ADAPTIVE_BUDGET": "on"}), \
             patch.object(runner, "ROOT", root), \
             patch.object(runner, "load_correction_stage",
                          return_value=(stage_path, stages)), \
             patch.object(runner, "context", return_value=("system", "user", "")), \
             patch.object(runner, "role_contracts",
                          return_value=("Pinned translator", ["AGENTS.md"])), \
             patch.object(runner, "checkpoint"), \
             patch.object(runner, "complete_for_job", return_value="{}"), \
             patch.object(runner, "parse_correction_patches", side_effect=parsed):
            result = runner.process_correction_batches(
                "f"*32, "synthetic", status, root, chapter, record)
        return result, calls

    def test_critical_issue_isolated_and_original_remains_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture = self.fixture(folder, critical=True)
            result, calls = self.execute_correction(fixture)
            self.assertIsNone(result)
            self.assertEqual(calls, [["fix-1"]])
            self.assertEqual(fixture[3]["model_calls_started"], 1)
            self.assertEqual(fixture[3]["adaptive_correction_budget"]["risk_class"],
                             "high")
            stage = json.loads(fixture[5].read_text("utf-8"))
            self.assertEqual(len(stage["patches"]), 1)
            self.assertIn("Первый абзац",
                          (fixture[0]/"books/synthetic/translated/unit.md").read_text("utf-8"))

    def test_fully_staged_resume_uses_zero_additional_provider_calls(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture = self.fixture(folder)
            staged = {
                "patches": list(fixture[4].values()),
                "glossary": [], "style_guide": [],
            }
            result, calls = self.execute_correction(fixture, staged)
            self.assertEqual(calls, [])
            self.assertEqual(fixture[3]["model_calls_started"], 0)
            self.assertIn("Начальный абзац", result[0])
            self.assertIn("Первый абзац",
                          (fixture[0] / "books/synthetic/translated/unit.md"
                           ).read_text("utf-8"))

    def test_simple_findings_group_without_skipping_independent_review(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture = self.fixture(folder)
            result, calls = self.execute_correction(fixture)
            self.assertEqual(calls, [["fix-1", "fix-2", "fix-3"]])
            self.assertEqual(fixture[3]["model_calls_started"], 1)
            self.assertIn("Начальный абзац", result[0])
            self.assertEqual(len(json.loads(fixture[5].read_text("utf-8"))["patches"]), 3)
            # It returns a candidate; canonical accept-translation and an
            # independent reviewer PASS remain outside this bounded unit call.
            self.assertIn("Первый абзац",
                          (fixture[0]/"books/synthetic/translated/unit.md").read_text("utf-8"))


if __name__ == "__main__":
    unittest.main()

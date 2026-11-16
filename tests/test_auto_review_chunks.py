"""Synthetic coverage, resumability and fail-closed reviewer regressions.

Contains no book source, provider network calls, or irreversible Git writes.
"""
from __future__ import annotations

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
import review_chunks as chunks
from omniroute import ModelError, TransientModelError


def example(length: int, language: str) -> str:
    return "\n\n".join(
        "%s paragraph %05d: " % (language, i) +
        "Synthetic, meaningless verification tokens. " * 5
        for i in range(length)
    )


def check_report(finding=None, *, outcome="PASS"):
    return json.dumps({
        "evidence_version": 2,
        "outcome": outcome,
        "checks": dict.fromkeys(("coverage", "meaning", "terminology", "style", "structure"), True),
        "findings": [finding] if finding else [],
        "style_guide": [],
    })


class ReviewChunkPlanTests(unittest.TestCase):
    def setUp(self):
        self.source = example(450, "English")
        self.target = example(520, "Russian")
        self.plan = chunks.plan(self.source, self.target)

    def test_complete_monotone_source_and_target_coverage(self):
        self.assertGreater(len(self.plan["chunks"]), 3)
        chunks.validate_plan(self.plan, self.source, self.target)
        for kind, text in (("source", self.source), ("translation", self.target)):
            pieces = [text[a:b] for a, b in
                      (chunk[kind + "_core"] for chunk in self.plan["chunks"])]
            self.assertEqual("".join(pieces), text)
            self.assertTrue(all(
                chunk[kind + "_window"][0] <= chunk[kind + "_core"][0]
                and chunk[kind + "_window"][1] >= chunk[kind + "_core"][1]
                for chunk in self.plan["chunks"]))

    def test_modified_plan_rejected(self):
        mutated = json.loads(json.dumps(self.plan))
        mutated["chunks"][0]["source_core"][1] -= 1
        with self.assertRaisesRegex(ValueError, "cover|partition"):
            chunks.validate_plan(mutated, self.source, self.target)
        mutated = json.loads(json.dumps(self.plan))
        mutated["chunks"][1]["source_window"][0] += 1
        with self.assertRaisesRegex(ValueError, "partition"):
            chunks.validate_plan(mutated, self.source, self.target)

    def test_stale_sha_or_workflow_revision_cannot_resume(self):
        stage = chunks.new_stage(self.plan, "chapter-000002", "a"*40)
        chunks.validate_stage(stage, self.plan, "chapter-000002", "a"*40)
        with self.assertRaisesRegex(ValueError, "stale"):
            chunks.validate_stage(stage, self.plan, "chapter-000002", "b"*40)
        with self.assertRaisesRegex(ValueError, "stale"):
            chunks.validate_plan(self.plan, self.source + "changed", self.target)
        with self.assertRaisesRegex(ValueError, "stale"):
            chunks.validate_plan(self.plan, self.source, self.target + "changed")

    def test_cannot_combine_missing_core_or_unresolved_pass(self):
        stage = chunks.new_stage(self.plan, "chapter-000002", "a"*40)
        with self.assertRaisesRegex(ValueError, "coverage"):
            chunks.combine(stage)
        empty_report = {"evidence_version": 2, "context": "independent_session",
                        "checks": dict.fromkeys(("coverage", "meaning", "terminology",
                                                  "style", "structure"), True),
                        "findings": []}
        for chunk in self.plan["chunks"]:
            stage["completed"][chunk["id"]] = {
                "outcome": "PASS", "report": empty_report, "style": []}
        self.assertEqual(chunks.combine(stage)["outcome"], "PASS")
        stage["completed"][self.plan["chunks"][1]["id"]]["report"] = {
            **empty_report, "checks": {**empty_report["checks"], "coverage": False}}
        with self.assertRaisesRegex(ValueError, "checks"):
            chunks.combine(stage)

    def test_previous_finding_uses_source_core_and_combined_ids_are_canonical(self):
        first = self.plan["chunks"][0]
        prior_finding = {"id": "meaning-1", "category": "meaning",
                         "severity": "major", "detail": "Synthetic issue",
                         "resolved": False,
                         "source_anchor": {"quote": "English paragraph 00000", "occurrence": 0},
                         "translation_anchor": {"quote": "Russian paragraph 00000", "occurrence": 0},
                         "resolves": None}
        previous = {"record_id": "a"*32, "quality_report": {
            "evidence_version": 2, "findings": [prior_finding]}}
        assigned = chunks.find_previous_for_chunk(previous, first, self.source)
        self.assertEqual([item["id"] for item in assigned], ["meaning-1"])
        self.assertEqual(chunks.find_previous_for_chunk(
            previous, self.plan["chunks"][1], self.source), [])
        followup = {**prior_finding, "id": "rechecked-1", "resolved": True,
                    "resolves": {"record_id": "a"*32, "finding_id": "meaning-1"}}
        stage = chunks.new_stage(self.plan, "chapter-000002", "f"*40)
        for part in self.plan["chunks"]:
            stage["completed"][part["id"]] = {
                "outcome": "PASS",
                "report": {"evidence_version": 2,
                           "context": "independent_session",
                           "checks": dict.fromkeys(("coverage", "meaning",
                                                     "terminology", "style", "structure"), True),
                           "findings": [followup] if part["id"] == first["id"] else []},
                "style": []}
        combined = chunks.combine(stage)
        self.assertEqual(combined["outcome"], "PASS")
        self.assertEqual(combined["findings"][0]["id"], "chunk-001-rechecked-1")
        # The real pinned v2 parser, not just a synthetic schema, accepts it.
        outcome, report, _ = runner.parse_literary_review(json.dumps(combined))
        self.assertEqual(outcome, "PASS")
        self.assertEqual(report["findings"][0]["resolves"]["record_id"], "a"*32)

    def test_finding_must_be_anchored_to_assigned_core(self):
        first = self.plan["chunks"][0]
        second = self.plan["chunks"][1]
        quote = "English paragraph 00000"
        finding = {"id": "meaning-1", "category": "meaning",
                   "severity": "major", "detail": "Synthetic meaning mismatch",
                   "resolved": False, "source_anchor": {"quote": quote, "occurrence": 0},
                   "translation_anchor": {"quote": "Russian paragraph 00000", "occurrence": 0},
                   "resolves": None}
        report = {"evidence_version": 2,
                  "checks": dict.fromkeys(("coverage", "meaning", "terminology",
                                            "style", "structure"), True),
                  "findings": [finding]}
        chunks.validate_chunk_report(first, report, self.source, self.target)
        with self.assertRaisesRegex(ValueError, "assigned source core"):
            chunks.validate_chunk_report(second, report, self.source, self.target)


class ReviewChunkWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.slug = "без-названия"
        self.book = self.root / "books" / self.slug
        (self.book / "extracted").mkdir(parents=True)
        (self.book / "translated").mkdir()
        self.source = example(450, "English")
        self.target = example(520, "Russian")
        self.chapter = {"unit_id": "chapter-000002", "number": 2,
                        "source_path": "extracted/chapter.md",
                        "translation_path": "translated/chapter.md",
                        "status": "translated"}
        (self.book / self.chapter["source_path"]).write_text(self.source, "utf-8")
        (self.book / self.chapter["translation_path"]).write_text(self.target, "utf-8")
        (self.book / "metadata.json").write_text(json.dumps({
            "title": "Synthetic", "author": "Test",
            "source_language": "en", "target_language": "ru"}), "utf-8")
        (self.book / "glossary.md").write_text("", "utf-8")
        (self.book / "style-guide.md").write_text("", "utf-8")
        self.status = {"workflow_revision": "f"*40, "model_calls_started": 0,
                       "state": "running", "phase": "claimed",
                       "active": {"unit_id": self.chapter["unit_id"],
                                  "chapter": 2, "role": "reviewer",
                                  "session_id": "auto-test-reviewer"}}
        self.patches = [
            patch.object(runner, "ROOT", self.root),
            patch.object(runner, "role_contracts",
                         return_value=("Pinned reviewer contract", ["AGENTS.md", "docs/REVIEWER.md"])),
            patch.object(runner, "checkpoint"),
            patch.object(runner, "full_shared_context", return_value=("", "")),
            patch.object(runner, "model_glossary_context", return_value=""),
            patch.object(runner, "checked_reviewer_response",
                         return_value=("PASS", {"checks": {}}, [])),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def run_one(self):
        return runner.process_review_chunks(
            "f"*32, self.slug, self.status, self.root, self.chapter,
            previous=None, owned={"session_id": "auto-test-reviewer"})

    def test_resume_uses_prior_completed_parts_and_only_aggregates_at_end(self):
        seen = []
        def complete(status, role, system, prompt):
            self.assertEqual(role, "REVIEWER")
            self.assertIn("Pinned reviewer contract", system)
            self.assertIn("core_to_review", prompt)
            seen.append(prompt)
            return check_report()
        with patch.object(runner, "complete_for_job", side_effect=complete):
            expected = len(chunks.plan(self.source, self.target)["chunks"])
            for n in range(expected):
                self.assertIsNone(self.run_one())
                self.assertEqual(self.status["review_chunk_progress"]["completed"], n+1)
                stage = runner.read_json(
                    runner.review_chunk_stage_path(self.slug, "chapter-000002"))
                self.assertEqual(len(stage["completed"]), n+1)
            self.assertEqual(self.run_one()[0], "PASS")
        self.assertEqual(len(seen), expected)
        self.assertEqual(self.status["model_calls_started"], expected)

    def test_timeout_preserves_previous_checkpoint_not_fabricated_pass(self):
        responses = [check_report(), TransientModelError(
            "model transport timeout or connection failure", code="transport")]
        def invoke(*args):
            result = responses.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        with patch.object(runner, "complete_for_job", side_effect=invoke):
            self.run_one()
            with self.assertRaises(TransientModelError):
                self.run_one()
        stage = runner.read_json(
            runner.review_chunk_stage_path(self.slug, "chapter-000002"))
        self.assertEqual(len(stage["completed"]), 1)
        self.assertEqual(self.status["model_calls_started"], 2)
        with patch.object(runner, "complete_for_job", return_value=check_report()):
            self.run_one()
        stage = runner.read_json(
            runner.review_chunk_stage_path(self.slug, "chapter-000002"))
        self.assertEqual(len(stage["completed"]), 2)

    def test_changed_translation_invalidates_but_does_not_retranslate_source(self):
        with patch.object(runner, "complete_for_job", return_value=check_report()):
            self.run_one()
            (self.book / self.chapter["translation_path"]).write_text(
                self.target + "\n\nRussian added paragraph.", "utf-8")
            self.run_one()
        stage = runner.read_json(
            runner.review_chunk_stage_path(self.slug, "chapter-000002"))
        self.assertEqual(len(stage["completed"]), 1)
        self.assertEqual(len(self.status["review_chunk_invalidations"]), 1)
        self.assertEqual((self.book / self.chapter["source_path"]).read_text("utf-8"),
                         self.source)

    def test_invalid_model_output_cannot_stage_false_review(self):
        with patch.object(runner, "complete_for_job", return_value='{"outcome":"PASS"}'):
            with self.assertRaises(ModelError):
                self.run_one()
        stage_path = runner.review_chunk_stage_path(self.slug, "chapter-000002")
        self.assertFalse(stage_path.exists())
        self.assertEqual(self.status["model_calls_started"], 1)


if __name__ == "__main__":
    unittest.main()

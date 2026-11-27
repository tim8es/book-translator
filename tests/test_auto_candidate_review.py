"""Synthetic source-grounded correction candidate preflight tests.

No book text, API key, model call, or network access is required.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "automation"))
import book_runner as auto
import candidate_review


def answer(*values):
    return json.dumps({
        "schema_version": 1,
        "checks": dict.fromkeys(candidate_review.CHECKS, True),
        "decisions": [
            {"finding_id": finding_id, "verdict": verdict,
             "risk": "none" if verdict == "approve" else "meaning"}
            for finding_id, verdict in values
        ],
    })


class CandidateReviewProtocolTests(unittest.TestCase):
    def test_disabled_by_default_and_invalid_mode_fails_closed(self):
        self.assertFalse(candidate_review.enabled({}))
        self.assertTrue(candidate_review.enabled({
            "BOOK_TRANSLATOR_SEMANTIC_PREFLIGHT": "on"}))
        with self.assertRaises(candidate_review.CandidateReviewError):
            candidate_review.enabled({"BOOK_TRANSLATOR_SEMANTIC_PREFLIGHT": "yes"})

    def test_prompt_has_source_before_after_and_never_claims_full_pass(self):
        source = "Он мог найти ключ."
        translated = "Он точно найдёт ключ."
        findings = [{
            "id": "mod-1", "category": "modality", "severity": "major",
            "detail": "Source expresses possibility.",
            "source_anchor": {"quote": "мог найти", "occurrence": 0},
        }]
        patches = [{
            "finding_id": "mod-1", "operation": "replace",
            "target_anchor": {"quote": "точно найдёт", "occurrence": 0},
            "replacement": "может найти",
        }]
        cases = candidate_review.cases(
            source, translated, patches, findings, position=auto._anchor_position)
        self.assertIn("мог найти", cases[0]["original_excerpt"])
        self.assertIn("точно найдёт", cases[0]["current_translation_excerpt"])
        self.assertIn("может найти", cases[0]["candidate_translation_excerpt"])
        system, user = candidate_review.build_prompt(cases, "Pinned independent reviewer")
        self.assertIn("independent", system.lower())
        self.assertIn("never chapter PASS", user)
        self.assertIn("mod-1", user)

    def test_shared_context_preserves_strict_gate_and_safe_bounds(self):
        context = {"glossary": "harbour ledger = портовый журнал",
                   "style_guide": "Use consistent terminology."}
        cases = [{"finding_id": "term", "category": "terminology"}]
        _, user = candidate_review.build_prompt(cases, "Pinned contract",
            require_resolution=True, shared_context=context)
        self.assertIn(context["glossary"], user)
        self.assertIn(context["style_guide"], user)
        self.assertIn("reject rather than guess", user)
        self.assertIn("actually resolves", user)
        self.assertIn("never chapter PASS", user)
        with self.assertRaises(candidate_review.CandidateReviewError):
            candidate_review.build_prompt(cases, "Pinned contract",
                shared_context={"glossary": "x" * candidate_review.MAX_EVIDENCE_CHARS,
                                "style_guide": ""})
        with self.assertRaises(candidate_review.CandidateReviewError):
            candidate_review.build_prompt(cases, "Pinned contract", shared_context={"glossary": ""})

    def test_strict_verdict_requires_all_cases_and_true_checks(self):
        self.assertEqual(candidate_review.parse_verdict(
            answer(("x", "approve"), ("y", "reject")), ["y", "x"]), ["y"])
        variants = [
            '{"schema_version":1}',
            answer(("x", "approve")),
            answer(("x", "reject"), ("x", "reject")),
            json.dumps({
                "schema_version": 1,
                "checks": dict.fromkeys(candidate_review.CHECKS, True),
                "decisions": [{"finding_id": "x", "verdict": "approve", "risk": "meaning"}],
            }),
            json.dumps({
                "schema_version": 1,
                "checks": dict.fromkeys(candidate_review.CHECKS, True),
                "decisions": [{"finding_id": "x", "verdict": "reject", "risk": "none"}],
            }),
            json.dumps({
                "schema_version": 1,
                "checks": {**dict.fromkeys(candidate_review.CHECKS, True), "meaning": False},
                "decisions": [{"finding_id": "x", "verdict": "approve", "risk": "none"},
                              {"finding_id": "y", "verdict": "approve", "risk": "none"}],
            }),
            json.dumps({
                "schema_version": True,
                "checks": dict.fromkeys(candidate_review.CHECKS, True),
                "decisions": [{"finding_id": "x", "verdict": "approve", "risk": "none"},
                              {"finding_id": "y", "verdict": "approve", "risk": "none"}],
            }),
            json.dumps({
                "schema_version": 1,
                "checks": dict.fromkeys(candidate_review.CHECKS, True),
                "decisions": [{"finding_id": "x", "verdict": [], "risk": "none"},
                              {"finding_id": "y", "verdict": "approve", "risk": "none"}],
            }),
            json.dumps({
                "schema_version": 1,
                "checks": dict.fromkeys(candidate_review.CHECKS, True),
                "decisions": [{"finding_id": "x", "verdict": "approve", "risk": {}},
                              {"finding_id": "y", "verdict": "approve", "risk": "none"}],
            }),
        ]
        for value in variants:
            with self.subTest(value=value[:100]):
                with self.assertRaises(candidate_review.CandidateReviewError):
                    candidate_review.parse_verdict(value, ["x", "y"])

    def test_case_count_limit_and_unknown_id_fail(self):
        source = "Source text."
        translation = "Translated text."
        finding = [{
            "id": "x", "source_anchor": {"quote": "Source", "occurrence": 0},
        }]
        patch_value = {
            "finding_id": "x", "operation": "replace",
            "target_anchor": {"quote": "Translated", "occurrence": 0},
            "replacement": "New",
        }
        with self.assertRaisesRegex(candidate_review.CandidateReviewError, "finding ID"):
            candidate_review.cases(source, translation,
                                   [{**patch_value, "finding_id": "unknown"}],
                                   finding, position=auto._anchor_position)
        with self.assertRaisesRegex(candidate_review.CandidateReviewError, "case count"):
            candidate_review.cases(source, translation, [],
                                   finding, position=auto._anchor_position)


class CandidateReviewRuntimeTests(unittest.TestCase):
    def setup_fixture(self, folder):
        root = Path(folder)
        book = root / "books" / "synthetic"
        (book / "extracted").mkdir(parents=True)
        (book / "translated").mkdir()
        (book / "glossary.md").write_text("", "utf-8")
        (book / "style-guide.md").write_text("", "utf-8")
        text = "Мост был старым. Он мог найти ключ. Погода была ясной."
        (book / "extracted/unit.md").write_text(text, "utf-8")
        (book / "translated/unit.md").write_text(text, "utf-8")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        chapter = {
            "number": 1, "unit_id": "chapter-000001",
            "source_path": "extracted/unit.md",
            "translation_path": "translated/unit.md",
            "translation_acceptance": {"source_sha256": digest,
                                       "translation_sha256": digest},
        }
        record = {
            "record_id": "review-1", "outcome": "CORRECTIONS_REQUIRED",
            "source_sha256": digest, "translation_sha256": digest,
            "quality_report": {"evidence_version": 2, "findings": [{
                "id": "repair-1", "resolved": False,
                "severity": "major", "category": "meaning",
                "detail": "Ambiguous reading should be kept.",
                "source_anchor": {"quote": "мог найти ключ", "occurrence": 0},
            }]},
        }
        status = {
            "workflow_revision": "c" * 40,
            "active": {"session_id": "test", "role": "corrector",
                       "unit_id": "chapter-000001"},
            "model_calls_started": 0,
        }
        patch_value = {
            "finding_id": "repair-1", "operation": "replace",
            "target_anchor": {"quote": "Он мог найти ключ", "occurrence": 0},
            "replacement": "Он, возможно, найдёт ключ",
        }
        stage = {"patches": [], "glossary": [], "style_guide": []}
        stage_path = book / ".workflow/auto-correction-batches/chapter-000001.json"
        return root, chapter, record, status, patch_value, stage, stage_path

    def run_correction(self, f, reviewer_answer):
        root, chapter, record, status, patch_value, stage, stage_path = f
        calls = []
        def complete(_status, role, system, user):
            calls.append(role)
            return "{}" if role == "CORRECTOR" else reviewer_answer
        with patch.dict("os.environ", {"BOOK_TRANSLATOR_SEMANTIC_PREFLIGHT": "on"}), \
             patch.object(auto, "ROOT", root), \
             patch.object(auto, "load_correction_stage",
                          return_value=(stage_path, stage)), \
             patch.object(auto, "context", return_value=("sys", "user", "")), \
             patch.object(auto, "role_contracts",
                          return_value=("Pinned reviewer contract", ["AGENTS.md"])), \
             patch.object(auto, "checkpoint"), \
             patch.object(auto, "complete_for_job", side_effect=complete), \
             patch.object(auto, "parse_correction_patches",
                          return_value=([patch_value], [], [])):
            result = auto.process_correction_batches(
                "d" * 32, "synthetic", status, root, chapter, record)
        return result, calls

    def test_approved_semantic_candidate_has_extra_reserved_independent_call(self):
        with tempfile.TemporaryDirectory() as folder:
            f = self.setup_fixture(folder)
            result, calls = self.run_correction(f, answer(("repair-1", "approve")))
            self.assertEqual(calls, ["CORRECTOR", "REVIEWER"])
            self.assertIn("возможно", result[0])
            self.assertEqual(f[3]["model_calls_started"], 2)
            self.assertEqual(f[3]["last_semantic_preflight"]["decision"],
                             "candidate_only_approved")
            artifact = f[0] / "books" / "synthetic" / f[1]["translation_path"]
            self.assertNotIn("возможно", artifact.read_text("utf-8"))

    def test_rejected_semantic_candidate_keeps_original_and_discards_stage(self):
        with tempfile.TemporaryDirectory() as folder:
            f = self.setup_fixture(folder)
            root, chapter, record, status, patch_value, stage, stage_path = f
            with self.assertRaisesRegex(auto.ModelError, "preflight vetoed"):
                self.run_correction(f, answer(("repair-1", "reject")))
            self.assertFalse(stage_path.exists())
            self.assertEqual(status["last_semantic_preflight"]["decision"], "veto")
            self.assertEqual(status["correction_regression_rejections"][-1]["reason"],
                             "semantic-preflight-veto")
            artifact = root / "books" / "synthetic" / chapter["translation_path"]
            self.assertIn("Он мог найти ключ", artifact.read_text("utf-8"))

    def test_invalid_semantic_report_fails_closed_without_accepting(self):
        with tempfile.TemporaryDirectory() as folder:
            f = self.setup_fixture(folder)
            with self.assertRaisesRegex(auto.ModelError, "invalid evidence"):
                self.run_correction(f, "{}")
            self.assertNotIn("last_semantic_preflight", f[3])
            self.assertEqual(f[3]["model_calls_started"], 2)

    def test_budget_exhaustion_blocks_without_inventing_approval(self):
        with tempfile.TemporaryDirectory() as folder:
            f = self.setup_fixture(folder)
            f[3]["model_calls_started"] = auto.DEFAULT_MODEL_CALL_LIMIT - 1
            with patch.object(auto, "cli", return_value=""):
                result, calls = self.run_correction(
                    f, answer(("repair-1", "approve")))
            self.assertIsNone(result)
            self.assertEqual(calls, ["CORRECTOR"])
            self.assertEqual(f[3]["phase"], "model-call-limit-reached")
            self.assertNotIn("last_semantic_preflight", f[3])


if __name__ == "__main__":
    unittest.main()


class CandidateContextBudgetTests(unittest.TestCase):
    def test_actual_pinned_reviewer_contract_fits_short_candidate(self):
        root = Path(__file__).resolve().parents[1]
        contract, paths = auto.role_contracts(root, "reviewer")
        self.assertIn("docs/REVIEWER.md", paths)
        self.assertIn("docs/QUALITY_CONVERGENCE.md", paths)
        case = {"finding_id": "one", "category": "meaning", "severity": "major",
                "reviewer_claim": "Preserve the number of coins.",
                "original_excerpt": "Seven coins, not nine. " * 20,
                "current_translation_excerpt": "Девять монет, а не семь. " * 20,
                "candidate_translation_excerpt": "Семь монет, а не девять. " * 20}
        system, user = candidate_review.build_prompt([case], contract, require_resolution=True)
        self.assertTrue(system.startswith(contract))
        self.assertGreater(len(system) + len(user), 38_000)
        self.assertLessEqual(len(system) + len(user), candidate_review.MAX_PROMPT_CHARS)

    def test_evidence_and_total_limits_remain_independent(self):
        case = {"finding_id": "one", "original_excerpt": "a" * candidate_review.MAX_EVIDENCE_CHARS}
        with self.assertRaises(candidate_review.CandidateReviewError):
            candidate_review.build_prompt([case], "Reviewer")
        with self.assertRaises(candidate_review.CandidateReviewError):
            candidate_review.build_prompt([{"finding_id": "one"}], "a" * 80_000)


class CandidateDeletionTests(unittest.TestCase):
    def test_canonical_deletion_is_independently_checked(self):
        source, target = "Seven coins.", "Семь монет и письмо."
        finding = {"id": "extra", "category": "addition", "severity": "major",
                   "source_anchor": {"quote": "Seven coins.", "occurrence": 0}}
        p = {"finding_id": "extra", "operation": "replace",
             "target_anchor": {"quote": " и письмо", "occurrence": 0}, "replacement": ""}
        cases = candidate_review.cases(source, target, [p], [finding],
            position=auto._anchor_position, combined_patches=[p])
        self.assertEqual(cases[0]["candidate_translation_excerpt"], "Семь монет.")
        self.assertEqual(auto.apply_correction_patches(target, [p]), "Семь монет.")
        with self.assertRaises(candidate_review.CandidateReviewError):
            candidate_review.cases(source, target, [dict(p, operation="insert_before")],
                [finding], position=auto._anchor_position)
        self.assertEqual(candidate_review.parse_rejections(answer(("extra", "reject")), ["extra"]),
                         {"extra": "meaning"})

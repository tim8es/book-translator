"""Synthetic, provider-free regression tests for the new quality contract."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "automation"), str(ROOT / "scripts")]
import book_runner as runner
import quality_controller as quality
import candidate_review
from workflow.review_quality import validate_quality_report, ReviewQualityError
from workflow.review_anchors import verify_correction_lineage, verify_evidence_anchors, EvidenceAnchorError
from workflow.review_adjudication import verify_sessions

SOURCE = "She closed the old door. The key might be in the box."
TARGET = "Она закрыла старую дверь. Ключ может быть в коробке."
S = hashlib.sha256(SOURCE.encode()).hexdigest()
T = hashlib.sha256(TARGET.encode()).hexdigest()
UNIT = "chapter-000001"
RID = "a" * 32
RID2 = "b" * 32
CHECKS = dict.fromkeys(("coverage", "meaning", "terminology", "style", "structure"), True)


def finding(fid="wording", category="style", severity="minor", resolved=False, ref=None):
    return {"id": fid, "category": category, "severity": severity,
            "detail": "Reviewer prefers a different natural phrasing.",
            "source_anchor": {"quote": "closed the old door", "occurrence": 0},
            "translation_anchor": {"quote": "закрыла старую дверь", "occurrence": 0},
            "resolved": resolved, "resolves": ref}


def report(*findings):
    return {"evidence_version": 2, "context": "independent_session",
            "checks": CHECKS.copy(), "findings": list(findings)}


def record(rid, quality_report, target=T, outcome="CORRECTIONS_REQUIRED"):
    return {"record_id": rid, "unit_id": UNIT, "source_sha256": S,
            "translation_sha256": target, "outcome": outcome,
            "quality_report": quality_report}


def decision(fid, verdict="acceptable_variant"):
    return {"finding_id": fid, "verdict": verdict,
            "reason": "Both expressions preserve the action and are idiomatic."}


def close(r, prior=None):
    candidates = quality.cases(r, SOURCE, TARGET, prior)
    decisions = {x["finding_id"]: decision(x["finding_id"]) for x in candidates}
    return quality.adjudicate(r, decisions, candidates, source=SOURCE,
        translation=TARGET, reviewer_session="reviewer-1", adjudicator_session="arbiter-1")


class QualityEvidenceTests(unittest.TestCase):
    def test_preference_is_closed_with_original_finding_and_hash_bound_evidence(self):
        r = report(finding())
        outcome, adjudicated = close(r)
        self.assertEqual(outcome, "PASS")
        self.assertFalse(r["findings"][0]["resolved"])
        self.assertEqual(adjudicated["findings"][0]["detail"], r["findings"][0]["detail"])
        self.assertTrue(adjudicated["findings"][0]["resolved"])
        self.assertEqual(validate_quality_report(adjudicated, outcome=outcome), adjudicated)
        verify_evidence_anchors(adjudicated, SOURCE.encode(), TARGET.encode())
        verify_sessions(adjudicated, "reviewer-1", "translator-1")

    def test_objective_and_major_defects_cannot_be_dismissed(self):
        for cat, severity in (("meaning", "minor"), ("structure", "minor"),
                              ("omission", "critical"), ("style", "major")):
            with self.subTest(cat=cat, severity=severity):
                r = report(finding(category=cat, severity=severity))
                outcome, result = close(r)
                self.assertEqual(outcome, "CORRECTIONS_REQUIRED")
                self.assertNotIn("adjudications", result)
                _, accepted = close(report(finding()))
                accepted["findings"][0].update(category=cat, severity=severity)
                with self.assertRaises(ReviewQualityError):
                    validate_quality_report(accepted, outcome="PASS")

    def test_unchanged_prior_dispute_requires_independent_evidence(self):
        previous = record(RID, report(finding()))
        current = report(finding("recheck", ref={"record_id": RID, "finding_id": "wording"}))
        outcome, result = close(current, previous)
        self.assertEqual(outcome, "PASS")
        verify_correction_lineage(result, previous=previous, source_sha256=S,
                                  translation_sha256=T, outcome=outcome)
        result.pop("adjudications")
        with self.assertRaises(EvidenceAnchorError):
            verify_correction_lineage(result, previous=previous, source_sha256=S,
                                      translation_sha256=T, outcome=outcome)

    def test_prior_substantive_error_cannot_be_relabelled_as_style(self):
        previous = record(RID, report(finding(category="meaning")))
        current = report(finding("recheck", ref={"record_id": RID, "finding_id": "wording"}))
        self.assertEqual(quality.cases(current, SOURCE, TARGET, previous), [])
        # Even if an adapter attempts to construct forged style arbitration,
        # canonical lineage must reject it, including on a changed artifact.
        _, forged = close(report(finding("recheck")))
        forged["findings"][0]["resolves"] = {"record_id": RID, "finding_id": "wording"}
        for target in (T, "c" * 64):
            with self.assertRaises(EvidenceAnchorError):
                verify_correction_lineage(forged, previous=previous, source_sha256=S,
                                          translation_sha256=target, outcome="PASS")

    def test_stale_artifact_and_non_independent_session_rejected(self):
        _, r = close(report(finding()))
        with self.assertRaises(EvidenceAnchorError):
            verify_evidence_anchors(r, SOURCE.encode(), (TARGET + " ").encode())
        with self.assertRaises(EvidenceAnchorError):
            verify_sessions(r, "reviewer-2", "translator-1")
        with self.assertRaises(EvidenceAnchorError):
            verify_sessions(r, "reviewer-1", "arbiter-1")
        r["adjudications"][0]["adjudicator_session_id"] = "reviewer-1"
        with self.assertRaises(ReviewQualityError):
            validate_quality_report(r, outcome="PASS")

    def test_mixed_report_cannot_pass_with_actual_remaining_error(self):
        r = report(finding(), finding("fidelity", "meaning", "minor"))
        outcome, result = close(r)
        self.assertEqual(outcome, "CORRECTIONS_REQUIRED")
        self.assertFalse(result["findings"][1]["resolved"])
        with self.assertRaises(ReviewQualityError):
            validate_quality_report(result, outcome="PASS")

    def test_invalid_arbiter_responses_are_not_decisions(self):
        candidates = quality.cases(report(finding()), SOURCE, TARGET, None)
        for response in ({}, {"decisions": []},
                         {"decisions": [decision("unknown")]},
                         {"decisions": [decision("wording"), decision("wording")]},
                         {"decisions": [{**decision("wording"), "reason": "fine"}]}):
            with self.assertRaises(ValueError):
                quality.parse(json.dumps(response), candidates)

    def test_stable_lineage_and_repair_strategy_change(self):
        old = record(RID, report(finding()))
        newer = record(RID2, report(finding("again", ref={
            "record_id": RID, "finding_id": "wording"})), target="c" * 64)
        ledger = {"records": [old, newer]}
        projected = quality.projection(ledger, UNIT, S)
        self.assertEqual(len(projected), 1)
        self.assertEqual(next(iter(projected.values()))["observations"], 2)
        chapter = {"unit_id": UNIT}
        plan = quality.strategy(ledger, chapter, newer, newer["quality_report"]["findings"])
        self.assertEqual(plan["again"]["level"], 1)
        veto = {"unit_id": UNIT, "review_record_id": RID2, "finding_id": "again"}
        plan = quality.strategy(ledger, chapter, newer, newer["quality_report"]["findings"], [veto])
        self.assertEqual(plan["again"]["level"], 2)

    def test_escalation_is_bound_to_exact_call_role_and_distinct_model(self):
        status = {"workflow_revision": "f" * 40, "model_calls_started": 7}
        self.assertIsNone(quality.escalation_binding(status, "CORRECTOR", UNIT, level=1, reason="test"))
        status["quality_model_escalation"] = quality.escalation_binding(
            status, "CORRECTOR", UNIT, level=2, reason="test")
        env = {"OMNIROUTE_CORRECTOR_MODEL": "primary", "OMNIROUTE_CORRECTOR_FALLBACK_MODEL": "fallback"}
        self.assertTrue(quality.escalation_route(status, "CORRECTOR", UNIT, env))
        self.assertFalse(quality.escalation_route(status, "REVIEWER", UNIT, env))
        self.assertFalse(quality.escalation_route(status, "CORRECTOR", "other", env))
        self.assertFalse(quality.escalation_route(status, "CORRECTOR", UNIT,
            {**env, "OMNIROUTE_CORRECTOR_FALLBACK_MODEL": "primary"}))
        status["model_calls_started"] += 1
        self.assertFalse(quality.escalation_route(status, "CORRECTOR", UNIT, env))

    def test_progress_credits_require_real_net_repairs(self):
        old = record(RID, report(finding("a"), finding("b", "meaning", "major")))
        new = record(RID2, report(
            finding("a-fixed", resolved=True, ref={"record_id": RID, "finding_id": "a"}),
            finding("b-open", "meaning", "major", ref={"record_id": RID, "finding_id": "b"})),
            target="c" * 64)
        for r in (old, new):
            r["workflow_revision"] = "f" * 40
        ledger = {"records": [old, new]}
        self.assertEqual(quality.progress_credits(ledger, UNIT, S, "f" * 40), [RID2])
        new["translation_sha256"] = T
        self.assertEqual(quality.progress_credits(ledger, UNIT, S, "f" * 40), [])
        new["translation_sha256"] = "c" * 64
        new["quality_report"]["adjudications"] = [{"finding_id": "a-fixed"}]
        self.assertEqual(quality.progress_credits(ledger, UNIT, S, "f" * 40), [])
        new["quality_report"].pop("adjudications")
        new["quality_report"]["findings"].append(finding("new-critical", "omission", "critical"))
        self.assertEqual(quality.progress_credits(ledger, UNIT, S, "f" * 40), [])

    def test_combined_candidate_includes_adjacent_patches(self):
        first, second = finding("first"), finding("second")
        second["source_anchor"] = {"quote": "key might be", "occurrence": 0}
        second["translation_anchor"] = {"quote": "может быть", "occurrence": 0}
        edits = [{"finding_id": f["id"], "operation": "replace",
                  "target_anchor": f["translation_anchor"], "replacement": replacement}
                 for f, replacement in ((first, "затворила старую дверь"), (second, "возможно, лежит"))]
        cases = candidate_review.cases(SOURCE, TARGET, edits[:1], [first, second],
                                      position=runner._anchor_position, combined_patches=edits)
        self.assertIn("затворила старую дверь", cases[0]["candidate_translation_excerpt"])
        self.assertIn("возможно, лежит", cases[0]["candidate_translation_excerpt"])
        _, user = candidate_review.build_prompt(cases, "Independent contract", require_resolution=True)
        self.assertIn("actually resolves", user)


class QualityRuntimeTests(unittest.TestCase):
    def test_unbound_budget_preserves_legacy_limits(self):
        self.assertFalse(runner.quality_budget_enabled({}, {"unit_id": UNIT}))
        self.assertFalse(runner.quality_budget_enabled({"workflow_revision": "f" * 40}, {"unit_id": UNIT}))
        self.assertFalse(runner.quality_budget_enabled({}, {"translation_acceptance": {"source_sha256": S}}))

    def test_durable_arbitration_resume_does_not_recontact_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            book = root / "books" / "test"
            book.mkdir(parents=True)
            (book / "glossary.md").write_text("Glossary", "utf-8")
            (book / "style-guide.md").write_text("Style", "utf-8")
            chapter = {"unit_id": UNIT, "number": 1, "translation_acceptance": {
                "source_sha256": S, "translation_sha256": T, "session_id": "translator-1"}}
            owned = {"session_id": "reviewer-1", "shared_state_revisions": {"glossary": "a" * 64, "style_guide": "b" * 64}}
            status = {"workflow_revision": "f" * 40, "model_calls_started": 0}
            with patch.object(runner, "ROOT", root), \
                 patch.object(runner, "checkpoint"), \
                 patch.object(runner, "model_call_limit", return_value=100), \
                 patch.object(runner, "role_contracts", return_value=("Independent reviewer", ["doc"])), \
                 patch.object(runner, "complete_for_job", return_value=json.dumps({
                     "decisions": [decision("wording")]})) as provider:
                outcome, r = runner.arbitrate_quality_review(
                    "job", "test", status, ROOT, chapter, owned, None,
                    SOURCE, TARGET, "CORRECTIONS_REQUIRED", report(finding()), [], [])
                self.assertEqual(outcome, "PASS")
                stage = runner.read_quality_stage("test", chapter, owned, None, status, SOURCE, TARGET)
                self.assertEqual(stage["state"], "completed")
                runner.arbitrate_quality_review("job", "test", status, ROOT, chapter, owned, None,
                    SOURCE, TARGET, outcome, r, [], [], existing=stage)
                self.assertEqual(provider.call_count, 1)
                self.assertEqual(status["model_calls_started"], 1)

    def test_arbitration_failure_preserves_valid_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            book = root / "books" / "test"
            book.mkdir(parents=True)
            (book / "glossary.md").write_text("Glossary", "utf-8")
            (book / "style-guide.md").write_text("Style", "utf-8")
            chapter = {"unit_id": UNIT, "number": 1, "translation_acceptance": {
                "source_sha256": S, "translation_sha256": T, "session_id": "translator-1"}}
            owned = {"session_id": "reviewer-1", "shared_state_revisions": {"glossary": "a" * 64, "style_guide": "b" * 64}}
            status = {"workflow_revision": "f" * 40, "model_calls_started": 0}
            with patch.object(runner, "ROOT", root), \
                 patch.object(runner, "checkpoint"), \
                 patch.object(runner, "model_call_limit", return_value=100), \
                 patch.object(runner, "role_contracts", return_value=("Independent reviewer", ["doc"])), \
                 patch.object(runner, "complete_for_job", return_value="invalid"):
                outcome, r = runner.arbitrate_quality_review(
                    "job", "test", status, ROOT, chapter, owned, None,
                    SOURCE, TARGET, "CORRECTIONS_REQUIRED", report(finding()), [], [])
                self.assertEqual(outcome, "CORRECTIONS_REQUIRED")
                self.assertFalse(r["findings"][0]["resolved"])
                self.assertEqual(status["model_calls_started"], 1)
                self.assertEqual(status["quality_arbitration"]["decision"], "unavailable-kept-all")


if __name__ == "__main__":
    unittest.main()

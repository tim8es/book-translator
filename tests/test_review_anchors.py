"""Anchored reviewer finding contract and exact-quote evidence verification."""

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from workflow.review_quality import ReviewQualityError, validate_quality_report
from workflow.review_anchors import (
    EvidenceAnchorError, verify_evidence_anchors, verify_correction_lineage,
)


def finding(fid="modality-1", *, resolved=False, translated=True, resolves=None):
    return {
        "id": fid, "category": "modality", "severity": "major",
        "detail": "Uncertain recognition is rendered as fact.",
        "resolved": resolved,
        "source_anchor": {"quote": "She might have recognized him.", "occurrence": 0},
        "translation_anchor": (
            {"quote": "Она узнала его.", "occurrence": 0}
            if translated else None
        ),
        "resolves": resolves,
    }


def report(*issues, context="independent_session"):
    return {
        "evidence_version": 2, "context": context,
        "checks": dict.fromkeys(["coverage", "meaning", "terminology", "style", "structure"], True),
        "findings": list(issues),
    }


class AnchoredReviewTests(unittest.TestCase):
    def test_valid_v2_roundtrip_and_quotes(self):
        evidence = report(finding())
        self.assertEqual(
            validate_quality_report(evidence, outcome="CORRECTIONS_REQUIRED"), evidence,
        )
        verify_evidence_anchors(
            evidence,
            "She might have recognized him.\n".encode(),
            "Она узнала его.\n".encode(),
        )

    def test_legacy_report_keeps_exact_existing_contract(self):
        legacy = {"context": "bounded_context",
                  "checks": dict.fromkeys(["coverage", "meaning", "terminology", "style", "structure"], True),
                  "findings": [{"severity": "minor", "detail": "Example", "resolved": True}]}
        self.assertEqual(validate_quality_report(legacy, outcome="PASS"), legacy)

    def test_missing_or_fake_quote_rejected(self):
        evidence = report(finding())
        with self.assertRaisesRegex(EvidenceAnchorError, "absent"):
            verify_evidence_anchors(evidence, b"No matching original", "Она узнала его.".encode())
        with self.assertRaisesRegex(EvidenceAnchorError, "absent"):
            verify_evidence_anchors(evidence, b"She might have recognized him.", b"Unrelated.")

    def test_repeated_quote_requires_disambiguation(self):
        phrase = "She might have recognized him."
        sample = (phrase + "\n" + phrase).encode()
        evidence = report(finding())
        with self.assertRaisesRegex(EvidenceAnchorError, "repeated"):
            verify_evidence_anchors(evidence, sample, "Она узнала его.".encode())
        evidence["findings"][0]["source_anchor"]["occurrence"] = 1
        verify_evidence_anchors(evidence, sample, "Она узнала его.".encode())

    def test_invalid_anchor_or_duplicate_finding_ids_rejected(self):
        data = report(finding(), finding())
        with self.assertRaisesRegex(ReviewQualityError, "duplicate"):
            validate_quality_report(data, outcome="CORRECTIONS_REQUIRED")
        data = report(finding())
        data["findings"][0]["source_anchor"]["occurrence"] = -1
        with self.assertRaisesRegex(ReviewQualityError, "occurrence"):
            validate_quality_report(data, outcome="CORRECTIONS_REQUIRED")

    def test_omission_may_have_no_target_but_resolution_requires_one(self):
        omission = finding(translated=False)
        omission["category"] = "omission"
        self.assertEqual(validate_quality_report(report(omission), outcome="CORRECTIONS_REQUIRED")["findings"][0]["translation_anchor"], None)
        omission["resolved"] = True
        with self.assertRaisesRegex(ReviewQualityError, "resolved"):
            validate_quality_report(report(omission), outcome="PASS")

    def test_prior_open_major_must_be_rechecked_against_changed_translation(self):
        initial = report(finding())
        previous = {
            "record_id": "a" * 32, "outcome": "CORRECTIONS_REQUIRED",
            "source_sha256": "1" * 64, "translation_sha256": "2" * 64,
            "quality_report": initial,
        }
        current = report(finding("recheck-1", resolved=True, resolves={
            "record_id": previous["record_id"], "finding_id": "modality-1",
        }))
        with self.assertRaisesRegex(EvidenceAnchorError, "changed translation"):
            verify_correction_lineage(current, previous=previous,
                                      source_sha256="1" * 64, translation_sha256="2" * 64,
                                      outcome="PASS")
        verify_correction_lineage(current, previous=previous,
                                  source_sha256="1" * 64, translation_sha256="3" * 64,
                                  outcome="PASS")
        with self.assertRaisesRegex(EvidenceAnchorError, "explicit rechecks"):
            verify_correction_lineage(report(), previous=previous,
                                      source_sha256="1" * 64, translation_sha256="3" * 64,
                                      outcome="PASS")

    def test_new_source_revision_does_not_inherit_old_finding_coordinates(self):
        previous = {
            "record_id": "a" * 32, "outcome": "CORRECTIONS_REQUIRED",
            "source_sha256": "1" * 64, "translation_sha256": "2" * 64,
            "quality_report": report(finding()),
        }
        verify_correction_lineage(report(), previous=previous,
                                  source_sha256="9" * 64, translation_sha256="2" * 64,
                                  outcome="PASS")


if __name__ == "__main__":
    unittest.main()

"""Review quality evidence validation and pass safety."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from workflow.review_quality import ReviewQualityError, validate_quality_report


def report(context="independent_session"):
    return {
        "context": context,
        "checks": dict.fromkeys(["coverage", "meaning", "terminology", "style", "structure"], True),
        "findings": [],
    }


class ReviewQualityTests(unittest.TestCase):
    def test_complete_report_is_deterministic(self):
        evidence = report()
        self.assertEqual(validate_quality_report(evidence, outcome="PASS"), evidence)

    def test_pass_blocks_unresolved_major_issue(self):
        evidence = report()
        evidence["findings"] = [{"severity": "major", "detail": "Missing paragraph", "resolved": False}]
        with self.assertRaisesRegex(ReviewQualityError, "PASS"):
            validate_quality_report(evidence, outcome="PASS")

    def test_corrections_need_actionable_findings(self):
        with self.assertRaisesRegex(ReviewQualityError, "unresolved finding"):
            validate_quality_report(report(), outcome="CORRECTIONS_REQUIRED")

    def test_checks_cannot_be_omitted_or_falsified(self):
        evidence = report("bounded_context")
        evidence["checks"]["coverage"] = False
        with self.assertRaises(ReviewQualityError):
            validate_quality_report(evidence, outcome="PASS")
        evidence["checks"].pop("meaning")
        with self.assertRaises(ReviewQualityError):
            validate_quality_report(evidence, outcome="PASS")


if __name__ == "__main__":
    unittest.main()

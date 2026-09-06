import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from workflow_v2.review_report import render_review_report_markdown


class WorkflowV2ReviewReportRevisionTests(unittest.TestCase):
    def test_markdown_exposes_current_review_contract_revision(self):
        snapshot = {
            "schema": "review-report-v1",
            "book_slug": "demo",
            "summary": {
                "total_units": 1,
                "pass": 1,
                "corrections_required": 0,
                "missing": 0,
                "stale": 0,
                "untranslated": 0,
                "pass_coverage": {"passed": 1, "total": 1, "percent": 100.0},
                "duplicate_records": 0,
            },
            "units": [
                {
                    "unit_id": "chapter-000001",
                    "chapter_number": 1,
                    "state": "pass",
                    "source_sha256": "a" * 64,
                    "translation_sha256": "b" * 64,
                    "current_review": {
                        "outcome": "PASS",
                        "workflow_revision": "workflow-revision",
                        "review_contract_revision": "docs/TRANSLATION.md@workflow-revision",
                        "review_commit": "review-commit",
                    },
                    "history": [],
                }
            ],
        }

        markdown = render_review_report_markdown(snapshot)

        self.assertIn("Review contract revision", markdown)
        self.assertIn("docs/TRANSLATION.md@workflow-revision", markdown)


if __name__ == "__main__":
    unittest.main()

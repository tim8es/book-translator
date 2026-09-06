import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BOOK_SCRIPT = PROJECT_ROOT / "scripts" / "book.py"
WORKFLOW_V2 = PROJECT_ROOT / "scripts" / "workflow_v2"
WORKFLOW_REVISION = "0123456789abcdef"


def sha256(content):
    return hashlib.sha256(content).hexdigest()


class WorkflowV2ReviewReportCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        (self.repo / "scripts").mkdir(parents=True)
        shutil.copy2(BOOK_SCRIPT, self.repo / "scripts" / "book.py")
        shutil.copytree(WORKFLOW_V2, self.repo / "scripts" / "workflow_v2")
        self.book = self.repo / "books" / "demo"
        (self.book / "extracted").mkdir(parents=True)
        (self.book / "translated").mkdir(parents=True)
        source = b"Source chapter\n"
        translation = "Перевод главы\n".encode("utf-8")
        (self.book / "extracted" / "001.md").write_bytes(source)
        (self.book / "translated" / "001.md").write_bytes(translation)

        metadata = {
            "schema_version": 1,
            "title": "Demo",
            "author": "Author",
            "source_language": "en",
            "target_language": "ru",
            "source_format": "markdown",
            "source_file": "demo.md",
            "chapter_count": 1,
            "workflow": {
                "repository": "https://github.com/tim8es/book-translator",
                "requested_ref": "refactor/workflow-engine-v2",
                "resolved_revision": WORKFLOW_REVISION,
                "review_evidence": "review-ledger-v1",
            },
        }
        progress = {
            "schema_version": 1,
            "book_slug": "demo",
            "chapters": [
                {
                    "number": 1,
                    "title": "One",
                    "slug": "one",
                    "source_path": "extracted/001.md",
                    "translation_path": "translated/001.md",
                    "status": "reviewed",
                }
            ],
        }
        record = {
            "record_id": "a" * 32,
            "sequence": 1,
            "unit_id": "chapter-000001",
            "outcome": "PASS",
            "source_sha256": sha256(source),
            "translation_sha256": sha256(translation),
            "workflow_revision": WORKFLOW_REVISION,
            "review_contract_revision": f"docs/TRANSLATION.md@{WORKFLOW_REVISION}",
            "reviewer_session_id": "reviewer-a",
            "reviewed_at": "2026-09-06T18:00:00Z",
            "state_revision": "state-1",
            "review_commit": "review-commit-a",
            "correction_round": 0,
            "supersedes_record_id": None,
        }
        ledger = {
            "schema_version": 1,
            "book_slug": "demo",
            "next_sequence": 2,
            "records": [record],
        }
        for name, data in (
            ("metadata.json", metadata),
            ("progress.json", progress),
            ("review-ledger.json", ledger),
        ):
            (self.book / name).write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

    def tearDown(self):
        self.tmp.cleanup()

    def run_book(self, *args, expect=0):
        result = subprocess.run(
            [sys.executable, str(self.repo / "scripts" / "book.py"), *args],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            expect,
            msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        return result

    def test_default_command_generates_canonical_markdown_and_identical_rerun(self):
        report = self.book / "REVIEW_REPORT.md"
        first = self.run_book("review-report", "demo")
        first_bytes = report.read_bytes()
        second = self.run_book("review-report", "demo")
        second_bytes = report.read_bytes()

        self.assertEqual(first_bytes, second_bytes)
        self.assertIn(b"# Review Report -- demo".replace(b"--", "—".encode("utf-8")), first_bytes)
        self.assertIn(b"PASS coverage: **1/1 (100.0%)**", first_bytes)
        self.assertEqual(first.stderr, "")
        self.assertEqual(second.stderr, "")
        self.assertIn("generated books/demo/REVIEW_REPORT.md", first.stdout)
        self.assertIn("pass=1/1", first.stdout)
        self.assertIn("unchanged", second.stdout)

    def test_json_mode_is_deterministic_and_does_not_write_report(self):
        report = self.book / "REVIEW_REPORT.md"
        report.write_bytes(b"existing report must stay unchanged\n")
        first = self.run_book("review-report", "demo", "--json")
        second = self.run_book("review-report", "demo", "--json")

        self.assertEqual(first.stdout, second.stdout)
        payload = json.loads(first.stdout)
        self.assertEqual(payload["schema"], "review-report-v1")
        self.assertEqual(payload["summary"]["pass_coverage"], {"passed": 1, "total": 1, "percent": 100.0})
        self.assertEqual(payload["units"][0]["current_review"]["review_commit"], "review-commit-a")
        self.assertEqual(report.read_bytes(), b"existing report must stay unchanged\n")
        self.assertEqual(first.stderr, "")

    def test_invalid_ledger_fails_closed_without_replacing_existing_report(self):
        report = self.book / "REVIEW_REPORT.md"
        report.write_bytes(b"last valid report\n")
        (self.book / "review-ledger.json").write_text("{not json\n", encoding="utf-8")

        result = self.run_book("review-report", "demo", expect=1)

        self.assertEqual(report.read_bytes(), b"last valid report\n")
        self.assertEqual(result.stdout, "")
        self.assertIn("ERROR:", result.stderr)
        self.assertIn("review ledger", result.stderr.lower())
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()

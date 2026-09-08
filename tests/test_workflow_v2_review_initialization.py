import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "book.py"
WORKFLOW_V2 = PROJECT_ROOT / "scripts" / "workflow_v2"
TEMPLATES = PROJECT_ROOT / "docs" / "templates"


class WorkflowV2ReviewInitializationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        (self.repo / "scripts").mkdir(parents=True)
        shutil.copy2(SCRIPT, self.repo / "scripts" / "book.py")
        shutil.copytree(WORKFLOW_V2, self.repo / "scripts" / "workflow_v2")
        if TEMPLATES.exists():
            shutil.copytree(TEMPLATES, self.repo / "docs" / "templates")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args, expect=0):
        result = subprocess.run(
            [sys.executable, str(self.repo / "scripts" / "book.py"), *args],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, expect, msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}")
        return result

    def test_extract_creates_review_evidence_marker_and_empty_ledger_without_install_provenance(self):
        source = self.repo / "sample.md"
        source.write_text("# One\n\nAlpha.\n\n# Two\n\nBeta.\n", encoding="utf-8")

        self.run_cli("extract", str(source), "--slug", "sample", "--target-language", "ru")

        book = self.repo / "books" / "sample"
        metadata = json.loads((book / "metadata.json").read_text(encoding="utf-8"))
        ledger = json.loads((book / "review-ledger.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["workflow"]["review_evidence"], "review-ledger-v1")
        self.assertEqual(
            ledger,
            {
                "schema_version": 1,
                "book_slug": "sample",
                "next_sequence": 1,
                "records": [],
            },
        )
        self.run_cli("validate", "sample")

    def test_extract_preserves_resolved_workflow_provenance_with_review_marker(self):
        (self.repo / ".book-translator-install.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "canonical_repository": "https://github.com/tim8es/book-translator",
                    "requested_ref": "refactor/workflow-engine-v2",
                    "resolved_revision": "0123456789abcdef",
                    "install_root": ".",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        source = self.repo / "sample.md"
        source.write_text("# One\n\nAlpha.\n", encoding="utf-8")

        self.run_cli("extract", str(source), "--slug", "sample", "--target-language", "ru")

        metadata = json.loads((self.repo / "books" / "sample" / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(
            metadata["workflow"],
            {
                "repository": "https://github.com/tim8es/book-translator",
                "requested_ref": "refactor/workflow-engine-v2",
                "resolved_revision": "0123456789abcdef",
                "review_evidence": "review-ledger-v1",
            },
        )


if __name__ == "__main__":
    unittest.main()

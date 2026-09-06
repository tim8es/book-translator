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
CORPUS_SCRIPT = PROJECT_ROOT / "scripts" / "corpus.py"
WORKFLOW_V2 = PROJECT_ROOT / "scripts" / "workflow_v2"
REVISION = "0123456789abcdef"


class WorkflowV2StatusCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        (self.repo / "scripts").mkdir(parents=True)
        shutil.copy2(BOOK_SCRIPT, self.repo / "scripts" / "book.py")
        shutil.copy2(CORPUS_SCRIPT, self.repo / "scripts" / "corpus.py")
        shutil.copytree(WORKFLOW_V2, self.repo / "scripts" / "workflow_v2")
        (self.repo / ".book-translator-install.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "canonical_repository": "https://github.com/tim8es/book-translator",
                    "requested_ref": "refactor/workflow-engine-v2",
                    "resolved_revision": REVISION,
                    "install_root": ".",
                }
            )
            + "\n",
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

    def run_corpus(self, *args, expect=0):
        result = subprocess.run(
            [sys.executable, str(self.repo / "scripts" / "corpus.py"), *args],
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

    def initialize_book(self):
        source = self.repo / "sample.md"
        source.write_text("# One\n\nAlpha.\n", encoding="utf-8")
        self.run_book("extract", str(source), "--slug", "sample", "--target-language", "ru")
        return self.repo / "books" / "sample"

    @staticmethod
    def sha256(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def canonical_json(self, result):
        payload = json.loads(result.stdout)
        self.assertEqual(
            result.stdout,
            json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
        )
        return payload

    def test_status_and_resume_json_are_deterministic_bounded_and_read_only(self):
        book = self.initialize_book()
        durable = [
            book / "metadata.json",
            book / "progress.json",
            book / "review-ledger.json",
        ]
        before = {path.name: self.sha256(path) for path in durable}

        status = self.canonical_json(self.run_book("status", "sample", "--json"))
        self.assertEqual(status["lifecycle"], {"extracted": 1, "pending": 0, "reviewed": 0, "translated": 0})
        self.assertEqual(status["reviews"], {"corrections_required": 0, "missing": 0, "pass": 0, "stale": 0, "untranslated": 1})
        self.assertEqual(status["corpus"]["state"], "unsealed")
        self.assertEqual(status["claims"], [])
        self.assertTrue(status["valid"])

        resume = self.canonical_json(self.run_book("resume", "sample", "--json"))
        self.assertEqual(resume["operation"], "translate")
        self.assertEqual(resume["unit_id"], "chapter-000001")
        self.assertEqual(resume["context"]["role"], "translator")
        self.assertEqual(resume["context"]["contracts"], ["AGENTS.md", "docs/TRANSLATION.md"])
        self.assertEqual(
            resume["context"]["files"],
            [
                "metadata.json",
                "progress.json",
                "glossary.md",
                "style-guide.md",
                "extracted/chapter-0001-one.md",
            ],
        )

        after = {path.name: self.sha256(path) for path in durable}
        self.assertEqual(after, before)
        self.assertFalse((book / "status.json").exists())

    def test_resume_blocks_on_real_corpus_hash_mismatch(self):
        book = self.initialize_book()
        self.run_corpus("seal", "sample")
        extracted = next((book / "extracted").glob("*.md"))
        extracted.write_text(extracted.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")

        result = self.run_book("resume", "sample", "--json", expect=1)
        payload = self.canonical_json(result)

        self.assertEqual(payload["operation"], "blocked")
        self.assertEqual(payload["reason"], "preflight_failed")
        self.assertTrue(any("hash mismatch" in error for error in payload["errors"]))

    def test_human_status_and_resume_are_concise(self):
        self.initialize_book()

        status = self.run_book("status", "sample")
        self.assertIn("sample: valid", status.stdout)
        self.assertIn("lifecycle", status.stdout)
        self.assertIn("corpus=unsealed", status.stdout)

        resume = self.run_book("resume", "sample")
        self.assertIn("next=translate", resume.stdout)
        self.assertIn("chapter-000001", resume.stdout)


if __name__ == "__main__":
    unittest.main()

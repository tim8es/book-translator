import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BOOK_SCRIPT = PROJECT_ROOT / "scripts" / "book.py"
WORKFLOW_V2 = PROJECT_ROOT / "scripts" / "workflow_v2"


class WorkflowV2PatchCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        (self.repo / "scripts").mkdir(parents=True)
        shutil.copy2(BOOK_SCRIPT, self.repo / "scripts" / "book.py")
        shutil.copytree(WORKFLOW_V2, self.repo / "scripts" / "workflow_v2")
        self.target = self.repo / "books" / "demo" / "translated" / "001.md"
        self.target.parent.mkdir(parents=True)

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

    def test_literal_patch_writes_target_and_prints_diff_then_summary(self):
        self.target.write_bytes(b"alpha\n")
        result = self.run_book(
            "patch",
            "books/demo/translated/001.md",
            "--old",
            "alpha",
            "--new",
            "beta",
            "--expected-count",
            "1",
        )

        self.assertEqual(self.target.read_bytes(), b"beta\n")
        self.assertIn("--- a/books/demo/translated/001.md", result.stdout)
        self.assertIn("+++ b/books/demo/translated/001.md", result.stdout)
        self.assertLess(result.stdout.index("--- a/"), result.stdout.index("patch books/"))
        self.assertTrue(
            result.stdout.endswith(
                "patch books/demo/translated/001.md: matches=1 changed=yes mode=apply\n"
            )
        )
        self.assertEqual(result.stderr, "")

    def test_count_mismatch_exits_one_without_mutation_or_traceback(self):
        self.target.write_bytes(b"alpha\n")
        before = self.target.read_bytes()
        result = self.run_book(
            "patch",
            "books/demo/translated/001.md",
            "--old",
            "alpha",
            "--new",
            "beta",
            "--expected-count",
            "2",
            expect=1,
        )

        self.assertEqual(self.target.read_bytes(), before)
        self.assertIn("ERROR:", result.stderr)
        self.assertIn("expected 2 match", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_dry_run_prints_same_style_diff_without_writing(self):
        self.target.write_bytes(b"alpha\n")
        before = self.target.read_bytes()
        result = self.run_book(
            "patch",
            "books/demo/translated/001.md",
            "--old",
            "alpha",
            "--new",
            "beta",
            "--expected-count",
            "1",
            "--dry-run",
        )

        self.assertEqual(self.target.read_bytes(), before)
        self.assertIn("-alpha", result.stdout)
        self.assertIn("+beta", result.stdout)
        self.assertTrue(
            result.stdout.endswith(
                "patch books/demo/translated/001.md: matches=1 changed=yes mode=dry-run\n"
            )
        )

    def test_regex_and_line_scope_reach_domain_behavior(self):
        self.target.write_text("Term: one\nTerm: two\nTerm: three\n", encoding="utf-8")
        result = self.run_book(
            "patch",
            "books/demo/translated/001.md",
            "--old",
            r"(Term):\s+([^\r\n]+)",
            "--new",
            r"\1 — \2",
            "--expected-count",
            "1",
            "--regex",
            "--line-start",
            "2",
            "--line-end",
            "2",
        )

        self.assertEqual(
            self.target.read_text(encoding="utf-8"),
            "Term: one\nTerm — two\nTerm: three\n",
        )
        self.assertIn("matches=1 changed=yes mode=apply", result.stdout)

    def test_unsafe_path_is_rejected_without_touching_outside_file(self):
        outside = self.repo.parent / "outside.md"
        outside.write_bytes(b"alpha\n")
        result = self.run_book(
            "patch",
            "../outside.md",
            "--old",
            "alpha",
            "--new",
            "beta",
            "--expected-count",
            "1",
            expect=1,
        )

        self.assertEqual(outside.read_bytes(), b"alpha\n")
        self.assertIn("ERROR:", result.stderr)
        self.assertIn("unsafe", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_negative_expected_count_is_rejected_without_write(self):
        self.target.write_bytes(b"alpha\n")
        result = self.run_book(
            "patch",
            "books/demo/translated/001.md",
            "--old",
            "alpha",
            "--new",
            "beta",
            "--expected-count",
            "-1",
            expect=1,
        )
        self.assertEqual(self.target.read_bytes(), b"alpha\n")
        self.assertIn("expected_count must be an integer >= 0", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()

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


class WorkflowV2EpicCloseoutContractTests(unittest.TestCase):
    def test_commit_discipline_policy_is_explicit_and_wired(self):
        policy_path = PROJECT_ROOT / "docs" / "COMMIT_DISCIPLINE.md"
        self.assertTrue(policy_path.is_file(), "docs/COMMIT_DISCIPLINE.md is missing")
        policy = policy_path.read_text(encoding="utf-8")

        for example in (
            "translate(chapter-000001):",
            "review(chapter-000001):",
            "fix(chapter-000001):",
            "state:",
            "workflow:",
            "build:",
        ):
            self.assertIn(example, policy)

        self.assertIn("Do not mix translation content with workflow or schema changes", policy)
        self.assertIn("Revert and recovery", policy)
        self.assertIn("state revision", policy)
        self.assertIn("squash", policy.lower())

        orchestration = (PROJECT_ROOT / "docs" / "ORCHESTRATION.md").read_text(
            encoding="utf-8"
        )
        setup = (PROJECT_ROOT / "docs" / "AGENT_SETUP.md").read_text(encoding="utf-8")
        self.assertIn("docs/COMMIT_DISCIPLINE.md", orchestration)
        self.assertIn("docs/COMMIT_DISCIPLINE.md", setup)
        self.assertIn(
            "STATE.md`, `FINAL_QUALITY_GATES.md`, and `REVIEW_REPORT.md` are generated projections",
            orchestration,
        )

    def test_only_generic_optional_ci_workflow_remains(self):
        workflow_dir = PROJECT_ROOT / ".github" / "workflows"
        workflows = sorted(path.name for path in workflow_dir.glob("*.yml"))
        self.assertEqual(workflows, ["tests.yml"])

        setup = (PROJECT_ROOT / "docs" / "AGENT_SETUP.md").read_text(encoding="utf-8")
        orchestration = (PROJECT_ROOT / "docs" / "ORCHESTRATION.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("GitHub Actions are not required", setup)
        self.assertIn("GitHub Actions are not required for runtime orchestration", orchestration)


class WorkflowV2RepresentativeDogfoodTests(unittest.TestCase):
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
                    "requested_ref": "main",
                    "resolved_revision": REVISION,
                    "install_root": ".",
                }
            )
            + "\n",
            encoding="utf-8",
        )

        source = self.repo / "dogfood.md"
        source.write_text(
            "# First\n\nAlpha source paragraph.\n\n"
            "# Second\n\nBeta source paragraph.\n",
            encoding="utf-8",
        )
        self.run_book(
            "extract",
            str(source),
            "--slug",
            "dogfood",
            "--source-language",
            "en",
            "--target-language",
            "ru",
        )

        subprocess.run(["git", "init"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "dogfood@example.invalid"],
            cwd=self.repo,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Workflow Dogfood"],
            cwd=self.repo,
            check=True,
        )
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(
            ["git", "commit", "-m", "dogfood baseline"],
            cwd=self.repo,
            check=True,
            capture_output=True,
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
        self.assertNotIn("Traceback", result.stderr)
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
        self.assertNotIn("Traceback", result.stderr)
        return result

    def json_book(self, *args):
        return json.loads(self.run_book(*args, "--json").stdout)

    @property
    def book(self):
        return self.repo / "books" / "dogfood"

    def progress(self):
        return json.loads((self.book / "progress.json").read_text(encoding="utf-8"))

    def chapter(self, number):
        return next(
            chapter for chapter in self.progress()["chapters"] if chapter["number"] == number
        )

    def test_resume_review_finalize_report_and_epub_build_end_to_end(self):
        self.run_book("validate", "dogfood")
        self.run_corpus("verify", "dogfood")

        initial = self.json_book("resume", "dogfood")
        self.assertEqual(initial["operation"], "translate")

        for number in (1, 2):
            translate_resume = self.json_book("resume", "dogfood")
            self.assertEqual(translate_resume["operation"], "translate")

            translator_session = f"translator-{number}"
            self.run_book(
                "claim",
                "dogfood",
                str(number),
                "--role",
                "translator",
                "--session-id",
                translator_session,
                "--json",
            )
            chapter = self.chapter(number)
            translation = self.book / chapter["translation_path"]
            translation.parent.mkdir(parents=True, exist_ok=True)
            translation.write_text(
                f"# Перевод {number}\n\nКанонический перевод главы {number}.\n",
                encoding="utf-8",
            )
            accepted = self.json_book(
                "accept-translation",
                "dogfood",
                str(number),
                "--session-id",
                translator_session,
            )
            self.assertTrue(accepted["changed"])
            self.assertEqual(self.chapter(number)["status"], "translated")
            self.run_book(
                "release",
                "dogfood",
                str(number),
                "--session-id",
                translator_session,
            )

            review_resume = self.json_book("resume", "dogfood")
            self.assertEqual(review_resume["operation"], "review")

            reviewer_session = f"reviewer-{number}"
            self.run_book(
                "claim",
                "dogfood",
                str(number),
                "--role",
                "reviewer",
                "--session-id",
                reviewer_session,
                "--json",
            )
            recorded = self.json_book(
                "review-record",
                "dogfood",
                str(number),
                "--outcome",
                "PASS",
                "--session-id",
                reviewer_session,
                "--review-commit",
                f"dogfood-review-{number}",
            )
            self.assertEqual(recorded["record"]["outcome"], "PASS")
            promoted = self.json_book("accept-review", "dogfood", str(number))
            self.assertEqual(promoted["status"], "reviewed")
            self.assertEqual(self.chapter(number)["status"], "reviewed")
            self.run_book(
                "release",
                "dogfood",
                str(number),
                "--session-id",
                reviewer_session,
            )

        review_snapshot = self.json_book("review-report", "dogfood")
        self.assertEqual(
            review_snapshot["summary"]["pass_coverage"],
            {"passed": 2, "total": 2, "percent": 100.0},
        )
        self.assertEqual(review_snapshot["summary"]["stale"], 0)
        self.assertEqual(review_snapshot["summary"]["missing"], 0)

        self.run_book("review-report", "dogfood")
        review_report_before_finalize = (self.book / "REVIEW_REPORT.md").read_bytes()

        completion = self.json_book(
            "finalize", "dogfood", "--session-id", "dogfood-finalizer"
        )
        self.assertTrue(completion["quality_gates"]["all_reviewed"])
        self.assertTrue(completion["quality_gates"]["review_pass_coverage_complete"])
        self.assertEqual(
            (self.book / "REVIEW_REPORT.md").read_bytes(), review_report_before_finalize
        )
        self.assertTrue((self.book / "STATE.md").is_file())
        self.assertTrue((self.book / "FINAL_QUALITY_GATES.md").is_file())

        self.run_book("validate", "dogfood")
        self.run_corpus("verify", "dogfood")

        complete_resume = self.json_book("resume", "dogfood")
        self.assertEqual(complete_resume["operation"], "complete")

        self.run_book("build", "dogfood", "--format", "epub")
        artifact = self.book / "output" / "dogfood.epub"
        manifest_path = self.book / "output" / "manifest.json"
        self.assertTrue(artifact.is_file())
        self.assertGreater(artifact.stat().st_size, 0)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["artifact_path"], "output/dogfood.epub")
        self.assertEqual(manifest["unit_count"], 2)
        self.assertFalse(manifest["preview"])

        build_status = self.json_book("build-status", "dogfood", "--format", "epub")
        self.assertEqual(build_status["state"], "current")
        self.assertEqual(build_status["artifact_path"], "output/dogfood.epub")


if __name__ == "__main__":
    unittest.main()

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


class WorkflowV2ParallelHandshakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        (self.repo / "scripts").mkdir(parents=True)
        shutil.copy2(BOOK_SCRIPT, self.repo / "scripts" / "book.py")
        shutil.copytree(WORKFLOW_V2, self.repo / "scripts" / "workflow_v2")
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
        source.write_text(
            "# One\n\nAlpha.\n\n# Two\n\nBeta.\n\n# Three\n\nGamma.\n",
            encoding="utf-8",
        )
        self.run_cli("extract", str(source), "--slug", "sample", "--target-language", "ru")
        subprocess.run(["git", "init"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "tests@example.invalid"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "Workflow Tests"], cwd=self.repo, check=True)
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "planning baseline"], cwd=self.repo, check=True, capture_output=True)

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args, expect=0):
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

    def git_head(self):
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def parallel_plan(self):
        result = self.run_cli("resume", "sample", "--parallel", "2", "--json")
        payload = json.loads(result.stdout)
        self.assertEqual(payload["operation"], "parallel")
        return payload

    def test_parallel_resume_snapshot_is_consumed_by_durable_claim(self):
        plan = self.parallel_plan()
        assignment = plan["assignments"][0]
        context = assignment["context"]
        snapshot = context["shared_state_revisions"]

        self.assertEqual(context["base_commit"], self.git_head())
        self.assertEqual(
            snapshot,
            {
                "glossary": context["state_revisions"]["glossary"],
                "style_guide": context["state_revisions"]["style_guide"],
            },
        )

        claimed = self.run_cli(
            "claim",
            "sample",
            str(assignment["chapter_number"]),
            "--role",
            assignment["context"]["role"],
            "--session-id",
            "parallel-worker-a",
            "--base-commit",
            context["base_commit"],
            "--glossary-revision",
            snapshot["glossary"],
            "--style-guide-revision",
            snapshot["style_guide"],
            "--json",
        )
        claim = json.loads(claimed.stdout)["claims"][0]
        self.assertEqual(claim["base_commit"], context["base_commit"])
        self.assertEqual(claim["shared_state_revisions"], snapshot)

        persisted = json.loads(
            (
                self.repo
                / "books"
                / "sample"
                / ".workflow"
                / "claims"
                / f"{assignment['unit_id']}.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(persisted["base_commit"], context["base_commit"])
        self.assertEqual(persisted["shared_state_revisions"], snapshot)

    def test_snapshot_claim_rejects_stale_planning_revisions_before_dispatch(self):
        plan = self.parallel_plan()
        assignment = plan["assignments"][0]
        context = assignment["context"]
        snapshot = context["shared_state_revisions"]
        glossary = self.repo / "books" / "sample" / "glossary.md"
        glossary.write_text(glossary.read_text(encoding="utf-8") + "drift\n", encoding="utf-8")

        rejected = self.run_cli(
            "claim",
            "sample",
            str(assignment["chapter_number"]),
            "--role",
            assignment["context"]["role"],
            "--session-id",
            "parallel-worker-a",
            "--base-commit",
            context["base_commit"],
            "--glossary-revision",
            snapshot["glossary"],
            "--style-guide-revision",
            snapshot["style_guide"],
            "--json",
            expect=1,
        )
        self.assertIn("shared state", rejected.stderr.lower())
        claim_path = (
            self.repo
            / "books"
            / "sample"
            / ".workflow"
            / "claims"
            / f"{assignment['unit_id']}.json"
        )
        self.assertFalse(claim_path.exists())

    def test_sequential_resume_keeps_legacy_context_shape(self):
        payload = json.loads(self.run_cli("resume", "sample", "--json").stdout)
        self.assertEqual(payload["operation"], "translate")
        self.assertNotIn("base_commit", payload["context"])
        self.assertNotIn("shared_state_revisions", payload["context"])


if __name__ == "__main__":
    unittest.main()

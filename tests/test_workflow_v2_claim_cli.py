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


class WorkflowV2ClaimCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        (self.repo / "scripts").mkdir(parents=True)
        shutil.copy2(SCRIPT, self.repo / "scripts" / "book.py")
        shutil.copytree(WORKFLOW_V2, self.repo / "scripts" / "workflow_v2")

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

    def initialize_book(self, *, with_workflow=True):
        if with_workflow:
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
        return self.repo / "books" / "sample"

    def test_claim_conflict_list_and_owner_release_json(self):
        book = self.initialize_book()

        claimed = self.run_cli(
            "claim",
            "sample",
            "1",
            "--role",
            "translator",
            "--session-id",
            "session-a",
            "--base-commit",
            "commit-a",
            "--json",
        )
        payload = json.loads(claimed.stdout)
        self.assertEqual([item["unit_id"] for item in payload["claims"]], ["chapter-000001"])
        self.assertEqual(payload["claims"][0]["role"], "translator")
        self.assertEqual(payload["claims"][0]["base_commit"], "commit-a")
        self.assertIn("revision", payload["claims"][0])

        conflict = self.run_cli(
            "claim",
            "sample",
            "1",
            "--role",
            "reviewer",
            "--session-id",
            "session-b",
            "--json",
            expect=1,
        )
        self.assertIn("chapter-000001", conflict.stderr)

        listed = json.loads(self.run_cli("claims", "sample", "--json").stdout)
        self.assertEqual([item["session_id"] for item in listed["claims"]], ["session-a"])

        foreign = self.run_cli(
            "release",
            "sample",
            "1",
            "--session-id",
            "session-b",
            "--json",
            expect=1,
        )
        self.assertIn("session-a", foreign.stderr)

        released = json.loads(
            self.run_cli(
                "release",
                "sample",
                "1",
                "--session-id",
                "session-a",
                "--json",
            ).stdout
        )
        self.assertEqual(released["results"][0]["status"], "released")
        self.assertEqual(json.loads(self.run_cli("claims", "sample", "--json").stdout)["claims"], [])
        self.assertEqual(len(list((book / ".workflow" / "claim-events").glob("*.json"))), 2)

    def test_range_claim_reports_success_only_after_all_units_exist(self):
        book = self.initialize_book()

        result = json.loads(
            self.run_cli(
                "claim",
                "sample",
                "1-3",
                "--role",
                "translator",
                "--session-id",
                "range-session",
                "--base-commit",
                "commit-a",
                "--json",
            ).stdout
        )

        self.assertEqual(
            [item["unit_id"] for item in result["claims"]],
            ["chapter-000001", "chapter-000002", "chapter-000003"],
        )
        self.assertEqual(
            sorted(path.name for path in (book / ".workflow" / "claims").glob("*.json")),
            ["chapter-000001.json", "chapter-000002.json", "chapter-000003.json"],
        )

    def test_cleanup_claims_removes_only_expired_claims(self):
        book = self.initialize_book()
        self.run_cli(
            "claim",
            "sample",
            "1",
            "--role",
            "translator",
            "--session-id",
            "expired-session",
            "--base-commit",
            "commit-a",
            "--json",
        )
        self.run_cli(
            "claim",
            "sample",
            "2",
            "--role",
            "translator",
            "--session-id",
            "live-session",
            "--base-commit",
            "commit-a",
            "--json",
        )

        expired_path = book / ".workflow" / "claims" / "chapter-000001.json"
        expired = json.loads(expired_path.read_text(encoding="utf-8"))
        expired["claimed_at"] = "2000-01-01T00:00:00Z"
        expired["expires_at"] = "2000-01-01T00:01:00Z"
        expired_path.write_text(json.dumps(expired, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        result = json.loads(self.run_cli("cleanup-claims", "sample", "--json").stdout)
        statuses = {item["unit_id"]: item["status"] for item in result["results"]}
        self.assertEqual(statuses["chapter-000001"], "cleaned")
        self.assertEqual(statuses["chapter-000002"], "live")
        self.assertFalse(expired_path.exists())
        self.assertTrue((book / ".workflow" / "claims" / "chapter-000002.json").is_file())

    def test_claim_refuses_to_fabricate_missing_workflow_revision(self):
        self.initialize_book(with_workflow=False)

        result = self.run_cli(
            "claim",
            "sample",
            "1",
            "--role",
            "translator",
            "--session-id",
            "session-a",
            "--base-commit",
            "commit-a",
            "--json",
            expect=1,
        )

        self.assertIn("workflow revision", result.stderr.lower())


if __name__ == "__main__":
    unittest.main()

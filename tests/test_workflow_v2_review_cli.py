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
REVISION = "0123456789abcdef"


class WorkflowV2ReviewCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        (self.repo / "scripts").mkdir(parents=True)
        shutil.copy2(SCRIPT, self.repo / "scripts" / "book.py")
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

    def initialize_book(self, slug="sample"):
        source = self.repo / f"{slug}.md"
        source.write_text("# One\n\nAlpha.\n", encoding="utf-8")
        self.run_cli("extract", str(source), "--slug", slug, "--target-language", "ru")
        book = self.repo / "books" / slug
        progress_path = book / "progress.json"
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
        translation = book / progress["chapters"][0]["translation_path"]
        translation.parent.mkdir(parents=True, exist_ok=True)
        translation.write_text("# Один\n\nАльфа.\n", encoding="utf-8")
        progress["chapters"][0]["status"] = "translated"
        progress_path.write_text(json.dumps(progress, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return book, translation

    def claim_reviewer(self, slug="sample", *, session_id="reviewer-a", role="reviewer"):
        return self.run_cli(
            "claim",
            slug,
            "1",
            "--role",
            role,
            "--session-id",
            session_id,
            "--base-commit",
            "dispatch-commit",
            "--json",
        )

    def assert_canonical_json(self, result):
        payload = json.loads(result.stdout)
        self.assertEqual(
            result.stdout,
            json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
        )
        return payload

    def test_pass_record_list_accept_and_stale_detection_end_to_end(self):
        book, translation = self.initialize_book()
        self.claim_reviewer()

        recorded = self.assert_canonical_json(
            self.run_cli(
                "review-record",
                "sample",
                "1",
                "--outcome",
                "PASS",
                "--session-id",
                "reviewer-a",
                "--review-commit",
                "review-commit-a",
                "--json",
            )
        )
        record = recorded["record"]
        self.assertEqual(record["unit_id"], "chapter-000001")
        self.assertEqual(record["outcome"], "PASS")
        self.assertEqual(record["sequence"], 1)
        self.assertEqual(record["correction_round"], 0)
        self.assertEqual(record["review_commit"], "review-commit-a")
        self.assertEqual(recorded["ledger_revision"], recorded["ledger_revision"].lower())

        listed = self.assert_canonical_json(self.run_cli("reviews", "sample", "--json"))
        self.assertEqual(len(listed["reviews"]), 1)
        self.assertEqual(listed["reviews"][0]["unit_id"], "chapter-000001")
        self.assertEqual(listed["reviews"][0]["state"], "pass")
        self.assertEqual(listed["reviews"][0]["current_record"]["record_id"], record["record_id"])
        self.assertEqual(len(listed["reviews"][0]["history"]), 1)

        accepted = self.assert_canonical_json(
            self.run_cli("accept-review", "sample", "1", "--json")
        )
        self.assertEqual(accepted["unit_id"], "chapter-000001")
        self.assertEqual(accepted["status"], "reviewed")
        self.assertTrue(accepted["changed"])
        progress = json.loads((book / "progress.json").read_text(encoding="utf-8"))
        self.assertEqual(progress["chapters"][0]["status"], "reviewed")

        translation.write_text("# Один\n\nИзменённая Альфа.\n", encoding="utf-8")
        stale = self.assert_canonical_json(self.run_cli("reviews", "sample", "--json"))
        self.assertEqual(stale["reviews"][0]["state"], "stale")
        self.assertIsNone(stale["reviews"][0]["current_record"])
        self.run_cli("accept-review", "sample", "1", "--json", expect=1)

    def test_corrections_required_is_recorded_and_blocks_acceptance(self):
        self.initialize_book()
        self.claim_reviewer()

        recorded = self.assert_canonical_json(
            self.run_cli(
                "review-record",
                "sample",
                "1",
                "--outcome",
                "CORRECTIONS_REQUIRED",
                "--session-id",
                "reviewer-a",
                "--json",
            )
        )
        self.assertEqual(recorded["record"]["correction_round"], 1)
        listed = self.assert_canonical_json(self.run_cli("reviews", "sample", "--json"))
        self.assertEqual(listed["reviews"][0]["state"], "corrections_required")
        self.run_cli("accept-review", "sample", "1", "--json", expect=1)

    def test_review_record_requires_matching_reviewer_claim(self):
        self.initialize_book()
        self.claim_reviewer(session_id="someone-else")
        foreign = self.run_cli(
            "review-record",
            "sample",
            "1",
            "--outcome",
            "PASS",
            "--session-id",
            "reviewer-a",
            "--json",
            expect=1,
        )
        self.assertIn("someone-else", foreign.stderr)

    def test_review_record_refuses_missing_resolved_revision(self):
        book, _ = self.initialize_book()
        metadata_path = book / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["workflow"]["resolved_revision"] = None
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.claim_reviewer()

        result = self.run_cli(
            "review-record",
            "sample",
            "1",
            "--outcome",
            "PASS",
            "--session-id",
            "reviewer-a",
            "--json",
            expect=1,
        )
        self.assertIn("resolved_revision", result.stderr)


if __name__ == "__main__":
    unittest.main()

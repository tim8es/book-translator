import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
BOOK_SCRIPT = SCRIPTS / "book.py"
CORPUS_SCRIPT = SCRIPTS / "corpus.py"
WORKFLOW_V2 = SCRIPTS / "workflow_v2"


class WorkflowV2TranslationAcceptanceReliabilityTests(unittest.TestCase):
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
                    "resolved_revision": "0123456789abcdef",
                    "install_root": ".",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        source = self.repo / "sample.md"
        source.write_text("# One\n\nAlpha.\n", encoding="utf-8")
        self.run_cli(
            "extract",
            str(source),
            "--slug",
            "sample",
            "--target-language",
            "ru",
        )
        subprocess.run(["git", "init"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "tests@example.invalid"],
            cwd=self.repo,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Workflow Tests"],
            cwd=self.repo,
            check=True,
        )
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(
            ["git", "commit", "-m", "baseline"],
            cwd=self.repo,
            check=True,
            capture_output=True,
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

    def progress(self):
        return json.loads(
            (self.repo / "books" / "sample" / "progress.json").read_text(
                encoding="utf-8"
            )
        )

    def translation_path(self):
        chapter = self.progress()["chapters"][0]
        return self.repo / "books" / "sample" / chapter["translation_path"]

    def claim(self, role, session_id):
        return json.loads(
            self.run_cli(
                "claim",
                "sample",
                "1",
                "--role",
                role,
                "--session-id",
                session_id,
                "--json",
            ).stdout
        )["claims"][0]

    def accept_translation(self, session_id):
        return json.loads(
            self.run_cli(
                "accept-translation",
                "sample",
                "1",
                "--session-id",
                session_id,
                "--json",
            ).stdout
        )

    def release(self, session_id):
        self.run_cli("release", "sample", "1", "--session-id", session_id)

    def establish_initial_accepted_translation(self):
        first_claim = self.claim("translator", "translator-a")
        translation = self.translation_path()
        translation.parent.mkdir(parents=True, exist_ok=True)
        translation.write_text("# Один\n\nПервый перевод.\n", encoding="utf-8")
        first_acceptance = self.accept_translation("translator-a")
        self.release("translator-a")
        return first_claim, first_acceptance

    def test_corrected_translated_artifact_rebinds_acceptance_to_fresh_translator_claim(self):
        first_claim, _ = self.establish_initial_accepted_translation()

        self.claim("reviewer", "reviewer-a")
        self.run_cli(
            "review-record",
            "sample",
            "1",
            "--outcome",
            "CORRECTIONS_REQUIRED",
            "--session-id",
            "reviewer-a",
        )
        self.release("reviewer-a")
        resumed = json.loads(self.run_cli("resume", "sample", "--json").stdout)
        self.assertEqual(resumed["operation"], "correct_translation")

        corrected_claim = self.claim("translator", "translator-b")
        translation = self.translation_path()
        translation.write_text("# Один\n\nИсправленный перевод.\n", encoding="utf-8")
        accepted = self.accept_translation("translator-b")

        self.assertTrue(accepted["changed"])
        chapter = self.progress()["chapters"][0]
        self.assertEqual(chapter["status"], "translated")
        evidence = chapter["translation_acceptance"]
        self.assertNotEqual(evidence["claim_id"], first_claim["claim_id"])
        self.assertEqual(evidence["claim_id"], corrected_claim["claim_id"])
        self.assertEqual(evidence["claim_revision"], corrected_claim["revision"])
        self.assertEqual(evidence["session_id"], "translator-b")

    def test_validate_rejects_translation_tamper_after_machine_acceptance(self):
        self.establish_initial_accepted_translation()
        translation = self.translation_path()
        translation.write_text("# Один\n\nTampered after acceptance.\n", encoding="utf-8")

        result = self.run_cli("validate", "sample", expect=1)
        combined = (result.stdout + result.stderr).lower()
        self.assertIn("translation_acceptance", combined)
        self.assertIn("sha256", combined)


if __name__ == "__main__":
    unittest.main()

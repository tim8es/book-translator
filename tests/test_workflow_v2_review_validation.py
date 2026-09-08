import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "book.py"
WORKFLOW_V2 = PROJECT_ROOT / "scripts" / "workflow_v2"
SCRIPTS = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from workflow_v2.filesystem import FilesystemStorage
from workflow_v2.repository import WorkflowStateRepository
from workflow_v2.reviews import ReviewLedgerManager
from workflow_v2.schemas import SchemaKind


REVISION = "0123456789abcdef"
NOW = datetime(2026, 9, 6, 0, 0, 0, tzinfo=timezone.utc)


class WorkflowV2ReviewValidationTests(unittest.TestCase):
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
        source = self.repo / "sample.md"
        source.write_text("# One\n\nAlpha.\n", encoding="utf-8")
        self.run_cli("extract", str(source), "--slug", "sample", "--target-language", "ru")
        self.book = self.repo / "books" / "sample"

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

    def state(self):
        return WorkflowStateRepository(FilesystemStorage(self.book))

    def make_translation(self, content="Перевод.\n"):
        progress = json.loads((self.book / "progress.json").read_text(encoding="utf-8"))
        path = self.book / progress["chapters"][0]["translation_path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def mark_status(self, status):
        repository = self.state()
        loaded = repository.read("progress.json", SchemaKind.PROGRESS)
        progress = loaded.data
        progress["chapters"][0]["status"] = status
        return repository.write_if_version("progress.json", SchemaKind.PROGRESS, progress, loaded.version)

    def record_pass(self):
        repository = self.state()
        progress_loaded = repository.read("progress.json", SchemaKind.PROGRESS)
        metadata = repository.read("metadata.json", SchemaKind.METADATA).data
        progress = progress_loaded.data
        repository.create(
            ".workflow/claims/chapter-000001.json",
            SchemaKind.CLAIM,
            {
                "schema_version": 1,
                "claim_id": "e" * 32,
                "unit_id": "chapter-000001",
                "role": "reviewer",
                "session_id": "reviewer-a",
                "base_revision": progress_loaded.version,
                "base_commit": None,
                "workflow_revision": REVISION,
                "claimed_at": "2026-09-05T23:30:00Z",
                "expires_at": "2026-09-06T01:00:00Z",
            },
        )

        def read_artifact(path):
            return (self.book / path).read_bytes()

        manager = ReviewLedgerManager(
            repository,
            artifact_reader=read_artifact,
            now=lambda: NOW,
            id_factory=lambda: "f" * 32,
        )
        manager.record(
            progress,
            progress_loaded.version,
            metadata,
            1,
            outcome="PASS",
            reviewer_session_id="reviewer-a",
        )

    def test_ledger_enabled_book_requires_ledger_file(self):
        (self.book / "review-ledger.json").unlink()
        result = self.run_cli("validate", "sample", expect=1)
        self.assertIn("review-ledger", result.stderr.lower())

    def test_ledger_enabled_book_rejects_malformed_ledger(self):
        (self.book / "review-ledger.json").write_text(
            json.dumps(
                {"schema_version": 1, "book_slug": "sample", "next_sequence": 2, "records": []}
            )
            + "\n",
            encoding="utf-8",
        )
        result = self.run_cli("validate", "sample", expect=1)
        self.assertIn("review-ledger", result.stderr.lower())

    def test_reviewed_status_without_current_pass_is_invalid(self):
        self.make_translation()
        self.mark_status("reviewed")
        result = self.run_cli("validate", "sample", expect=1)
        self.assertIn("current pass", result.stderr.lower())

    def test_exact_current_pass_allows_reviewed_status(self):
        self.make_translation()
        self.mark_status("translated")
        self.record_pass()
        self.mark_status("reviewed")
        self.run_cli("validate", "sample")

    def test_editing_reviewed_translation_makes_validation_stale(self):
        translation = self.make_translation()
        self.mark_status("translated")
        self.record_pass()
        self.mark_status("reviewed")
        translation.write_text("Изменённый перевод.\n", encoding="utf-8")

        result = self.run_cli("validate", "sample", expect=1)
        self.assertIn("stale", result.stderr.lower())

    def test_book_without_review_evidence_marker_keeps_legacy_validation_behavior(self):
        metadata_path = self.book / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        del metadata["workflow"]["review_evidence"]
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (self.book / "review-ledger.json").unlink()
        self.make_translation()
        self.mark_status("reviewed")

        self.run_cli("validate", "sample")


if __name__ == "__main__":
    unittest.main()

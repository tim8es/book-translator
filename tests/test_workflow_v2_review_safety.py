import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "scripts"
BOOK_SCRIPT = SCRIPTS / "book.py"
WORKFLOW_V2 = SCRIPTS / "workflow_v2"
sys.path.insert(0, str(SCRIPTS))

from workflow_v2.filesystem import FilesystemStorage
from workflow_v2.repository import WorkflowStateRepository
from workflow_v2.reviews import ReviewConflict, ReviewLedgerManager
from workflow_v2.schemas import SchemaKind


REVISION = "0123456789abcdef"
NOW = datetime(2026, 9, 6, 0, 0, 0, tzinfo=timezone.utc)


class ReviewRecordProgressSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repository = WorkflowStateRepository(FilesystemStorage(self.root))
        self.artifacts = {
            "extracted/001.md": b"Source.\n",
            "translated/001.md": "Перевод.\n".encode("utf-8"),
        }
        self.metadata = {
            "schema_version": 1,
            "title": "Example",
            "target_language": "ru",
            "source_format": "markdown",
            "source_file": "example.md",
            "chapter_count": 1,
            "workflow": {
                "repository": "https://github.com/tim8es/book-translator",
                "requested_ref": "refactor/workflow-engine-v2",
                "resolved_revision": REVISION,
                "review_evidence": "review-ledger-v1",
            },
        }
        self.progress = {
            "schema_version": 1,
            "book_slug": "example",
            "chapters": [
                {
                    "number": 1,
                    "title": "One",
                    "slug": "one",
                    "source_path": "extracted/001.md",
                    "translation_path": "translated/001.md",
                    "status": "translated",
                }
            ],
        }
        self.progress_revision = self.repository.create("progress.json", SchemaKind.PROGRESS, self.progress)
        self.repository.create(
            "review-ledger.json",
            SchemaKind.REVIEW_LEDGER,
            {"schema_version": 1, "book_slug": "example", "next_sequence": 1, "records": []},
        )
        self.repository.create(
            ".workflow/claims/chapter-000001.json",
            SchemaKind.CLAIM,
            {
                "schema_version": 1,
                "claim_id": "1" * 32,
                "unit_id": "chapter-000001",
                "role": "reviewer",
                "session_id": "reviewer-a",
                "base_revision": self.progress_revision,
                "base_commit": None,
                "workflow_revision": REVISION,
                "claimed_at": "2026-09-05T23:30:00Z",
                "expires_at": "2026-09-06T01:00:00Z",
            },
        )

    def tearDown(self):
        self.tmp.cleanup()

    def manager(self):
        return ReviewLedgerManager(
            self.repository,
            artifact_reader=lambda path: self.artifacts[path],
            now=lambda: NOW,
            id_factory=lambda: "2" * 32,
        )

    def test_record_rejects_stale_progress_snapshot_without_appending(self):
        newer = {
            **self.progress,
            "book_note": "newer-state",
            "chapters": [dict(chapter) for chapter in self.progress["chapters"]],
        }
        self.repository.write_if_version(
            "progress.json",
            SchemaKind.PROGRESS,
            newer,
            self.progress_revision,
        )

        with self.assertRaises(ReviewConflict):
            self.manager().record(
                self.progress,
                self.progress_revision,
                self.metadata,
                1,
                outcome="PASS",
                reviewer_session_id="reviewer-a",
            )

        ledger = self.repository.read("review-ledger.json", SchemaKind.REVIEW_LEDGER).data
        self.assertEqual(ledger["records"], [])
        self.assertEqual(ledger["next_sequence"], 1)


class ReviewEvidenceMarkerSafetyTests(unittest.TestCase):
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

    def test_validate_rejects_unknown_review_evidence_mode_instead_of_falling_back_to_legacy(self):
        metadata_path = self.repo / "books" / "sample" / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["workflow"]["review_evidence"] = "review-ledger-v99"
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        result = self.run_cli("validate", "sample", expect=1)
        self.assertIn("unsupported review_evidence", result.stderr.lower())
        self.assertIn("review-ledger-v99", result.stderr)


if __name__ == "__main__":
    unittest.main()

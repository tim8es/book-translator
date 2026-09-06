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
SCRIPTS = PROJECT_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from workflow_v2.coordination import FINALIZATION_PATH
from workflow_v2.filesystem import FilesystemStorage
from workflow_v2.finalize import build_reviewed_candidate, sha256_bytes
from workflow_v2.repository import WorkflowStateRepository
from workflow_v2.schemas import SchemaKind


REVISION = "0123456789abcdef"
REPORTS = ("STATE.md", "FINAL_QUALITY_GATES.md", "REVIEW_REPORT.md")


class WorkflowV2FinalizeReliabilityTests(unittest.TestCase):
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

    def canonical_json(self, result):
        payload = json.loads(result.stdout)
        self.assertEqual(
            result.stdout,
            json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
        )
        return payload

    def repository(self, book):
        return WorkflowStateRepository(FilesystemStorage(book))

    def initialize_ready_book(self):
        source = self.repo / "sample.md"
        source.write_text("# One\n\nAlpha.\n", encoding="utf-8")
        self.run_book(
            "extract",
            str(source),
            "--slug",
            "sample",
            "--target-language",
            "ru",
        )
        book = self.repo / "books" / "sample"
        repository = self.repository(book)
        progress = repository.read("progress.json", SchemaKind.PROGRESS)
        translated = dict(progress.data)
        translated["chapters"] = [dict(item) for item in progress.data["chapters"]]
        translation_path = book / translated["chapters"][0]["translation_path"]
        translation_path.parent.mkdir(parents=True, exist_ok=True)
        translation_path.write_text("# Один\n\nАльфа.\n", encoding="utf-8")
        translated["chapters"][0]["status"] = "translated"
        repository.write_if_version(
            "progress.json", SchemaKind.PROGRESS, translated, progress.version
        )

        self.run_book(
            "claim",
            "sample",
            "1",
            "--role",
            "reviewer",
            "--session-id",
            "reviewer-crashed",
            "--base-commit",
            "review-dispatch",
            "--json",
        )
        self.run_book(
            "review-record",
            "sample",
            "1",
            "--outcome",
            "PASS",
            "--session-id",
            "reviewer-crashed",
            "--review-commit",
            "review-commit",
            "--json",
        )
        self.run_book(
            "release",
            "sample",
            "1",
            "--session-id",
            "reviewer-crashed",
            "--json",
        )
        return book

    def preparing_marker(self, book):
        repository = self.repository(book)
        progress = repository.read("progress.json", SchemaKind.PROGRESS)
        candidate = build_reviewed_candidate(progress.data)
        candidate_bytes = repository.serialize("progress.json", SchemaKind.PROGRESS, candidate)
        marker = {
            "schema_version": 1,
            "lock_id": "f" * 32,
            "book_slug": "sample",
            "workflow_revision": REVISION,
            "base_progress_revision": progress.version,
            "candidate_progress_sha256": sha256_bytes(candidate_bytes),
            "phase": "preparing",
            "promoted_progress_revision": None,
            "session_id": "crashed-finalizer",
            "started_at": "2026-09-06T20:20:00Z",
        }
        marker_revision = repository.create(
            FINALIZATION_PATH, SchemaKind.FINALIZATION_LOCK, marker
        )
        return repository, progress, candidate, candidate_bytes, marker, marker_revision

    def promote_progress_only(self, book):
        repository, progress, candidate, candidate_bytes, marker, marker_revision = self.preparing_marker(book)
        promoted_revision = repository.storage.write_if_version(
            "progress.json", candidate_bytes, progress.version
        )
        return (
            repository,
            candidate,
            candidate_bytes,
            marker,
            marker_revision,
            promoted_revision,
        )

    def promote_marker(self, repository, marker, marker_revision, promoted_revision):
        promoted_marker = dict(marker)
        promoted_marker["phase"] = "promoted"
        promoted_marker["promoted_progress_revision"] = promoted_revision
        repository.write_if_version(
            FINALIZATION_PATH,
            SchemaKind.FINALIZATION_LOCK,
            promoted_marker,
            marker_revision,
        )

    def assert_completed(self, book):
        progress = self.repository(book).read("progress.json", SchemaKind.PROGRESS)
        self.assertEqual(
            [chapter["status"] for chapter in progress.data["chapters"]],
            ["reviewed"],
        )
        self.assertFalse((book / FINALIZATION_PATH).exists())
        for path in REPORTS:
            self.assertTrue((book / path).is_file(), path)
        return progress

    def test_crash_after_marker_is_visible_to_fresh_resume_and_retry_completes(self):
        book = self.initialize_ready_book()
        self.preparing_marker(book)
        progress_before = (book / "progress.json").read_bytes()

        resumed = self.canonical_json(self.run_book("resume", "sample", "--json"))
        self.assertEqual(resumed["operation"], "finalize")
        self.assertEqual((book / "progress.json").read_bytes(), progress_before)

        human = self.run_book("resume", "sample")
        self.assertNotIn("Traceback", human.stderr)
        self.assertIn("next=finalize", human.stdout)
        self.assertEqual((book / "progress.json").read_bytes(), progress_before)

        self.canonical_json(
            self.run_book("finalize", "sample", "--session-id", "fresh-finalizer", "--json")
        )
        self.assert_completed(book)

    def test_crash_after_progress_cas_recovers_without_rewriting_progress(self):
        book = self.initialize_ready_book()
        _, _, _, _, _, promoted_revision = self.promote_progress_only(book)
        progress_path = book / "progress.json"
        inode_after_crash = progress_path.stat().st_ino
        bytes_after_crash = progress_path.read_bytes()

        resumed = self.canonical_json(self.run_book("resume", "sample", "--json"))
        self.assertEqual(resumed["operation"], "finalize")
        result = self.canonical_json(
            self.run_book("finalize", "sample", "--session-id", "fresh-finalizer", "--json")
        )

        self.assertEqual(result["state_revisions"]["progress"], promoted_revision)
        self.assertEqual(progress_path.read_bytes(), bytes_after_crash)
        self.assertEqual(progress_path.stat().st_ino, inode_after_crash)
        self.assert_completed(book)

    def test_partial_reports_are_regenerated_and_successful_rerun_is_byte_idempotent(self):
        book = self.initialize_ready_book()
        (
            repository,
            _,
            _,
            marker,
            marker_revision,
            promoted_revision,
        ) = self.promote_progress_only(book)
        self.promote_marker(repository, marker, marker_revision, promoted_revision)

        progress_path = book / "progress.json"
        progress_inode = progress_path.stat().st_ino
        progress_bytes = progress_path.read_bytes()
        (book / "STATE.md").write_text("partial crash output\n", encoding="utf-8")
        self.assertFalse((book / "FINAL_QUALITY_GATES.md").exists())
        self.assertFalse((book / "REVIEW_REPORT.md").exists())

        resumed = self.canonical_json(self.run_book("resume", "sample", "--json"))
        self.assertEqual(resumed["operation"], "finalize")
        self.canonical_json(
            self.run_book("finalize", "sample", "--session-id", "fresh-finalizer", "--json")
        )
        self.assert_completed(book)
        self.assertEqual(progress_path.read_bytes(), progress_bytes)
        self.assertEqual(progress_path.stat().st_ino, progress_inode)
        canonical_reports = {path: (book / path).read_bytes() for path in REPORTS}
        self.assertNotEqual(canonical_reports["STATE.md"], b"partial crash output\n")

        self.canonical_json(
            self.run_book("finalize", "sample", "--session-id", "rerun-finalizer", "--json")
        )
        self.assertEqual(progress_path.read_bytes(), progress_bytes)
        self.assertEqual(progress_path.stat().st_ino, progress_inode)
        self.assertEqual(
            {path: (book / path).read_bytes() for path in REPORTS},
            canonical_reports,
        )
        self.assertFalse((book / FINALIZATION_PATH).exists())


if __name__ == "__main__":
    unittest.main()

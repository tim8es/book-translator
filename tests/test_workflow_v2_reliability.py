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
SCRIPTS = PROJECT_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from workflow_v2.filesystem import FilesystemStorage
from workflow_v2.repository import WorkflowStateRepository
from workflow_v2.schemas import SchemaKind


REVISION = "0123456789abcdef"


class WorkflowV2Phase1ReliabilityTests(unittest.TestCase):
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

    def initialize_book(self, *, private=False):
        source = self.repo / "sample.md"
        source.write_text("# One\n\nAlpha.\n", encoding="utf-8")
        args = [
            "extract",
            str(source),
            "--slug",
            "sample",
            "--target-language",
            "ru",
        ]
        if private:
            args.append("--private-source")
        self.run_book(*args)
        return self.repo / "books" / "sample"

    def canonical_json(self, result):
        payload = json.loads(result.stdout)
        self.assertEqual(
            result.stdout,
            json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
        )
        return payload

    def book_storage(self, book):
        return FilesystemStorage(book)

    def book_repository(self, book):
        return WorkflowStateRepository(self.book_storage(book))

    def authoritative_snapshot(self, book):
        return {
            path.relative_to(book).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(book.rglob("*"))
            if path.is_file()
        }

    def write_claim(
        self,
        book,
        *,
        role="translator",
        session_id="translator-crashed",
        claim_id="1" * 32,
        claimed_at="2020-01-01T00:00:00Z",
        expires_at="2020-01-01T00:01:00Z",
    ):
        repository = self.book_repository(book)
        progress = repository.read("progress.json", SchemaKind.PROGRESS)
        claim = {
            "schema_version": 1,
            "claim_id": claim_id,
            "unit_id": "chapter-000001",
            "role": role,
            "session_id": session_id,
            "base_revision": progress.version,
            "base_commit": None,
            "workflow_revision": REVISION,
            "claimed_at": claimed_at,
            "expires_at": expires_at,
        }
        repository.create(
            ".workflow/claims/chapter-000001.json",
            SchemaKind.CLAIM,
            claim,
        )
        return claim

    def replace_claim_with_expired(
        self,
        book,
        *,
        role,
        session_id,
        claim_id,
    ):
        repository = self.book_repository(book)
        path = ".workflow/claims/chapter-000001.json"
        current = repository.read(path, SchemaKind.CLAIM)
        repository.delete_if_version(path, SchemaKind.CLAIM, current.version)
        return self.write_claim(
            book,
            role=role,
            session_id=session_id,
            claim_id=claim_id,
        )

    def claim_events(self, book):
        repository = self.book_repository(book)
        paths = repository.storage.list(".workflow/claim-events")
        return [repository.read(path, SchemaKind.CLAIM_EVENT).data for path in paths]

    def progress_document(self, book):
        return self.book_repository(book).read("progress.json", SchemaKind.PROGRESS)

    def write_translation(self, book, text="# Один\n\nАльфа.\n"):
        progress = self.progress_document(book).data
        translation = book / progress["chapters"][0]["translation_path"]
        translation.parent.mkdir(parents=True, exist_ok=True)
        translation.write_text(text, encoding="utf-8")
        return translation

    def mark_translated(self, book, text="# Один\n\nАльфа.\n"):
        translation = self.write_translation(book, text)
        repository = self.book_repository(book)
        loaded = repository.read("progress.json", SchemaKind.PROGRESS)
        updated = dict(loaded.data)
        updated["chapters"] = [dict(chapter) for chapter in loaded.data["chapters"]]
        updated["chapters"][0]["status"] = "translated"
        revision = repository.write_if_version(
            "progress.json",
            SchemaKind.PROGRESS,
            updated,
            loaded.version,
        )
        return translation, revision

    def claim_reviewer(self, session_id="reviewer-crashed"):
        return self.run_book(
            "claim",
            "sample",
            "1",
            "--role",
            "reviewer",
            "--session-id",
            session_id,
            "--base-commit",
            "dispatch-commit",
            "--json",
        )

    def record_pass(self, session_id="reviewer-crashed"):
        return self.canonical_json(
            self.run_book(
                "review-record",
                "sample",
                "1",
                "--outcome",
                "PASS",
                "--session-id",
                session_id,
                "--review-commit",
                "review-commit",
                "--json",
            )
        )

    def release_claim(self, session_id):
        return self.canonical_json(
            self.run_book(
                "release",
                "sample",
                "1",
                "--session-id",
                session_id,
                "--json",
            )
        )

    def test_fresh_resume_blocks_on_surviving_live_claim_without_mutation(self):
        book = self.initialize_book()
        claimed = self.canonical_json(
            self.run_book(
                "claim",
                "sample",
                "1",
                "--role",
                "translator",
                "--session-id",
                "translator-crashed",
                "--base-commit",
                "dispatch-commit",
                "--json",
            )
        )
        self.assertEqual(claimed["claims"][0]["unit_id"], "chapter-000001")

        before = self.authoritative_snapshot(book)
        payload = self.canonical_json(
            self.run_book("resume", "sample", "--json", expect=1)
        )
        after = self.authoritative_snapshot(book)

        self.assertEqual(payload["operation"], "blocked")
        self.assertEqual(payload["reason"], "unit_claimed")
        self.assertEqual(payload["unit_id"], "chapter-000001")
        self.assertEqual(payload["claim"]["session_id"], "translator-crashed")
        self.assertEqual(after, before)

    def test_expired_claim_cleanup_is_audited_retry_safe_and_restores_resume(self):
        book = self.initialize_book()
        self.write_claim(book)

        cleaned = self.canonical_json(
            self.run_book("cleanup-claims", "sample", "--json")
        )
        self.assertEqual(
            [(item["unit_id"], item["status"]) for item in cleaned["results"]],
            [("chapter-000001", "cleaned")],
        )

        events = self.claim_events(book)
        self.assertEqual(len(events), 2)
        request = next(item for item in events if item["action"] == "cleanup_requested")
        completion = next(item for item in events if item["action"] == "cleaned")
        self.assertEqual(request["reason"], "lease_expired")
        self.assertEqual(request["unit_id"], "chapter-000001")
        self.assertEqual(completion["request_event_id"], request["event_id"])

        event_snapshot = self.authoritative_snapshot(book)
        second = self.canonical_json(
            self.run_book("cleanup-claims", "sample", "--json")
        )
        self.assertEqual(second, {"results": []})
        self.assertEqual(self.authoritative_snapshot(book), event_snapshot)

        resumed = self.canonical_json(self.run_book("resume", "sample", "--json"))
        self.assertEqual(resumed["operation"], "translate")
        self.assertEqual(resumed["unit_id"], "chapter-000001")

    def test_owner_release_retry_fails_deterministically_without_duplicate_audit(self):
        book = self.initialize_book()
        self.run_book(
            "claim",
            "sample",
            "1",
            "--role",
            "translator",
            "--session-id",
            "translator-a",
            "--base-commit",
            "dispatch-commit",
            "--json",
        )

        released = self.canonical_json(
            self.run_book(
                "release",
                "sample",
                "1",
                "--session-id",
                "translator-a",
                "--json",
            )
        )
        self.assertEqual(
            [(item["unit_id"], item["status"]) for item in released["results"]],
            [("chapter-000001", "released")],
        )
        events_before_retry = self.claim_events(book)
        self.assertEqual(len(events_before_retry), 2)

        retry = self.run_book(
            "release",
            "sample",
            "1",
            "--session-id",
            "translator-a",
            "--json",
            expect=1,
        )
        self.assertIn("no active claim", retry.stderr)
        self.assertEqual(self.claim_events(book), events_before_retry)
        self.assertFalse(
            (book / ".workflow" / "claims" / "chapter-000001.json").exists()
        )

    def test_crash_after_translation_bytes_keeps_extracted_lifecycle_until_claim_cleanup(self):
        book = self.initialize_book()
        self.run_book(
            "claim",
            "sample",
            "1",
            "--role",
            "translator",
            "--session-id",
            "translator-crashed",
            "--base-commit",
            "dispatch-commit",
            "--json",
        )
        translation = self.write_translation(book)

        status = self.canonical_json(self.run_book("status", "sample", "--json"))
        self.assertEqual(status["lifecycle"]["extracted"], 1)
        self.assertEqual(status["lifecycle"]["translated"], 0)
        blocked = self.canonical_json(
            self.run_book("resume", "sample", "--json", expect=1)
        )
        self.assertEqual(blocked["reason"], "unit_claimed")

        self.replace_claim_with_expired(
            book,
            role="translator",
            session_id="translator-crashed",
            claim_id="2" * 32,
        )
        self.run_book("cleanup-claims", "sample", "--json")
        before = self.authoritative_snapshot(book)
        resumed = self.canonical_json(self.run_book("resume", "sample", "--json"))
        after = self.authoritative_snapshot(book)

        self.assertEqual(resumed["operation"], "translate")
        self.assertEqual(translation.read_text(encoding="utf-8"), "# Один\n\nАльфа.\n")
        self.assertEqual(self.progress_document(book).data["chapters"][0]["status"], "extracted")
        self.assertEqual(after, before)

    def test_crash_after_translated_progress_blocks_until_claim_cleanup_then_resumes_review(self):
        book = self.initialize_book()
        self.run_book(
            "claim",
            "sample",
            "1",
            "--role",
            "translator",
            "--session-id",
            "translator-crashed",
            "--base-commit",
            "dispatch-commit",
            "--json",
        )
        self.mark_translated(book)

        status = self.canonical_json(self.run_book("status", "sample", "--json"))
        self.assertEqual(status["lifecycle"]["translated"], 1)
        self.assertEqual(status["reviews"]["missing"], 1)
        blocked = self.canonical_json(
            self.run_book("resume", "sample", "--json", expect=1)
        )
        self.assertEqual(blocked["reason"], "unit_claimed")

        self.replace_claim_with_expired(
            book,
            role="translator",
            session_id="translator-crashed",
            claim_id="3" * 32,
        )
        self.run_book("cleanup-claims", "sample", "--json")
        ledger_before = (book / "review-ledger.json").read_bytes()
        resumed = self.canonical_json(self.run_book("resume", "sample", "--json"))

        self.assertEqual(resumed["operation"], "review")
        self.assertEqual((book / "review-ledger.json").read_bytes(), ledger_before)

    def test_crash_after_pass_blocks_until_claim_cleanup_then_accept_review_is_idempotent(self):
        book = self.initialize_book()
        self.mark_translated(book)
        self.claim_reviewer()
        recorded = self.record_pass()
        self.assertEqual(recorded["record"]["outcome"], "PASS")

        status = self.canonical_json(self.run_book("status", "sample", "--json"))
        self.assertEqual(status["lifecycle"]["translated"], 1)
        self.assertEqual(status["reviews"]["pass"], 1)
        self.assertEqual(status["claims"][0]["role"], "reviewer")
        blocked = self.canonical_json(
            self.run_book("resume", "sample", "--json", expect=1)
        )
        self.assertEqual(blocked["reason"], "unit_claimed")

        self.replace_claim_with_expired(
            book,
            role="reviewer",
            session_id="reviewer-crashed",
            claim_id="4" * 32,
        )
        self.run_book("cleanup-claims", "sample", "--json")
        resumed = self.canonical_json(self.run_book("resume", "sample", "--json"))
        self.assertEqual(resumed["operation"], "accept_review")

        first = self.canonical_json(
            self.run_book("accept-review", "sample", "1", "--json")
        )
        self.assertTrue(first["changed"])
        storage = self.book_storage(book)
        progress_after_first = storage.read("progress.json")
        ledger_after_first = storage.read("review-ledger.json")

        second = self.canonical_json(
            self.run_book("accept-review", "sample", "1", "--json")
        )
        self.assertFalse(second["changed"])
        progress_after_second = storage.read("progress.json")
        ledger_after_second = storage.read("review-ledger.json")
        self.assertEqual(progress_after_second.content, progress_after_first.content)
        self.assertEqual(progress_after_second.version, progress_after_first.version)
        self.assertEqual(ledger_after_second.content, ledger_after_first.content)
        self.assertEqual(ledger_after_second.version, ledger_after_first.version)

    def test_stale_pass_on_translated_unit_resumes_review(self):
        book = self.initialize_book()
        translation, _ = self.mark_translated(book)
        self.claim_reviewer("reviewer-a")
        self.record_pass("reviewer-a")
        self.release_claim("reviewer-a")

        translation.write_text("# Один\n\nИзменённая Альфа.\n", encoding="utf-8")
        status = self.canonical_json(self.run_book("status", "sample", "--json"))
        self.assertTrue(status["valid"])
        self.assertEqual(status["reviews"]["stale"], 1)
        resumed = self.canonical_json(self.run_book("resume", "sample", "--json"))
        self.assertEqual(resumed["operation"], "review")

    def test_stale_pass_on_reviewed_unit_fails_closed(self):
        book = self.initialize_book()
        translation, _ = self.mark_translated(book)
        self.claim_reviewer("reviewer-a")
        self.record_pass("reviewer-a")
        self.release_claim("reviewer-a")
        accepted = self.canonical_json(
            self.run_book("accept-review", "sample", "1", "--json")
        )
        self.assertTrue(accepted["changed"])

        translation.write_text("# Один\n\nИзменённая Альфа.\n", encoding="utf-8")
        status = self.canonical_json(self.run_book("status", "sample", "--json"))
        self.assertFalse(status["valid"])
        self.assertEqual(status["reviews"]["stale"], 1)
        self.assertTrue(
            any("reviewed without current PASS evidence" in error for error in status["errors"])
        )
        resumed = self.canonical_json(
            self.run_book("resume", "sample", "--json", expect=1)
        )
        self.assertEqual(resumed["operation"], "blocked")
        self.assertEqual(resumed["reason"], "preflight_failed")


if __name__ == "__main__":
    unittest.main()

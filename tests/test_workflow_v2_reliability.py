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

    def claim_events(self, book):
        repository = self.book_repository(book)
        paths = repository.storage.list(".workflow/claim-events")
        return [repository.read(path, SchemaKind.CLAIM_EVENT).data for path in paths]

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


if __name__ == "__main__":
    unittest.main()

"""Verify that one workflow makes the same decisions on both storage backends.

The GitHub backend uses a deterministic in-memory API stand-in: these tests
exercise Python code, not a claim that a GitHub-only chat can execute Python.
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

from github_fake import FakeGitHubApiClient
from workflow.filesystem import FilesystemStorage
from workflow.github_storage import GitHubStorage
from workflow.repository import WorkflowStateRepository
from workflow.schemas import SCHEMA_VERSION, SchemaKind
from workflow.session_planner import plan_sessions
from workflow.status import StatusResolver
from workflow.storage import StorageVersionConflict


def chapter(number: int, state: str) -> dict:
    return {
        "number": number,
        "title": f"Chapter {number}",
        "slug": f"chapter-{number:04d}",
        "source_path": f"extracted/chapter-{number:04d}.md",
        "translation_path": f"translated/chapter-{number:04d}.md",
        "status": state,
    }


def setup(storage) -> tuple[WorkflowStateRepository, StatusResolver]:
    repo = WorkflowStateRepository(storage)
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "title": "Backend parity",
        "target_language": "ru",
        "source_format": "txt",
        "source_file": "example.txt",
        "chapter_count": 3,
        "workflow": {
            "repository": "https://github.com/tim8es/book-translator",
            "requested_ref": "main",
            "resolved_revision": "test-revision",
            "review_evidence": "review-ledger-v1",
        },
    }
    progress = {
        "schema_version": SCHEMA_VERSION,
        "book_slug": "sample",
        "chapters": [
            chapter(1, "reviewed"),
            chapter(2, "translated"),
            chapter(3, "extracted"),
        ],
    }
    repo.create("metadata.json", SchemaKind.METADATA, metadata)
    progress_version = repo.create("progress.json", SchemaKind.PROGRESS, progress)
    source = b"Source chapter 1.\n"
    translation = "Перевод главы 1.\n".encode("utf-8")
    for number in range(1, 4):
        storage.create_if_absent(
            f"extracted/chapter-{number:04d}.md",
            source if number == 1 else f"Source chapter {number}.\n".encode(),
        )
    storage.create_if_absent("translated/chapter-0001.md", translation)
    storage.create_if_absent("translated/chapter-0002.md", b"Translation chapter 2.\n")
    storage.create_if_absent("glossary.md", b"# Glossary\n")
    storage.create_if_absent("style-guide.md", b"# Style\n")
    record = {
        "record_id": "1" * 32,
        "sequence": 1,
        "unit_id": "chapter-000001",
        "outcome": "PASS",
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "translation_sha256": hashlib.sha256(translation).hexdigest(),
        "workflow_revision": "test-revision",
        "review_contract_revision": "docs/TRANSLATION.md@test-revision",
        "reviewer_session_id": "different-review-session",
        "reviewed_at": "2026-09-06T12:30:00Z",
        "state_revision": progress_version,
        "review_commit": None,
        "correction_round": 0,
        "supersedes_record_id": None,
    }
    repo.create("review-ledger.json", SchemaKind.REVIEW_LEDGER, {
        "schema_version": SCHEMA_VERSION,
        "book_slug": "sample",
        "next_sequence": 2,
        "records": [record],
    })
    resolver = StatusResolver(repo, artifact_reader=lambda path: storage.read(path).content)
    return repo, resolver


def decision_view(resolver: StatusResolver) -> tuple[dict, dict, list]:
    status = resolver.status(corpus={"state": "verified", "source_sha256": "a" * 64})
    resume = resolver.resume(status)
    parallel = resolver.resume(status, parallel=2)
    worker_steps = [
        (assignment["unit_id"], assignment["operation"])
        for assignment in parallel["assignments"]
    ]
    return status, resume, worker_steps


class WorkflowStorageParityTests(unittest.TestCase):
    def test_resume_parallel_planning_and_review_integrity_match(self):
        with tempfile.TemporaryDirectory() as temp:
            local, local_status = setup(FilesystemStorage(Path(temp)))
            fake = FakeGitHubApiClient()
            remote, remote_status = setup(GitHubStorage(
                fake, repository="owner/repo", branch="main", root_prefix="books/sample"
            ))
            status_fs, resume_fs, workers_fs = decision_view(local_status)
            status_gh, resume_gh, workers_gh = decision_view(remote_status)

            for key in ("valid", "errors", "lifecycle", "reviews", "units", "claims"):
                self.assertEqual(status_fs[key], status_gh[key], key)
            self.assertTrue(status_fs["valid"])
            self.assertEqual(resume_fs["operation"], resume_gh["operation"])
            self.assertEqual((resume_gh["operation"], resume_gh["unit_id"]), (
                "review", "chapter-000002",
            ))
            self.assertEqual(workers_fs, workers_gh)
            self.assertEqual(workers_gh, [
                ("chapter-000002", "review"), ("chapter-000003", "translate"),
            ])
            sizes = {"chapter-000002": 100, "chapter-000003": 200}
            dispatches = []
            for status in (status_fs, status_gh):
                plan = plan_sessions(
                    status, sessions=3, source_sizes=sizes,
                    base_commit="sha-123", dispatch_id="a" * 32,
                )
                dispatches.append([
                    (session["session_id"], [(u["unit_id"], u["operation"]) for u in session["units"]])
                    for session in plan["sessions"]
                ])
            self.assertEqual(dispatches[0], dispatches[1])

            # An altered, previously reviewed translation invalidates both
            # books without inventing or reusing old literary PASS evidence.
            for repo, resolver in ((local, local_status), (remote, remote_status)):
                stored = repo.storage.read("translated/chapter-0001.md")
                repo.storage.write_if_version(
                    "translated/chapter-0001.md", b"Tampered translation.\n", stored.version
                )
                invalid = resolver.status(corpus={"state": "verified", "source_sha256": "a" * 64})
                self.assertFalse(invalid["valid"])
                self.assertIn("reviewed without current PASS", " ".join(invalid["errors"]))
                self.assertEqual(resolver.resume(invalid)["operation"], "blocked")

    def test_progress_compare_and_swap_rejects_stale_writers_on_both_backends(self):
        with tempfile.TemporaryDirectory() as temp:
            fs, _ = setup(FilesystemStorage(Path(temp)))
            gh, _ = setup(GitHubStorage(
                FakeGitHubApiClient(), repository="owner/repo", branch="main",
                root_prefix="books/sample",
            ))
            for repo in (fs, gh):
                loaded = repo.read("progress.json", SchemaKind.PROGRESS)
                changed = dict(loaded.data)
                changed["chapters"] = [dict(ch) for ch in loaded.data["chapters"]]
                changed["chapters"][2]["title"] = "Edited title"
                repo.write_if_version("progress.json", SchemaKind.PROGRESS, changed, loaded.version)
                with self.assertRaises(StorageVersionConflict):
                    repo.write_if_version("progress.json", SchemaKind.PROGRESS, loaded.data, loaded.version)


if __name__ == "__main__":
    unittest.main()

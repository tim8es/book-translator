import hashlib
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from workflow_v2.filesystem import FilesystemStorage
from workflow_v2.repository import WorkflowStateRepository
from workflow_v2.schemas import SCHEMA_VERSION, SchemaKind
from workflow_v2.status import StatusResolver


class WorkflowStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repository = WorkflowStateRepository(FilesystemStorage(self.root))
        self.metadata = {
            "schema_version": SCHEMA_VERSION,
            "title": "Demo",
            "target_language": "ru",
            "source_format": "txt",
            "source_file": "demo.txt",
            "chapter_count": 4,
            "workflow": {
                "repository": "https://github.com/tim8es/book-translator",
                "requested_ref": "refactor/workflow-engine-v2",
                "resolved_revision": "rev-1",
                "review_evidence": "review-ledger-v1",
            },
        }
        self.progress = {
            "schema_version": SCHEMA_VERSION,
            "book_slug": "demo",
            "chapters": [
                self.chapter(1, "reviewed"),
                self.chapter(2, "translated"),
                self.chapter(3, "translated"),
                self.chapter(4, "extracted"),
            ],
        }
        self.repository.create("metadata.json", SchemaKind.METADATA, self.metadata)
        self.progress_revision = self.repository.create(
            "progress.json", SchemaKind.PROGRESS, self.progress
        )
        for number in range(1, 5):
            self.write_artifact(f"extracted/chapter-{number:04d}.md", f"source {number}\n")
        for number in range(1, 4):
            self.write_artifact(f"translated/chapter-{number:04d}.md", f"translation {number}\n")

        record_1 = self.review_record(1, sequence=1, translation=b"translation 1\n")
        record_2 = self.review_record(2, sequence=2, translation=b"older translation 2\n")
        self.repository.create(
            "review-ledger.json",
            SchemaKind.REVIEW_LEDGER,
            {
                "schema_version": SCHEMA_VERSION,
                "book_slug": "demo",
                "next_sequence": 3,
                "records": [record_1, record_2],
            },
        )
        self.repository.create(
            ".workflow/claims/chapter-000003.json",
            SchemaKind.CLAIM,
            {
                "schema_version": SCHEMA_VERSION,
                "claim_id": "c" * 32,
                "unit_id": "chapter-000003",
                "role": "reviewer",
                "session_id": "review-session",
                "base_revision": self.progress_revision,
                "base_commit": None,
                "workflow_revision": "rev-1",
                "claimed_at": "2026-09-06T12:00:00Z",
                "expires_at": "2026-09-06T13:00:00Z",
            },
        )
        self.resolver = StatusResolver(
            self.repository,
            artifact_reader=lambda path: (self.root / path).read_bytes(),
        )

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def chapter(number, status):
        return {
            "number": number,
            "title": f"Chapter {number}",
            "slug": f"chapter-{number:04d}",
            "source_path": f"extracted/chapter-{number:04d}.md",
            "translation_path": f"translated/chapter-{number:04d}.md",
            "status": status,
        }

    def write_artifact(self, relative_path, text):
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def review_record(self, number, *, sequence, translation):
        source = (self.root / f"extracted/chapter-{number:04d}.md").read_bytes()
        return {
            "record_id": f"{number:032x}",
            "sequence": sequence,
            "unit_id": f"chapter-{number:06d}",
            "outcome": "PASS",
            "source_sha256": hashlib.sha256(source).hexdigest(),
            "translation_sha256": hashlib.sha256(translation).hexdigest(),
            "workflow_revision": "rev-1",
            "review_contract_revision": "docs/TRANSLATION.md@rev-1",
            "reviewer_session_id": "review-session",
            "reviewed_at": "2026-09-06T12:30:00Z",
            "state_revision": self.progress_revision,
            "review_commit": None,
            "correction_round": 0,
            "supersedes_record_id": None,
        }

    def test_status_reports_deterministic_lifecycle_review_and_claim_counts(self):
        status = self.resolver.status(
            structural_errors=(),
            corpus={"state": "verified", "source_sha256": "a" * 64},
        )

        self.assertEqual(
            status["lifecycle"],
            {"pending": 0, "extracted": 1, "translated": 2, "reviewed": 1},
        )
        self.assertEqual(
            status["reviews"],
            {
                "pass": 1,
                "stale": 1,
                "missing": 1,
                "corrections_required": 0,
                "untranslated": 1,
            },
        )
        self.assertEqual(
            status["claims"],
            [
                {
                    "unit_id": "chapter-000003",
                    "role": "reviewer",
                    "session_id": "review-session",
                    "expires_at": "2026-09-06T13:00:00Z",
                }
            ],
        )
        self.assertTrue(status["valid"])
        self.assertEqual(status["workflow_revision"], "rev-1")

    def test_resume_selects_first_valid_operation_and_bounded_context(self):
        status = self.resolver.status(
            structural_errors=(),
            corpus={"state": "verified", "source_sha256": "a" * 64},
        )

        resume = self.resolver.resume(status)

        self.assertEqual(resume["operation"], "review")
        self.assertEqual(resume["unit_id"], "chapter-000002")
        self.assertEqual(resume["chapter_number"], 2)
        self.assertEqual(resume["context"]["role"], "reviewer")
        self.assertEqual(resume["context"]["profile"], "reviewer")
        self.assertEqual(resume["context"]["workflow_revision"], "rev-1")
        self.assertEqual(
            resume["context"]["contracts"],
            ["AGENTS.md", "docs/TRANSLATION.md"],
        )
        self.assertEqual(
            resume["context"]["files"],
            [
                "metadata.json",
                "progress.json",
                "glossary.md",
                "style-guide.md",
                "extracted/chapter-0002.md",
                "translated/chapter-0002.md",
            ],
        )
        self.assertEqual(
            set(resume["context"]["state_revisions"]),
            {"metadata", "progress", "review_ledger"},
        )

    def test_accept_review_uses_bounded_orchestrator_context(self):
        status = self.resolver.status(
            structural_errors=(),
            corpus={"state": "verified", "source_sha256": "a" * 64},
        )
        unit = next(item for item in status["units"] if item["chapter_number"] == 2)
        unit["review"] = "pass"
        status["units"] = [
            item for item in status["units"] if item["chapter_number"] != 2
        ]
        status["units"].insert(1, unit)

        resume = self.resolver.resume(status)

        self.assertEqual(resume["operation"], "accept_review")
        self.assertEqual(resume["context"]["role"], "orchestrator")
        self.assertEqual(resume["context"]["profile"], "orchestrator")
        self.assertEqual(
            resume["context"]["contracts"],
            ["AGENTS.md", "docs/ORCHESTRATION.md"],
        )
        self.assertEqual(
            resume["context"]["files"],
            [
                "metadata.json",
                "progress.json",
                "extracted/chapter-0002.md",
                "translated/chapter-0002.md",
                "review-ledger.json",
            ],
        )

    def test_resume_blocks_when_corpus_integrity_is_invalid(self):
        status = self.resolver.status(
            structural_errors=(),
            corpus={"state": "invalid", "error": "extracted hash mismatch"},
        )

        resume = self.resolver.resume(status)

        self.assertFalse(status["valid"])
        self.assertEqual(resume["operation"], "blocked")
        self.assertEqual(resume["reason"], "preflight_failed")
        self.assertIn("extracted hash mismatch", resume["errors"])

    def test_status_and_resume_do_not_mutate_durable_state(self):
        paths = [
            "metadata.json",
            "progress.json",
            "review-ledger.json",
            ".workflow/claims/chapter-000003.json",
        ]
        before = {path: self.repository.storage.read(path).version for path in paths}

        status = self.resolver.status(
            structural_errors=(),
            corpus={"state": "verified", "source_sha256": "a" * 64},
        )
        self.resolver.resume(status)

        after = {path: self.repository.storage.read(path).version for path in paths}
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()

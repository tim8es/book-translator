import argparse
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from workflow_v2.claims import ClaimManager
from workflow_v2.filesystem import FilesystemStorage
from workflow_v2.repository import WorkflowStateRepository
from workflow_v2.schemas import SCHEMA_VERSION, SchemaError, SchemaKind
from workflow_v2.status import StatusResolver
from workflow_v2.status_cli import register_status_commands


class WorkflowV2ParallelSliceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.storage = FilesystemStorage(self.root)
        self.repository = WorkflowStateRepository(self.storage)
        self.metadata = {
            "schema_version": SCHEMA_VERSION,
            "title": "Demo",
            "target_language": "ru",
            "source_format": "txt",
            "source_file": "demo.txt",
            "chapter_count": 3,
            "workflow": {
                "requested_ref": "refactor/workflow-engine-v2",
                "resolved_revision": "workflow-revision",
            },
        }
        self.progress = {
            "schema_version": SCHEMA_VERSION,
            "book_slug": "demo",
            "chapters": [
                {
                    "number": number,
                    "title": f"Chapter {number}",
                    "slug": f"chapter-{number:04d}",
                    "source_path": f"extracted/chapter-{number:04d}.md",
                    "translation_path": f"translated/chapter-{number:04d}.md",
                    "status": "extracted",
                }
                for number in (1, 2, 3)
            ],
        }
        self.repository.create("metadata.json", SchemaKind.METADATA, self.metadata)
        self.progress_revision = self.repository.create(
            "progress.json", SchemaKind.PROGRESS, self.progress
        )
        self.glossary_revision = self.storage.create_if_absent(
            "glossary.md", b"# Glossary\n"
        )
        self.style_revision = self.storage.create_if_absent(
            "style-guide.md", b"# Style\n"
        )
        self.repository.create(
            ".workflow/claims/chapter-000002.json",
            SchemaKind.CLAIM,
            {
                "schema_version": SCHEMA_VERSION,
                "claim_id": "c" * 32,
                "unit_id": "chapter-000002",
                "role": "translator",
                "session_id": "other-session",
                "base_revision": self.progress_revision,
                "base_commit": None,
                "workflow_revision": "workflow-revision",
                "claimed_at": "2026-09-07T12:00:00Z",
                "expires_at": "2026-09-07T13:00:00Z",
            },
        )
        self.resolver = StatusResolver(
            self.repository,
            artifact_reader=lambda path: (self.root / path).read_bytes(),
        )

    def tearDown(self):
        self.temp.cleanup()

    def status(self):
        return self.resolver.status(
            structural_errors=(),
            corpus={"state": "verified", "source_sha256": "a" * 64},
        )

    def test_status_freezes_glossary_and_style_revisions_for_worker_context(self):
        status = self.status()

        self.assertEqual(status["state_revisions"]["glossary"], self.glossary_revision)
        self.assertEqual(status["state_revisions"]["style_guide"], self.style_revision)

        sequential = self.resolver.resume(status)
        self.assertEqual(
            sequential["context"]["state_revisions"]["glossary"],
            self.glossary_revision,
        )
        self.assertEqual(
            sequential["context"]["state_revisions"]["style_guide"],
            self.style_revision,
        )

    def test_explicit_parallel_resume_returns_disjoint_unclaimed_units(self):
        batch = self.resolver.resume(self.status(), parallel=2)

        self.assertEqual(batch["operation"], "parallel")
        self.assertEqual(batch["parallel"], 2)
        self.assertEqual(
            [item["unit_id"] for item in batch["assignments"]],
            ["chapter-000001", "chapter-000003"],
        )
        self.assertEqual(
            [item["operation"] for item in batch["assignments"]],
            ["translate", "translate"],
        )
        self.assertEqual(
            len({item["unit_id"] for item in batch["assignments"]}),
            len(batch["assignments"]),
        )
        for item in batch["assignments"]:
            self.assertEqual(
                item["context"]["state_revisions"]["glossary"],
                self.glossary_revision,
            )
            self.assertEqual(
                item["context"]["state_revisions"]["style_guide"],
                self.style_revision,
            )

    def test_default_resume_keeps_sequential_behavior(self):
        result = self.resolver.resume(self.status())

        self.assertEqual(result["operation"], "translate")
        self.assertEqual(result["unit_id"], "chapter-000001")
        self.assertNotIn("assignments", result)

    def test_parallel_claim_persists_frozen_shared_state_revisions(self):
        ids = iter(f"{number:032x}" for number in range(1, 20))
        manager = ClaimManager(
            self.repository,
            now=lambda: datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc),
            id_factory=lambda: next(ids),
        )
        frozen = {
            "glossary": self.glossary_revision,
            "style_guide": self.style_revision,
        }

        claim = manager.acquire(
            self.progress,
            "1",
            role="translator",
            session_id="parallel-session",
            base_revision=self.progress_revision,
            base_commit="base-commit",
            workflow_revision="workflow-revision",
            shared_state_revisions=frozen,
        )[0]

        self.assertEqual(claim.data["shared_state_revisions"], frozen)
        persisted = self.repository.read(claim.path, SchemaKind.CLAIM).data
        self.assertEqual(persisted["shared_state_revisions"], frozen)

    def test_claim_schema_rejects_partial_shared_state_snapshot(self):
        with self.assertRaises(SchemaError):
            self.repository.create(
                ".workflow/claims/chapter-000001.json",
                SchemaKind.CLAIM,
                {
                    "schema_version": SCHEMA_VERSION,
                    "claim_id": "d" * 32,
                    "unit_id": "chapter-000001",
                    "role": "translator",
                    "session_id": "parallel-session",
                    "base_revision": self.progress_revision,
                    "base_commit": None,
                    "workflow_revision": "workflow-revision",
                    "shared_state_revisions": {"glossary": self.glossary_revision},
                    "claimed_at": "2026-09-07T12:00:00Z",
                    "expires_at": "2026-09-07T13:00:00Z",
                },
            )

    def test_resume_cli_accepts_invocation_scoped_parallel_flag(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        subparsers.add_parser("extract")
        subparsers.add_parser("validate")
        register_status_commands(
            subparsers,
            self.root,
            preflight=lambda slug: ((), {"state": "verified"}),
        )

        args = parser.parse_args(["resume", "demo", "--parallel", "3"])

        self.assertEqual(args.parallel, 3)


if __name__ == "__main__":
    unittest.main()

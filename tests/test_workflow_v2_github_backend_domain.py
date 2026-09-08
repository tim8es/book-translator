from datetime import datetime, timezone
import unittest

from github_fake import FakeGitHubApiClient
from workflow_v2.claims import ClaimConflict, ClaimManager
from workflow_v2.coordination import BookCoordinationManager, CoordinationConflict
from workflow_v2.github_storage import GitHubStorage
from workflow_v2.repository import WorkflowStateRepository
from workflow_v2.schemas import SchemaKind
from workflow_v2.status import StatusResolver
from workflow_v2.storage import StorageVersionConflict


NOW = datetime(2026, 9, 7, 12, 0, 0, tzinfo=timezone.utc)


class WorkflowV2GitHubBackendDomainTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeGitHubApiClient()
        self.storage = GitHubStorage(
            self.client,
            repository="owner/repository",
            branch="refactor/workflow-engine-v2",
            root_prefix="books/sample",
        )
        self.repository = WorkflowStateRepository(self.storage)
        self.metadata = {
            "schema_version": 1,
            "title": "Sample",
            "target_language": "ru",
            "source_format": "markdown",
            "source_file": "source.md",
            "chapter_count": 1,
            "workflow": {
                "repository": "https://github.com/tim8es/book-translator",
                "requested_ref": "refactor/workflow-engine-v2",
                "resolved_revision": "rev-1",
            },
        }
        self.progress = {
            "schema_version": 1,
            "book_slug": "sample",
            "chapters": [
                {
                    "number": 1,
                    "title": "One",
                    "slug": "one",
                    "source_path": "chapters/chapter-001.md",
                    "translation_path": "translations/chapter-001.md",
                    "status": "extracted",
                }
            ],
        }
        self.metadata_revision = self.repository.create(
            "metadata.json", SchemaKind.METADATA, self.metadata
        )
        self.progress_revision = self.repository.create(
            "progress.json", SchemaKind.PROGRESS, self.progress
        )

    def claim_manager(self, session_seed=1):
        ids = iter([f"{session_seed + index:032x}" for index in range(20)])
        return ClaimManager(
            self.repository,
            now=lambda: NOW,
            id_factory=lambda: next(ids),
            coordination=BookCoordinationManager(
                self.repository,
                now=lambda: NOW,
                id_factory=lambda: next(ids),
            ),
        )

    def test_claim_conflict_release_and_audit_match_backend_neutral_domain_behavior(self):
        manager = self.claim_manager(1)
        claims = manager.acquire(
            self.progress,
            "1",
            role="translator",
            session_id="translator-a",
            base_revision=self.progress_revision,
            base_commit=None,
            workflow_revision="rev-1",
        )
        self.assertEqual([claim.data["unit_id"] for claim in claims], ["chapter-000001"])

        with self.assertRaises(ClaimConflict):
            self.claim_manager(100).acquire(
                self.progress,
                "1",
                role="translator",
                session_id="translator-b",
                base_revision=self.progress_revision,
                base_commit=None,
                workflow_revision="rev-1",
            )

        result = manager.release(self.progress, "1", session_id="translator-a")
        self.assertEqual([item.status for item in result], ["released"])
        self.assertEqual(manager.list_active(), [])
        self.assertEqual(len(self.storage.list(".workflow/claim-events")), 2)

    def test_coordination_live_conflict_release_and_reacquire_match_filesystem_semantics(self):
        ids = iter([f"{index:032x}" for index in range(1, 10)])
        coordination = BookCoordinationManager(
            self.repository,
            now=lambda: NOW,
            id_factory=lambda: next(ids),
        )
        first = coordination.acquire(operation="claim_admission", session_id="a")
        with self.assertRaises(CoordinationConflict):
            coordination.acquire(operation="finalize_admission", session_id="b")
        coordination.release(first)
        second = coordination.acquire(operation="finalize_admission", session_id="b")
        coordination.release(second)

    def test_status_and_resume_are_read_only_on_github_storage(self):
        self.client.mutations.clear()
        resolver = StatusResolver(
            self.repository,
            artifact_reader=lambda path: (_ for _ in ()).throw(FileNotFoundError(path)),
        )

        status = resolver.status(corpus={"state": "verified", "storage_mode": "embedded"})
        resume = resolver.resume(status)

        self.assertTrue(status["valid"])
        self.assertEqual(status["lifecycle"]["extracted"], 1)
        self.assertEqual(resume["operation"], "translate")
        self.assertEqual(self.client.mutations, [])

    def test_repository_stale_cas_preserves_competing_winner(self):
        loaded = self.repository.read("progress.json", SchemaKind.PROGRESS)
        winner = dict(loaded.data)
        winner["chapters"] = [dict(winner["chapters"][0], title="Winner")]
        winner_revision = self.repository.write_if_version(
            "progress.json", SchemaKind.PROGRESS, winner, loaded.version
        )

        stale = dict(loaded.data)
        stale["chapters"] = [dict(stale["chapters"][0], title="Loser")]
        with self.assertRaises(StorageVersionConflict):
            self.repository.write_if_version(
                "progress.json", SchemaKind.PROGRESS, stale, loaded.version
            )

        current = self.repository.read("progress.json", SchemaKind.PROGRESS)
        self.assertEqual(current.version, winner_revision)
        self.assertEqual(current.data["chapters"][0]["title"], "Winner")


if __name__ == "__main__":
    unittest.main()

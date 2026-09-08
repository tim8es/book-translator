import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from workflow_v2.coordination import BookCoordinationManager
from workflow_v2.filesystem import FilesystemStorage
from workflow_v2.repository import WorkflowStateRepository
from workflow_v2.reviews import ReviewClaimError, ReviewEvidenceError, ReviewLedgerManager
from workflow_v2.schemas import SCHEMA_VERSION, SchemaKind

try:
    from workflow_v2.proposals import ProposalConflict, ProposalManager
except (ImportError, ModuleNotFoundError):
    ProposalConflict = None
    ProposalManager = None


NOW = datetime(2026, 9, 7, 17, 0, tzinfo=timezone.utc)
WORKFLOW_REVISION = "workflow-revision"


class WorkflowV2ProposalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.storage = FilesystemStorage(self.root)
        self.repository = WorkflowStateRepository(self.storage)
        self.progress = {
            "schema_version": SCHEMA_VERSION,
            "book_slug": "demo",
            "chapters": [
                {
                    "number": 1,
                    "title": "One",
                    "slug": "one",
                    "source_path": "extracted/001-one.md",
                    "translation_path": "translated/001-one.md",
                    "status": "translated",
                }
            ],
        }
        self.progress_revision = self.repository.create(
            "progress.json", SchemaKind.PROGRESS, self.progress
        )
        self.glossary_revision = self.storage.create_if_absent(
            "glossary.md", b"# Glossary\nold\n"
        )
        self.style_revision = self.storage.create_if_absent(
            "style-guide.md", b"# Style\nold\n"
        )
        self.claim = {
            "schema_version": SCHEMA_VERSION,
            "claim_id": "c" * 32,
            "unit_id": "chapter-000001",
            "role": "reviewer",
            "session_id": "worker-a",
            "base_revision": self.progress_revision,
            "base_commit": "base-commit",
            "workflow_revision": WORKFLOW_REVISION,
            "shared_state_revisions": {
                "glossary": self.glossary_revision,
                "style_guide": self.style_revision,
            },
            "claimed_at": "2026-09-07T16:30:00Z",
            "expires_at": "2026-09-07T18:30:00Z",
        }
        self.repository.create(
            ".workflow/claims/chapter-000001.json", SchemaKind.CLAIM, self.claim
        )
        self.ids = iter(f"{value:032x}" for value in range(1, 50))

    def tearDown(self):
        self.temp.cleanup()

    def manager(self):
        self.assertIsNotNone(ProposalManager, "workflow_v2.proposals ProposalManager is not implemented")
        return ProposalManager(
            self.repository,
            now=lambda: NOW,
            id_factory=lambda: next(self.ids),
        )

    def submit(self, *, target="glossary"):
        return self.manager().submit(
            "chapter-000001",
            session_id="worker-a",
            target=target,
            suggestion="Prefer ‘термин’ for this concept.",
            rationale="Keeps terminology stable.",
        )

    def load_json(self, path):
        return json.loads(self.storage.read(path).content.decode("utf-8"))

    def review_fixture(self):
        metadata = {
            "schema_version": SCHEMA_VERSION,
            "title": "Demo",
            "target_language": "ru",
            "source_format": "markdown",
            "source_file": "demo.md",
            "chapter_count": 1,
            "workflow": {
                "resolved_revision": WORKFLOW_REVISION,
                "review_evidence": "review-ledger-v1",
            },
        }
        self.repository.create(
            "review-ledger.json",
            SchemaKind.REVIEW_LEDGER,
            {"schema_version": SCHEMA_VERSION, "book_slug": "demo", "next_sequence": 1, "records": []},
        )
        artifacts = {
            "extracted/001-one.md": b"source\n",
            "translated/001-one.md": b"translation\n",
        }
        review = ReviewLedgerManager(
            self.repository,
            artifact_reader=lambda path: artifacts[path],
            now=lambda: NOW,
            id_factory=lambda: "e" * 32,
        )
        return metadata, review

    def test_submit_persists_immutable_claim_bound_proposal_without_shared_write(self):
        before_glossary = self.storage.read("glossary.md")
        before_style = self.storage.read("style-guide.md")

        result = self.submit()

        proposal = result.proposal
        self.assertEqual(proposal["claim_id"], self.claim["claim_id"])
        self.assertEqual(proposal["unit_id"], "chapter-000001")
        self.assertEqual(proposal["role"], "reviewer")
        self.assertEqual(proposal["base_commit"], "base-commit")
        self.assertEqual(proposal["shared_state_revisions"], self.claim["shared_state_revisions"])
        self.assertEqual(self.storage.read("glossary.md"), before_glossary)
        self.assertEqual(self.storage.read("style-guide.md"), before_style)
        self.assertEqual(self.load_json(result.path), proposal)

    def test_reconcile_marks_stale_when_any_frozen_shared_revision_changed(self):
        proposal = self.submit(target="glossary")
        self.storage.write_if_version(
            "style-guide.md", b"# Style\nchanged\n", self.style_revision
        )
        glossary_before = self.storage.read("glossary.md")

        result = self.manager().reconcile(
            proposal.proposal["proposal_id"],
            session_id="orchestrator-a",
            accept=True,
            replacement=b"# Glossary\nnew\n",
        )

        self.assertEqual(result.resolution["status"], "stale")
        self.assertFalse(result.changed_shared_state)
        self.assertEqual(self.storage.read("glossary.md"), glossary_before)
        self.assertIsNone(result.resolution["resulting_revision"])

    def test_reconcile_accepts_with_cas_and_persists_idempotent_resolution(self):
        proposal = self.submit(target="glossary")
        replacement = b"# Glossary\nnew\n"
        manager = self.manager()

        first = manager.reconcile(
            proposal.proposal["proposal_id"],
            session_id="orchestrator-a",
            accept=True,
            replacement=replacement,
        )
        second = manager.reconcile(
            proposal.proposal["proposal_id"],
            session_id="orchestrator-a",
            accept=True,
            replacement=replacement,
        )

        self.assertEqual(first.resolution["status"], "accepted")
        self.assertTrue(first.changed_shared_state)
        self.assertEqual(self.storage.read("glossary.md").content, replacement)
        self.assertEqual(first.resolution["resulting_revision"], self.storage.read("glossary.md").version)
        self.assertEqual(second.resolution, first.resolution)
        self.assertFalse(second.changed_shared_state)
        self.assertEqual(self.load_json(first.path), first.resolution)

    def test_reconcile_is_blocked_by_live_book_coordination_mutex(self):
        proposal = self.submit(target="glossary")
        before = self.storage.read("glossary.md")
        coordinator = BookCoordinationManager(
            self.repository,
            now=lambda: NOW,
            id_factory=lambda: "f" * 32,
        )
        lease = coordinator.acquire(
            operation="claim_admission",
            session_id="other-orchestrator",
            lease_seconds=60,
        )

        try:
            with self.assertRaises(ProposalConflict):
                self.manager().reconcile(
                    proposal.proposal["proposal_id"],
                    session_id="orchestrator-a",
                    accept=True,
                    replacement=b"# Glossary\nnew\n",
                )
            self.assertEqual(self.storage.read("glossary.md"), before)
        finally:
            coordinator.release(lease)

    def test_rejected_proposal_never_mutates_shared_state(self):
        proposal = self.submit(target="style_guide")
        before = self.storage.read("style-guide.md")

        result = self.manager().reconcile(
            proposal.proposal["proposal_id"],
            session_id="orchestrator-a",
            accept=False,
            reason="Not appropriate globally.",
        )

        self.assertEqual(result.resolution["status"], "rejected")
        self.assertFalse(result.changed_shared_state)
        self.assertEqual(self.storage.read("style-guide.md"), before)

    def test_reviewer_result_rejects_shared_state_drift_and_records_snapshot_when_current(self):
        metadata, review = self.review_fixture()

        self.storage.write_if_version(
            "style-guide.md", b"# Style\ndrifted\n", self.style_revision
        )
        with self.assertRaises(ReviewClaimError):
            review.record(
                self.progress,
                self.progress_revision,
                metadata,
                1,
                outcome="PASS",
                reviewer_session_id="worker-a",
            )

        current_style = self.storage.read("style-guide.md")
        claim_loaded = self.repository.read(
            ".workflow/claims/chapter-000001.json", SchemaKind.CLAIM
        )
        updated_claim = dict(claim_loaded.data)
        updated_claim["shared_state_revisions"] = {
            "glossary": self.storage.read("glossary.md").version,
            "style_guide": current_style.version,
        }
        self.repository.write_if_version(
            ".workflow/claims/chapter-000001.json",
            SchemaKind.CLAIM,
            updated_claim,
            claim_loaded.version,
        )

        recorded = review.record(
            self.progress,
            self.progress_revision,
            metadata,
            1,
            outcome="PASS",
            reviewer_session_id="worker-a",
        )
        self.assertEqual(
            recorded.record["shared_state_revisions"],
            updated_claim["shared_state_revisions"],
        )

    def test_accept_review_rejects_drift_after_pass_record_before_promotion(self):
        metadata, review = self.review_fixture()
        recorded = review.record(
            self.progress,
            self.progress_revision,
            metadata,
            1,
            outcome="PASS",
            reviewer_session_id="worker-a",
        )
        self.assertEqual(
            recorded.record["shared_state_revisions"],
            self.claim["shared_state_revisions"],
        )

        self.storage.write_if_version(
            "glossary.md", b"# Glossary\nchanged after review\n", self.glossary_revision
        )
        before = self.repository.read("progress.json", SchemaKind.PROGRESS)

        with self.assertRaises(ReviewEvidenceError):
            review.accept_review(
                self.progress,
                self.progress_revision,
                metadata,
                1,
            )

        after = self.repository.read("progress.json", SchemaKind.PROGRESS)
        self.assertEqual(after.version, before.version)
        self.assertEqual(after.data["chapters"][0]["status"], "translated")


if __name__ == "__main__":
    unittest.main()

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
BOOK_SCRIPT = SCRIPTS / "book.py"
CORPUS_SCRIPT = SCRIPTS / "corpus.py"
WORKFLOW_V2 = SCRIPTS / "workflow_v2"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from workflow_v2.coordination import (
    BookCoordinationManager,
    CoordinationConflict,
)
from workflow_v2.filesystem import FilesystemStorage
from workflow_v2.proposals import (
    PROPOSAL_RECONCILE_LEASE_SECONDS,
    ProposalConflict,
    ProposalError,
    ProposalManager,
)
from workflow_v2.repository import WorkflowStateRepository
from workflow_v2.schemas import SCHEMA_VERSION, SchemaKind
from workflow_v2.storage import StorageError, StorageNotFound


NOW = datetime(2026, 9, 8, 0, 0, tzinfo=timezone.utc)
WORKFLOW_REVISION = "workflow-revision"


class ProposalCrashRecoveryReleaseTests(unittest.TestCase):
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
        progress_revision = self.repository.create(
            "progress.json", SchemaKind.PROGRESS, self.progress
        )
        self.glossary_revision = self.storage.create_if_absent(
            "glossary.md", b"# Glossary\nold\n"
        )
        self.style_revision = self.storage.create_if_absent(
            "style-guide.md", b"# Style\nold\n"
        )
        self.repository.create(
            ".workflow/claims/chapter-000001.json",
            SchemaKind.CLAIM,
            {
                "schema_version": SCHEMA_VERSION,
                "claim_id": "c" * 32,
                "unit_id": "chapter-000001",
                "role": "reviewer",
                "session_id": "worker-a",
                "base_revision": progress_revision,
                "base_commit": "base-commit",
                "workflow_revision": WORKFLOW_REVISION,
                "shared_state_revisions": {
                    "glossary": self.glossary_revision,
                    "style_guide": self.style_revision,
                },
                "claimed_at": "2026-09-07T23:30:00Z",
                "expires_at": "2026-09-08T00:30:00Z",
            },
        )
        self.ids = iter(f"{value:032x}" for value in range(1, 50))

    def tearDown(self):
        self.temp.cleanup()

    def manager(self):
        return ProposalManager(
            self.repository,
            now=lambda: NOW,
            id_factory=lambda: next(self.ids),
        )

    def test_retry_recovers_accepted_target_after_resolution_crash_and_other_state_drift(self):
        proposal = self.manager().submit(
            "chapter-000001",
            session_id="worker-a",
            target="glossary",
            suggestion="Prefer the accepted term.",
            rationale="Stable terminology.",
        )
        replacement = b"# Glossary\naccepted\n"
        manager = self.manager()
        original_create = self.storage.create_if_absent
        failed = False

        def fail_first_resolution_write(path, content):
            nonlocal failed
            if path.endswith(".resolution.json") and not failed:
                failed = True
                raise StorageError("injected resolution persistence failure")
            return original_create(path, content)

        self.storage.create_if_absent = fail_first_resolution_write
        try:
            with self.assertRaises(ProposalError):
                manager.reconcile(
                    proposal.proposal["proposal_id"],
                    session_id="orchestrator-a",
                    accept=True,
                    replacement=replacement,
                )
        finally:
            self.storage.create_if_absent = original_create

        applied = self.storage.read("glossary.md")
        self.assertEqual(applied.content, replacement)
        self.storage.write_if_version(
            "style-guide.md",
            b"# Style\nchanged after target CAS\n",
            self.style_revision,
        )

        retried = self.manager().reconcile(
            proposal.proposal["proposal_id"],
            session_id="orchestrator-b",
            accept=True,
            replacement=replacement,
        )

        self.assertEqual(retried.resolution["status"], "accepted")
        self.assertFalse(retried.changed_shared_state)
        self.assertEqual(retried.resolution["resulting_revision"], applied.version)
        self.assertEqual(self.storage.read("glossary.md").version, applied.version)
        self.assertEqual(self.storage.read("glossary.md").content, replacement)


class ProposalCoordinationLeaseReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.storage = FilesystemStorage(self.root)
        self.repository = WorkflowStateRepository(self.storage)
        progress_revision = self.repository.create(
            "progress.json",
            SchemaKind.PROGRESS,
            {
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
            },
        )
        self.glossary_revision = self.storage.create_if_absent(
            "glossary.md", b"# Glossary\nold\n"
        )
        self.style_revision = self.storage.create_if_absent(
            "style-guide.md", b"# Style\nold\n"
        )
        self.repository.create(
            ".workflow/claims/chapter-000001.json",
            SchemaKind.CLAIM,
            {
                "schema_version": SCHEMA_VERSION,
                "claim_id": "c" * 32,
                "unit_id": "chapter-000001",
                "role": "reviewer",
                "session_id": "worker-a",
                "base_revision": progress_revision,
                "base_commit": "base-commit",
                "workflow_revision": WORKFLOW_REVISION,
                "shared_state_revisions": {
                    "glossary": self.glossary_revision,
                    "style_guide": self.style_revision,
                },
                "claimed_at": "2026-09-07T23:30:00Z",
                "expires_at": "2026-09-08T00:30:00Z",
            },
        )
        self.clock = [NOW]
        self.coordinator_a = BookCoordinationManager(
            self.repository,
            now=lambda: self.clock[0],
            id_factory=lambda: "a" * 32,
        )
        self.coordinator_b = BookCoordinationManager(
            self.repository,
            now=lambda: self.clock[0],
            id_factory=lambda: "b" * 32,
        )
        self.ids = iter(f"{value:032x}" for value in range(100, 200))

    def tearDown(self):
        self.temp.cleanup()

    def test_expired_holder_cannot_renew_after_reacquire(self):
        lease = self.coordinator_a.acquire(
            operation="proposal_reconcile",
            session_id="orchestrator-a",
            lease_seconds=60,
        )
        self.clock[0] = NOW + timedelta(seconds=60)
        replacement = self.coordinator_b.acquire(
            operation="proposal_reconcile",
            session_id="orchestrator-b",
            lease_seconds=60,
        )
        try:
            with self.assertRaises(CoordinationConflict):
                self.coordinator_a.renew(lease, lease_seconds=900)
        finally:
            self.coordinator_b.release(replacement)

    def test_stale_reconciler_cannot_mutate_after_lease_takeover(self):
        submitter = ProposalManager(
            self.repository,
            now=lambda: self.clock[0],
            id_factory=lambda: next(self.ids),
            coordination=self.coordinator_a,
        )
        proposal = submitter.submit(
            "chapter-000001",
            session_id="worker-a",
            target="glossary",
            suggestion="Prefer the accepted term.",
            rationale="Stable terminology.",
        )
        proposal_id = proposal.proposal["proposal_id"]
        replacement = b"# Glossary\naccepted\n"
        coordinator_b = self.coordinator_b
        clock = self.clock

        class TakeoverProposalManager(ProposalManager):
            def _reconcile_locked(self, *args, **kwargs):
                clock[0] = NOW + timedelta(seconds=PROPOSAL_RECONCILE_LEASE_SECONDS)
                self.takeover_lease = coordinator_b.acquire(
                    operation="proposal_reconcile",
                    session_id="orchestrator-b",
                    lease_seconds=60,
                )
                return super()._reconcile_locked(*args, **kwargs)

        manager = TakeoverProposalManager(
            self.repository,
            now=lambda: self.clock[0],
            id_factory=lambda: next(self.ids),
            coordination=self.coordinator_a,
        )
        with self.assertRaises(ProposalConflict):
            manager.reconcile(
                proposal_id,
                session_id="orchestrator-a",
                accept=True,
                replacement=replacement,
            )

        self.assertEqual(self.storage.read("glossary.md").content, b"# Glossary\nold\n")
        with self.assertRaises(StorageNotFound):
            self.storage.read(f".workflow/proposals/{proposal_id}.resolution.json")
        self.coordinator_b.release(manager.takeover_lease)


class ParallelHumanContractReleaseTests(unittest.TestCase):
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
                    "resolved_revision": "0123456789abcdef",
                    "install_root": ".",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        source = self.repo / "sample.md"
        source.write_text(
            "# One\n\nAlpha.\n\n# Two\n\nBeta.\n\n# Three\n\nGamma.\n",
            encoding="utf-8",
        )
        self.run_cli("extract", str(source), "--slug", "sample", "--target-language", "ru")
        subprocess.run(["git", "init"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "tests@example.invalid"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "Workflow Tests"], cwd=self.repo, check=True)
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "planning baseline"], cwd=self.repo, check=True, capture_output=True)

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args, expect=0):
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

    def _translation_path(self, chapter_number=1):
        progress = json.loads(
            (self.repo / "books" / "sample" / "progress.json").read_text(encoding="utf-8")
        )
        chapter = next(item for item in progress["chapters"] if item["number"] == chapter_number)
        return self.repo / "books" / "sample" / chapter["translation_path"]

    def _parallel_claim(self, chapter_number=1, session_id="worker-a"):
        plan = json.loads(
            self.run_cli("resume", "sample", "--parallel", "2", "--json").stdout
        )
        assignment = next(
            item for item in plan["assignments"] if item["chapter_number"] == chapter_number
        )
        context = assignment["context"]
        snapshot = context["shared_state_revisions"]
        claim = json.loads(
            self.run_cli(
                "claim",
                "sample",
                str(chapter_number),
                "--role",
                "translator",
                "--session-id",
                session_id,
                "--base-commit",
                context["base_commit"],
                "--glossary-revision",
                snapshot["glossary"],
                "--style-guide-revision",
                snapshot["style_guide"],
                "--json",
            ).stdout
        )["claims"][0]
        return context, claim

    def test_human_parallel_resume_emits_copyable_exact_claim_snapshot_flags(self):
        plan = json.loads(
            self.run_cli("resume", "sample", "--parallel", "2", "--json").stdout
        )
        context = plan["assignments"][0]["context"]
        snapshot = context["shared_state_revisions"]

        human = self.run_cli("resume", "sample", "--parallel", "2").stdout
        expected = (
            f"--base-commit {context['base_commit']} "
            f"--glossary-revision {snapshot['glossary']} "
            f"--style-guide-revision {snapshot['style_guide']}"
        )
        self.assertIn(expected, human)

    def test_authoritative_contract_documents_parallel_claim_snapshot_flags(self):
        text = (ROOT / "docs" / "ORCHESTRATION.md").read_text(encoding="utf-8")

        self.assertIn("--base-commit", text)
        self.assertIn("--glossary-revision", text)
        self.assertIn("--style-guide-revision", text)
        self.assertIn("resume --parallel", text)

    def test_parallel_translation_acceptance_persists_exact_claim_snapshot_and_artifact_identity(self):
        context, claim = self._parallel_claim()
        translation = self._translation_path()
        translation.parent.mkdir(parents=True, exist_ok=True)
        translation_bytes = "# Один\n\nПеревод.\n".encode("utf-8")
        translation.write_bytes(translation_bytes)
        source_path = self.repo / "books" / "sample" / "extracted" / "001-one.md"

        accepted = json.loads(
            self.run_cli(
                "accept-translation",
                "sample",
                "1",
                "--session-id",
                "worker-a",
                "--json",
            ).stdout
        )
        self.assertTrue(accepted["changed"])
        self.assertEqual(accepted["status"], "translated")
        self.assertEqual(accepted["unit_id"], "chapter-000001")

        progress_path = self.repo / "books" / "sample" / "progress.json"
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
        chapter = progress["chapters"][0]
        evidence = chapter["translation_acceptance"]
        self.assertEqual(chapter["status"], "translated")
        self.assertEqual(evidence["claim_id"], claim["claim_id"])
        self.assertEqual(evidence["claim_revision"], claim["revision"])
        self.assertEqual(evidence["session_id"], "worker-a")
        self.assertEqual(evidence["base_revision"], claim["base_revision"])
        self.assertEqual(evidence["base_commit"], context["base_commit"])
        self.assertEqual(evidence["workflow_revision"], claim["workflow_revision"])
        self.assertEqual(evidence["shared_state_revisions"], context["shared_state_revisions"])
        self.assertEqual(
            evidence["source_sha256"], hashlib.sha256(source_path.read_bytes()).hexdigest()
        )
        self.assertEqual(
            evidence["translation_sha256"], hashlib.sha256(translation_bytes).hexdigest()
        )

        self.run_cli("release", "sample", "1", "--session-id", "worker-a")
        before_retry = progress_path.read_bytes()
        retried = json.loads(
            self.run_cli(
                "accept-translation",
                "sample",
                "1",
                "--session-id",
                "worker-a",
                "--json",
            ).stdout
        )
        self.assertFalse(retried["changed"])
        self.assertEqual(progress_path.read_bytes(), before_retry)

    def test_parallel_translation_acceptance_rejects_shared_state_drift_without_progress_mutation(self):
        self._parallel_claim()
        translation = self._translation_path()
        translation.parent.mkdir(parents=True, exist_ok=True)
        translation.write_text("# Один\n\nПеревод.\n", encoding="utf-8")
        progress_path = self.repo / "books" / "sample" / "progress.json"
        before = progress_path.read_bytes()
        glossary = self.repo / "books" / "sample" / "glossary.md"
        glossary.write_text(glossary.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")

        result = self.run_cli(
            "accept-translation",
            "sample",
            "1",
            "--session-id",
            "worker-a",
            expect=1,
        )
        self.assertIn("shared state", result.stderr.lower())
        self.assertEqual(progress_path.read_bytes(), before)

    def test_translation_acceptance_is_blocked_by_live_proposal_reconciliation_mutex(self):
        self._parallel_claim()
        translation = self._translation_path()
        translation.parent.mkdir(parents=True, exist_ok=True)
        translation.write_text("# Один\n\nПеревод.\n", encoding="utf-8")
        progress_path = self.repo / "books" / "sample" / "progress.json"
        before = progress_path.read_bytes()
        lock = {
            "schema_version": 1,
            "lock_id": "d" * 32,
            "operation": "proposal_reconcile",
            "session_id": "orchestrator-b",
            "acquired_at": "2026-09-08T00:00:00Z",
            "expires_at": "2099-01-01T00:00:00Z",
        }
        lock_path = self.repo / "books" / "sample" / ".workflow" / "coordination-lock.json"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(json.dumps(lock) + "\n", encoding="utf-8")

        result = self.run_cli(
            "accept-translation",
            "sample",
            "1",
            "--session-id",
            "worker-a",
            expect=1,
        )
        self.assertIn("coordination", result.stderr.lower())
        self.assertEqual(progress_path.read_bytes(), before)

    def test_sequential_legacy_claim_can_use_translation_acceptance_without_snapshot(self):
        claim = json.loads(
            self.run_cli(
                "claim",
                "sample",
                "1",
                "--role",
                "translator",
                "--session-id",
                "worker-a",
                "--json",
            ).stdout
        )["claims"][0]
        self.assertNotIn("shared_state_revisions", claim)
        translation = self._translation_path()
        translation.parent.mkdir(parents=True, exist_ok=True)
        translation.write_text("# Один\n\nПеревод.\n", encoding="utf-8")

        accepted = json.loads(
            self.run_cli(
                "accept-translation",
                "sample",
                "1",
                "--session-id",
                "worker-a",
                "--json",
            ).stdout
        )
        self.assertTrue(accepted["changed"])
        progress = json.loads(
            (self.repo / "books" / "sample" / "progress.json").read_text(encoding="utf-8")
        )
        self.assertIsNone(progress["chapters"][0]["translation_acceptance"]["shared_state_revisions"])

    def test_authoritative_contract_requires_machine_translation_acceptance_before_release(self):
        text = (ROOT / "docs" / "ORCHESTRATION.md").read_text(encoding="utf-8")
        self.assertIn("accept-translation", text)
        self.assertIn("translation_acceptance", text)


if __name__ == "__main__":
    unittest.main()

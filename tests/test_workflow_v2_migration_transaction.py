import base64
import hashlib
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from workflow_v2 import FilesystemStorage, WorkflowStateRepository
from workflow_v2.coordination import BookCoordinationManager
from workflow_v2.migration_journal import MIGRATION_PATH, serialize_migration_journal
from workflow_v2.migrations import MigrationError, MigrationPlanner
from workflow_v2.storage import StorageError, StorageNotFound

try:
    from workflow_v2.migrations import MigrationExecutor, MigrationResult
except ImportError:
    MigrationExecutor = None
    MigrationResult = None


CANONICAL = "https://github.com/tim8es/book-translator"
OLD = "old-revision"
NEW = "new-revision"
REQUESTED = "refactor/workflow-engine-v2"
NOW = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)


class RecordingStorage:
    def __init__(self, inner):
        self.inner = inner
        self.events = []
        self.fail_once = None
        self._failed = False

    def _maybe_fail(self, operation, path):
        if self.fail_once == (operation, path) and not self._failed:
            self._failed = True
            raise StorageError(f"injected {operation} failure for {path}")

    def read(self, path):
        return self.inner.read(path)

    def write_if_version(self, path, content, expected_version):
        self.events.append(("write", path))
        self._maybe_fail("write", path)
        return self.inner.write_if_version(path, content, expected_version)

    def create_if_absent(self, path, content):
        self.events.append(("create", path))
        self._maybe_fail("create", path)
        return self.inner.create_if_absent(path, content)

    def delete_if_version(self, path, expected_version):
        self.events.append(("delete", path))
        self._maybe_fail("delete", path)
        return self.inner.delete_if_version(path, expected_version)

    def list(self, prefix=""):
        return self.inner.list(prefix)


class WorkflowV2MigrationTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.book_dir = Path(self.temp.name) / "books" / "legacy"
        for name in ("source", "extracted", "translated", "output", ".workflow/claims"):
            (self.book_dir / name).mkdir(parents=True, exist_ok=True)
        (self.book_dir / "source/legacy.md").write_bytes(b"source-book\n")
        (self.book_dir / "extracted/001-one.md").write_bytes(b"source chapter\n")
        (self.book_dir / "translated/001-one.md").write_bytes("перевод\n".encode("utf-8"))
        self._write_json("metadata.json", self._metadata())
        self._write_json("progress.json", self._progress())

        self.base_storage = FilesystemStorage(self.book_dir)
        self.storage = RecordingStorage(self.base_storage)
        self.repository = WorkflowStateRepository(self.storage)
        self.planner = MigrationPlanner(
            self.repository,
            book_dir=self.book_dir,
            artifact_reader=lambda path: (self.book_dir / path).read_bytes(),
            now=lambda: NOW,
        )
        self.coordination = BookCoordinationManager(
            self.repository,
            now=lambda: NOW,
            id_factory=lambda: "c" * 32,
        )

    def tearDown(self):
        self.temp.cleanup()

    def require_executor(self):
        self.assertIsNotNone(MigrationExecutor, "MigrationExecutor is not implemented")
        self.assertIsNotNone(MigrationResult, "MigrationResult is not implemented")

    @staticmethod
    def installed():
        return {
            "canonical_repository": CANONICAL,
            "requested_ref": REQUESTED,
            "resolved_revision": NEW,
        }

    @staticmethod
    def _metadata():
        return {
            "title": "Legacy",
            "author": "Author",
            "source_language": "en",
            "target_language": "ru",
            "source_format": "markdown",
            "source_file": "legacy.md",
            "chapter_count": 1,
            "imported_at": "2026-09-01T10:00:00+00:00",
            "workflow": {
                "repository": CANONICAL,
                "requested_ref": "legacy-ref",
                "resolved_revision": OLD,
            },
        }

    @staticmethod
    def _progress():
        return {
            "book_slug": "legacy",
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

    def _write_json(self, path, data):
        target = self.book_dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _plan(self):
        return self.planner.plan(slug="legacy", to_revision=NEW, installed=self.installed())

    def _executor(self):
        self.require_executor()
        return MigrationExecutor(
            self.repository,
            self.planner,
            coordination=self.coordination,
        )

    def _snapshot(self, *, transient=False):
        result = {}
        for path in sorted(self.book_dir.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(self.book_dir).as_posix()
            if not transient and relative in {
                MIGRATION_PATH,
                ".workflow/coordination-lock.json",
            }:
                continue
            result[relative] = path.read_bytes()
        return result

    @staticmethod
    def _journal_for(plan, *, phase="prepared", resulting=None):
        resulting = resulting or {}
        documents = []
        for write in plan.writes:
            original = write.original_bytes
            documents.append(
                {
                    "path": write.path,
                    "kind": write.kind.value,
                    "original_exists": write.original_exists,
                    "original_revision": write.original_version if write.original_exists else None,
                    "original_sha256": (
                        hashlib.sha256(original).hexdigest()
                        if write.original_exists and original is not None
                        else None
                    ),
                    "original_bytes_base64": (
                        base64.b64encode(original).decode("ascii")
                        if write.original_exists and original is not None
                        else None
                    ),
                    "target_sha256": hashlib.sha256(write.target_bytes).hexdigest(),
                    "resulting_revision": resulting.get(write.path),
                }
            )
        return {
            "schema_version": 1,
            "operation": "workflow_upgrade",
            "book_slug": plan.book_slug,
            "from_revision": plan.from_revision,
            "to_revision": plan.to_revision,
            "phase": phase,
            "documents": documents,
        }

    def _create_journal(self, data):
        return self.base_storage.create_if_absent(MIGRATION_PATH, serialize_migration_journal(data))

    def _apply_write_directly(self, write):
        if write.original_exists:
            return self.base_storage.write_if_version(
                write.path,
                write.target_bytes,
                write.original_version,
            )
        return self.base_storage.create_if_absent(write.path, write.target_bytes)

    def test_coordination_accepts_workflow_upgrade_operation(self):
        lease = self.coordination.acquire(operation="workflow_upgrade", session_id="upgrade-1")
        try:
            self.assertEqual(lease.data["operation"], "workflow_upgrade")
        finally:
            self.coordination.release(lease)

    def test_execute_journals_before_targets_updates_journal_and_writes_metadata_last(self):
        plan = self._plan()
        expected_paths = tuple(write.path for write in plan.writes)
        self.assertEqual(
            expected_paths,
            ("source-manifest.json", "review-ledger.json", "progress.json", "metadata.json"),
        )

        result = self._executor().execute(plan, session_id="upgrade-1")

        self.assertEqual(result.outcome, "changed")
        self.assertEqual(result.migrated_paths, expected_paths)
        with self.assertRaises(StorageNotFound):
            self.base_storage.read(MIGRATION_PATH)
        with self.assertRaises(StorageNotFound):
            self.base_storage.read(".workflow/coordination-lock.json")

        target_events = [
            path
            for operation, path in self.storage.events
            if path in expected_paths and operation in {"create", "write"}
        ]
        self.assertEqual(target_events, list(expected_paths))
        journal_events = [
            (index, operation)
            for index, (operation, path) in enumerate(self.storage.events)
            if path == MIGRATION_PATH
        ]
        self.assertTrue(journal_events)
        first_target = next(
            index for index, (_, path) in enumerate(self.storage.events) if path == expected_paths[0]
        )
        self.assertEqual(journal_events[0][1], "create")
        self.assertLess(journal_events[0][0], first_target)
        self.assertGreaterEqual(
            sum(1 for operation, path in self.storage.events if path == MIGRATION_PATH and operation == "write"),
            len(expected_paths) + 1,
            "journal must record every resulting revision and the final applied phase",
        )
        self.assertLess(
            next(index for index, event in enumerate(self.storage.events) if event[1] == "progress.json"),
            next(index for index, event in enumerate(self.storage.events) if event[1] == "metadata.json"),
        )

    def test_apply_failure_rolls_back_exact_originals_and_deletes_new_targets(self):
        plan = self._plan()
        before = self._snapshot()
        self.storage.fail_once = ("write", "progress.json")

        with self.assertRaises(MigrationError):
            self._executor().execute(plan, session_id="upgrade-1")

        self.assertEqual(self._snapshot(), before)
        with self.assertRaises(StorageNotFound):
            self.base_storage.read(MIGRATION_PATH)
        with self.assertRaises(StorageNotFound):
            self.base_storage.read("source-manifest.json")
        with self.assertRaises(StorageNotFound):
            self.base_storage.read("review-ledger.json")

    def test_recover_all_target_state_validates_and_only_removes_journal(self):
        plan = self._plan()
        resulting = {}
        for write in plan.writes:
            resulting[write.path] = self._apply_write_directly(write)
        self._create_journal(self._journal_for(plan, phase="applied", resulting=resulting))
        before = self._snapshot()

        result = self._executor().recover(session_id="upgrade-2", installed=self.installed())

        self.assertIsNotNone(result)
        self.assertEqual(result.outcome, "recovered")
        self.assertEqual(result.migrated_paths, tuple(write.path for write in plan.writes))
        self.assertEqual(self._snapshot(), before)
        with self.assertRaises(StorageNotFound):
            self.base_storage.read(MIGRATION_PATH)

    def test_recover_known_mixture_rolls_back_then_replans_and_executes(self):
        plan = self._plan()
        self._create_journal(self._journal_for(plan))
        self._apply_write_directly(plan.writes[0])

        result = self._executor().recover(session_id="upgrade-2", installed=self.installed())

        self.assertIsNotNone(result)
        self.assertEqual(result.outcome, "changed")
        self.assertEqual(result.migrated_paths, tuple(write.path for write in plan.writes))
        with self.assertRaises(StorageNotFound):
            self.base_storage.read(MIGRATION_PATH)
        final_plan = self.planner.plan(slug="legacy", to_revision=NEW, installed=self.installed())
        self.assertFalse(final_plan.changed)

    def test_recover_unknown_state_preserves_unknown_bytes_and_journal(self):
        plan = self._plan()
        self._create_journal(self._journal_for(plan))
        foreign = b"foreign concurrent bytes\n"
        metadata = self.base_storage.read("metadata.json")
        self.base_storage.write_if_version("metadata.json", foreign, metadata.version)
        journal_before = self.base_storage.read(MIGRATION_PATH)

        with self.assertRaises(MigrationError):
            self._executor().recover(session_id="upgrade-2", installed=self.installed())

        self.assertEqual(self.base_storage.read("metadata.json").content, foreign)
        self.assertEqual(self.base_storage.read(MIGRATION_PATH).content, journal_before.content)
        with self.assertRaises(StorageNotFound):
            self.base_storage.read("source-manifest.json")
        with self.assertRaises(StorageNotFound):
            self.base_storage.read("review-ledger.json")


if __name__ == "__main__":
    unittest.main()

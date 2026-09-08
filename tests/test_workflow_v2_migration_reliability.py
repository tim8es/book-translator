import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from workflow_v2 import FilesystemStorage, WorkflowStateRepository
from workflow_v2.coordination import BookCoordinationManager
from workflow_v2.migration_journal import MIGRATION_PATH, load_migration_journal
from workflow_v2.migrations import MigrationError, MigrationExecutor, MigrationPlanner
from workflow_v2.storage import StorageNotFound, StorageVersionConflict


CANONICAL = "https://github.com/tim8es/book-translator"
OLD = "old-revision"
NEW = "new-revision"
REQUESTED = "refactor/workflow-engine-v2"
NOW = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
RECOVERY_NOW = NOW + timedelta(minutes=2)


class SimulatedProcessCrash(BaseException):
    """Bypass executor Exception handlers to model abrupt process loss."""


class FaultStorage:
    def __init__(self, inner):
        self.inner = inner
        self.crash = None
        self.conflict = None
        self._crashed = False
        self._conflicted = False

    def read(self, path):
        return self.inner.read(path)

    def _before(self, operation, path):
        if self.crash == (operation, path) and not self._crashed:
            self._crashed = True
            raise SimulatedProcessCrash(f"crash at {operation}:{path}")
        if self.conflict == (operation, path) and not self._conflicted:
            self._conflicted = True
            raise StorageVersionConflict(f"injected conflict at {operation}:{path}")

    def write_if_version(self, path, content, expected_version):
        self._before("write", path)
        return self.inner.write_if_version(path, content, expected_version)

    def create_if_absent(self, path, content):
        self._before("create", path)
        return self.inner.create_if_absent(path, content)

    def delete_if_version(self, path, expected_version):
        self._before("delete", path)
        return self.inner.delete_if_version(path, expected_version)

    def list(self, prefix=""):
        return self.inner.list(prefix)


class WorkflowV2MigrationReliabilityTests(unittest.TestCase):
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

    def tearDown(self):
        self.temp.cleanup()

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

    def _runtime(self, storage, *, now=NOW):
        repository = WorkflowStateRepository(storage)
        planner = MigrationPlanner(
            repository,
            book_dir=self.book_dir,
            artifact_reader=lambda path: (self.book_dir / path).read_bytes(),
            now=lambda: now,
        )
        coordination = BookCoordinationManager(
            repository,
            now=lambda: now,
            id_factory=lambda: "d" * 32,
        )
        executor = MigrationExecutor(repository, planner, coordination=coordination)
        return repository, planner, executor

    def _plan(self, storage=None):
        _, planner, _ = self._runtime(storage or self.base_storage)
        return planner.plan(slug="legacy", to_revision=NEW, installed=self.installed())

    def _recover_fresh(self):
        _, planner, executor = self._runtime(self.base_storage, now=RECOVERY_NOW)
        result = executor.recover(session_id="recovery-session", installed=self.installed())
        return planner, result

    def _snapshot(self, *, include_transient=False):
        result = {}
        for path in sorted(self.book_dir.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(self.book_dir).as_posix()
            if not include_transient and relative in {
                MIGRATION_PATH,
                ".workflow/coordination-lock.json",
            }:
                continue
            result[relative] = path.read_bytes()
        return result

    def assert_converged(self, planner):
        final = planner.plan(slug="legacy", to_revision=NEW, installed=self.installed())
        self.assertFalse(final.changed)
        with self.assertRaises(StorageNotFound):
            self.base_storage.read(MIGRATION_PATH)
        with self.assertRaises(StorageNotFound):
            self.base_storage.read(".workflow/coordination-lock.json")

    def test_prepared_journal_with_no_target_writes_recovers_from_fresh_process(self):
        faults = FaultStorage(self.base_storage)
        faults.crash = ("create", "source-manifest.json")
        _, planner, executor = self._runtime(faults)
        plan = planner.plan(slug="legacy", to_revision=NEW, installed=self.installed())
        before = self._snapshot()

        with self.assertRaises(SimulatedProcessCrash):
            executor.execute(plan, session_id="crashed-session")

        journal, _ = load_migration_journal(self.base_storage)
        self.assertEqual(journal["phase"], "prepared")
        self.assertTrue(all(item["resulting_revision"] is None for item in journal["documents"]))
        self.assertEqual(self._snapshot(), before)

        fresh_planner, result = self._recover_fresh()
        self.assertIsNotNone(result)
        self.assertEqual(result.outcome, "changed")
        self.assert_converged(fresh_planner)

    def test_crash_after_manifest_and_ledger_rolls_back_replans_and_converges(self):
        faults = FaultStorage(self.base_storage)
        faults.crash = ("write", "progress.json")
        _, planner, executor = self._runtime(faults)
        plan = planner.plan(slug="legacy", to_revision=NEW, installed=self.installed())

        with self.assertRaises(SimulatedProcessCrash):
            executor.execute(plan, session_id="crashed-session")

        self.assertIsNotNone(self.base_storage.read("source-manifest.json"))
        self.assertIsNotNone(self.base_storage.read("review-ledger.json"))
        journal, _ = load_migration_journal(self.base_storage)
        states = {entry["path"]: entry["resulting_revision"] for entry in journal["documents"]}
        self.assertIsNotNone(states["source-manifest.json"])
        self.assertIsNotNone(states["review-ledger.json"])
        self.assertIsNone(states["progress.json"])
        self.assertIsNone(states["metadata.json"])

        fresh_planner, result = self._recover_fresh()
        self.assertIsNotNone(result)
        self.assertEqual(result.outcome, "changed")
        self.assert_converged(fresh_planner)

    def test_crash_after_metadata_before_journal_deletion_recovers_as_completed(self):
        faults = FaultStorage(self.base_storage)
        faults.crash = ("delete", MIGRATION_PATH)
        _, planner, executor = self._runtime(faults)
        plan = planner.plan(slug="legacy", to_revision=NEW, installed=self.installed())

        with self.assertRaises(SimulatedProcessCrash):
            executor.execute(plan, session_id="crashed-session")

        journal, _ = load_migration_journal(self.base_storage)
        self.assertEqual(journal["phase"], "applied")
        self.assertTrue(all(item["resulting_revision"] for item in journal["documents"]))

        fresh_planner, result = self._recover_fresh()
        self.assertIsNotNone(result)
        self.assertEqual(result.outcome, "recovered")
        self.assert_converged(fresh_planner)

    def test_cas_conflict_during_apply_restores_exact_original_state(self):
        faults = FaultStorage(self.base_storage)
        faults.conflict = ("write", "progress.json")
        _, planner, executor = self._runtime(faults)
        plan = planner.plan(slug="legacy", to_revision=NEW, installed=self.installed())
        before = self._snapshot()

        with self.assertRaises(MigrationError):
            executor.execute(plan, session_id="conflict-session")

        self.assertEqual(self._snapshot(), before)
        with self.assertRaises(StorageNotFound):
            self.base_storage.read(MIGRATION_PATH)
        with self.assertRaises(StorageNotFound):
            self.base_storage.read("source-manifest.json")
        with self.assertRaises(StorageNotFound):
            self.base_storage.read("review-ledger.json")

    def test_unknown_concurrent_mutation_after_crash_preserves_bytes_and_journal(self):
        faults = FaultStorage(self.base_storage)
        faults.crash = ("write", "progress.json")
        _, planner, executor = self._runtime(faults)
        plan = planner.plan(slug="legacy", to_revision=NEW, installed=self.installed())

        with self.assertRaises(SimulatedProcessCrash):
            executor.execute(plan, session_id="crashed-session")

        foreign = b"foreign concurrent metadata bytes\n"
        current = self.base_storage.read("metadata.json")
        self.base_storage.write_if_version("metadata.json", foreign, current.version)
        journal_before = self.base_storage.read(MIGRATION_PATH).content

        with self.assertRaises(MigrationError):
            self._recover_fresh()

        self.assertEqual(self.base_storage.read("metadata.json").content, foreign)
        self.assertEqual(self.base_storage.read(MIGRATION_PATH).content, journal_before)

    def test_completed_upgrade_rerun_is_byte_and_write_idempotent(self):
        _, planner, executor = self._runtime(self.base_storage)
        plan = planner.plan(slug="legacy", to_revision=NEW, installed=self.installed())
        first = executor.execute(plan, session_id="first-session")
        self.assertEqual(first.outcome, "changed")
        before = self._snapshot(include_transient=True)

        _, fresh_planner, fresh_executor = self._runtime(self.base_storage, now=RECOVERY_NOW)
        second_plan = fresh_planner.plan(slug="legacy", to_revision=NEW, installed=self.installed())
        self.assertFalse(second_plan.changed)
        second = fresh_executor.execute(second_plan, session_id="second-session")

        self.assertEqual(second.outcome, "unchanged")
        self.assertEqual(second.migrated_paths, ())
        self.assertEqual(self._snapshot(include_transient=True), before)


if __name__ == "__main__":
    unittest.main()

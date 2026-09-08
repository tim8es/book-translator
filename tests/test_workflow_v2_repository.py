import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from workflow_v2.filesystem import FilesystemStorage
from workflow_v2.schemas import SchemaError, SchemaKind
from workflow_v2.storage import StorageNotFound, StorageVersionConflict

try:
    from workflow_v2.repository import LoadedDocument, RepositoryError, WorkflowStateRepository
except ModuleNotFoundError:
    LoadedDocument = None
    RepositoryError = None
    WorkflowStateRepository = None


class WorkflowV2RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.storage = FilesystemStorage(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def require_api(self):
        self.assertIsNotNone(WorkflowStateRepository, "workflow_v2.repository is not implemented")

    def repo(self):
        self.require_api()
        return WorkflowStateRepository(self.storage)

    def metadata(self):
        return {
            "schema_version": 1,
            "title": "Пример",
            "target_language": "ru",
            "source_format": "markdown",
            "source_file": "example.md",
            "chapter_count": 0,
            "workflow": {},
        }

    def test_create_validates_and_serializes_utf8_json_deterministically(self):
        repo = self.repo()
        data = self.metadata()

        version = repo.create("metadata.json", SchemaKind.METADATA, data)
        stored = self.storage.read("metadata.json")

        expected = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        self.assertEqual(stored.content, expected)
        self.assertEqual(version, stored.version)
        self.assertIn("Пример".encode("utf-8"), stored.content)

    def test_invalid_create_is_rejected_before_storage_mutation(self):
        repo = self.repo()
        data = self.metadata()
        del data["title"]

        with self.assertRaises(SchemaError):
            repo.create("metadata.json", SchemaKind.METADATA, data)

        self.assertEqual(self.storage.list(), [])

    def test_read_returns_revision_and_legacy_flag_without_rewriting(self):
        repo = self.repo()
        legacy = self.metadata()
        del legacy["schema_version"]
        original = (json.dumps(legacy, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        version = self.storage.create_if_absent("metadata.json", original)

        loaded = repo.read("metadata.json", SchemaKind.METADATA, allow_legacy=True)

        self.assertIsInstance(loaded, LoadedDocument)
        self.assertTrue(loaded.legacy)
        self.assertEqual(loaded.version, version)
        self.assertEqual(loaded.data["schema_version"], 1)
        self.assertEqual(self.storage.read("metadata.json").content, original)

    def test_stale_repository_write_propagates_conflict_without_mutation(self):
        repo = self.repo()
        first = self.metadata()
        first_version = repo.create("metadata.json", SchemaKind.METADATA, first)

        second = dict(first)
        second["title"] = "Second"
        current_version = repo.write_if_version(
            "metadata.json",
            SchemaKind.METADATA,
            second,
            first_version,
        )

        stale = dict(first)
        stale["title"] = "Stale"
        with self.assertRaises(StorageVersionConflict):
            repo.write_if_version(
                "metadata.json",
                SchemaKind.METADATA,
                stale,
                first_version,
            )

        loaded = repo.read("metadata.json", SchemaKind.METADATA)
        self.assertEqual(loaded.data["title"], "Second")
        self.assertEqual(loaded.version, current_version)

    def test_delete_if_version_validates_and_removes_matching_document(self):
        repo = self.repo()
        self.assertTrue(
            hasattr(repo, "delete_if_version"),
            "workflow state repository must expose delete_if_version",
        )
        version = repo.create("metadata.json", SchemaKind.METADATA, self.metadata())

        repo.delete_if_version("metadata.json", SchemaKind.METADATA, version)

        with self.assertRaises(StorageNotFound):
            self.storage.read("metadata.json")

    def test_delete_if_version_rejects_stale_document_revision(self):
        repo = self.repo()
        self.assertTrue(
            hasattr(repo, "delete_if_version"),
            "workflow state repository must expose delete_if_version",
        )
        original = self.metadata()
        first = repo.create("metadata.json", SchemaKind.METADATA, original)
        replacement = dict(original)
        replacement["title"] = "Replacement"
        current = repo.write_if_version("metadata.json", SchemaKind.METADATA, replacement, first)

        with self.assertRaises(StorageVersionConflict):
            repo.delete_if_version("metadata.json", SchemaKind.METADATA, first)

        loaded = repo.read("metadata.json", SchemaKind.METADATA)
        self.assertEqual(loaded.version, current)
        self.assertEqual(loaded.data["title"], "Replacement")

    def test_invalid_json_error_identifies_logical_path(self):
        repo = self.repo()
        self.storage.create_if_absent("metadata.json", b"{not json}\n")

        with self.assertRaises(RepositoryError) as ctx:
            repo.read("metadata.json", SchemaKind.METADATA)

        self.assertIn("metadata.json", str(ctx.exception))
        self.assertIn("JSON", str(ctx.exception))

    def test_invalid_utf8_error_identifies_logical_path(self):
        repo = self.repo()
        self.storage.create_if_absent("metadata.json", b"\xff\xfe\xfa")

        with self.assertRaises(RepositoryError) as ctx:
            repo.read("metadata.json", SchemaKind.METADATA)

        self.assertIn("metadata.json", str(ctx.exception))
        self.assertIn("UTF-8", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

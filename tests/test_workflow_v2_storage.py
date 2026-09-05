import hashlib
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

try:
    from workflow_v2.filesystem import FilesystemStorage
    from workflow_v2.storage import (
        InvalidStoragePath,
        StorageAlreadyExists,
        StorageNotFound,
        StorageVersionConflict,
        StoredValue,
    )
except ModuleNotFoundError:
    FilesystemStorage = None
    InvalidStoragePath = None
    StorageAlreadyExists = None
    StorageNotFound = None
    StorageVersionConflict = None
    StoredValue = None


class WorkflowV2FilesystemStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def require_api(self):
        self.assertIsNotNone(FilesystemStorage, "workflow_v2 filesystem storage is not implemented")

    def storage(self):
        self.require_api()
        return FilesystemStorage(self.root)

    def test_create_and_read_expose_sha256_revision(self):
        storage = self.storage()
        content = "Привет\n".encode("utf-8")
        expected = hashlib.sha256(content).hexdigest()

        created_version = storage.create_if_absent("state/progress.json", content)
        loaded = storage.read("state/progress.json")

        self.assertEqual(created_version, expected)
        self.assertIsInstance(loaded, StoredValue)
        self.assertEqual(loaded.content, content)
        self.assertEqual(loaded.version, expected)

    def test_create_if_absent_never_overwrites_existing_content(self):
        storage = self.storage()
        original = b"original\n"
        storage.create_if_absent("progress.json", original)

        with self.assertRaises(StorageAlreadyExists):
            storage.create_if_absent("progress.json", b"replacement\n")

        self.assertEqual(storage.read("progress.json").content, original)

    def test_write_if_version_updates_only_matching_revision(self):
        storage = self.storage()
        first = b"one\n"
        second = b"two\n"
        first_version = storage.create_if_absent("progress.json", first)

        second_version = storage.write_if_version("progress.json", second, first_version)

        self.assertEqual(second_version, hashlib.sha256(second).hexdigest())
        self.assertEqual(storage.read("progress.json").content, second)
        self.assertNotEqual(first_version, second_version)

    def test_stale_write_is_rejected_without_mutation(self):
        storage = self.storage()
        first_version = storage.create_if_absent("progress.json", b"one\n")
        current_version = storage.write_if_version("progress.json", b"two\n", first_version)

        with self.assertRaises(StorageVersionConflict):
            storage.write_if_version("progress.json", b"stale writer\n", first_version)

        loaded = storage.read("progress.json")
        self.assertEqual(loaded.content, b"two\n")
        self.assertEqual(loaded.version, current_version)

    def test_delete_if_version_removes_only_matching_revision(self):
        storage = self.storage()
        self.assertTrue(
            hasattr(storage, "delete_if_version"),
            "filesystem storage must expose delete_if_version",
        )
        version = storage.create_if_absent("claims/chapter-000001.json", b"claim\n")

        storage.delete_if_version("claims/chapter-000001.json", version)

        with self.assertRaises(StorageNotFound):
            storage.read("claims/chapter-000001.json")

    def test_delete_if_version_rejects_stale_revision_without_mutation(self):
        storage = self.storage()
        self.assertTrue(
            hasattr(storage, "delete_if_version"),
            "filesystem storage must expose delete_if_version",
        )
        first = storage.create_if_absent("claims/chapter-000001.json", b"first\n")
        current = storage.write_if_version("claims/chapter-000001.json", b"second\n", first)

        with self.assertRaises(StorageVersionConflict):
            storage.delete_if_version("claims/chapter-000001.json", first)

        loaded = storage.read("claims/chapter-000001.json")
        self.assertEqual(loaded.content, b"second\n")
        self.assertEqual(loaded.version, current)

    def test_delete_if_version_missing_path_raises_not_found(self):
        storage = self.storage()
        self.assertTrue(
            hasattr(storage, "delete_if_version"),
            "filesystem storage must expose delete_if_version",
        )
        with self.assertRaises(StorageNotFound):
            storage.delete_if_version("missing.json", "deadbeef")

    def test_missing_read_and_write_raise_not_found(self):
        storage = self.storage()
        with self.assertRaises(StorageNotFound):
            storage.read("missing.json")
        with self.assertRaises(StorageNotFound):
            storage.write_if_version("missing.json", b"x", "deadbeef")

    def test_invalid_paths_are_rejected_before_touching_filesystem(self):
        storage = self.storage()
        invalid = [
            "",
            "/absolute.json",
            "../outside.json",
            "state/../outside.json",
            "./state.json",
            "state\\..\\outside.json",
        ]
        for path in invalid:
            with self.subTest(path=path):
                with self.assertRaises(InvalidStoragePath):
                    storage.create_if_absent(path, b"x")

        self.assertEqual(list(self.root.rglob("*")), [])

    def test_list_returns_sorted_relative_posix_regular_files(self):
        storage = self.storage()
        storage.create_if_absent("z.json", b"z")
        storage.create_if_absent("nested/b.json", b"b")
        storage.create_if_absent("nested/a.json", b"a")

        self.assertEqual(
            storage.list(),
            ["nested/a.json", "nested/b.json", "z.json"],
        )
        self.assertEqual(
            storage.list("nested"),
            ["nested/a.json", "nested/b.json"],
        )
        self.assertEqual(storage.list("missing"), [])

    def test_successful_replace_leaves_no_temporary_files(self):
        storage = self.storage()
        version = storage.create_if_absent("nested/progress.json", b"one")
        storage.write_if_version("nested/progress.json", b"two", version)

        files = sorted(path.relative_to(self.root).as_posix() for path in self.root.rglob("*") if path.is_file())
        self.assertEqual(files, ["nested/progress.json"])


if __name__ == "__main__":
    unittest.main()

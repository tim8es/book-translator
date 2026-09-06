import copy
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

try:
    from workflow_v2.schemas import SchemaError, SchemaKind, parse_document
except ModuleNotFoundError:
    SchemaError = None
    SchemaKind = None
    parse_document = None

try:
    from workflow_v2.migrations import (
        MigrationCompatibilityError,
        detect_schema_version,
        migrate_document,
    )
except ModuleNotFoundError:
    MigrationCompatibilityError = None
    detect_schema_version = None
    migrate_document = None


class WorkflowV2MigrationRegistryTests(unittest.TestCase):
    def require_api(self):
        self.assertIsNotNone(migrate_document, "workflow_v2.migrations is not implemented")
        self.assertIsNotNone(detect_schema_version, "detect_schema_version is not implemented")
        self.assertIsNotNone(MigrationCompatibilityError, "migration compatibility error is not implemented")
        self.assertIsNotNone(SchemaKind, "workflow_v2.schemas is unavailable")

    @staticmethod
    def metadata():
        return {
            "schema_version": 1,
            "title": "Legacy Example",
            "author": "Author",
            "source_language": "en",
            "target_language": "ru",
            "source_format": "markdown",
            "source_file": "legacy.md",
            "chapter_count": 1,
            "imported_at": "2026-09-01T10:00:00+00:00",
            "workflow": {
                "repository": "https://github.com/tim8es/book-translator",
                "requested_ref": "legacy-ref",
                "resolved_revision": "legacy-revision",
            },
        }

    @staticmethod
    def progress():
        return {
            "schema_version": 1,
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

    @staticmethod
    def ledger():
        return {
            "schema_version": 1,
            "book_slug": "legacy",
            "next_sequence": 1,
            "records": [],
        }

    @staticmethod
    def claim():
        return {
            "schema_version": 1,
            "claim_id": "0123456789abcdef0123456789abcdef",
            "unit_id": "chapter-000001",
            "role": "translator",
            "session_id": "session-1",
            "base_revision": "progress-revision",
            "base_commit": None,
            "workflow_revision": "legacy-revision",
            "claimed_at": "2026-09-01T10:00:00Z",
            "expires_at": "2026-09-01T11:00:00Z",
        }

    @staticmethod
    def source_manifest():
        return {
            "schema_version": 1,
            "source_file": "legacy.md",
            "source_format": "markdown",
            "source_sha256": "a" * 64,
            "chapter_count": 1,
            "extracted": [
                {
                    "number": 1,
                    "title": "One",
                    "path": "extracted/001-one.md",
                    "sha256": "b" * 64,
                }
            ],
        }

    def test_detect_schema_version_treats_missing_as_zero_and_rejects_non_integer(self):
        self.require_api()
        self.assertEqual(detect_schema_version({"title": "legacy"}), 0)
        self.assertEqual(detect_schema_version({"schema_version": 1}), 1)
        with self.assertRaises(MigrationCompatibilityError):
            detect_schema_version({"schema_version": "1"})

    def test_v0_supported_documents_add_only_schema_version_and_validate_as_v1(self):
        self.require_api()
        documents = {
            SchemaKind.METADATA: self.metadata(),
            SchemaKind.PROGRESS: self.progress(),
            SchemaKind.REVIEW_LEDGER: self.ledger(),
            SchemaKind.CLAIM: self.claim(),
            SchemaKind.SOURCE_MANIFEST: self.source_manifest(),
        }
        for kind, current in documents.items():
            with self.subTest(kind=kind):
                legacy = copy.deepcopy(current)
                del legacy["schema_version"]
                original = copy.deepcopy(legacy)
                result = migrate_document(kind, legacy)
                self.assertEqual(result.kind, kind)
                self.assertEqual(result.from_version, 0)
                self.assertEqual(result.to_version, 1)
                self.assertTrue(result.changed)
                self.assertEqual(result.data, current)
                self.assertEqual(legacy, original, "migration must not mutate caller data")
                self.assertEqual(parse_document(kind, result.data).data, current)

    def test_explicit_v1_is_validated_and_returned_unchanged(self):
        self.require_api()
        current = self.progress()
        result = migrate_document(SchemaKind.PROGRESS, current)
        self.assertEqual(result.from_version, 1)
        self.assertEqual(result.to_version, 1)
        self.assertFalse(result.changed)
        self.assertEqual(result.data, current)
        self.assertIsNot(result.data, current)

    def test_future_version_and_incomplete_v0_fail_precisely(self):
        self.require_api()
        future = self.metadata()
        future["schema_version"] = 2
        with self.assertRaises(MigrationCompatibilityError) as future_error:
            migrate_document(SchemaKind.METADATA, future)
        self.assertIn("unsupported", str(future_error.exception).lower())
        self.assertIn("2", str(future_error.exception))

        incomplete = {"claim_id": "0123456789abcdef0123456789abcdef"}
        with self.assertRaises(MigrationCompatibilityError) as legacy_error:
            migrate_document(SchemaKind.CLAIM, incomplete)
        self.assertIn("claim", str(legacy_error.exception).lower())
        self.assertIn("v0", str(legacy_error.exception).lower())


class WorkflowV2MigrationJournalSchemaTests(unittest.TestCase):
    def require_journal_schema(self):
        self.assertIsNotNone(SchemaKind)
        self.assertIsNotNone(parse_document)
        kind = getattr(SchemaKind, "MIGRATION_JOURNAL", None)
        self.assertIsNotNone(kind, "SchemaKind.MIGRATION_JOURNAL is not implemented")
        return kind

    @staticmethod
    def valid_journal():
        original = b'{"legacy": true}\n'
        import base64
        import hashlib

        return {
            "schema_version": 1,
            "operation": "workflow_upgrade",
            "book_slug": "legacy",
            "from_revision": "old-revision",
            "to_revision": "new-revision",
            "phase": "prepared",
            "documents": [
                {
                    "path": "metadata.json",
                    "kind": "metadata",
                    "original_exists": True,
                    "original_revision": "old-storage-revision",
                    "original_sha256": hashlib.sha256(original).hexdigest(),
                    "original_bytes_base64": base64.b64encode(original).decode("ascii"),
                    "target_sha256": "c" * 64,
                    "resulting_revision": None,
                },
                {
                    "path": "review-ledger.json",
                    "kind": "review_ledger",
                    "original_exists": False,
                    "original_revision": None,
                    "original_sha256": None,
                    "original_bytes_base64": None,
                    "target_sha256": "d" * 64,
                    "resulting_revision": None,
                },
            ],
        }

    def test_valid_prepared_journal_parses(self):
        kind = self.require_journal_schema()
        journal = self.valid_journal()
        parsed = parse_document(kind, journal)
        self.assertEqual(parsed.data, journal)

    def test_journal_rejects_invalid_phase_operation_and_unsafe_path(self):
        kind = self.require_journal_schema()
        for field, value in (
            ("phase", "rolling-back"),
            ("operation", "finalize"),
        ):
            with self.subTest(field=field):
                invalid = self.valid_journal()
                invalid[field] = value
                with self.assertRaises(SchemaError):
                    parse_document(kind, invalid)

        invalid = self.valid_journal()
        invalid["documents"][0]["path"] = "../metadata.json"
        with self.assertRaises(SchemaError):
            parse_document(kind, invalid)

    def test_journal_rejects_bad_hash_base64_and_original_identity_combinations(self):
        kind = self.require_journal_schema()

        bad_hash = self.valid_journal()
        bad_hash["documents"][0]["target_sha256"] = "not-a-hash"
        with self.assertRaises(SchemaError):
            parse_document(kind, bad_hash)

        bad_base64 = self.valid_journal()
        bad_base64["documents"][0]["original_bytes_base64"] = "***not-base64***"
        with self.assertRaises(SchemaError):
            parse_document(kind, bad_base64)

        inconsistent_missing = self.valid_journal()
        inconsistent_missing["documents"][1]["original_revision"] = "should-be-null"
        with self.assertRaises(SchemaError):
            parse_document(kind, inconsistent_missing)

        inconsistent_existing = self.valid_journal()
        inconsistent_existing["documents"][0]["original_sha256"] = None
        with self.assertRaises(SchemaError):
            parse_document(kind, inconsistent_existing)

    def test_applied_journal_requires_resulting_revision_for_every_document(self):
        kind = self.require_journal_schema()
        applied = self.valid_journal()
        applied["phase"] = "applied"
        with self.assertRaises(SchemaError):
            parse_document(kind, applied)

        for entry in applied["documents"]:
            entry["resulting_revision"] = "new-storage-revision"
        parsed = parse_document(kind, applied)
        self.assertEqual(parsed.data["phase"], "applied")


if __name__ == "__main__":
    unittest.main()

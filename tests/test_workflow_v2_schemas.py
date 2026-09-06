import copy
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

try:
    from workflow_v2.schemas import (
        ParsedDocument,
        SchemaError,
        SchemaKind,
        UnsupportedSchemaVersion,
        parse_document,
    )
except ModuleNotFoundError:
    ParsedDocument = None
    SchemaError = None
    SchemaKind = None
    UnsupportedSchemaVersion = None
    parse_document = None


class WorkflowV2SchemaTests(unittest.TestCase):
    def require_api(self):
        self.assertIsNotNone(parse_document, "workflow_v2.schemas is not implemented")

    def valid_metadata(self):
        return {
            "schema_version": 1,
            "title": "Example",
            "author": "Author",
            "source_language": "en",
            "target_language": "ru",
            "source_format": "markdown",
            "source_file": "example.md",
            "chapter_count": 2,
            "imported_at": "2026-09-05T12:00:00+00:00",
            "workflow": {
                "repository": "https://github.com/tim8es/book-translator",
                "requested_ref": "refactor/workflow-engine-v2",
                "resolved_revision": "abc123",
            },
        }

    def valid_progress(self):
        return {
            "schema_version": 1,
            "book_slug": "example",
            "chapters": [
                {
                    "number": 1,
                    "title": "One",
                    "slug": "one",
                    "source_path": "extracted/001-one.md",
                    "translation_path": "translated/001-one.md",
                    "status": "extracted",
                }
            ],
        }

    def valid_claim(self):
        return {
            "schema_version": 1,
            "claim_id": "0123456789abcdef0123456789abcdef",
            "unit_id": "chapter-000001",
            "role": "translator",
            "session_id": "session-1",
            "base_revision": "state-rev-1",
            "base_commit": None,
            "workflow_revision": "workflow-rev-1",
            "claimed_at": "2026-09-05T12:00:00Z",
            "expires_at": "2026-09-05T12:30:00Z",
        }

    def test_all_schema_kinds_parse_version_one(self):
        self.require_api()
        documents = {
            SchemaKind.METADATA: self.valid_metadata(),
            SchemaKind.PROGRESS: self.valid_progress(),
            SchemaKind.CLAIM: self.valid_claim(),
            SchemaKind.REVIEW_LEDGER: {
                "schema_version": 1,
                "book_slug": "example",
                "next_sequence": 1,
                "records": [],
            },
            SchemaKind.SOURCE_MANIFEST: {
                "schema_version": 1,
                "source_file": "example.md",
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
            },
            SchemaKind.GENERATED_STATE: {
                "schema_version": 1,
                "book_slug": "example",
                "source_revision": "state-rev-1",
                "generated_at": "2026-09-05T12:00:00+00:00",
                "data": {"status": "in_progress"},
            },
        }

        for kind, data in documents.items():
            with self.subTest(kind=kind):
                parsed = parse_document(kind, data)
                self.assertIsInstance(parsed, ParsedDocument)
                self.assertFalse(parsed.legacy)
                self.assertEqual(parsed.data, data)

    def test_claim_requires_identity_canonical_unit_and_valid_lease_interval(self):
        self.require_api()
        parsed = parse_document(SchemaKind.CLAIM, self.valid_claim())
        self.assertEqual(parsed.data["unit_id"], "chapter-000001")

        missing_identity = self.valid_claim()
        del missing_identity["claim_id"]
        with self.assertRaises(SchemaError):
            parse_document(SchemaKind.CLAIM, missing_identity)

        bad_unit = self.valid_claim()
        bad_unit["unit_id"] = "chapter:1"
        with self.assertRaises(SchemaError):
            parse_document(SchemaKind.CLAIM, bad_unit)

        reversed_lease = self.valid_claim()
        reversed_lease["expires_at"] = reversed_lease["claimed_at"]
        with self.assertRaises(SchemaError):
            parse_document(SchemaKind.CLAIM, reversed_lease)

        non_utc = self.valid_claim()
        non_utc["claimed_at"] = "2026-09-05T14:00:00+02:00"
        non_utc["expires_at"] = "2026-09-05T14:30:00+02:00"
        with self.assertRaises(SchemaError):
            parse_document(SchemaKind.CLAIM, non_utc)

        bad_commit = self.valid_claim()
        bad_commit["base_commit"] = {"sha": "abc"}
        with self.assertRaises(SchemaError):
            parse_document(SchemaKind.CLAIM, bad_commit)

    def test_claim_event_schema_validates_request_and_completion_shapes(self):
        self.require_api()
        self.assertTrue(hasattr(SchemaKind, "CLAIM_EVENT"), "claim_event schema kind is required")
        claim = self.valid_claim()
        request = {
            "schema_version": 1,
            "event_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "action": "cleanup_requested",
            "unit_id": claim["unit_id"],
            "claim_revision": "claim-revision",
            "claim": claim,
            "occurred_at": "2026-09-05T13:00:00Z",
            "reason": "lease_expired",
        }
        completion = {
            "schema_version": 1,
            "event_id": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "action": "cleaned",
            "unit_id": claim["unit_id"],
            "request_event_id": request["event_id"],
            "occurred_at": "2026-09-05T13:00:01Z",
        }

        self.assertEqual(parse_document(SchemaKind.CLAIM_EVENT, request).data, request)
        self.assertEqual(parse_document(SchemaKind.CLAIM_EVENT, completion).data, completion)

        invalid = dict(completion)
        invalid["action"] = "deleted"
        with self.assertRaises(SchemaError):
            parse_document(SchemaKind.CLAIM_EVENT, invalid)

    def test_unknown_additional_fields_are_preserved(self):
        self.require_api()
        data = self.valid_metadata()
        data["future_field"] = {"enabled": True}
        parsed = parse_document(SchemaKind.METADATA, data)
        self.assertEqual(parsed.data["future_field"], {"enabled": True})

    def test_unsupported_explicit_version_is_rejected(self):
        self.require_api()
        data = self.valid_metadata()
        data["schema_version"] = 2
        with self.assertRaises(UnsupportedSchemaVersion):
            parse_document(SchemaKind.METADATA, data, allow_legacy=True)

    def test_missing_required_field_is_rejected_precisely(self):
        self.require_api()
        data = self.valid_progress()
        del data["book_slug"]
        with self.assertRaises(SchemaError) as ctx:
            parse_document(SchemaKind.PROGRESS, data)
        self.assertIn("book_slug", str(ctx.exception))

    def test_progress_rejects_invalid_status_and_unsafe_paths(self):
        self.require_api()
        bad_status = self.valid_progress()
        bad_status["chapters"][0]["status"] = "done"
        with self.assertRaises(SchemaError):
            parse_document(SchemaKind.PROGRESS, bad_status)

        bad_path = self.valid_progress()
        bad_path["chapters"][0]["source_path"] = "../outside.md"
        with self.assertRaises(SchemaError):
            parse_document(SchemaKind.PROGRESS, bad_path)

    def test_source_manifest_rejects_invalid_hash(self):
        self.require_api()
        data = {
            "schema_version": 1,
            "source_file": "example.md",
            "source_format": "markdown",
            "source_sha256": "not-a-hash",
            "chapter_count": 0,
            "extracted": [],
        }
        with self.assertRaises(SchemaError) as ctx:
            parse_document(SchemaKind.SOURCE_MANIFEST, data)
        self.assertIn("source_sha256", str(ctx.exception))

    def test_explicit_source_contract_is_enforced_by_schema_api(self):
        self.require_api()
        data = self.valid_metadata()
        data["source"] = {
            "storage_mode": "private_external",
            "filename": "wrong.md",
            "size_bytes": 10,
            "sha256": "a" * 64,
        }
        with self.assertRaises(SchemaError) as ctx:
            parse_document(SchemaKind.METADATA, data)
        self.assertIn("source.filename", str(ctx.exception))

        manifest = {
            "schema_version": 1,
            "source_file": "example.md",
            "source_format": "markdown",
            "source_sha256": "a" * 64,
            "source_storage_mode": "private_external",
            "chapter_count": 0,
            "extracted": [],
        }
        with self.assertRaises(SchemaError) as ctx:
            parse_document(SchemaKind.SOURCE_MANIFEST, manifest)
        self.assertIn("source_size_bytes", str(ctx.exception))

    def test_legacy_metadata_and_progress_are_normalized_in_memory_only(self):
        self.require_api()
        for kind, original in (
            (SchemaKind.METADATA, self.valid_metadata()),
            (SchemaKind.PROGRESS, self.valid_progress()),
        ):
            with self.subTest(kind=kind):
                legacy = copy.deepcopy(original)
                del legacy["schema_version"]
                parsed = parse_document(kind, legacy, allow_legacy=True)
                self.assertTrue(parsed.legacy)
                self.assertEqual(parsed.data["schema_version"], 1)
                self.assertNotIn("schema_version", legacy)

    def test_missing_version_is_rejected_for_non_legacy_schema_kinds(self):
        self.require_api()
        claim = self.valid_claim()
        del claim["schema_version"]
        with self.assertRaises(SchemaError):
            parse_document(SchemaKind.CLAIM, claim, allow_legacy=True)


if __name__ == "__main__":
    unittest.main()
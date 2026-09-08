import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from workflow_v2.filesystem import FilesystemStorage
from workflow_v2.repository import WorkflowStateRepository
from workflow_v2.schemas import SchemaError, SchemaKind, parse_document


class WorkflowV2FinalizeSchemaTests(unittest.TestCase):
    def coordination(self):
        return {
            "schema_version": 1,
            "lock_id": "a" * 32,
            "operation": "claim_admission",
            "session_id": "session-a",
            "acquired_at": "2026-09-06T20:00:00Z",
            "expires_at": "2026-09-06T20:01:00Z",
        }

    def finalization(self):
        return {
            "schema_version": 1,
            "lock_id": "b" * 32,
            "book_slug": "demo",
            "workflow_revision": "workflow-rev",
            "base_progress_revision": "base-rev",
            "candidate_progress_sha256": "c" * 64,
            "phase": "preparing",
            "promoted_progress_revision": None,
            "session_id": "session-a",
            "started_at": "2026-09-06T20:00:00Z",
        }

    def progress(self):
        return {
            "schema_version": 1,
            "book_slug": "demo",
            "chapters": [
                {
                    "number": 1,
                    "title": "One",
                    "slug": "one",
                    "source_path": "extracted/001.md",
                    "translation_path": "translated/001.md",
                    "status": "translated",
                }
            ],
        }

    def test_lock_schema_kinds_accept_valid_documents(self):
        self.assertTrue(
            hasattr(SchemaKind, "COORDINATION_LOCK"),
            "coordination lock schema kind is not implemented",
        )
        self.assertTrue(
            hasattr(SchemaKind, "FINALIZATION_LOCK"),
            "finalization lock schema kind is not implemented",
        )
        self.assertEqual(
            parse_document(SchemaKind.COORDINATION_LOCK, self.coordination()).data,
            self.coordination(),
        )
        self.assertEqual(
            parse_document(SchemaKind.FINALIZATION_LOCK, self.finalization()).data,
            self.finalization(),
        )

    def test_lock_schemas_reject_invalid_operation_interval_hash_and_phase(self):
        self.assertTrue(
            hasattr(SchemaKind, "COORDINATION_LOCK") and hasattr(SchemaKind, "FINALIZATION_LOCK"),
            "finalize lock schema kinds are not implemented",
        )

        bad_operation = self.coordination()
        bad_operation["operation"] = "translate"
        with self.assertRaises(SchemaError):
            parse_document(SchemaKind.COORDINATION_LOCK, bad_operation)

        bad_interval = self.coordination()
        bad_interval["expires_at"] = bad_interval["acquired_at"]
        with self.assertRaises(SchemaError):
            parse_document(SchemaKind.COORDINATION_LOCK, bad_interval)

        bad_hash = self.finalization()
        bad_hash["candidate_progress_sha256"] = "not-a-hash"
        with self.assertRaises(SchemaError):
            parse_document(SchemaKind.FINALIZATION_LOCK, bad_hash)

        preparing_with_revision = self.finalization()
        preparing_with_revision["promoted_progress_revision"] = "progress-rev"
        with self.assertRaises(SchemaError):
            parse_document(SchemaKind.FINALIZATION_LOCK, preparing_with_revision)

        promoted_without_revision = self.finalization()
        promoted_without_revision["phase"] = "promoted"
        with self.assertRaises(SchemaError):
            parse_document(SchemaKind.FINALIZATION_LOCK, promoted_without_revision)

        valid_promoted = self.finalization()
        valid_promoted["phase"] = "promoted"
        valid_promoted["promoted_progress_revision"] = "progress-rev"
        self.assertEqual(
            parse_document(SchemaKind.FINALIZATION_LOCK, valid_promoted).data,
            valid_promoted,
        )

    def test_repository_public_serializer_matches_persisted_bytes_and_validates(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = FilesystemStorage(Path(tmp))
            repository = WorkflowStateRepository(storage)
            self.assertTrue(
                hasattr(repository, "serialize"),
                "workflow state repository public serializer is not implemented",
            )
            progress = self.progress()
            serialized = repository.serialize("progress.json", SchemaKind.PROGRESS, progress)
            version = repository.create("progress.json", SchemaKind.PROGRESS, progress)
            stored = storage.read("progress.json")

            expected = (json.dumps(progress, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            self.assertEqual(serialized, expected)
            self.assertEqual(serialized, stored.content)
            self.assertEqual(version, stored.version)

            invalid = self.progress()
            invalid["chapters"][0]["status"] = "done"
            with self.assertRaises(SchemaError):
                repository.serialize("invalid.json", SchemaKind.PROGRESS, invalid)
            self.assertNotIn("invalid.json", storage.list())


if __name__ == "__main__":
    unittest.main()

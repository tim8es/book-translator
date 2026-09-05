import copy
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from workflow_v2.schemas import SchemaError, SchemaKind, parse_document


class WorkflowV2ReviewLedgerSchemaTests(unittest.TestCase):
    def record(self, *, record_id="0" * 31 + "1", sequence=1, unit_id="chapter-000001", outcome="PASS", supersedes=None):
        return {
            "record_id": record_id,
            "sequence": sequence,
            "unit_id": unit_id,
            "outcome": outcome,
            "source_sha256": "a" * 64,
            "translation_sha256": "b" * 64,
            "workflow_revision": "0123456789abcdef",
            "review_contract_revision": "docs/TRANSLATION.md@0123456789abcdef",
            "reviewer_session_id": "reviewer-a",
            "reviewed_at": "2026-09-06T00:00:00Z",
            "state_revision": "progress-revision",
            "review_commit": None,
            "correction_round": 0 if outcome == "PASS" else 1,
            "supersedes_record_id": supersedes,
        }

    def ledger(self):
        return {
            "schema_version": 1,
            "book_slug": "sample",
            "next_sequence": 2,
            "records": [self.record()],
        }

    def test_accepts_strict_version_one_ledger(self):
        ledger = self.ledger()
        self.assertEqual(parse_document(SchemaKind.REVIEW_LEDGER, ledger).data, ledger)

    def test_requires_next_sequence_to_follow_stored_history(self):
        ledger = self.ledger()
        ledger["next_sequence"] = 9
        with self.assertRaises(SchemaError):
            parse_document(SchemaKind.REVIEW_LEDGER, ledger)

        empty = {
            "schema_version": 1,
            "book_slug": "sample",
            "next_sequence": 2,
            "records": [],
        }
        with self.assertRaises(SchemaError):
            parse_document(SchemaKind.REVIEW_LEDGER, empty)

    def test_rejects_duplicate_record_ids_and_sequences(self):
        ledger = self.ledger()
        first = ledger["records"][0]
        second = self.record(record_id=first["record_id"], sequence=2, supersedes=first["record_id"])
        ledger["records"].append(second)
        ledger["next_sequence"] = 3
        with self.assertRaises(SchemaError):
            parse_document(SchemaKind.REVIEW_LEDGER, ledger)

        ledger = self.ledger()
        first = ledger["records"][0]
        ledger["records"].append(
            self.record(record_id="0" * 31 + "2", sequence=1, supersedes=first["record_id"])
        )
        ledger["next_sequence"] = 2
        with self.assertRaises(SchemaError):
            parse_document(SchemaKind.REVIEW_LEDGER, ledger)

    def test_rejects_broken_or_cross_unit_supersession(self):
        ledger = self.ledger()
        first = ledger["records"][0]
        ledger["records"].append(
            self.record(record_id="0" * 31 + "2", sequence=2, supersedes="f" * 32)
        )
        ledger["next_sequence"] = 3
        with self.assertRaises(SchemaError):
            parse_document(SchemaKind.REVIEW_LEDGER, ledger)

        ledger = self.ledger()
        first = ledger["records"][0]
        ledger["records"].append(
            self.record(
                record_id="0" * 31 + "2",
                sequence=2,
                unit_id="chapter-000002",
                supersedes=first["record_id"],
            )
        )
        ledger["next_sequence"] = 3
        with self.assertRaises(SchemaError):
            parse_document(SchemaKind.REVIEW_LEDGER, ledger)

    def test_rejects_non_monotonic_history_and_invalid_record_fields(self):
        ledger = self.ledger()
        first = ledger["records"][0]
        ledger["records"].append(
            self.record(record_id="0" * 31 + "2", sequence=3, supersedes=first["record_id"])
        )
        ledger["next_sequence"] = 4
        with self.assertRaises(SchemaError):
            parse_document(SchemaKind.REVIEW_LEDGER, ledger)

        mutations = {
            "record_id": "not-an-id",
            "unit_id": "chapter-1",
            "outcome": "MAYBE",
            "source_sha256": "bad",
            "translation_sha256": "bad",
            "reviewed_at": "2026-09-06T03:00:00+03:00",
            "state_revision": "",
            "correction_round": -1,
            "review_commit": {"sha": "abc"},
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                invalid = copy.deepcopy(self.ledger())
                invalid["records"][0][field] = value
                with self.assertRaises(SchemaError):
                    parse_document(SchemaKind.REVIEW_LEDGER, invalid)


if __name__ == "__main__":
    unittest.main()

import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))
import paired_quality_qualification as pair


class PairProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.target = "Четыре пуговицы."
        self.hashes = {"source_sha256": "a" * 64,
                       "translation_sha256": pair.export.sha(self.target.encode("utf-8"))}
        self.chapters = [{"number": 1, "unit_id": "chapter-000001",
                         "translation_acceptance": dict(self.hashes, session_id="chat-translator")}]
        self.records = [dict(self.hashes, unit_id="chapter-000001", sequence=1,
                             outcome="PASS", reviewer_session_id="chat-reviewer")]
        self.requests = [dict(self.hashes, chapter=1, session_id="chat-translator",
                              operation="accept-translation", translation=self.target),
                         dict(self.hashes, chapter=1, session_id="chat-reviewer",
                              operation="review-record", outcome="PASS")]

    def verify(self):
        pair.verify_receipts(self.chapters, self.records, self.requests)

    def test_matching_canonical_receipts(self):
        self.verify()

    def test_reject_wrong_bytes_or_missing_receipt(self):
        self.requests[0]["translation"] = "Изменено"
        with self.assertRaisesRegex(ValueError, "translation receipt"):
            self.verify()
        self.requests[0]["translation"] = self.target
        self.requests.pop()
        with self.assertRaisesRegex(ValueError, "PASS receipt"):
            self.verify()

    def test_reject_same_session_or_superseding_rejection(self):
        self.records[0]["reviewer_session_id"] = "chat-translator"
        with self.assertRaises(ValueError):
            self.verify()
        self.records[0]["reviewer_session_id"] = "chat-reviewer"
        self.records.append(dict(self.records[0], sequence=2, outcome="CORRECTIONS_REQUIRED"))
        with self.assertRaises(ValueError):
            self.verify()

    def test_preflight_failure_prevents_auto_calls(self):
        args = Mock(executor_revision="a" * 40)
        report = {}
        with patch.object(pair.export, "git", return_value="a" * 40), \
             patch.object(pair, "admit_chatops", side_effect=ValueError("not ready")), \
             patch.object(pair.qualification, "step") as step:
            with self.assertRaises(ValueError):
                pair.run(args, report)
        step.assert_not_called()
        self.assertEqual(report["stage"], "chatops-preflight")

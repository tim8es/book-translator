import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from workflow.review_queue import next_review


def book():
    return {
        "valid": True, "book_slug": "example", "workflow_revision": "rev",
        "corpus": {"source_sha256": "a" * 64},
        "finalization": {"active": False}, "claims": [],
        "state_revisions": {"glossary": "g", "style_guide": "s"},
        "units": [
            {"unit_id": "chapter-000001", "chapter_number": 1,
             "lifecycle": "extracted", "review": "untranslated"},
            {"unit_id": "chapter-000002", "chapter_number": 2,
             "lifecycle": "translated", "review": "missing"},
            {"unit_id": "chapter-000003", "chapter_number": 3,
             "lifecycle": "translated", "review": "missing"},
        ],
    }


class ReviewQueueTests(unittest.TestCase):
    def test_live_queue_reveals_review_only_after_accepted_translation(self):
        status = book()
        result = next_review(status, "reviewer-a")
        self.assertEqual(result["unit_id"], "chapter-000002")
        self.assertTrue(result["advisory_only"])
        status["claims"] = [{"unit_id": "chapter-000002", "role": "reviewer",
                             "session_id": "reviewer-b",
                             "expires_at": "2999-01-01T00:00:00Z"}]
        self.assertEqual(next_review(status, "reviewer-a")["unit_id"], "chapter-000003")
        self.assertEqual(next_review(status, "reviewer-b")["action"], "work")

    def test_expired_own_claim_does_not_return_work(self):
        status = book()
        status["claims"] = [{
            "unit_id": "chapter-000002", "role": "reviewer",
            "session_id": "reviewer-a", "expires_at": "2020-01-01T00:00:00Z",
        }]
        result = next_review(status, "reviewer-a")
        self.assertEqual(result["action"], "blocked")
        self.assertEqual(result["reason"], "claim_expired")

    def test_scope_blocks_when_wave_changes_and_filters_units(self):
        status = book()
        wave = {
            "book_slug": "example", "workflow_revision": "rev",
            "source_sha256": "a" * 64, "shared_state_revisions": {
                "glossary": "g", "style_guide": "s",
            }, "unit_ids": ["chapter-000003"],
        }
        self.assertEqual(next_review(status, "r", wave=wave)["unit_id"], "chapter-000003")
        wave["source_sha256"] = "b" * 64
        self.assertEqual(next_review(status, "r", wave=wave)["action"], "blocked")

    def test_wait_complete_and_fail_closed(self):
        status = book()
        status["units"][1]["lifecycle"] = "reviewed"
        status["units"][2]["lifecycle"] = "reviewed"
        self.assertEqual(next_review(status, "r")["action"], "wait")
        status["units"][0]["lifecycle"] = "reviewed"
        self.assertEqual(next_review(status, "r")["action"], "complete")
        status["valid"] = False
        self.assertEqual(next_review(status, "r")["action"], "blocked")
        with self.assertRaises(ValueError):
            next_review(status, "")


if __name__ == "__main__":
    unittest.main()

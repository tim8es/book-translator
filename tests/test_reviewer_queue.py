import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from workflow.review_queue import next_review


def state():
    return {
        "valid": True, "book_slug": "sample", "claims": [],
        "units": [
            {"unit_id": "chapter-000001", "chapter_number": 1, "lifecycle": "extracted", "review": "untranslated"},
            {"unit_id": "chapter-000002", "chapter_number": 2, "lifecycle": "translated", "review": "missing"},
            {"unit_id": "chapter-000003", "chapter_number": 3, "lifecycle": "translated", "review": "missing"},
        ],
    }


class ReviewerQueueTests(unittest.TestCase):
    def test_rolling_queue_prioritizes_first_accepted_translation(self):
        s = state()
        self.assertEqual(next_review(s, "reviewer-a")["unit_id"], "chapter-000002")
        s["claims"] = [{"unit_id": "chapter-000002", "role": "reviewer", "session_id": "other"}]
        self.assertEqual(next_review(s, "reviewer-a")["unit_id"], "chapter-000003")
        s["claims"][0]["session_id"] = "reviewer-a"
        s["claims"][0]["expires_at"] = "2099-01-01T00:00:00Z"
        self.assertEqual(next_review(s, "reviewer-a")["action"], "work")
        s["claims"][0]["expires_at"] = "2020-01-01T00:00:00Z"
        self.assertEqual(next_review(s, "reviewer-a")["reason"], "claim_expired")

    def test_wave_scoped_and_wait_without_accepted_work(self):
        s = state()
        self.assertEqual(next_review(s, "r", wave_units={"chapter-000001"})["action"], "wait")
        self.assertEqual(next_review(s, "r", wave_units={"chapter-000003"})["unit_id"], "chapter-000003")
        s["units"][2]["lifecycle"] = "reviewed"
        self.assertEqual(next_review(s, "r", wave_units={"chapter-000003"})["action"], "complete")

    def test_requested_corrections_belong_to_translator_not_reviewer(self):
        s = state()
        s["units"][1]["review"] = "corrections_required"
        self.assertEqual(next_review(s, "reviewer-a")["unit_id"], "chapter-000003")
        s["units"][2]["lifecycle"] = "reviewed"
        self.assertEqual(next_review(s, "reviewer-a")["action"], "wait")

    def test_invalid_preflight_blocks(self):
        s = state()
        s["valid"] = False
        self.assertEqual(next_review(s, "r")["action"], "blocked")


if __name__ == "__main__":
    unittest.main()

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from workflow.review_delta import compare


class ReviewDeltaTests(unittest.TestCase):
    def test_local_change_gives_bounded_candidate(self):
        before = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph.\n\nFourth paragraph.".encode()
        after = "First paragraph.\n\nEdited paragraph.\n\nThird paragraph.\n\nFourth paragraph.".encode()
        result = compare(before, after)
        self.assertEqual(result["mode"], "focused_candidate")
        self.assertEqual(result["change_groups"], 1)
        self.assertEqual(result["sections"][0]["preceding_context"], "First paragraph.")
        self.assertEqual(result["sections"][0]["following_context"], "Third paragraph.")

    def test_full_recheck_on_major_rewrite_and_same_text(self):
        before = b"alpha" * 150
        self.assertEqual(compare(before, b"beta" * 150)["mode"], "full")
        self.assertEqual(compare(before, before)["mode"], "full")


if __name__ == "__main__":
    unittest.main()

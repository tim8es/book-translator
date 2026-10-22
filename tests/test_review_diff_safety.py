import hashlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from workflow.review_diff import plan


def h(value):
    return hashlib.sha256(value).hexdigest()


class ReviewDiffSafetyTests(unittest.TestCase):
    def test_bounded_change_is_only_advisory(self):
        old = ("one\ntwo\nthree\nfour\nfive\nsix\nseven\neight\nnine\nten\n").encode()
        new = old.replace(b"five", b"corrected")
        result = plan(old, new, previous_sha256=h(old), current_sha256=h(new))
        self.assertTrue(result["scoped_review_candidate"])
        self.assertTrue(result["advisory_only"])
        self.assertEqual(len(result["changed_regions"]), 1)
        self.assertNotIn("outcome", result)

    def test_changed_baseline_or_current_bytes_fail_closed(self):
        old, new = b"hello\n", b"world\n"
        with self.assertRaisesRegex(ValueError, "pinned"):
            plan(old, new, previous_sha256=h(new), current_sha256=h(new))
        with self.assertRaisesRegex(ValueError, "pinned"):
            plan(old, new, previous_sha256=h(old), current_sha256=h(old))

    def test_broad_rewrite_requires_full_review(self):
        old = ("\n".join(f"line-{i}" for i in range(20)) + "\n").encode()
        new = ("\n".join(f"revised-{i}" for i in range(20)) + "\n").encode()
        result = plan(old, new, previous_sha256=h(old), current_sha256=h(new))
        self.assertFalse(result["scoped_review_candidate"])
        self.assertEqual(result["reason"], "broad_or_fragmented_change")


if __name__ == "__main__":
    unittest.main()

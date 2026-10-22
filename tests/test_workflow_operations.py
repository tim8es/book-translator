"""Keep the three workflow entrypoints aligned on chapter operations."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from workflow.operations import next_unit_operation


class UnitOperationTests(unittest.TestCase):
    def test_chapter_states_map_to_one_operation(self):
        cases = (
            ("pending", "missing", None),
            ("extracted", "untranslated", "translate"),
            ("extracted", "missing", "translate"),
            ("translated", "missing", "review"),
            ("translated", "stale", "review"),
            ("translated", "corrections_required", "correct_translation"),
            ("translated", "pass", "accept_review"),
            ("reviewed", "pass", None),
            ("translated", "untranslated", None),
            ("unknown", "pass", None),
        )
        for lifecycle, review, expected in cases:
            with self.subTest(lifecycle=lifecycle, review=review):
                self.assertEqual(next_unit_operation(lifecycle, review), expected)


if __name__ == "__main__":
    unittest.main()

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

try:
    from workflow_v2.claims import InvalidClaimSelector, canonical_unit_id, resolve_selector
except ModuleNotFoundError:
    InvalidClaimSelector = None
    canonical_unit_id = None
    resolve_selector = None


class WorkflowV2ClaimSelectorTests(unittest.TestCase):
    def require_api(self):
        self.assertIsNotNone(canonical_unit_id, "workflow_v2.claims canonical_unit_id is not implemented")
        self.assertIsNotNone(resolve_selector, "workflow_v2.claims resolve_selector is not implemented")
        self.assertIsNotNone(InvalidClaimSelector, "workflow_v2.claims InvalidClaimSelector is not implemented")

    def progress(self):
        return {
            "schema_version": 1,
            "book_slug": "example",
            "chapters": [
                {
                    "number": number,
                    "title": f"Chapter {number}",
                    "slug": f"chapter-{number}",
                    "source_path": f"extracted/{number:03d}.md",
                    "translation_path": f"translated/{number:03d}.md",
                    "status": "extracted",
                }
                for number in (1, 2, 3, 4, 5)
            ],
        }

    def test_canonical_unit_id_uses_six_digit_chapter_number(self):
        self.require_api()
        self.assertEqual(canonical_unit_id(1), "chapter-000001")
        self.assertEqual(canonical_unit_id(42), "chapter-000042")
        with self.assertRaises(InvalidClaimSelector):
            canonical_unit_id(0)

    def test_selector_resolves_single_and_inclusive_range_in_canonical_order(self):
        self.require_api()
        progress = self.progress()
        self.assertEqual(resolve_selector(progress, "2"), ["chapter-000002"])
        self.assertEqual(
            resolve_selector(progress, "2-4"),
            ["chapter-000002", "chapter-000003", "chapter-000004"],
        )

    def test_selector_rejects_invalid_reversed_and_missing_units(self):
        self.require_api()
        progress = self.progress()
        for selector in ("0", "-1", "1-", "1-a", "3-2", "1,2", "chapter-1"):
            with self.subTest(selector=selector):
                with self.assertRaises(InvalidClaimSelector):
                    resolve_selector(progress, selector)

        missing = self.progress()
        missing["chapters"] = [chapter for chapter in missing["chapters"] if chapter["number"] != 3]
        with self.assertRaises(InvalidClaimSelector):
            resolve_selector(missing, "2-4")

    def test_selector_rejects_duplicate_progress_chapter_numbers_before_mutation(self):
        self.require_api()
        progress = self.progress()
        progress["chapters"].append(dict(progress["chapters"][0]))
        with self.assertRaises(InvalidClaimSelector):
            resolve_selector(progress, "1")


if __name__ == "__main__":
    unittest.main()

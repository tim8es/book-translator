import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from workflow_v2.filesystem import FilesystemStorage

try:
    from workflow_v2.text_patch import TextPatchError, TextPatchResult, patch_text
except (ImportError, ModuleNotFoundError):
    TextPatchError = None
    TextPatchResult = None
    patch_text = None


class WorkflowV2TextPatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.storage = FilesystemStorage(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def require_api(self):
        self.assertIsNotNone(TextPatchError, "workflow_v2.text_patch TextPatchError is not implemented")
        self.assertIsNotNone(TextPatchResult, "workflow_v2.text_patch TextPatchResult is not implemented")
        self.assertIsNotNone(patch_text, "workflow_v2.text_patch patch_text is not implemented")

    def create(self, content=b"alpha beta alpha\n"):
        version = self.storage.create_if_absent("sample.md", content)
        return self.storage.read("sample.md"), version

    def test_literal_expected_count_mismatch_is_no_write(self):
        self.require_api()
        before, _ = self.create(b"alpha\n")

        with self.assertRaisesRegex(TextPatchError, r"expected 2 match.*observed 1"):
            patch_text(
                self.storage,
                "sample.md",
                old="alpha",
                new="beta",
                expected_count=2,
            )

        after = self.storage.read("sample.md")
        self.assertEqual(after, before)

    def test_literal_success_replaces_exact_non_overlapping_occurrences(self):
        self.require_api()
        before, _ = self.create(b"alpha beta alpha\n")

        result = patch_text(
            self.storage,
            "sample.md",
            old="alpha",
            new="gamma",
            expected_count=2,
        )

        self.assertIsInstance(result, TextPatchResult)
        self.assertEqual(result.path, "sample.md")
        self.assertEqual(result.match_count, 2)
        self.assertTrue(result.changed)
        self.assertFalse(result.dry_run)
        self.assertEqual(result.original_version, before.version)
        self.assertIsNotNone(result.new_version)
        self.assertEqual(self.storage.read("sample.md").content, b"gamma beta gamma\n")
        self.assertIn("--- a/sample.md", result.diff)
        self.assertIn("+++ b/sample.md", result.diff)
        self.assertIn("-alpha beta alpha", result.diff)
        self.assertIn("+gamma beta gamma", result.diff)

    def test_expected_count_zero_asserts_absence_without_rewrite(self):
        self.require_api()
        before, _ = self.create(b"alpha\n")

        result = patch_text(
            self.storage,
            "sample.md",
            old="missing",
            new="replacement",
            expected_count=0,
        )

        self.assertEqual(result.match_count, 0)
        self.assertFalse(result.changed)
        self.assertFalse(result.dry_run)
        self.assertIsNone(result.new_version)
        self.assertEqual(result.diff, "")
        self.assertEqual(self.storage.read("sample.md"), before)

    def test_identical_replacement_is_no_write_even_with_matches(self):
        self.require_api()
        before, _ = self.create(b"alpha alpha\n")

        result = patch_text(
            self.storage,
            "sample.md",
            old="alpha",
            new="alpha",
            expected_count=2,
        )

        self.assertEqual(result.match_count, 2)
        self.assertFalse(result.changed)
        self.assertIsNone(result.new_version)
        self.assertEqual(result.diff, "")
        self.assertEqual(self.storage.read("sample.md"), before)

    def test_dry_run_reports_diff_without_writing(self):
        self.require_api()
        before, _ = self.create(b"alpha\n")

        result = patch_text(
            self.storage,
            "sample.md",
            old="alpha",
            new="beta",
            expected_count=1,
            dry_run=True,
        )

        self.assertEqual(result.match_count, 1)
        self.assertTrue(result.changed)
        self.assertTrue(result.dry_run)
        self.assertIsNone(result.new_version)
        self.assertIn("-alpha", result.diff)
        self.assertIn("+beta", result.diff)
        self.assertEqual(self.storage.read("sample.md"), before)

    def test_unicode_replacement_preserves_exact_utf8(self):
        self.require_api()
        self.create("термин — старый\n".encode("utf-8"))

        result = patch_text(
            self.storage,
            "sample.md",
            old="старый",
            new="новый",
            expected_count=1,
        )

        self.assertTrue(result.changed)
        self.assertEqual(
            self.storage.read("sample.md").content,
            "термин — новый\n".encode("utf-8"),
        )

    def test_empty_literal_and_invalid_expected_count_fail_before_write(self):
        self.require_api()
        before, _ = self.create(b"alpha\n")

        for expected_count in (-1, True, False, 1.5, "1"):
            with self.subTest(expected_count=expected_count):
                with self.assertRaises(TextPatchError):
                    patch_text(
                        self.storage,
                        "sample.md",
                        old="alpha",
                        new="beta",
                        expected_count=expected_count,
                    )
                self.assertEqual(self.storage.read("sample.md"), before)

        with self.assertRaises(TextPatchError):
            patch_text(
                self.storage,
                "sample.md",
                old="",
                new="beta",
                expected_count=0,
            )
        self.assertEqual(self.storage.read("sample.md"), before)


if __name__ == "__main__":
    unittest.main()

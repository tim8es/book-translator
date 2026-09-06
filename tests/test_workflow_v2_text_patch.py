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
            patch_text(self.storage, "sample.md", old="alpha", new="beta", expected_count=2)
        self.assertEqual(self.storage.read("sample.md"), before)

    def test_literal_success_replaces_exact_non_overlapping_occurrences(self):
        self.require_api()
        before, _ = self.create(b"alpha beta alpha\n")
        result = patch_text(self.storage, "sample.md", old="alpha", new="gamma", expected_count=2)
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

    def test_expected_count_zero_asserts_absence_without_rewrite(self):
        self.require_api()
        before, _ = self.create(b"alpha\n")
        result = patch_text(self.storage, "sample.md", old="missing", new="replacement", expected_count=0)
        self.assertEqual(result.match_count, 0)
        self.assertFalse(result.changed)
        self.assertFalse(result.dry_run)
        self.assertIsNone(result.new_version)
        self.assertEqual(result.diff, "")
        self.assertEqual(self.storage.read("sample.md"), before)

    def test_identical_replacement_is_no_write_even_with_matches(self):
        self.require_api()
        before, _ = self.create(b"alpha alpha\n")
        result = patch_text(self.storage, "sample.md", old="alpha", new="alpha", expected_count=2)
        self.assertEqual(result.match_count, 2)
        self.assertFalse(result.changed)
        self.assertIsNone(result.new_version)
        self.assertEqual(result.diff, "")
        self.assertEqual(self.storage.read("sample.md"), before)

    def test_dry_run_reports_diff_without_writing(self):
        self.require_api()
        before, _ = self.create(b"alpha\n")
        result = patch_text(
            self.storage, "sample.md", old="alpha", new="beta", expected_count=1, dry_run=True
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
            self.storage, "sample.md", old="старый", new="новый", expected_count=1
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
            patch_text(self.storage, "sample.md", old="", new="beta", expected_count=0)
        self.assertEqual(self.storage.read("sample.md"), before)

    def test_multiline_literal_can_cross_paragraph_boundary(self):
        self.require_api()
        self.create(b"first\n\nsecond\nthird\n")
        result = patch_text(
            self.storage,
            "sample.md",
            old="first\n\nsecond",
            new="joined",
            expected_count=1,
        )
        self.assertTrue(result.changed)
        self.assertEqual(self.storage.read("sample.md").content, b"joined\nthird\n")

    def test_mixed_line_endings_outside_replacement_are_byte_preserved(self):
        self.require_api()
        self.create(b"one\r\ntwo\nthree\r\n")
        patch_text(self.storage, "sample.md", old="two", new="TWO", expected_count=1)
        self.assertEqual(self.storage.read("sample.md").content, b"one\r\nTWO\nthree\r\n")

    def test_inclusive_line_scope_counts_and_changes_only_selected_lines(self):
        self.require_api()
        self.create(b"alpha one\r\nalpha two\nalpha three\r\nalpha four\n")
        result = patch_text(
            self.storage,
            "sample.md",
            old="alpha",
            new="beta",
            expected_count=2,
            line_start=2,
            line_end=3,
        )
        self.assertEqual(result.match_count, 2)
        self.assertEqual(
            self.storage.read("sample.md").content,
            b"alpha one\r\nbeta two\nbeta three\r\nalpha four\n",
        )

    def test_open_ended_line_scope_expands_to_file_edges(self):
        self.require_api()
        self.create(b"alpha one\nalpha two\nalpha three\n")
        patch_text(
            self.storage,
            "sample.md",
            old="alpha",
            new="beta",
            expected_count=2,
            line_start=2,
        )
        self.assertEqual(
            self.storage.read("sample.md").content,
            b"alpha one\nbeta two\nbeta three\n",
        )

    def test_invalid_line_scopes_fail_without_writing(self):
        self.require_api()
        invalid_scopes = [
            {"line_start": 0},
            {"line_start": -1},
            {"line_start": True},
            {"line_end": False},
            {"line_start": 3, "line_end": 2},
            {"line_end": 4},
        ]
        for index, scope in enumerate(invalid_scopes):
            with self.subTest(scope=scope):
                local = FilesystemStorage(self.root / f"scope-{index}")
                before_version = local.create_if_absent("sample.md", b"one\ntwo\nthree\n")
                with self.assertRaises(TextPatchError):
                    patch_text(local, "sample.md", old="one", new="ONE", expected_count=1, **scope)
                after = local.read("sample.md")
                self.assertEqual(after.content, b"one\ntwo\nthree\n")
                self.assertEqual(after.version, before_version)

        empty = FilesystemStorage(self.root / "empty")
        before_version = empty.create_if_absent("sample.md", b"")
        with self.assertRaises(TextPatchError):
            patch_text(
                empty,
                "sample.md",
                old="x",
                new="y",
                expected_count=0,
                line_start=1,
            )
        self.assertEqual(empty.read("sample.md").version, before_version)

    def test_regex_capture_group_replacement_uses_exact_count(self):
        self.require_api()
        self.create(b"Term: Value\nOther: Keep\n")
        result = patch_text(
            self.storage,
            "sample.md",
            old=r"(Term):\s+([^\r\n]+)",
            new=r"\1 — \2",
            expected_count=1,
            regex=True,
        )
        self.assertEqual(result.match_count, 1)
        self.assertEqual(
            self.storage.read("sample.md").content,
            "Term — Value\nOther: Keep\n".encode("utf-8"),
        )

    def test_invalid_regex_and_replacement_fail_without_writing(self):
        self.require_api()
        for index, (old, new) in enumerate((("(", "x"), (r"(alpha)", r"\2"))):
            with self.subTest(old=old, new=new):
                local = FilesystemStorage(self.root / f"regex-{index}")
                before_version = local.create_if_absent("sample.md", b"alpha\n")
                with self.assertRaises(TextPatchError):
                    patch_text(
                        local,
                        "sample.md",
                        old=old,
                        new=new,
                        expected_count=1,
                        regex=True,
                    )
                after = local.read("sample.md")
                self.assertEqual(after.content, b"alpha\n")
                self.assertEqual(after.version, before_version)

    def test_invalid_utf8_fails_without_writing(self):
        self.require_api()
        before_version = self.storage.create_if_absent("sample.md", b"\xffalpha")
        with self.assertRaisesRegex(TextPatchError, "not valid UTF-8"):
            patch_text(self.storage, "sample.md", old="alpha", new="beta", expected_count=1)
        after = self.storage.read("sample.md")
        self.assertEqual(after.content, b"\xffalpha")
        self.assertEqual(after.version, before_version)

    def test_stale_cas_conflict_preserves_concurrent_winner(self):
        self.require_api()

        class RacingStorage(FilesystemStorage):
            def __init__(self, root):
                super().__init__(root)
                self.triggered = False
                self.winner = FilesystemStorage(root)

            def write_if_version(self, path, content, expected_version):
                if not self.triggered:
                    self.triggered = True
                    self.winner.write_if_version(path, b"winner\n", expected_version)
                return super().write_if_version(path, content, expected_version)

        racing = RacingStorage(self.root / "race")
        racing.create_if_absent("sample.md", b"alpha\n")
        with self.assertRaisesRegex(TextPatchError, "changed before commit"):
            patch_text(racing, "sample.md", old="alpha", new="beta", expected_count=1)
        self.assertEqual(FilesystemStorage(self.root / "race").read("sample.md").content, b"winner\n")


if __name__ == "__main__":
    unittest.main()

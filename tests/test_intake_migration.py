"""Regression tests for read-only intake audit and trust boundaries."""
import hashlib
import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "scripts" / "migrate_intake.py"
spec = importlib.util.spec_from_file_location("migrate_intake", SRC)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class IntakeMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.book = Path(self.tmp.name) / "books" / "test-book"
        (self.book / "incoming").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def source(self, blank=False):
        path = Path(self.tmp.name) / "original.epub"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("META-INF/container.xml",
                '<container><rootfiles><rootfile full-path="content.opf"/></rootfiles></container>')
            archive.writestr("content.opf",
                '<package><manifest><item id="one" href="split_001.xhtml" media-type="application/xhtml+xml"/>'
                '<item id="two" href="split_002.xhtml" media-type="application/xhtml+xml"/>'
                '</manifest><spine><itemref idref="one"/><itemref idref="two"/></spine></package>')
            archive.writestr("split_001.xhtml", "<html><body><h1>Preface</h1><p>Text</p></body></html>")
            archive.writestr("split_002.xhtml", "<html><body><h1>Chapter 1</h1><p>Content</p>" + ("<p></p>" if blank else "") + "</body></html>")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        (self.book / "INTAKE.md").write_text(
            "Original EPUB SHA-256: " + chr(96) + digest + chr(96) + "\n", encoding="utf-8")
        return path

    def test_no_source_reports_blocker_without_touching_draft(self):
        draft = self.book / "incoming/chapter-001-ru-draft.md"
        draft.write_text("# Глава 1\n\nТекст\n", encoding="utf-8")
        before = draft.read_bytes()
        result = migration.audit(self.book)
        self.assertFalse(result["source_verified"])
        self.assertEqual(result["reviewed_promotions"], 0)
        self.assertIn("metadata.json", result["missing_canonical_files"])
        self.assertEqual(result["chapters"][0]["status"], "source_unverified")
        self.assertEqual(draft.read_bytes(), before)
        self.assertFalse((self.book / "migration-staging").exists())

    def test_valid_source_sha_and_explicit_offset(self):
        source = self.source()
        draft = self.book / "incoming/chapter-001-ru-draft.md"
        draft.write_text("Глава 1\n", encoding="utf-8")
        report = migration.audit(self.book, source, offset=1)
        self.assertTrue(report["source_verified"])
        self.assertEqual(report["source_units"], 2)
        self.assertEqual(report["chapters"][0]["unit_number"], 2)
        self.assertEqual(report["chapters"][0]["status"], "candidate_unreviewed")
        self.assertEqual(report["reviewed_promotions"], 0)

    def test_mismatched_source_blocks_stage(self):
        source = self.source()
        (self.book / "INTAKE.md").write_text("Original EPUB SHA-256: " + "0" * 64, encoding="utf-8")
        report = migration.audit(self.book, source, offset=1)
        self.assertFalse(report["source_verified"])
        self.assertIn("Source SHA-256 mismatch", report["blockers"])
        with self.assertRaises(migration.MigrationError):
            migration.stage(self.book, report, 1)

    def test_conflicting_parts_are_detected(self):
        for part, value in [(1, "один"), (2, "два")]:
            (self.book / f"incoming/chapter-041-ru-draft.part-{part:02d}.json").write_text(
                json.dumps({"draft": {"0": value}, "source_path": "source.xhtml"}), encoding="utf-8")
        mapping, sources, errors = migration.merge_indexed(sorted((self.book / "incoming").glob("*.json")))
        self.assertEqual(len(mapping), 1)
        self.assertEqual(sources, ["source.xhtml"])
        self.assertTrue(any("conflicting" in msg for msg in errors))

    def test_disjoint_parts_combine(self):
        for part, index in [(1, 0), (2, 1)]:
            (self.book / f"incoming/chapter-041-ru-draft.part-{part:02d}.json").write_text(
                json.dumps({"draft": {str(index): "Перевод"}}), encoding="utf-8")
        mapping, _, errors = migration.merge_indexed(sorted((self.book / "incoming").glob("*.json")))
        self.assertEqual(sorted(mapping), [0, 1])
        self.assertEqual(errors, [])

    def test_index_coverage_and_source_name_checked(self):
        source = self.source()
        (self.book / "incoming/chapter-001-ru-draft.part-01.json").write_text(
            json.dumps({"draft": {"0": "Глава 1"},
                        "source_path": "different.xhtml"}), encoding="utf-8")
        report = migration.audit(self.book, source, offset=1)
        self.assertEqual(report["chapters"][0]["status"], "needs_reconciliation")
        self.assertTrue(any("source path" in x for x in report["chapters"][0]["issues"]))

    def test_markdown_index_gaps_are_detected(self):
        source = self.source()
        (self.book / "incoming/chapter-001-ru-draft.md").write_text(
            "<!-- p:000 -->\nГлава\n", encoding="utf-8")
        report = migration.audit(self.book, source, offset=1)
        self.assertEqual(report["chapters"][0]["status"], "needs_reconciliation")


    def test_indexed_markdown_fragments_can_be_reassembled(self):
        source = self.source()
        (self.book / "incoming/chapter-001-ru-draft.part-01.md").write_text(
            "<!-- source-block 0 -->\nГлава\n"
            "<!-- source-block 1 -->\nТекст\n", encoding="utf-8")
        report = migration.audit(self.book, source, offset=1)
        self.assertEqual(report["chapters"][0]["status"], "candidate_unreviewed")
        self.assertEqual(report["chapters"][0]["indexed_blocks"], 2)
        self.assertEqual(report["chapters"][0]["method"], "indexed_parts")

    def test_incomplete_indexed_markdown_remains_unaccepted(self):
        source = self.source()
        (self.book / "incoming/chapter-001-ru-draft.part-01.md").write_text(
            "<!-- source-block 0 -->\nГлава\n", encoding="utf-8")
        report = migration.audit(self.book, source, offset=1)
        self.assertEqual(report["chapters"][0]["status"], "needs_reconciliation")

    def test_afterword_candidate_is_always_unreviewed(self):
        source = self.source()
        (self.book / "incoming/afterword-ru-draft.json").write_text(
            json.dumps({"source_path": "split_002.xhtml",
                        "draft": {"0": "Конец", "1": "Спасибо"}}), encoding="utf-8")
        report = migration.audit(self.book, source, offset=1)
        self.assertEqual(report["afterword"]["status"], "candidate_unreviewed")
        self.assertEqual(report["afterword"]["unit_number"], 2)
        self.assertEqual(report["reviewed_promotions"], 0)

    def test_staging_refuses_invalid_canonical_state(self):
        for name in migration.CANONICAL:
            (self.book / name).write_text("{}", encoding="utf-8")
        fake = {"source_verified": True, "missing_canonical_files": [],
                "blockers": [], "chapters": []}
        with self.assertRaises(migration.MigrationError):
            migration.stage(self.book, fake, 1)
        self.assertFalse((self.book / "migration-staging").exists())


    def test_nonempty_block_index_scheme_accepts_historical_gaps(self):
        source = self.source(blank=True)
        (self.book / "incoming/chapter-001-ru-draft.part-01.json").write_text(
            json.dumps({"source_path": "split_002.xhtml", "draft": {"0": "Заголовок", "1": "Текст"}}),
            encoding="utf-8")
        report = migration.audit(self.book, source, offset=1)
        ch = report["chapters"][0]
        self.assertEqual(ch["status"], "candidate_unreviewed")
        self.assertEqual(ch["indexing_scheme"], "nonempty_blocks")
        self.assertEqual(ch["source_blocks"], 2)
        self.assertEqual(ch["source_blocks_all"], 3)

    def test_all_block_index_scheme_with_empty_source_blocks(self):
        source = self.source(blank=True)
        (self.book / "incoming/chapter-001-ru-draft.part-01.json").write_text(
            json.dumps({"source_path": "split_002.xhtml",
                        "draft": {"0": "Заголовок", "1": "Текст", "2": ""}}),
            encoding="utf-8")
        report = migration.audit(self.book, source, offset=1)
        self.assertEqual(report["chapters"][0]["status"], "candidate_unreviewed")
        self.assertEqual(report["chapters"][0]["indexing_scheme"], "all_blocks")
        self.assertEqual(report["chapters"][0]["source_blocks"], 3)

    def test_missing_chapter_cannot_be_promoted(self):
        source = self.source()
        report = migration.audit(self.book, source, offset=1)
        self.assertEqual(report["ready_candidates"], 0)
        self.assertEqual(report["reviewed_promotions"], 0)


if __name__ == "__main__":
    unittest.main()

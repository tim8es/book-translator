"""Edition identity regression coverage: no retranslation of old chapters."""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "automation"))
import book_runner as worker


JOB = "a" * 32
AUTHOR = "Original test fiction"
ONE = "The Map of Quiet Places — 1 Chapter (Test Edition)"
TWO = "The Map of Quiet Places — 2 Chapters (Test Edition)"
THREE = "The Map of Quiet Places — 3 Chapters (Test Edition)"
CHAPTER = (
    "<h1>Chapter 1. The Key Beneath the Clock</h1><p>"
    + "Mara walked into the station with the strange map. "
      "She unfolded it and read the marks under the clock. " * 24
    + "</p>"
)


def make_epub(path: Path, title: str, chapters: list[str],
              author: str = AUTHOR) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest = "".join(
        f'<item id="chapter{i}" href="chapter{i}.xhtml" media-type="application/xhtml+xml"/>'
        for i in range(1, len(chapters) + 1))
    spine = "".join(f'<itemref idref="chapter{i}"/>' for i in range(1, len(chapters) + 1))
    package = (
        '<package xmlns="http://www.idpf.org/2007/opf" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f'<metadata><dc:title>{title}</dc:title><dc:creator>{author}</dc:creator></metadata>'
        f'<manifest>{manifest}</manifest><spine>{spine}</spine></package>'
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml",
                   '<container><rootfiles><rootfile full-path="OEBPS/content.opf"/>'
                   '</rootfiles></container>')
        z.writestr("OEBPS/content.opf", package)
        for i, text in enumerate(chapters, 1):
            z.writestr(f"OEBPS/chapter{i}.xhtml",
                       '<html xmlns="http://www.w3.org/1999/xhtml"><body>' +
                       text + '</body></html>')


class EditionIdentityTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.original = self.root / "uploads" / "one.epub"
        self.next = self.root / "uploads" / "two.epub"
        self.chapter2 = "<h1>Chapter 2. A Map That Would Not Stay Still</h1><p>" + (
            "It was morning again and the map had moved. " * 25) + "</p>"
        make_epub(self.original, ONE, [CHAPTER])
        make_epub(self.next, TWO, [CHAPTER, self.chapter2])
        self.book_slug = "the-map-of-quiet-places-1-chapter-test-edition"
        self.book = self.root / "books" / self.book_slug
        (self.book / "source").mkdir(parents=True)
        shutil.copyfile(self.original, self.book / "source" / "source.epub")
        self.digest = hashlib.sha256(self.original.read_bytes()).hexdigest()
        worker.store_json(self.book / "metadata.json", {
            "title": ONE, "author": AUTHOR, "target_language": "ru",
            "source_file": "source.epub",
            "source": {"filename": "source.epub", "original_filename": "one.epub",
                       "sha256": self.digest, "storage_mode": "embedded"},
            "workflow": {"resolved_revision": "b" * 40},
        })

    def match(self, path=None, *, title=None, author=AUTHOR):
        path = path or self.next
        return worker.locate_book(
            title or worker.epub_identity(path)[0], author, path.name, "ru",
            source_epub=path, source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        )

    def test_identical_source_hash_is_immediate_duplicate(self):
        with patch.object(worker, "ROOT", self.root):
            self.assertEqual(self.match(self.original)[0], self.book_slug)

    def test_additive_revision_same_chapter_different_epub_title(self):
        self.assertNotEqual(self.original.read_bytes(), self.next.read_bytes())
        self.assertEqual(worker.epub_identity(self.original)[1], AUTHOR)
        self.assertEqual(worker.epub_identity(self.next)[1], AUTHOR)
        with patch.object(worker, "ROOT", self.root):
            self.assertEqual(self.match()[0], self.book_slug)

    def test_changed_chapter_requires_manual_disambiguation_before_init(self):
        changed = self.root / "uploads" / "changed.epub"
        make_epub(changed, TWO, [CHAPTER.replace("Mara", "Lena"), self.chapter2])
        with patch.object(worker, "ROOT", self.root):
            with self.assertRaisesRegex(worker.BookIdentityError, "identity is unproven"):
                self.match(changed)

    def test_identical_frontmatter_cannot_prove_different_book(self):
        short = "<p>Copyright 2026. All rights reserved.</p>"
        make_epub(self.original, ONE, [short])
        shutil.copyfile(self.original, self.book / "source" / "source.epub")
        make_epub(self.next, TWO, [short, self.chapter2])
        with patch.object(worker, "ROOT", self.root):
            with self.assertRaisesRegex(worker.BookIdentityError, "identity is unproven"):
                self.match()

    def test_same_original_file_name_not_an_identity(self):
        other = self.root / "uploads" / "source.epub"
        make_epub(other, "A Completely Different Novel", [CHAPTER], author=AUTHOR)
        with patch.object(worker, "ROOT", self.root):
            slug, data = self.match(other)
            self.assertEqual(slug, "a-completely-different-novel")
            self.assertEqual(data, {})

    def test_original_source_unavailable_is_not_recreated(self):
        (self.book / "source" / "source.epub").unlink()
        with patch.object(worker, "ROOT", self.root):
            with self.assertRaisesRegex(worker.BookIdentityError, "identity is unproven"):
                self.match()

    def test_different_authors_not_merged_on_shared_chapter(self):
        other = self.root / "uploads" / "other.epub"
        make_epub(other, TWO, [CHAPTER, self.chapter2], author="Completely Different Writer")
        with patch.object(worker, "ROOT", self.root):
            self.assertNotEqual(self.match(other, author="Completely Different Writer")[0],
                                self.book_slug)

    def test_short_unrelated_titles_not_merged(self):
        other = self.root / "uploads" / "other.epub"
        make_epub(other, "An Unrelated Tale", [CHAPTER, self.chapter2])
        with patch.object(worker, "ROOT", self.root):
            slug, metadata = self.match(other)
            self.assertEqual(slug, "an-unrelated-tale")
            self.assertEqual(metadata, {})

    def test_ambiguous_additive_source_match_fails_closed(self):
        second = self.root / "books" / "another-edition"
        (second / "source").mkdir(parents=True)
        shutil.copyfile(self.original, second / "source" / "source.epub")
        worker.store_json(second / "metadata.json", {
            "title": "The Map of Quiet Places — 0 Chapter (Test Edition)",
            "author": AUTHOR, "target_language": "ru",
            "source": {"filename": "source.epub", "sha256": self.digest,
                       "storage_mode": "embedded"},
        })
        with patch.object(worker, "ROOT", self.root):
            with self.assertRaisesRegex(worker.BookIdentityError, "multiple distinct editions"):
                self.match()

    def test_new_revision_routes_through_pinned_source_update_not_init(self):
        folder = self.root / "intake" / "auto" / JOB
        folder.mkdir(parents=True)
        incoming = folder / "source.epub"
        shutil.copyfile(self.next, incoming)
        worker.store_json(folder / "request.json", {
            "schema_version": 1, "job_id": JOB,
            "source_sha256": hashlib.sha256(incoming.read_bytes()).hexdigest(),
            "target_language": "ru", "source_filename": "two.epub",
        })
        engine = self.root / "installed-engine"
        engine.mkdir()
        next_chapter = {"unit_id": "chapter-000002", "number": 2, "status": "extracted"}
        with patch.object(worker, "ROOT", self.root), \
             patch.object(worker, "install_engine", return_value=(engine, "b" * 40)), \
             patch.object(worker, "init") as initialize, \
             patch.object(worker, "verify"), \
             patch.object(worker, "cli", side_effect=[json.dumps({"state": "promoted"}), ""]) as cli, \
             patch.object(worker, "checkpoint"), \
             patch.object(worker, "canonical_dispatch", return_value=("translator", next_chapter)), \
             patch.object(worker, "claim") as claim:
            status = worker.run(JOB)
        initialize.assert_not_called()
        self.assertEqual(status["phase"], "matched-existing-book")
        self.assertEqual(cli.call_args_list[0].args[:2], ("update-source", self.book_slug))
        claim.assert_called_once()
        self.assertEqual(claim.call_args.args[-1], next_chapter)

    def test_reviewed_unit_is_skipped_after_additive_promotion(self):
        # Canonical update-source preserves an unchanged unit's reviewed status.
        # The adapter's dispatch must pick only the new extracted unit.
        book = self.root / "books" / self.book_slug
        worker.store_json(book / "progress.json", {"chapters": [
            {"unit_id": "chapter-000001", "number": 1, "status": "reviewed"},
            {"unit_id": "chapter-000002", "number": 2, "status": "extracted"},
        ]})
        worker.store_json(book / "review-ledger.json", {"records": []})
        with patch.object(worker, "ROOT", self.root):
            role, chapter = worker.next_operation(self.book_slug, {"correction_rounds": {}})
            self.assertEqual(role, "translator")
            self.assertEqual(chapter["unit_id"], "chapter-000002")


if __name__ == "__main__":
    unittest.main()

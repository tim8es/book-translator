"""Regression tests for EPUB font-size wrappers and Auto intake recovery."""
from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "automation"))

from workflow import epub_preserve
import book_runner

XHTML = "http://www.w3.org/1999/xhtml"


def synthetic_epub(path: Path, prose: str) -> None:
    page = (
        f'<html xmlns="{XHTML}"><head><title>Test</title></head><body>'
        f'{prose}<img src="picture.png" alt=""/></body></html>'
    )
    package = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="2.0">'
        '<metadata><language xmlns="http://purl.org/dc/elements/1.1/">en</language></metadata>'
        '<manifest>'
        '<item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="picture" href="picture.png" media-type="image/png"/>'
        '</manifest><spine><itemref idref="chapter"/></spine></package>'
    )
    container = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
        '<rootfiles><rootfile full-path="OPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OPS/content.opf", package)
        archive.writestr("OPS/chapter.xhtml", page)
        archive.writestr("OPS/picture.png", b"\x89PNG\r\n\x1a\nfixture")


class EpubBigTests(unittest.TestCase):
    def test_whole_paragraph_big_survives_translated_build(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "book.epub"
            synthetic_epub(source,
                           '<p class="heading"><big class="large"><i>Alpha</i> Title</big></p>'
                           '<p>Regular prose.</p>')
            original = source.read_bytes()
            chapters = epub_preserve.extract(source, lambda **fields: SimpleNamespace(**fields))
            self.assertEqual(len(chapters), 1)
            self.assertIn("*Alpha* Title", chapters[0].content)
            certified = epub_preserve.audit(source, chapters)
            self.assertEqual(certified["state"], "verified")

            translation = (chapters[0].content
                           .replace("*Alpha* Title", "*Альфа* Заголовок")
                           .replace("Regular prose.", "Обычный текст."))
            output = epub_preserve.build(
                source, [{"markdown": translation}],
                expected_source_sha256=hashlib.sha256(original).hexdigest(),
                language="ru",
            )
            self.assertEqual(source.read_bytes(), original)
            with zipfile.ZipFile(io.BytesIO(output)) as archive:
                page = ET.fromstring(archive.read("OPS/chapter.xhtml"))
                big = page.find(f".//{{{XHTML}}}p/{{{XHTML}}}big")
                self.assertIsNotNone(big)
                self.assertEqual(big.get("class"), "large")
                self.assertEqual(big.find(f"{{{XHTML}}}em").text, "Альфа")
                self.assertIn("Заголовок", "".join(big.itertext()))
                self.assertEqual(archive.read("OPS/picture.png"), b"\x89PNG\r\n\x1a\nfixture")

    def test_simple_div_big_wrapper_is_a_translation_unit(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "book.epub"
            synthetic_epub(source, '<div id="title"><big>Cover heading</big></div>')
            chapters = epub_preserve.extract(source, lambda **kw: SimpleNamespace(**kw))
            self.assertIn("Cover heading", chapters[0].content)
            result = epub_preserve.build(
                source, [{"markdown": chapters[0].content.replace("Cover heading", "Заголовок")}],
                expected_source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                language="ru",
            )
            with zipfile.ZipFile(io.BytesIO(result)) as archive:
                page = ET.fromstring(archive.read("OPS/chapter.xhtml"))
                big = page.find(f".//{{{XHTML}}}div/{{{XHTML}}}big")
                self.assertEqual(big.text, "Заголовок")

    def test_nested_prose_containers_keep_layout_and_translate_leaf_blocks(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "book.epub"
            synthetic_epub(
                source,
                '<blockquote class="quote"><p><big>Quoted words.</big></p></blockquote>'
                '<ul><li><p>List item.</p></li></ul>',
            )
            chapters = epub_preserve.extract(source, lambda **kw: SimpleNamespace(**kw))
            self.assertEqual(len(chapters), 1)
            self.assertEqual(chapters[0].content.count("<!-- bt-block:"), 2)
            self.assertEqual(epub_preserve.audit(source, chapters)["state"], "verified")
            translation = (chapters[0].content
                           .replace("Quoted words.", "Цитата.")
                           .replace("List item.", "Пункт списка."))
            output = epub_preserve.build(
                source, [{"markdown": translation}],
                expected_source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                language="ru",
            )
            with zipfile.ZipFile(io.BytesIO(output)) as archive:
                page = ET.fromstring(archive.read("OPS/chapter.xhtml"))
                big = page.find(f".//{{{XHTML}}}blockquote/{{{XHTML}}}p/{{{XHTML}}}big")
                self.assertEqual(big.text, "Цитата.")
                self.assertEqual(
                    page.find(f".//{{{XHTML}}}ul/{{{XHTML}}}li/{{{XHTML}}}p").text,
                    "Пункт списка.",
                )
                self.assertEqual(
                    page.find(f".//{{{XHTML}}}blockquote").get("class"), "quote"
                )

    def test_nested_container_direct_prose_is_not_silently_lost(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "book.epub"
            synthetic_epub(source, "<blockquote>Unmapped <p>Mapped.</p></blockquote>")
            with self.assertRaisesRegex(epub_preserve.PreservedEpubError, "Unmapped prose"):
                epub_preserve.analyze(source)

    def test_single_link_div_text_and_destination_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "book.epub"
            synthetic_epub(
                source,
                '<div class="nav">» <a href="#target" rel="next">Continue</a></div>'
                '<p id="target">Chapter text.</p>',
            )
            chapters = epub_preserve.extract(source, lambda **kw: SimpleNamespace(**kw))
            self.assertEqual(chapters[0].content.count("<!-- bt-block:"), 2)
            self.assertEqual(epub_preserve.audit(source, chapters)["state"], "verified")
            translation = chapters[0].content.replace("Chapter text.", "Текст главы.")
            out = epub_preserve.build(
                source, [{"markdown": translation}],
                expected_source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                language="ru",
            )
            with zipfile.ZipFile(io.BytesIO(out)) as archive:
                page = ET.fromstring(archive.read("OPS/chapter.xhtml"))
                nav = page.find(f".//{{{XHTML}}}div")
                self.assertEqual(nav.text, "» ")
                link = nav.find(f"{{{XHTML}}}a")
                self.assertEqual(link.get("href"), "#target")
                self.assertEqual(link.get("rel"), "next")
                self.assertEqual(link.text, "Continue")

    def test_source_author_byline_keeps_external_author_link(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "book.epub"
            synthetic_epub(
                source,
                '<div class="byline">by <a href="https://example.org/writer" '
                'rel="author">Original Name</a></div>',
            )
            chapters = epub_preserve.extract(source, lambda **kw: SimpleNamespace(**kw))
            self.assertIn("by Original Name", chapters[0].content)
            self.assertEqual(epub_preserve.audit(source, chapters)["state"], "verified")
            translated = chapters[0].content.replace("by Original Name", "Автор: Original Name")
            data = epub_preserve.build(
                source, [{"markdown": translated}],
                expected_source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                language="ru",
            )
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                root = ET.fromstring(archive.read("OPS/chapter.xhtml"))
                div = root.find(f".//{{{XHTML}}}div")
                self.assertEqual(div.text, "Автор: ")
                author = div.find(f"{{{XHTML}}}a")
                self.assertEqual(author.text, "Original Name")
                self.assertEqual(author.get("href"), "https://example.org/writer")
                self.assertEqual(author.get("rel"), "author")

    def test_leaf_div_preserves_classed_big_wrapper(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "book.epub"
            synthetic_epub(source, '<div><big class="huge">Title</big></div>')
            chapters = epub_preserve.extract(source, lambda **kw: SimpleNamespace(**kw))
            output = epub_preserve.build(
                source, [{"markdown": chapters[0].content.replace("Title", "Название")}],
                expected_source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                language="ru",
            )
            with zipfile.ZipFile(io.BytesIO(output)) as archive:
                page = ET.fromstring(archive.read("OPS/chapter.xhtml"))
                big = page.find(f".//{{{XHTML}}}div/{{{XHTML}}}big")
                self.assertEqual(big.attrib, {"class": "huge"})
                self.assertEqual(big.text, "Название")

    def test_link_div_with_inline_image_remains_blocked(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "book.epub"
            synthetic_epub(source, '<div>→ <a href="#target"><img src="picture.png"/></a></div>')
            with self.assertRaises(epub_preserve.PreservedEpubError):
                epub_preserve.analyze(source)

    def test_partial_inline_big_fails_closed_instead_of_losing_style(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "book.epub"
            synthetic_epub(source, "<p>Before <big>large</big> after.</p>")
            with self.assertRaisesRegex(epub_preserve.PreservedEpubError, "Inline <big>"):
                epub_preserve.analyze(source)

    def test_nontrivial_unknown_semantics_still_fail(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "book.epub"
            synthetic_epub(source, "<p>Hello <script>evil</script>.</p>")
            with self.assertRaisesRegex(epub_preserve.PreservedEpubError, "Unsupported semantic element"):
                epub_preserve.analyze(source)


class PreflightRecoveryTests(unittest.TestCase):
    JOB = "05eed6ea814d3b5eec014c6d05b39e3f"

    def status(self):
        return {
            "schema_version": 1,
            "job_id": self.JOB,
            "book_slug": None,
            "state": "blocked",
            "phase": "operator-intervention-required",
            "failures": 1,
            "last_error": "JobError: canonical CLI rejected operation: "
                          "ERROR: EPUB source-preserving extraction blocked: "
                          "Unsupported semantic element <big>\n",
        }

    def test_only_one_preflight_retry_is_eligible(self):
        base = self.status()
        self.assertTrue(book_runner.recoverable_epub_preflight(base))
        self.assertTrue(book_runner.recoverable_epub_preflight({
            **base,
            "preflight_retries": 1,
            "last_error": "JobError: canonical CLI rejected operation: "
                          "ERROR: EPUB source-preserving extraction blocked: "
                          "Nested prose blocks require explicit mapping\n",
        }))
        self.assertTrue(book_runner.recoverable_epub_preflight({
            **base,
            "preflight_retries": 2,
            "last_error": "JobError: canonical CLI rejected operation: "
                          "ERROR: EPUB source-preserving extraction blocked: "
                          "OPS/part.xhtml: Unmapped prose in <div>\n",
        }))
        self.assertTrue(book_runner.recoverable_epub_preflight({
            **base,
            "preflight_retries": 3,
            "last_error": "JobError: canonical CLI rejected operation: "
                          "ERROR: EPUB source-preserving extraction blocked: "
                          "OPS/part.xhtml: Unsupported semantic element <sub>\n",
        }))
        for updates in (
            {"preflight_retries": 1},
            {"book_slug": "existing-book"},
            {"workflow_revision": "a" * 40},
            {"active": {"role": "translator"}},
            {"phase": "model-blocked"},
            {"last_error": "JobError: another failure"},
        ):
            self.assertFalse(book_runner.recoverable_epub_preflight({**base, **updates}))

    def test_eligible_job_is_selected_once_then_quarantined(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "intake" / "auto" / self.JOB).mkdir(parents=True)
            (root / "automation" / "status").mkdir(parents=True)
            (root / "intake" / "auto" / self.JOB / "request.json").write_text("{}")
            status_file = root / "automation" / "status" / (self.JOB + ".json")
            status = self.status()
            status_file.write_text(json.dumps(status))
            with patch.object(book_runner, "ROOT", root), \
                 patch.object(book_runner, "book_key", return_value="intake-test"):
                self.assertEqual(book_runner.ready_jobs(),
                                 [{"job_id": self.JOB, "book_key": "intake-test"}])
                status["preflight_retries"] = 1
                status_file.write_text(json.dumps(status))
                self.assertEqual(book_runner.ready_jobs(), [])
                status["last_error"] = (
                    "JobError: canonical CLI rejected operation: ERROR: "
                    "EPUB source-preserving extraction blocked: "
                    "Nested prose blocks require explicit mapping"
                )
                status_file.write_text(json.dumps(status))
                self.assertEqual(book_runner.ready_jobs(),
                                 [{"job_id": self.JOB, "book_key": "intake-test"}])
                status["preflight_retries"] = 2
                status_file.write_text(json.dumps(status))
                self.assertEqual(book_runner.ready_jobs(), [])
                status["last_error"] = (
                    "JobError: canonical CLI rejected operation: ERROR: "
                    "EPUB source-preserving extraction blocked: "
                    "source.xhtml: Unmapped prose in <div>"
                )
                status_file.write_text(json.dumps(status))
                self.assertEqual(book_runner.ready_jobs(),
                                 [{"job_id": self.JOB, "book_key": "intake-test"}])
                status["preflight_retries"] = 3
                status_file.write_text(json.dumps(status))
                self.assertEqual(book_runner.ready_jobs(), [])
                status["last_error"] = (
                    "JobError: canonical CLI rejected operation: ERROR: "
                    "EPUB source-preserving extraction blocked: "
                    "OPS/part.xhtml: Unsupported semantic element <sub>"
                )
                status_file.write_text(json.dumps(status))
                self.assertEqual(book_runner.ready_jobs(),
                                 [{"job_id": self.JOB, "book_key": "intake-test"}])
                status["preflight_retries"] = 4
                status_file.write_text(json.dumps(status))
                self.assertEqual(book_runner.ready_jobs(), [])


if __name__ == "__main__":
    unittest.main()

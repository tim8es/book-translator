"""Bounded, source-bound XHTML links between EPUB spine documents."""
from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from workflow import epub_preserve as ep
import book


def source_epub(path: Path, href: str, *, second: str = '<p id="land">Destination.</p>',
                link_body: str | None = None) -> None:
    anchor = (link_body if link_body is not None else f'<a href="{href}">next chapter</a>')
    first = ('<html xmlns="http://www.w3.org/1999/xhtml"><body>'
             '<details><summary>Read more</summary><p>Visit ' + anchor +
             ' for context.</p></details></body></html>')
    second_xhtml = ('<html xmlns="http://www.w3.org/1999/xhtml"><body>'
                    + second + '</body></html>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml",
                   '<container><rootfile full-path="OPS/book.opf"/></container>')
        z.writestr("OPS/book.opf", '<package><metadata><title>Test</title>'
                   '<language>en</language></metadata><manifest>'
                   '<item id="one" href="first.xhtml" media-type="application/xhtml+xml"/>'
                   '<item id="two" href="second.xhtml" media-type="application/xhtml+xml"/>'
                   '</manifest><spine><itemref idref="one"/><itemref idref="two"/>'
                   '</spine></package>')
        z.writestr("OPS/first.xhtml", first)
        z.writestr("OPS/second.xhtml", second_xhtml)


class CrossSpineLinksTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "book.epub"

    def test_link_and_disclosure_are_source_bound_in_translated_epub(self):
        source_epub(self.path, "second.xhtml#land")
        original = self.path.read_bytes()
        self.assertTrue(ep.needs_preserved_xhtml(self.path))
        chapters, _ = book.read_epub(self.path)
        self.assertEqual(len(chapters), 2)
        report = ep.audit(self.path, chapters)
        self.assertEqual(report["state"], "verified")
        first = chapters[0].content
        self.assertIn("next chapter", first)
        self.assertIn("<!-- bt-inline:", first)
        target = (first.replace("Read more", "Подробнее")
                  .replace("Visit ", "Перейдите ")
                  .replace("next chapter", "к следующей главе")
                  .replace(" for context.", " для контекста."))
        out = ep.build(
            self.path, [{"markdown": target}, {"markdown": chapters[1].content.replace(
                "Destination.", "Назначение.")}],
            expected_source_sha256=hashlib.sha256(original).hexdigest(), language="ru")
        ep.validate(out, source=self.path, expected_docs=2)
        with zipfile.ZipFile(io.BytesIO(out)) as z:
            root = ET.fromstring(z.read("OPS/first.xhtml"))
            linked = [n for n in root.iter() if n.tag.endswith("}a")]
            self.assertEqual(len(linked), 1)
            self.assertEqual(linked[0].get("href"), "second.xhtml#land")
            self.assertIn("к следующей главе", "".join(linked[0].itertext()))
            self.assertIsNotNone(next((n for n in root.iter() if n.tag.endswith("}details")), None))
        self.assertEqual(self.path.read_bytes(), original)

    def test_missing_and_duplicate_cross_document_anchors_fail(self):
        for body in ('<p id="wrong">Target.</p>',
                     '<p id="land">One.</p><p id="land">Two.</p>'):
            with self.subTest(body=body):
                source_epub(self.path, "second.xhtml#land", second=body)
                with self.assertRaisesRegex(ep.PreservedEpubError,
                                            "missing or duplicate hyperlink fragment"):
                    book.read_epub(self.path)

    def test_cross_document_targets_must_be_in_spine(self):
        for href in ("not-listed.xhtml", "../other.xhtml", "second.xhtml#missing"):
            with self.subTest(href=href):
                source_epub(self.path, href)
                with self.assertRaises((ep.PreservedEpubError, book.BookError)):
                    book.read_epub(self.path)

    def test_unsupported_url_schemes_and_document_types_stay_blocked(self):
        for href in ("javascript:alert(1)", "second.svg#land",
                     "second.xhtml?x=1", "other.epub#entry"):
            with self.subTest(href=href):
                source_epub(self.path, href)
                with self.assertRaises((ep.PreservedEpubError, book.BookError)):
                    book.read_epub(self.path)

    def test_blocked_cross_link_retry_needs_exact_chatops_source(self):
        sys.path.insert(0, str(ROOT / "automation"))
        import book_runner as runner

        source_epub(self.path, "second.xhtml#land")
        source = self.path.read_bytes()
        digest = hashlib.sha256(source).hexdigest()
        job_id = "ac37df793a0a6b348c42de5376ce0e3d"
        root = Path(self.tmp.name)
        intake = root / "intake" / "auto" / job_id
        intake.mkdir(parents=True)
        (intake / "source.epub").write_bytes(source)
        (intake / "request.json").write_text(json.dumps({
            "schema_version": 1, "job_id": job_id, "source_sha256": digest,
            "target_language": "ru"}), encoding="utf8")
        control = {"schema_version": 1, "job_id": job_id,
                   "source_sha256": digest, "mode": "chatops"}
        (intake / "chatops-control.json").write_text(json.dumps(control), encoding="utf8")
        status = {"schema_version": 1, "job_id": job_id, "state": "blocked",
                  "phase": "operator-intervention-required", "book_slug": None,
                  "updated_at": "2026-11-22T15:31:49+00:00",
                  "last_error": "JobError: canonical CLI rejected operation: ERROR: "
                  "EPUB source-preserving extraction blocked: Alligator_Bites_Never_split_002.xhtml: "
                  "Unsupported cross-document hyperlink in prose\n"}
        with patch.object(runner, "ROOT", root):
            self.assertTrue(runner.known_epub_preflight_blocker(status))
            (intake / "chatops-control.json").write_text(
                json.dumps({**control, "source_sha256": "a" * 64}), encoding="utf8")
            self.assertFalse(runner.known_epub_preflight_blocker(status))
            (intake / "chatops-control.json").write_text(json.dumps(control), encoding="utf8")
            for change in (
                {"book_slug": "other-book"},
                {"active": {"role": "translator"}},
                {"last_error": status["last_error"].replace("cross-document", "other")},
                {"last_error": status["last_error"].replace("preserving", "coverage")},
            ):
                self.assertFalse(runner.known_epub_preflight_blocker({**status, **change}))

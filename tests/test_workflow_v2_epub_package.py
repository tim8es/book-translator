import io
import importlib
import sys
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


epub_output = importlib.import_module("workflow_v2.epub_output")


class WorkflowV2EpubPackageTests(unittest.TestCase):
    def api(self, name):
        value = getattr(epub_output, name, None)
        self.assertIsNotNone(value, f"{name} is not implemented")
        return value

    def units(self):
        return [
            {
                "number": 1,
                "title": "Один",
                "slug": "one",
                "markdown": "# Один\n\nПервый абзац.\n\n- A\n- B\n",
            },
            {
                "number": 2,
                "title": "Два",
                "slug": "two",
                "markdown": "# Два\n\n```\n<raw>&code\n```\n",
            },
        ]

    def build(self, *, cover=None):
        return self.api("build_epub_bytes")(
            book_slug="sample",
            title="Пример книги",
            author="Автор",
            language="ru",
            units=self.units(),
            fingerprint="a" * 64,
            cover=cover,
        )

    @staticmethod
    def repack(content, *, replace=None, remove=()):
        replace = replace or {}
        source = zipfile.ZipFile(io.BytesIO(content), "r")
        out = io.BytesIO()
        with source, zipfile.ZipFile(out, "w") as target:
            for info in source.infolist():
                if info.filename in remove:
                    continue
                payload = replace.get(info.filename, source.read(info.filename))
                copied = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                copied.compress_type = info.compress_type
                copied.external_attr = info.external_attr
                target.writestr(copied, payload)
        return out.getvalue()

    def test_markdown_renderer_outputs_parseable_escaped_xhtml_subset(self):
        render = self.api("render_markdown_xhtml")
        content = render(
            "Глава",
            "# Заголовок\n\nТекст <script>alert(1)</script>.\n\n1. один\n2. два\n\n```\n<x>&y\n```\n",
            language="ru",
        )
        root = ET.fromstring(content)
        self.assertTrue(root.tag.endswith("html"))
        text = content.decode("utf-8")
        self.assertIn("lang=\"ru\"", text)
        self.assertIn("&lt;script&gt;", text)
        self.assertNotIn("<script>", text)
        self.assertIn("<ol>", text)
        self.assertIn("<pre><code>", text)

    def test_epub_bytes_are_deterministic_and_have_required_order_metadata_nav_and_spine(self):
        first = self.build()
        second = self.build()
        self.assertEqual(first, second)

        with zipfile.ZipFile(io.BytesIO(first), "r") as zf:
            names = zf.namelist()
            self.assertEqual(names[0], "mimetype")
            self.assertEqual(zf.getinfo("mimetype").compress_type, zipfile.ZIP_STORED)
            self.assertEqual(zf.read("mimetype"), b"application/epub+zip")
            for required in (
                "META-INF/container.xml",
                "EPUB/package.opf",
                "EPUB/nav.xhtml",
                "EPUB/styles.css",
                "EPUB/text/001-one.xhtml",
                "EPUB/text/002-two.xhtml",
            ):
                self.assertIn(required, names)
            opf = zf.read("EPUB/package.opf").decode("utf-8")
            nav = zf.read("EPUB/nav.xhtml").decode("utf-8")
            self.assertIn("Пример книги", opf)
            self.assertIn("<dc:language>ru</dc:language>", opf)
            self.assertLess(opf.index("idref=\"chapter-001\""), opf.index("idref=\"chapter-002\""))
            self.assertLess(nav.index("text/001-one.xhtml"), nav.index("text/002-two.xhtml"))

    def test_cover_is_optional_and_when_present_is_packaged_with_cover_image_property(self):
        without_cover = self.build()
        with zipfile.ZipFile(io.BytesIO(without_cover), "r") as zf:
            self.assertFalse(any("cover" in name.lower() for name in zf.namelist()))

        cover = {
            "name": "cover.png",
            "media_type": "image/png",
            "content": b"fake-png-bytes",
        }
        with_cover = self.build(cover=cover)
        with zipfile.ZipFile(io.BytesIO(with_cover), "r") as zf:
            self.assertIn("EPUB/images/cover.png", zf.namelist())
            opf = zf.read("EPUB/package.opf").decode("utf-8")
            self.assertIn("properties=\"cover-image\"", opf)
            self.assertIn("image/png", opf)

    def test_validator_rejects_cover_media_type_that_disagrees_with_extension(self):
        validate = self.api("validate_epub_bytes")
        valid = self.build(
            cover={
                "name": "cover.png",
                "media_type": "image/png",
                "content": b"fake-png-bytes",
            }
        )
        with zipfile.ZipFile(io.BytesIO(valid), "r") as zf:
            opf = zf.read("EPUB/package.opf").replace(b"image/png", b"image/jpeg")
        mismatched = self.repack(valid, replace={"EPUB/package.opf": opf})
        with self.assertRaises(Exception):
            validate(mismatched, expected_unit_count=2)

    def test_validator_accepts_valid_package_and_rejects_container_and_content_corruption(self):
        validate = self.api("validate_epub_bytes")
        valid = self.build()
        result = validate(valid, expected_unit_count=2)
        self.assertEqual(result["unit_count"], 2)

        with self.assertRaises(Exception):
            validate(b"not-a-zip", expected_unit_count=2)

        bad_mimetype = self.repack(valid, replace={"mimetype": b"application/zip"})
        with self.assertRaises(Exception):
            validate(bad_mimetype, expected_unit_count=2)

        missing_nav = self.repack(valid, remove={"EPUB/nav.xhtml"})
        with self.assertRaises(Exception):
            validate(missing_nav, expected_unit_count=2)

        missing_chapter = self.repack(valid, remove={"EPUB/text/002-two.xhtml"})
        with self.assertRaises(Exception):
            validate(missing_chapter, expected_unit_count=2)

        malformed_chapter = self.repack(
            valid,
            replace={"EPUB/text/001-one.xhtml": b"<html><body>"},
        )
        with self.assertRaises(Exception):
            validate(malformed_chapter, expected_unit_count=2)

        with self.assertRaises(Exception):
            validate(valid, expected_unit_count=1)


if __name__ == "__main__":
    unittest.main()

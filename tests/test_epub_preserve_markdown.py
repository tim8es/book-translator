"""Integration regression: editable Markdown block exchange remains fail-closed."""
from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from workflow.epub_preserve_markdown import (
    EpubMarkedMarkdownError, make_markdown_bundle, preview_from_markdown,
)


def source_epub():
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml",
                   '<container><rootfile full-path="OPS/package.opf"/></container>')
        z.writestr("OPS/package.opf",
                   '<package xmlns:dc="http://purl.org/dc/elements/1.1/">'
                   '<metadata><dc:title>Home</dc:title><dc:language>en</dc:language></metadata>'
                   '<manifest><item id="story" href="story.xhtml" media-type="application/xhtml+xml"/>'
                   '<item id="css" href="book.css" media-type="text/css"/></manifest>'
                   '<spine><itemref idref="story"/></spine></package>')
        z.writestr("OPS/book.css", "p { margin-bottom:1.2em; }")
        z.writestr("OPS/story.xhtml",
                   '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
                   '<link rel="stylesheet" href="book.css"/></head>'
                   '<body><p id="a">He <em>never</em> forgot '
                   '<a href="#b">her</a>.</p><p id="b">A note.</p></body></html>')
    return out.getvalue()


class MarkedMarkdownTests(unittest.TestCase):
    def test_full_exchange_preserves_css_links_and_inline_emphasis(self):
        original = source_epub()
        manifest, files = make_markdown_bundle(original)
        self.assertEqual(len(manifest["documents"]), 1)
        self.assertEqual(list(files), ["0001.md"])
        self.assertNotIn("never", json.dumps(manifest))
        self.assertIn("<!-- BT:BLOCK b000001", files["0001.md"])
        updated = files["0001.md"].replace(
            "He [[BT:OPEN:0001]]never[[BT:CLOSE:0001]] forgot "
            "[[BT:OPEN:0002]]her[[BT:CLOSE:0002]].",
            "Он [[BT:OPEN:0001]]никогда[[BT:CLOSE:0001]] не забывал "
            "[[BT:OPEN:0002]]её[[BT:CLOSE:0002]].",
        ).replace("A note.", "Примечание.")
        self.assertNotEqual(updated, files["0001.md"])
        output = preview_from_markdown(original, manifest, {"0001.md": updated}, language="ru", title="Дом")
        with zipfile.ZipFile(io.BytesIO(output)) as z:
            self.assertEqual(z.testzip(), None)
            self.assertEqual(z.read("OPS/book.css"), b"p { margin-bottom:1.2em; }")
            text = z.read("OPS/story.xhtml").decode()
            self.assertIn('href="#b"', text)
            self.assertIn("никогда", text)
            self.assertIn("Примечание", text)
            self.assertIn("Дом", z.read("OPS/package.opf").decode())

    def test_rejects_missing_and_unexpected_block_or_file(self):
        original = source_epub()
        manifest, files = make_markdown_bundle(original)
        with self.assertRaisesRegex(EpubMarkedMarkdownError, "missing or unexpected"):
            preview_from_markdown(original, manifest, {}, language="ru")
        with self.assertRaisesRegex(EpubMarkedMarkdownError, "missing or unexpected"):
            preview_from_markdown(original, manifest, {"0001.md": files["0001.md"], "0002.md": ""}, language="ru")
        tampered = files["0001.md"].replace("BT:BLOCK b000001", "BT:BLOCK b000002", 1)
        with self.assertRaisesRegex(EpubMarkedMarkdownError, "missing or reordered"):
            preview_from_markdown(original, manifest, {"0001.md": tampered}, language="ru")
        tampered = files["0001.md"].replace("BT:OPEN:0001", "BT:OPEN:0099")
        with self.assertRaisesRegex(EpubMarkedMarkdownError, "unknown"):
            preview_from_markdown(original, manifest, {"0001.md": tampered}, language="ru")

    def test_rejects_source_drift_and_manifest_tampering(self):
        original = source_epub()
        manifest, files = make_markdown_bundle(original)
        bad = dict(manifest, source_sha256="0" * 64)
        with self.assertRaisesRegex(EpubMarkedMarkdownError, "manifest differs"):
            preview_from_markdown(original, bad, files, language="ru")
        with self.assertRaisesRegex(EpubMarkedMarkdownError, "source EPUB rejected|manifest differs"):
            preview_from_markdown(original + b" ", manifest, files, language="ru")

    def test_cli_staging_is_explicit_and_preview_cannot_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            original = root / "original.epub"
            original.write_bytes(source_epub())
            stage = root / "blocks"
            dest = root / "book_PREVIEW.epub"
            script = ROOT / "scripts" / "book.py"
            def invoke(*args):
                return subprocess.run([sys.executable, str(script), *map(str, args)],
                                      cwd=ROOT, capture_output=True, text=True)
            exported = invoke("epub-preserve-export", original, "--output-dir", stage)
            self.assertEqual(exported.returncode, 0, exported.stderr)
            self.assertTrue((stage / "0001.md").is_file())
            blocked = invoke("epub-preserve-export", original, "--output-dir", stage)
            self.assertNotEqual(blocked.returncode, 0)
            rejected = invoke("epub-preserve-preview", original, "--translations-dir", stage,
                              "--language", "ru", "--output", root / "final.epub")
            self.assertNotEqual(rejected.returncode, 0)
            built = invoke("epub-preserve-preview", original, "--translations-dir", stage,
                           "--language", "ru", "--output", dest)
            self.assertEqual(built.returncode, 0, built.stderr)
            self.assertTrue(dest.is_file())
            again = invoke("epub-preserve-preview", original, "--translations-dir", stage,
                           "--language", "ru", "--output", dest)
            self.assertNotEqual(again.returncode, 0)
            self.assertEqual(dest.read_bytes(), dest.read_bytes())


if __name__ == "__main__":
    unittest.main()

"""Independent XHTML source-token coverage for marked Markdown EPUB staging."""
from __future__ import annotations

import io
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from workflow.epub_preserve_markdown import make_markdown_bundle
from workflow.epub_preserve_coverage import MarkedCoverageError, attest_marked_source


def fixture(*, hidden=False):
    attr = ' style="display:none"' if hidden else ''
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml",
                   '<container><rootfile full-path="OPS/book.opf"/></container>')
        z.writestr("OPS/book.opf",
                   '<package xmlns:dc="http://purl.org/dc/elements/1.1/">'
                   '<metadata><dc:title>One</dc:title><dc:language>en</dc:language></metadata>'
                   '<manifest><item id="story" href="story.xhtml" media-type="application/xhtml+xml"/>'
                   '<item id="css" href="style.css" media-type="text/css"/></manifest>'
                   '<spine><itemref idref="story"/></spine></package>')
        z.writestr("OPS/style.css", "p { font-weight: normal; }")
        z.writestr("OPS/story.xhtml",
                   '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>One</title>'
                   '<link href="style.css" rel="stylesheet"/></head><body>'
                   '<h1>Chapter</h1><p id="p1">He <em>never</em> forgot '
                   '<a href="#note">her</a>.</p>'
                   f'<p id="note"{attr}>A note.</p></body></html>')
    return out.getvalue()


class MarkedSourceCoverageTests(unittest.TestCase):
    def test_independent_reference_tokens_pass_without_leaking_prose(self):
        epub = fixture()
        manifest, markdown = make_markdown_bundle(epub)
        report = attest_marked_source(epub, manifest, markdown)
        self.assertEqual(report["state"], "verified_staging_only")
        self.assertEqual(report["document_count"], 1)
        self.assertEqual(report["documents"][0]["block_count"], 3)
        self.assertNotIn("forgot", str(report))
        self.assertEqual(report["source_sha256"], manifest["source_sha256"])

    def test_changed_prose_cannot_pass_source_attestation(self):
        epub = fixture()
        manifest, markdown = make_markdown_bundle(epub)
        edited = markdown["0001.md"].replace("never", "always")
        with self.assertRaisesRegex(MarkedCoverageError, "tokens differ"):
            attest_marked_source(epub, manifest, {"0001.md": edited})

    def test_missing_document_or_changed_header_is_rejected(self):
        epub = fixture()
        manifest, markdown = make_markdown_bundle(epub)
        with self.assertRaisesRegex(MarkedCoverageError, "missing or extra"):
            attest_marked_source(epub, manifest, {})
        altered = markdown["0001.md"].replace("BT:DOC", "BT:CHANGE", 1)
        with self.assertRaisesRegex(MarkedCoverageError, "mapped source"):
            attest_marked_source(epub, manifest, {"0001.md": altered})

    def test_hidden_original_prose_never_receives_coverage_pass(self):
        epub = fixture(hidden=True)
        manifest, markdown = make_markdown_bundle(epub)
        with self.assertRaisesRegex(MarkedCoverageError, "hidden"):
            attest_marked_source(epub, manifest, markdown)

    def test_audit_cli_writes_text_free_stage_report_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            original = folder / "original.epub"
            original.write_bytes(fixture())
            stage = folder / "blocks"
            report = folder / "independent-staging-audit.json"
            script = ROOT / "scripts" / "book.py"
            def run(*args):
                return subprocess.run([sys.executable, str(script), *map(str, args)],
                                      cwd=ROOT, capture_output=True, text=True)
            first = run("epub-preserve-export", original, "--output-dir", stage)
            self.assertEqual(first.returncode, 0, first.stderr)
            audit = run("epub-preserve-audit", original, "--translations-dir", stage, "--output", report)
            self.assertEqual(audit.returncode, 0, audit.stderr)
            self.assertIn('"verified_staging_only"', report.read_text(encoding="utf-8"))
            self.assertNotIn("never", report.read_text(encoding="utf-8"))
            again = run("epub-preserve-audit", original, "--translations-dir", stage, "--output", report)
            self.assertNotEqual(again.returncode, 0)


if __name__ == "__main__":
    unittest.main()

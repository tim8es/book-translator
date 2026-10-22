"""Independent structure/resource preservation tests for opt-in EPUB builds."""
from __future__ import annotations

import io
import sys
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from workflow.epub_preserve_validation import EpubPreservationError, verify_preservation
from workflow.epub_preserve_build import preview_from_source
from workflow.xhtml_template import inspect_xhtml


def fixture():
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w") as archive:
        archive.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml",
                         '<container><rootfile full-path="OPS/book.opf"/></container>')
        archive.writestr("OPS/book.opf",
                         '<package xmlns:dc="http://purl.org/dc/elements/1.1/">'
                         '<metadata><dc:title>Old Title</dc:title><dc:language>en</dc:language></metadata>'
                         '<manifest>'
                         '<item id="story" href="story.xhtml" media-type="application/xhtml+xml"/>'
                         '<item id="css" href="original.css" media-type="text/css"/>'
                         '</manifest><spine><itemref idref="story"/></spine></package>')
        archive.writestr("OPS/original.css", "body { line-height: 1.3; }")
        archive.writestr("OPS/story.xhtml",
                         '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
                         '<link rel="stylesheet" href="original.css"/></head>'
                         '<body><h1>Heading</h1><p id="note">He <em>never</em> left.</p>'
                         '<hr/><p>A note.</p></body></html>')
    return result.getvalue()


def build_identity(source):
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        document = inspect_xhtml(archive.read("OPS/story.xhtml"))
    values = {b.key: b.text for b in document.blocks}
    values["b000002"] = "Он [[BT:OPEN:0001]]никогда[[BT:CLOSE:0001]] не уходил."
    return preview_from_source(source, {"OPS/story.xhtml": values}, language="ru", title="Новое название")


def tamper(epub, resource, replacement):
    result = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(epub)) as original, zipfile.ZipFile(result, "w") as rebuilt:
        for member in original.infolist():
            data = original.read(member.filename)
            if member.filename == resource:
                data = replacement(data)
            rebuilt.writestr(member, data)
    return result.getvalue()


class PreservationGateTests(unittest.TestCase):
    def test_successful_preview_attestation(self):
        source = fixture()
        output = build_identity(source)
        record = verify_preservation(source, output)
        self.assertEqual(record["validator"], "epub-source-preservation-v1")
        self.assertEqual(record["spine_document_count"], 1)
        self.assertEqual(record["resource_count"], 2)
        self.assertTrue(record["unchanged_nontext_resources"])
        self.assertEqual(record["changed_paths"], ["OPS/book.opf", "OPS/story.xhtml"])

    def test_nontext_asset_byte_changes_are_rejected(self):
        source = fixture()
        output = build_identity(source)
        corrupted = tamper(output, "OPS/original.css", lambda raw: raw + b" body { display: none; }")
        with self.assertRaisesRegex(EpubPreservationError, "resource bytes changed"):
            verify_preservation(source, corrupted)

    def test_unexpected_structural_mutation_is_rejected(self):
        source = fixture()
        output = build_identity(source)
        corrupted = tamper(output, "OPS/story.xhtml",
                           lambda raw: raw.replace(b'id="note"', b'id="changed"'))
        with self.assertRaisesRegex(EpubPreservationError, "structure changed"):
            verify_preservation(source, corrupted)

    def test_inline_emphasis_attribute_mutation_is_rejected(self):
        source = fixture()
        output = build_identity(source)

        def change_inline(raw):
            root = ET.fromstring(raw)
            em = next(x for x in root.iter() if x.tag.rsplit("}", 1)[-1] == "em")
            em.set("class", "tampered")
            return ET.tostring(root, encoding="utf-8")

        corrupted = tamper(output, "OPS/story.xhtml", change_inline)
        with self.assertRaisesRegex(EpubPreservationError, "structure changed"):
            verify_preservation(source, corrupted)

    def test_opf_is_restricted_to_title_language(self):
        source = fixture()
        output = build_identity(source)
        corrupted = tamper(output, "OPS/book.opf",
                           lambda raw: raw.replace(b"<metadata>", b'<metadata injected="yes">'))
        with self.assertRaises(EpubPreservationError):
            verify_preservation(source, corrupted)

    def test_missing_or_added_member_is_rejected(self):
        source = fixture()
        output = build_identity(source)
        result = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(output)) as previous, zipfile.ZipFile(result, "w") as writer:
            for member in previous.infolist():
                writer.writestr(member, previous.read(member.filename))
            writer.writestr("OPS/injected.xhtml", "<html/>")
        with self.assertRaisesRegex(EpubPreservationError, "added, removed or reordered"):
            verify_preservation(source, result.getvalue())


if __name__ == "__main__":
    unittest.main()

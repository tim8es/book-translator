"""Release gates for real-world EPUB navigation, archive safety and image-only units."""
from __future__ import annotations

import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from workflow.epub_structure import (
    StructureError, audit_structural, build, manifest_and_spine, read_chapters,
)


def make_epub(*, nav_in_spine=False, multi_nav=True, ncx=False, cover=False, extra_name=None):
    opf_manifest = ('<item id="story" href="story.xhtml" media-type="application/xhtml+xml"/>'
                    '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
                    '<item id="pic" href="cover.png" media-type="image/png"/>')
    if ncx:
        opf_manifest += '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
    if cover:
        opf_manifest += '<item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>'
    spine = '<itemref idref="story"/>'
    if nav_in_spine:
        spine += '<itemref idref="nav"/>'
    if cover:
        spine += '<itemref idref="cover"/>'
    package = (
        '<package xmlns:dc="http://purl.org/dc/elements/1.1/" version="3.0">'
        '<metadata><dc:title>Story</dc:title><dc:language>en</dc:language></metadata>'
        f'<manifest>{opf_manifest}</manifest><spine>{spine}</spine></package>'
    )
    story = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        '<section><h1 id="first">First chapter</h1><p>Text one.</p>'
        '<h2 id="second">Second section</h2><p>Text two.</p></section>'
        '</body></html>'
    )
    nav = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        '<h1>Table of contents</h1>' if nav_in_spine else
        '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
    ) + (
        '<nav><ol><li><a href="story.xhtml#first">First chapter</a></li>'
        '<li><a href="story.xhtml#second">Second section</a></li></ol></nav>'
    ) + (
        '<nav><ol><li><a href="story.xhtml#first">Landmark</a></li></ol></nav>'
        if multi_nav else ""
    ) + '</body></html>'
    ncx_data = (
        '<ncx><navMap>'
        '<navPoint><navLabel><text>First chapter</text></navLabel>'
        '<content src="story.xhtml#first"/></navPoint>'
        '<navPoint><navLabel><text>Second section</text></navLabel>'
        '<content src="story.xhtml#second"/></navPoint>'
        '</navMap></ncx>'
    )
    files = {
        "mimetype": "application/epub+zip",
        "META-INF/container.xml": '<container><rootfile full-path="OPS/content.opf"/></container>',
        "OPS/content.opf": package,
        "OPS/story.xhtml": story,
        "OPS/nav.xhtml": nav,
        "OPS/cover.png": b"\x89PNG\r\n",
    }
    if ncx:
        files["OPS/toc.ncx"] = ncx_data
    if cover:
        files["OPS/cover.xhtml"] = ('<html xmlns="http://www.w3.org/1999/xhtml"><body>'
                                   '<div><img src="cover.png"/></div></body></html>')
    if extra_name:
        files[extra_name] = b"unreferenced"
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for path, value in files.items():
            z.writestr(path, value,
                       compress_type=zipfile.ZIP_STORED if path == "mimetype" else zipfile.ZIP_DEFLATED)
    return out.getvalue()


def source_units(data):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "source.epub"
        path.write_bytes(data)
        return read_chapters(path)[0]




def rewrite_epub(original, *, replace=None, add=None):
    """Build a controlled EPUB mutation for negative security tests."""
    replace = replace or {}
    add = add or {}
    memory = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(original)) as old, zipfile.ZipFile(memory, "w") as output:
        for item in old.infolist():
            value = old.read(item.filename)
            if item.filename in replace:
                value = replace[item.filename](value)
            output.writestr(item, value)
        for filename, value in add.items():
            output.writestr(filename, value)
    return memory.getvalue()

class NavigationSecurityTests(unittest.TestCase):
    def test_two_navigation_sections_are_allowed(self):
        raw = make_epub(multi_nav=True)
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            _, _, _, spine = manifest_and_spine(z)
        self.assertEqual(spine, ["OPS/story.xhtml"])

    def test_each_fragment_uses_its_translated_heading(self):
        raw = make_epub(ncx=True)
        units = source_units(raw)
        text = units[0][1].replace("First chapter", "Глава первая")
        text = text.replace("Second section", "Раздел второй")
        built = build(raw, document_paths=[x[2] for x in units],
                      translations=[text], target_language="ru", title="История")
        with zipfile.ZipFile(io.BytesIO(built)) as z:
            nav = ET.fromstring(z.read("OPS/nav.xhtml"))
            labels = [("".join(a.itertext()).strip()) for a in nav.iter()
                      if a.tag.rsplit("}", 1)[-1] == "a"]
            self.assertEqual(labels[:2], ["Глава первая", "Раздел второй"])
            ncx = ET.fromstring(z.read("OPS/toc.ncx"))
            nlabels = [n.text for n in ncx.iter()
                       if n.tag.rsplit("}", 1)[-1] == "text"]
            self.assertEqual(nlabels, ["Глава первая", "Раздел второй"])

    def test_in_spine_navigation_keeps_translated_nonlink_text(self):
        raw = make_epub(nav_in_spine=True, multi_nav=False)
        units = source_units(raw)
        story_translation = units[0][1].replace("First chapter", "Глава первая")
        nav_translation = units[1][1].replace("Table of contents", "Оглавление")
        built = build(raw, document_paths=[x[2] for x in units],
                      translations=[story_translation, nav_translation],
                      target_language="ru", title="История")
        with zipfile.ZipFile(io.BytesIO(built)) as z:
            nav = ET.fromstring(z.read("OPS/nav.xhtml"))
            headings = [("".join(n.itertext()).strip()) for n in nav.iter()
                        if n.tag.rsplit("}", 1)[-1] == "h1"]
            self.assertEqual(headings, ["Оглавление"])

    def test_image_only_spine_has_valid_structural_coverage(self):
        raw = make_epub(cover=True)
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "test.epub"
            source.write_bytes(raw)
            units, layout = read_chapters(source)
            self.assertEqual(len(units), 2)
            class Chapter:
                def __init__(self, content):
                    self.content = content
            report = audit_structural(source, [Chapter(u[1]) for u in units])
            self.assertEqual(report["documents"][-1]["text_tokens"], 0)
            self.assertGreater(report["documents"][-1]["text_blocks"], 0)

    def test_traversal_archive_member_is_blocked(self):
        for bad in ("../payload.sh", "OPS/../../payload.sh", "C:/payload.sh", "OPS\\payload.sh"):
            with self.subTest(path=bad):
                raw = make_epub(extra_name=bad)
                with zipfile.ZipFile(io.BytesIO(raw)) as z:
                    with self.assertRaisesRegex(StructureError, "unsafe EPUB archive"):
                        manifest_and_spine(z)

    def test_archive_symlink_member_is_blocked(self):
        original = make_epub()
        out = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(original)) as old, zipfile.ZipFile(out, "w") as new:
            for item in old.infolist():
                new.writestr(item, old.read(item.filename))
            linked = zipfile.ZipInfo("OPS/link")
            linked.create_system = 3
            linked.external_attr = (0o120777 << 16)
            new.writestr(linked, "../payload")
        with zipfile.ZipFile(io.BytesIO(out.getvalue())) as z:
            with self.assertRaisesRegex(StructureError, "symlink"):
                manifest_and_spine(z)


    def test_active_xhtml_and_executable_resources_are_blocked(self):
        base = make_epub()
        for substitution in (
            (b"</body>", b'<script>alert(1)</script></body>'),
            (b"<body>", b'<body onclick="alert(1)">'),
            (b"</body>", b'<a href="javascript:alert(1)">Unsafe</a></body>'),
        ):
            with self.subTest(substitution=substitution[1][:50]):
                result = io.BytesIO()
                with zipfile.ZipFile(io.BytesIO(base)) as original, zipfile.ZipFile(result, "w") as out:
                    for info in original.infolist():
                        value = original.read(info.filename)
                        if info.filename == "OPS/story.xhtml":
                            value = value.replace(*substitution)
                        out.writestr(info, value)
                with zipfile.ZipFile(io.BytesIO(result.getvalue())) as z:
                    with self.assertRaisesRegex(StructureError, "unsafe|active"):
                        manifest_and_spine(z)

    def test_duplicate_anchor_ids_are_rejected_at_build(self):
        raw = make_epub()
        corrupted = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(raw)) as original, zipfile.ZipFile(corrupted, "w") as out:
            for info in original.infolist():
                value = original.read(info.filename)
                if info.filename == "OPS/story.xhtml":
                    value = value.replace(b'id="first"', b'id="second"')
                out.writestr(info, value)
        source = corrupted.getvalue()
        units = source_units(source)
        with self.assertRaisesRegex(StructureError, "duplicate internal XHTML anchor"):
            build(source, document_paths=[x[2] for x in units],
                  translations=[x[1] for x in units], target_language="ru", title="История")

    def test_partial_protected_marker_is_never_accepted_as_prose(self):
        raw = make_epub()
        units = source_units(raw)
        altered = units[0][1].replace("Text one.", "Text one. ⟦BT:O:0001")
        with self.assertRaisesRegex(StructureError, "protected"):
            build(raw, document_paths=[x[2] for x in units],
                  translations=[altered], target_language="ru", title="История")



    def test_remote_auto_loaded_resources_are_rejected(self):
        for snippet in (
            b'<img src="https://tracker.example/pixel.png"/>',
            b'<link rel="stylesheet" href="https://tracker.example/style.css"/>',
        ):
            with self.subTest(snippet=snippet[:40]):
                raw = rewrite_epub(make_epub(), replace={
                    "OPS/story.xhtml": lambda body: body.replace(b"<body>", b"<body>" + snippet)
                })
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    with self.assertRaisesRegex(StructureError, "remote XHTML"):
                        manifest_and_spine(archive)

    def test_off_spine_nav_cannot_hide_non_navigation_prose(self):
        raw = rewrite_epub(make_epub(), replace={
            "OPS/nav.xhtml": lambda body: body.replace(b"<ol>", b"<p>Untranslated appendix.</p><ol>", 1)
        })
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            with self.assertRaisesRegex(StructureError, "navigation.*prose"):
                manifest_and_spine(archive)

    def test_mime_declared_xhtml_is_scanned_even_with_xml_extension(self):
        raw = rewrite_epub(make_epub(), replace={
            "OPS/content.opf": lambda body: body.replace(
                b"</manifest>",
                b'<item id="hidden" href="active.xml" media-type="application/xhtml+xml"/></manifest>'
            )
        }, add={
            "OPS/active.xml": (b'<html><head><script>alert(1)</script></head>'
                               b'<body/></html>')
        })
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            with self.assertRaisesRegex(StructureError, "active XHTML"):
                manifest_and_spine(archive)

    def test_doctype_and_entities_rejected_in_story_and_package(self):
        cases = [
            ("OPS/story.xhtml", lambda b: b'<!DOCTYPE html [<!ENTITY x "x">]>' + b),
            ("OPS/content.opf", lambda b: b'<!DOCTYPE package [<!ENTITY x "x">]>' + b),
        ]
        for member, edit in cases:
            with self.subTest(member=member):
                raw = rewrite_epub(make_epub(), replace={member: edit})
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    with self.assertRaisesRegex(StructureError, "DTD/entities"):
                        manifest_and_spine(archive)

    def test_svg_media_spoofing_and_css_remote_import_are_rejected(self):
        raw = rewrite_epub(make_epub(), replace={
            "OPS/content.opf": lambda b: b.replace(
                b"</manifest>", b'<item id="fake" href="fake.svg" media-type="image/png"/></manifest>'
            )
        }, add={"OPS/fake.svg": b'<svg><script>alert(1)</script></svg>'})
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            with self.assertRaisesRegex(StructureError, "executable"):
                manifest_and_spine(archive)
        png_spoofed = rewrite_epub(make_epub(), replace={
            "OPS/cover.png": lambda _: b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>'
        })
        with zipfile.ZipFile(io.BytesIO(png_spoofed)) as archive:
            with self.assertRaisesRegex(StructureError, "SVG masquerading"):
                manifest_and_spine(archive)
        css = rewrite_epub(make_epub(), replace={
            "OPS/content.opf": lambda b: b.replace(
                b"</manifest>", b'<item id="style" href="style.css" media-type="text/css"/></manifest>'
            )
        }, add={"OPS/style.css": b'@import url(https://tracker.example/malicious.css);'})
        with zipfile.ZipFile(io.BytesIO(css)) as archive:
            with self.assertRaisesRegex(StructureError, "remote or executable CSS"):
                manifest_and_spine(archive)

    def test_broken_local_image_is_rejected_even_when_xml_parses(self):
        source = rewrite_epub(make_epub(), replace={
            "OPS/story.xhtml": lambda b: b.replace(b"<body>", b'<body><img src="missing.png"/>')
        })
        # A bare image outside a block might block extraction first, which is
        # independently fail-closed; embed it in an existing paragraph instead.
        source = rewrite_epub(make_epub(), replace={
            "OPS/story.xhtml": lambda b: b.replace(b"<p>Text two.</p>", b'<p>Text two.<img src="missing.png"/></p>')
        })
        units = source_units(source)
        with self.assertRaisesRegex(StructureError, "broken internal link/resource"):
            build(source, document_paths=[u[2] for u in units],
                  translations=[u[1] for u in units], target_language="ru", title="История")



if __name__ == "__main__":
    unittest.main()

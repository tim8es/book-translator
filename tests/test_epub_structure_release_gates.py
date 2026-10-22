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



    @staticmethod
    def mutate_epub(original: bytes, replace=None, additions=None) -> bytes:
        """Modify synthetic test package without needing published-book content."""
        replace = replace or {}
        additions = additions or {}
        out = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(original)) as z, zipfile.ZipFile(out, "w") as writer:
            for info in z.infolist():
                data = z.read(info.filename)
                for old, new in replace.get(info.filename, []):
                    data = data.replace(old, new)
                writer.writestr(info, data)
            for name, payload in additions.items():
                writer.writestr(name, payload)
        return out.getvalue()

    def test_remote_autoloaded_resources_are_blocked_but_external_anchors_allowed(self):
        raw = make_epub()
        modifications = (
            (b"</body>", b'<p><img src="https://tracker.invalid/pixel"/></p></body>'),
            (b"<body>", b'<head><link href="https://tracker.invalid/style.css" rel="stylesheet"/></head><body>'),
            (b"</body>", b'<p><img src="//tracker.invalid/pixel"/></p></body>'),
        )
        for old, new in modifications:
            with self.subTest(new=new[:65]):
                data = self.mutate_epub(raw, {"OPS/story.xhtml": [(old, new)]})
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    with self.assertRaisesRegex(StructureError, "remote/unsafe"):
                        manifest_and_spine(z)
        valid = self.mutate_epub(
            raw, {"OPS/story.xhtml": [(b"</body>", b'<p><a href="https://example.org/works">Site</a></p></body>')]}
        )
        with zipfile.ZipFile(io.BytesIO(valid)) as z:
            manifest_and_spine(z)

    def test_xhtml_mime_is_checked_even_with_xml_filename(self):
        original = make_epub()
        data = self.mutate_epub(
            original,
            {"OPS/content.opf": [
                (b"</manifest>", b'<item id="hidden" href="hidden.xml" media-type="application/xhtml+xml"/></manifest>')
            ]},
            {"OPS/hidden.xml": b'<html><head><script>alert(1)</script></head><body></body></html>'},
        )
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            with self.assertRaisesRegex(StructureError, "active"):
                manifest_and_spine(z)

    def test_unmapped_navigation_prose_is_not_coverage_excluded(self):
        original = make_epub()
        data = self.mutate_epub(
            original, {"OPS/nav.xhtml": [
                (b"</nav>", b'<p>Secret chapter prose not in reading order.</p></nav>')
            ]}
        )
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            with self.assertRaisesRegex(StructureError, "unmapped prose/element"):
                manifest_and_spine(z)

    def test_xml_dtd_declarations_are_rejected_in_package_and_spine(self):
        original = make_epub()
        for path, needle, doctype in (
            ("OPS/story.xhtml", b"<html", b'<!DOCTYPE html [<!ENTITY internal "text">]><html'),
            ("OPS/content.opf", b"<package", b'<!DOCTYPE package [<!ENTITY internal "text">]><package'),
            ("META-INF/container.xml", b"<container", b'<!DOCTYPE container [<!ENTITY internal "text">]><container'),
        ):
            with self.subTest(path=path):
                data = self.mutate_epub(original, {path: [(needle, doctype)]})
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    with self.assertRaisesRegex(StructureError, "DTD/entity"):
                        manifest_and_spine(z)

    def test_disguised_svg_and_remote_css_are_rejected(self):
        original = make_epub()
        svg = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"><script>bad()</script></svg>'
        mislabeled = self.mutate_epub(
            original, {"OPS/content.opf": [
                (b"</manifest>", b'<item id="imposter" href="imposter.png" media-type="image/png"/></manifest>')
            ]}, {"OPS/imposter.png": svg}
        )
        with zipfile.ZipFile(io.BytesIO(mislabeled)) as z:
            with self.assertRaisesRegex(StructureError, "SVG"):
                manifest_and_spine(z)
        css = self.mutate_epub(
            original, {"OPS/content.opf": [
                (b"</manifest>", b'<item id="css" href="style.css" media-type="text/css"/></manifest>')
            ]}, {"OPS/style.css": b'@import url("https://tracker.invalid/styles.css");'}
        )
        with zipfile.ZipFile(io.BytesIO(css)) as z:
            with self.assertRaisesRegex(StructureError, "remote CSS"):
                manifest_and_spine(z)

    def test_xhtml_navigation_resource_links_are_checked(self):
        original = make_epub()
        data = self.mutate_epub(
            original, {"OPS/story.xhtml": [(b"</body>", b'<p><img src="missing.png"/></p></body>')]}
        )
        units = source_units(data)
        with self.assertRaisesRegex(StructureError, "broken internal resource"):
            build(data, document_paths=[u[2] for u in units],
                  translations=[u[1] for u in units], target_language="ru", title="История")


    def test_meta_refresh_redirects_are_blocked_even_in_head(self):
        original = make_epub()
        for directive in ("refresh", "REFRESH", "set-cookie"):
            with self.subTest(http_equiv=directive):
                changed = self.mutate_epub(original, {
                    "OPS/story.xhtml": [(
                        b"<body>",
                        (f'<head><meta http-equiv="{directive}" '
                         f'content="0;url=https://tracker.invalid/open"/></head><body>').encode("utf-8"),
                    )]
                })
                with zipfile.ZipFile(io.BytesIO(changed)) as z:
                    with self.assertRaisesRegex(StructureError, "unsafe meta HTTP directive"):
                        manifest_and_spine(z)

    def test_inert_legacy_meta_content_type_is_allowed(self):
        original = make_epub()
        for directive, content in (
            ("Content-Type", "application/xhtml+xml; charset=utf-8"),
            ("X-UA-Compatible", "IE=edge"),
            ("Content-Language", "en"),
        ):
            with self.subTest(meta_directive=directive):
                inserted = (
                    f'<head><meta http-equiv="{directive}" content="{content}"/></head><body>'
                ).encode("utf-8")
                source = self.mutate_epub(original, {
                    "OPS/story.xhtml": [(b"<body>", inserted)]
                })
                with zipfile.ZipFile(io.BytesIO(source)) as z:
                    manifest_and_spine(z)



if __name__ == "__main__":
    unittest.main()

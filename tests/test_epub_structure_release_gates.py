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

    def test_duplicate_anchor_ids_are_rejected_at_admission(self):
        raw = make_epub()
        corrupted = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(raw)) as original, zipfile.ZipFile(corrupted, "w") as out:
            for info in original.infolist():
                value = original.read(info.filename)
                if info.filename == "OPS/story.xhtml":
                    value = value.replace(b'id="first"', b'id="second"')
                out.writestr(info, value)
        source = corrupted.getvalue()
        # Duplicate anchors must be rejected at admission, before translation.
        with self.assertRaisesRegex(StructureError, "duplicate internal XHTML anchor"):
            source_units(source)

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
                    with self.assertRaisesRegex(StructureError, "remote/unsafe|external stylesheet"):
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



    def test_embedded_style_remote_import_is_rejected(self):
        original = make_epub()
        for content in (
            b'@import url("https://tracker.invalid/css");',
            b'body { background:url(//tracker.invalid/pixel.png) }',
        ):
            with self.subTest(css=content):
                revised = self.mutate_epub(original, {"OPS/story.xhtml": [
                    (b"<body>", b"<head><style>" + content + b"</style></head><body>")
                ]})
                with zipfile.ZipFile(io.BytesIO(revised)) as z:
                    with self.assertRaisesRegex(StructureError, "remote embedded CSS"):
                        manifest_and_spine(z)

    def test_off_spine_styled_nav_label_is_localized_with_wrapper_retained(self):
        source = self.mutate_epub(
            make_epub(multi_nav=False), {"OPS/nav.xhtml": [
                (b">First chapter</a>", b"><span class='toc'>First chapter</span></a>")
            ]}
        )
        units = source_units(source)
        rendered = units[0][1].replace("First chapter", "Первая глава")
        result = build(source, document_paths=[x[2] for x in units],
                       translations=[rendered],
                       target_language="ru", title="Книга")
        with zipfile.ZipFile(io.BytesIO(result)) as z:
            root = ET.fromstring(z.read("OPS/nav.xhtml"))
            first = next(n for n in root.iter() if n.tag.rsplit("}", 1)[-1] == "a")
            self.assertEqual("".join(first.itertext()), "Первая глава")
            self.assertEqual(first[0].tag.rsplit("}", 1)[-1], "span")
            self.assertEqual(first[0].attrib["class"], "toc")

    def test_off_spine_nav_without_translated_target_heading_is_blocked(self):
        source = self.mutate_epub(
            make_epub(multi_nav=False), {
                "OPS/story.xhtml": [(b"<p>Text one.</p>", b'<p id="plain">Text one.</p>')],
                "OPS/nav.xhtml": [(b"story.xhtml#first", b"story.xhtml#plain")],
            }
        )
        # Source admission must reject unusable labels before translation.
        with tempfile.TemporaryDirectory() as tmp:
            source_path = Path(tmp) / "original.epub"
            source_path.write_bytes(source)
            with self.assertRaisesRegex(StructureError, "label cannot be translated"):
                read_chapters(source_path)

    def test_off_spine_multitext_styled_label_is_rejected(self):
        source = self.mutate_epub(
            make_epub(multi_nav=False), {"OPS/nav.xhtml": [
                (b">First chapter</a>", b">First <em>chapter</em></a>")
            ]}
        )
        with tempfile.TemporaryDirectory() as tmp:
            source_path = Path(tmp) / "original.epub"
            source_path.write_bytes(source)
            with self.assertRaisesRegex(StructureError, "without losing formatting"):
                read_chapters(source_path)


    def test_css_escape_remote_loads_are_rejected_before_translation(self):
        source = make_epub()
        cases = (
            b'url(h\\74 tps://tracker.invalid/pixel)',
            b'@im\\70 ort url(https://tracker.invalid/style.css)',
            b'body {background:url(\\68\\74\\74\\70\\73://tracker.invalid/x)}',
        )
        for css in cases:
            with self.subTest(css=css):
                epub = self.mutate_epub(source, {
                    "OPS/content.opf": [(b"</manifest>", b'<item id="xcss" href="x.css" media-type="text/css"/></manifest>')]
                }, {"OPS/x.css": css})
                with zipfile.ZipFile(io.BytesIO(epub)) as archive:
                    with self.assertRaisesRegex(StructureError, "remote CSS"):
                        manifest_and_spine(archive)

    def test_text_attributes_are_rejected_at_source_admission(self):
        original = make_epub()
        samples = (
            b'<p><img src="cover.png" alt="English description"/></p>',
            b'<p><em title="Meaning">Word</em></p>',
            b'<p aria-label="English accessibility label">Text</p>',
        )
        for element in samples:
            with self.subTest(element=element):
                revised = self.mutate_epub(original, {
                    "OPS/story.xhtml": [(b"</body>", element + b"</body>")]
                })
                with zipfile.ZipFile(io.BytesIO(revised)) as z:
                    with self.assertRaisesRegex(StructureError, "untranslated XHTML textual attribute"):
                        manifest_and_spine(z)

    def test_local_css_escapes_remain_supported(self):
        source = make_epub()
        css = b'body { background:url(cover.png); } p::before { content:"\\00a0"; }'
        revised = self.mutate_epub(source, {
            "OPS/content.opf": [(b"</manifest>", b'<item id="xcss" href="x.css" media-type="text/css"/></manifest>')]
        }, {"OPS/x.css": css})
        with zipfile.ZipFile(io.BytesIO(revised)) as archive:
            manifest_and_spine(archive)

    def test_head_title_localizes_from_translated_heading(self):
        source = self.mutate_epub(make_epub(multi_nav=False), {
            "OPS/story.xhtml": [(b"<body>", b"<head><title>First chapter</title></head><body>")],
            "OPS/nav.xhtml": [(b"<body>", b"<head><title>Contents</title></head><body>")]
        })
        chapters = source_units(source)
        revised = chapters[0][1].replace("First chapter", "Первая глава")
        result = build(source, document_paths=[x[2] for x in chapters],
                       translations=[revised], target_language="ru", title="Книга")
        with zipfile.ZipFile(io.BytesIO(result)) as z:
            doc = ET.fromstring(z.read("OPS/story.xhtml"))
            nav = ET.fromstring(z.read("OPS/nav.xhtml"))
            self.assertEqual(doc.find(".//{*}title").text, "Первая глава")
            self.assertEqual(nav.find(".//{*}title").text, "Книга")

    def test_unmappable_head_title_is_blocked_before_admission(self):
        source = self.mutate_epub(make_epub(), {
            "OPS/story.xhtml": [
                (b"<body>", b"<head><title>English title</title></head><body>"),
                (b"<h1 id=\"first\">First chapter</h1>", b"<p id=\"first\">First chapter</p>"),
                (b"<h2 id=\"second\">Second section</h2>", b"<p id=\"second\">Second section</p>")
            ]
        })
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "book.epub"
            path.write_bytes(source)
            with self.assertRaisesRegex(StructureError, "unmappable XHTML head title"):
                read_chapters(path)



    def test_css_escaped_line_continuation_and_misdeclared_stylesheet_fail_closed(self):
        original = make_epub()
        escaped = b'@import "h' + bytes([92, 10]) + b'ttps://tracker.invalid/x.css";'
        for path, media in (("evil.css", "image/png"), ("evil.data", "application/octet-stream")):
            with self.subTest(resource=path):
                revised = self.mutate_epub(original, {
                    "OPS/content.opf": [(
                        b"</manifest>",
                        (f'<item id="evil" href="{path}" media-type="{media}"/></manifest>').encode()
                    )],
                    "OPS/story.xhtml": [(b"<body>",
                        (f'<head><link rel="stylesheet" href="{path}"/></head><body>').encode())],
                }, {f"OPS/{path}": escaped})
                with zipfile.ZipFile(io.BytesIO(revised)) as z:
                    with self.assertRaisesRegex(StructureError, "remote CSS"):
                        manifest_and_spine(z)

    def test_description_meta_is_rejected_and_viewport_passes(self):
        original = make_epub()
        content = b'<head><meta name="description" content="English blurb"/></head><body>'
        bad = self.mutate_epub(original, {"OPS/story.xhtml": [(b"<body>", content)]})
        with zipfile.ZipFile(io.BytesIO(bad)) as z:
            with self.assertRaisesRegex(StructureError, "untranslated XHTML meta content"):
                manifest_and_spine(z)
        viewport = b'<head><meta name="viewport" content="width=device-width,initial-scale=1"/></head><body>'
        good = self.mutate_epub(original, {"OPS/story.xhtml": [(b"<body>", viewport)]})
        with zipfile.ZipFile(io.BytesIO(good)) as z:
            manifest_and_spine(z)

    def test_offspine_navigation_head_title_supports_french(self):
        source = self.mutate_epub(make_epub(multi_nav=False), {
            "OPS/nav.xhtml": [(b"<body>", b"<head><title>Contents</title></head><body>")]
        })
        units = source_units(source)
        output = build(source, document_paths=[x[2] for x in units],
                       translations=[units[0][1].replace("First chapter", "Premier chapitre")],
                       target_language="fr", title="Livre traduit")
        with zipfile.ZipFile(io.BytesIO(output)) as z:
            nav = ET.fromstring(z.read("OPS/nav.xhtml"))
            self.assertEqual(nav.find(".//{*}title").text, "Livre traduit")

    def test_unmappable_ncx_label_is_rejected_before_translation(self):
        original = make_epub(ncx=True)
        tampered = self.mutate_epub(original, {
            "OPS/story.xhtml": [(b"<p>Text one.</p>", b'<p id="plain">Text one.</p>')],
            "OPS/toc.ncx": [(b"story.xhtml#first", b"story.xhtml#plain")],
        })
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "book.epub"
            path.write_bytes(tampered)
            with self.assertRaisesRegex(StructureError, "NCX navigation label cannot be mapped"):
                read_chapters(path)

    def test_ncx_doc_title_is_localized_and_page_list_text_rejected(self):
        original = make_epub(ncx=True)
        source = self.mutate_epub(original, {
            "OPS/toc.ncx": [(b"<ncx>", b"<ncx><docTitle><text>English Book</text></docTitle>")]
        })
        units = source_units(source)
        output = build(source, document_paths=[x[2] for x in units],
                       translations=[units[0][1].replace("First chapter", "Premier chapitre")],
                       target_language="fr", title="Livre traduit")
        with zipfile.ZipFile(io.BytesIO(output)) as z:
            tree = ET.fromstring(z.read("OPS/toc.ncx"))
            doc_title = next(x for x in tree.iter()
                             if x.tag.rsplit("}", 1)[-1] == "docTitle")
            self.assertEqual("".join(doc_title.itertext()), "Livre traduit")
        bad = self.mutate_epub(source, {
            "OPS/toc.ncx": [(b"</ncx>", b'<pageList><pageTarget><navLabel><text>English page</text></navLabel></pageTarget></pageList></ncx>')]
        })
        with zipfile.ZipFile(io.BytesIO(bad)) as z:
            with self.assertRaisesRegex(StructureError, "unmapped NCX page/list labels"):
                manifest_and_spine(z)



    def test_offspine_nonlink_navigation_labels_block_source_admission(self):
        original = make_epub(multi_nav=False)
        for replacement in (
            b'<nav><h1>Table of Contents</h1><ol>',
            b'<nav><ol><li><span>Part One</span><ol><li><a href="story.xhtml#first">First chapter</a></li></ol></li>',
        ):
            with self.subTest(label=replacement[:55]):
                if b"<h1>" in replacement:
                    patched = self.mutate_epub(original, {
                        "OPS/nav.xhtml": [(b"<nav><ol>", replacement)]
                    })
                else:
                    patched = self.mutate_epub(original, {
                        "OPS/nav.xhtml": [(b'<nav><ol><li><a href="story.xhtml#first">First chapter</a></li>',
                                            replacement)]
                    })
                with zipfile.ZipFile(io.BytesIO(patched)) as z:
                    with self.assertRaisesRegex(StructureError, "unmapped non-link navigation label"):
                        manifest_and_spine(z)

    def test_structural_opf_author_override_preserves_subtitle_refinements(self):
        raw = make_epub(multi_nav=False)
        expanded = self.mutate_epub(raw, {
            "OPS/content.opf": [(
                b"<dc:title>Story</dc:title>",
                b'<dc:title id="book-title">Story</dc:title>'
                b'<dc:title id="subtitle">The Original Subtitle</dc:title>'
                b'<meta refines="#subtitle" property="title-type">subtitle</meta>'
            )]
        })
        units = source_units(expanded)
        built = build(expanded, document_paths=[x[2] for x in units],
                      translations=[units[0][1]],
                      target_language="ru", title="Русская книга", author="Исправленный автор")
        with zipfile.ZipFile(io.BytesIO(built)) as z:
            package = ET.fromstring(z.read("OPS/content.opf"))
            titles = [el.text for el in package.iter() if el.tag.rsplit("}", 1)[-1] == "title"]
            creators = [el.text for el in package.iter() if el.tag.rsplit("}", 1)[-1] == "creator"]
            self.assertEqual(titles, ["Русская книга", "The Original Subtitle"])
            self.assertEqual(creators, ["Исправленный автор"])
            self.assertTrue(any(el.get("refines") == "#subtitle" for el in package.iter()))

    def test_refined_primary_title_is_updated_without_overwriting_secondary(self):
        raw = make_epub(multi_nav=False)
        expanded = self.mutate_epub(raw, {
            "OPS/content.opf": [(
                b"<dc:title>Story</dc:title>",
                b'<dc:title id="sub">Source Subtitle</dc:title>'
                b'<dc:title id="primary">Story</dc:title>'
                b'<meta refines="#primary" property="title-type">main</meta>'
            )]
        })
        units = source_units(expanded)
        built = build(expanded, document_paths=[x[2] for x in units],
                      translations=[units[0][1]], target_language="ru",
                      title="Русское название")
        with zipfile.ZipFile(io.BytesIO(built)) as z:
            package = ET.fromstring(z.read("OPS/content.opf"))
            titles = [el.text for el in package.iter() if el.tag.rsplit("}", 1)[-1] == "title"]
            self.assertEqual(titles, ["Source Subtitle", "Русское название"])



    def test_nested_misdeclared_css_import_cannot_load_remote_resources(self):
        source = make_epub()
        for imported in (b'@import "https://tracker.invalid/pixel.css";',
                         b'@import url(h\\74 tps://tracker.invalid/pixel.css);'):
            with self.subTest(imported=imported):
                epub = self.mutate_epub(
                    source,
                    {"OPS/content.opf": [(
                        b"</manifest>",
                        b'<item id="style" href="style.css" media-type="text/css"/></manifest>',
                    )]},
                    {"OPS/style.css": b'@import "nested.data";',
                     "OPS/nested.data": imported},
                )
                with zipfile.ZipFile(io.BytesIO(epub)) as z:
                    with self.assertRaisesRegex(StructureError, "remote CSS"):
                        manifest_and_spine(z)

    def test_multi_level_local_css_import_is_allowed(self):
        source = make_epub()
        epub = self.mutate_epub(
            source,
            {"OPS/content.opf": [(
                b"</manifest>",
                b'<item id="style" href="style.css" media-type="text/css"/></manifest>',
            )]},
            {"OPS/style.css": b'@import "nested.data";',
             "OPS/nested.data": b'@import url("final.css") screen;',
             "OPS/final.css": b'p {line-height: 1.4}'},
        )
        with zipfile.ZipFile(io.BytesIO(epub)) as z:
            manifest_and_spine(z)

    def test_missing_import_is_blocked_at_source_admission(self):
        source = make_epub()
        epub = self.mutate_epub(
            source,
            {"OPS/content.opf": [(
                b"</manifest>",
                b'<item id="style" href="style.css" media-type="text/css"/></manifest>',
            )]},
            {"OPS/style.css": b'@import "missing.data";'},
        )
        with zipfile.ZipFile(io.BytesIO(epub)) as z:
            with self.assertRaisesRegex(StructureError, "CSS import target is missing"):
                manifest_and_spine(z)

    def test_author_override_changes_author_not_prior_illustrator_or_editor(self):
        source = make_epub(multi_nav=False)
        creators = (
            b'<dc:creator id="editor">A. Editor</dc:creator>'
            b'<meta refines="#editor" property="role">edt</meta>'
            b'<dc:creator id="writer">Real Author</dc:creator>'
            b'<meta refines="#writer" property="role">aut</meta>'
        )
        source = self.mutate_epub(source, {"OPS/content.opf": [
            (b"</metadata>", creators + b"</metadata>"),
        ]})
        units = source_units(source)
        result = build(source, document_paths=[u[2] for u in units],
                       translations=[u[1] for u in units], target_language="ru",
                       title="Книга", author="Новый автор")
        with zipfile.ZipFile(io.BytesIO(result)) as z:
            package = ET.fromstring(z.read("OPS/content.opf"))
            vals = {node.get("id"): (node.text or "") for node in package.iter()
                    if node.tag.rsplit("}", 1)[-1] == "creator"}
            self.assertEqual(vals["editor"], "A. Editor")
            self.assertEqual(vals["writer"], "Новый автор")



    def test_xhtml_embedded_css_imports_follow_nondisclosed_assets(self):
        source = make_epub()
        changed = self.mutate_epub(
            source,
            {"OPS/story.xhtml": [(b"<body>", b'<head><style>@import "hidden.data";</style></head><body>')]},
            {"OPS/hidden.data": b'@import "https://tracker.invalid/steal.css";'},
        )
        with zipfile.ZipFile(io.BytesIO(changed)) as archive:
            with self.assertRaisesRegex(StructureError, "remote CSS"):
                manifest_and_spine(archive)



if __name__ == "__main__":
    unittest.main()

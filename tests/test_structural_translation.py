"""Regression tests for public, source-bound Markdown/EPUB structural translation."""
from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from workflow import structural_translation as structure


def batch(ids, **translation):
    return json.dumps({
        "segments": [{"id": key, "translation": value}
                     for key, value in translation.items()],
        "glossary": [], "style_guide": [],
    }, ensure_ascii=False)


class StructuralTranslationTests(unittest.TestCase):
    def test_no_links_or_epub_markers_uses_ordinary_translator(self):
        self.assertIsNone(structure.plan("# Prose\n\nNo links here.\n"))

    def test_terminal_link_is_immutable_even_when_model_returns_no_link(self):
        original = "# Chapter\n\nIt ends here.\n\n[Visit the author](https://site.example/x)\n"
        p = structure.plan(original)
        self.assertEqual(p["source_sha256"],
                         hashlib.sha256(original.encode("utf-8")).hexdigest())
        self.assertEqual([x["id"] for x in structure.spans(p)], ["s00001"])
        self.assertNotIn("https://site.example", structure.spans(p)[0]["source"])
        changes, _, _ = structure.parse_batch(
            batch([], s00001="# Глава\n\nВсё заканчивается здесь."), ["s00001"])
        self.assertEqual(structure.render(p, changes),
                         "# Глава\n\nВсё заканчивается здесь.\n\n"
                         "[Visit the author](https://site.example/x)\n")

    def test_inline_links_can_reorder_normal_language_only_within_text_spans(self):
        src = "Click [here](https://example.org/a) to continue."
        p = structure.plan(src)
        self.assertEqual(len(structure.spans(p)), 2)
        self.assertEqual(structure.render(p, {
            "s00001": "Нажмите",
            "s00002": "для продолжения.",
        }), "Нажмите [here](https://example.org/a) для продолжения.\n")

    def test_multiple_external_internal_image_and_epub_markers(self):
        src = ("<!-- bt-block:01 -->\n"
               "See [A](https://x.example?a=1) or [B](#chapter-two) "
               "and ![picture](../images/x.png)."
               "\n<!-- bt-anchor:c2 -->\n")
        p = structure.plan(src)
        self.assertEqual(sum(x["kind"] == "link" for x in p["parts"]), 3)
        self.assertEqual(sum(x["kind"] == "epub_marker" for x in p["parts"]), 2)
        self.assertEqual(sum(x["kind"] == "text" for x in p["parts"]), 4)
        out = structure.render(p, {
            "s00001": "См.",
            "s00002": "или",
            "s00003": "и",
            "s00004": ".",
        })
        self.assertIn("<!-- bt-block:01 -->", out)
        self.assertIn("<!-- bt-anchor:c2 -->", out)
        self.assertIn("[A](https://x.example?a=1)", out)
        self.assertIn("[B](#chapter-two)", out)
        self.assertIn("![picture](../images/x.png)", out)
        self.assertEqual(len(list(structure.LINK_RE.finditer(out))), 3)

    def test_adjacent_link_only_unit_requires_no_model_calls(self):
        src = "[A](https://first.example)\n[B](https://second.example)"
        p = structure.plan(src)
        self.assertEqual(structure.spans(p), [])
        self.assertEqual(structure.next_spans(p, {}), [])
        self.assertEqual(structure.render(p, {}), src + "\n")

    def test_partial_batches_only_accept_whitelisted_ids(self):
        p = structure.plan("First [here](https://x.test) second.")
        asked = structure.next_spans(p, {}, limit=2)
        values, glossary, style = structure.parse_batch(
            batch([], s00001="Первое"), [x["id"] for x in asked])
        self.assertEqual(values, {"s00001": "Первое"})
        self.assertEqual((glossary, style), ([], []))
        self.assertEqual([x["id"] for x in structure.next_spans(p, values)],
                         ["s00002"])
        with self.assertRaisesRegex(structure.StructureError, "missing"):
            structure.render(p, values)

    def test_untrusted_extra_or_duplicate_segment_ids_are_rejected(self):
        for keys in (
            ["s99999"], ["s00001", "s00001"],
        ):
            response = json.dumps({
                "segments": [{"id": key, "translation": "text"} for key in keys],
                "glossary": [], "style_guide": [],
            })
            with self.assertRaises(structure.StructureError):
                structure.parse_batch(response, ["s00001", "s00002"])

    def test_model_cannot_inject_new_links_or_markers(self):
        p = structure.plan("One [link](https://x.test) two.")
        for translation in (
            "Oops [new](https://attacker.test)",
            "Added <!-- bt-anchor:x --> marker",
            "ZXBTLINK123456789aN0001STARTXZ",
        ):
            with self.assertRaises(structure.StructureError):
                structure.render(p, {"s00001": translation, "s00002": "два."})

    def test_wrong_layout_and_surplus_fragments_fail_closed(self):
        p = structure.plan("A [label](https://example.test) B")
        with self.assertRaisesRegex(structure.StructureError, "extra"):
            structure.render(p, {"s00001": "A", "s00002": "B", "s99999": "C"})
        with self.assertRaisesRegex(structure.StructureError, "invalid"):
            structure.next_spans(p, {"s99999": "wrong"})
        with self.assertRaisesRegex(structure.StructureError, "size"):
            structure.next_spans(p, {}, limit=200)

    def test_technical_tokens_in_source_are_rejected(self):
        with self.assertRaisesRegex(structure.StructureError, "reserved"):
            structure.plan("Before ZXBTLINK123456789aN0001STARTXZ [a](#b)")

    def test_repeated_identical_links_preserve_both_locations(self):
        src = "Up [read](#x) down [read](#x) end."
        p = structure.plan(src)
        out = structure.render(p, {
            "s00001": "Вверх",
            "s00002": "вниз",
            "s00003": "конец.",
        })
        self.assertEqual(out.count("[read](#x)"), 2)
        self.assertEqual(out, "Вверх [read](#x) вниз [read](#x) конец.\n")


if __name__ == "__main__":
    unittest.main()

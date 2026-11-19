"""Exact chapter provenance, resume, complete context and strict fallback."""
from __future__ import annotations
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"automation"))
sys.path.insert(0,str(ROOT/"scripts"))
import chapter_context
import book_runner
from workflow import structural_translation as helper


class FullChapterContextTests(unittest.TestCase):
    def fixture(self):
        source=("<!-- bt-block:000001 -->\n"
            "Mira saw the key. She thought it might belong to Anton.\n"
            "<!-- bt-block:000002 -->\n"
            "Anton had never confirmed that the door was open.\n"
            "<!-- bt-block:000003 -->\n"
            "She waited beside the window and remembered his promise.")
        plan=book_runner.bounded_source_plan(source,helper)
        ordered=helper.spans(plan)
        return source,plan,ordered

    def test_full_context_contains_complete_original_and_prior_translations(self):
        source,plan,ordered=self.fixture()
        prior={ordered[0]["id"]:"Мира увидела ключ. Она думала, что он мог принадлежать Антону."}
        selected=ordered[1:]
        with patch.dict(os.environ,{"BOOK_TRANSLATOR_FULL_CHAPTER_CONTEXT":"on"}):
            prompt,audit=chapter_context.append(helper,plan,source,prior,selected,"system","requested")
        self.assertEqual(audit["mode"],"full")
        evidence=json.loads(prompt.rsplit("\n",1)[-1])
        self.assertEqual(evidence["original_context"],source)
        self.assertEqual(evidence["context_source_range"],[0,len(source)])
        self.assertEqual(evidence["previously_translated_segments"],
                         [{"id":ordered[0]["id"],"translation":prior[ordered[0]["id"]]}])
        self.assertIn("Translate ONLY the selected",prompt)
        self.assertIn('"selected_segment_ids"',prompt)
        self.assertEqual(audit["prior_segments"],1)
        self.assertEqual(audit["selected_segments"],len(selected))
        self.assertNotIn("Мира увидела",str(audit))

    def test_off_and_budget_fallback_do_not_mutate_state(self):
        source,plan,ordered=self.fixture()
        before={"segments":{ordered[0]["id"]:"Сохранённый текст"}}
        with patch.dict(os.environ,{"BOOK_TRANSLATOR_FULL_CHAPTER_CONTEXT":"off"}):
            prompt,audit=chapter_context.append(helper,plan,source,before["segments"],ordered[1:],"s","request")
            self.assertEqual((prompt,audit),("request",{"mode":"disabled"}))
        with patch.dict(os.environ,{"BOOK_TRANSLATOR_FULL_CHAPTER_CONTEXT":"on"}),\
                patch.object(chapter_context,"MAX_PROMPT_CHARS",100):
            prompt,audit=chapter_context.append(helper,plan,source,before["segments"],ordered[1:],"s","request")
            with self.assertRaisesRegex(ValueError,"budget cannot hold"):
                chapter_context.append(helper,plan,source,before["segments"],ordered[1:],"s","request")
        self.assertEqual(before,{"segments":{ordered[0]["id"]:"Сохранённый текст"}})

    def test_large_chapter_uses_overlapping_windows_and_larger_batches(self):
        # Simulate 100k+ source chars without model calls. Source-ID spans and
        # the previous translated spans must be reproducible on resume.
        paragraphs=[f"Scene {i:03d}: " + ("A lengthy literary sentence. "*45)
                    for i in range(110)]
        source="\n\n".join(paragraphs)
        plan=book_runner.bounded_source_plan(source,helper)
        ordered=helper.spans(plan)
        prior={item["id"]:"Русский текст. "*60 for item in ordered[:35]}
        selected=helper.next_spans(plan,prior,limit=24)
        self.assertGreater(len(source),chapter_context.MAX_SOURCE_CHARS)
        self.assertGreater(len(selected),1)
        with patch.dict(os.environ,{"BOOK_TRANSLATOR_FULL_CHAPTER_CONTEXT":"on"}):
            prompt,audit=chapter_context.append(helper,plan,source,prior,selected,"system","requested")
            repeated,again=chapter_context.append(helper,plan,source,prior,selected,"system","requested")
        self.assertEqual((prompt,audit),(repeated,again))
        self.assertEqual(audit["mode"],"window")
        self.assertGreater(audit["source_window_chars"],10000)
        self.assertLessEqual(audit["source_window_chars"],chapter_context.WINDOW_SOURCE_CHARS)
        evidence=json.loads(prompt.rsplit("\n",1)[-1])
        start,end=evidence["context_source_range"]
        self.assertEqual(evidence["original_context"],source[start:end])
        self.assertEqual(evidence["selected_segment_ids"],[s["id"] for s in selected])
        self.assertTrue(evidence["previously_translated_segments"])
        self.assertEqual(prior,{item["id"]:"Русский текст. "*60 for item in ordered[:35]})

    def test_large_chapter_later_window_keeps_last_context(self):
        paragraphs=[("A great deal of original narrative and dialogue. "*18)
                    for _ in range(180)]
        source="\n\n".join(paragraphs)
        plan=book_runner.bounded_source_plan(source,helper)
        ordered=helper.spans(plan)
        prior={item["id"]:"Переведено. "*35 for item in ordered[:-4]}
        selected=ordered[-4:]
        with patch.dict(os.environ,{"BOOK_TRANSLATOR_FULL_CHAPTER_CONTEXT":"on"}):
            prompt,audit=chapter_context.append(helper,plan,source,prior,selected,"system","requested")
        self.assertEqual(audit["mode"],"window")
        self.assertEqual(audit["source_window_end"],len(source))
        self.assertLessEqual(audit["prompt_chars"],chapter_context.MAX_PROMPT_CHARS)
        self.assertIn("A great deal",prompt)

    def test_budget_shrinks_context_not_selected_batch(self):
        paragraphs=[("Word dialogue with meaningful context. "*35)
                    for _ in range(65)]
        source="\n\n".join(paragraphs)
        plan=book_runner.bounded_source_plan(source,helper)
        ordered=helper.spans(plan)
        with patch.dict(os.environ,{"BOOK_TRANSLATOR_FULL_CHAPTER_CONTEXT":"on"}),\
                patch.object(chapter_context,"MAX_PROMPT_CHARS",24000):
            prompt,audit=chapter_context.append(helper,plan,source,{},ordered[10:13],"system","requested")
        self.assertEqual(audit["mode"],"window")
        self.assertLess(audit["source_window_chars"],len(source))
        self.assertLessEqual(audit["prompt_chars"],24000)
        self.assertEqual(json.loads(prompt.rsplit("\n",1)[-1])["selected_segment_ids"],
                         [x["id"] for x in ordered[10:13]])

    def test_wrong_source_or_overlap_is_rejected(self):
        source,plan,ordered=self.fixture()
        with patch.dict(os.environ,{"BOOK_TRANSLATOR_FULL_CHAPTER_CONTEXT":"on"}):
            with self.assertRaises(ValueError):
                chapter_context.append(helper,plan,source+"x",{},ordered,"s","u")
            with self.assertRaises(ValueError):
                chapter_context.append(helper,plan,source,{ordered[0]["id"]:"Завершено"},
                                       ordered,"s","u")
            with self.assertRaises(ValueError):
                chapter_context.append(helper,plan,source,{"unknown":"Завершено"},
                                       ordered,"s","u")

    def test_enabled_context_never_changes_approved_selection_or_wire_schema(self):
        source,plan,ordered=self.fixture()
        completed={ordered[0]["id"]:"Текст на русском."}
        expected=helper.next_spans(plan,completed)
        with patch.dict(os.environ,{"BOOK_TRANSLATOR_FULL_CHAPTER_CONTEXT":"on"}):
            _,audit=chapter_context.append(helper,plan,source,completed,expected,"system","user")
        self.assertEqual(audit["mode"],"full")
        self.assertEqual(helper.next_spans(plan,completed),expected)
        self.assertEqual(helper.render(plan,{x["id"]:x["source"] for x in ordered}),
                         source.rstrip()+"\n")


if __name__=="__main__":
    unittest.main()

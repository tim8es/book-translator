"""Exact chapter provenance, resume, complete context and strict fallback."""
from __future__ import annotations
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
        self.assertIn(source,prompt)
        self.assertIn(prior[ordered[0]["id"]],prompt)
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
            self.assertEqual(prompt,"request")
            self.assertEqual(audit["reason"],"prompt_too_long")
        self.assertEqual(before,{"segments":{ordered[0]["id"]:"Сохранённый текст"}})

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

"""Deterministic scheduling A/B; no model or literary quality claims."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "automation"))
import coherent_batches as coherent
import adaptive_budget

class Helper:
    @staticmethod
    def spans(plan):
        return plan
    @staticmethod
    def next_spans(plan, completed, limit):
        result = []
        for item in plan:
            if item["id"] in completed:
                continue
            if len(result) >= limit or sum(len(x["source"]) for x in result) + len(item["source"]) > 3500:
                break
            result.append(item)
        return result

class CoherentTests(unittest.TestCase):
    def test_translation_ab_and_resume(self):
        plan = [{"id": str(i), "source": "x"*150} for i in range(168)]
        def run(mode):
            done = {}
            calls = 0
            with patch.dict(os.environ, {"BOOK_TRANSLATOR_COHERENT_BATCHES": mode}):
                while len(done) < len(plan):
                    selected = coherent.structural_selection(Helper, plan, done, 24)
                    self.assertTrue(selected)
                    self.assertLessEqual(sum(len(x["source"]) for x in selected), 12000)
                    self.assertFalse(set(done) & {x["id"] for x in selected})
                    done.update({x["id"]: "translated" for x in selected})
                    calls += 1
            self.assertEqual(set(done), {x["id"] for x in plan})
            return calls
        before, after = run("off"), run("on")
        print(f"synthetic structural selection calls: {before} -> {after}")
        self.assertEqual((before, after), (8, 3))
        with patch.dict(os.environ, {"BOOK_TRANSLATOR_COHERENT_BATCHES": "on"}):
            self.assertEqual(len(coherent.structural_selection(Helper, plan, {}, 1, fallback=True)), 1)
            self.assertEqual(len(coherent.structural_selection(Helper, plan, {}, 12, pressure=1)), 12)

    def test_correction_ab_and_risk(self):
        findings = [{"id": str(i), "category": "meaning", "severity": "minor",
                     "translation_anchor": {"quote": f"word-{i:02d}.", "occurrence": 0}}
                    for i in range(23)]
        translation = " ".join(x["translation_anchor"]["quote"] for x in findings)
        def run(mode):
            pending = findings[:]
            calls = 0
            with patch.dict(os.environ, {"BOOK_TRANSLATOR_COHERENT_BATCHES": mode}):
                while pending:
                    old = adaptive_budget.plan(pending, unit_id="u", record_id="r")
                    selected = coherent.correction_selection(pending, translation,
                        pending[:old["group_size"]], [], "u", "r")
                    self.assertTrue(selected)
                    self.assertEqual(selected, pending[:len(selected)])
                    pending = pending[len(selected):]
                    calls += 1
            return calls
        before, after = run("off"), run("on")
        print(f"synthetic compatible correction selection calls: {before} -> {after}")
        self.assertEqual((before, after), (12, 3))
        with patch.dict(os.environ, {"BOOK_TRANSLATOR_COHERENT_BATCHES": "on"}):
            critical = dict(findings[0], severity="critical")
            self.assertEqual(coherent.correction_selection([critical]+findings[1:], translation, [critical], [], "u", "r"), [critical])
            self.assertEqual(coherent.correction_selection(findings, translation, findings[:2], [], "u", "r", pressure=1), findings[:2])
            overlap = dict(findings[1], translation_anchor=findings[0]["translation_anchor"])
            self.assertEqual(coherent.correction_selection([findings[0], overlap], translation, findings[:2], [], "u", "r"), findings[:1])

    def test_direct_review_gate(self):
        with patch.dict(os.environ, {"BOOK_TRANSLATOR_COHERENT_BATCHES": "on"}):
            self.assertTrue(coherent.prefer_direct_review(30000, 60000, 28000, 48000, 210000))
            self.assertFalse(coherent.prefer_direct_review(30000, 200000, 28000, 48000, 210000))
            self.assertFalse(coherent.prefer_direct_review(30000, 60000, 49000, 48000, 210000))
        with patch.dict(os.environ, {"BOOK_TRANSLATOR_COHERENT_BATCHES": "off"}):
            self.assertFalse(coherent.prefer_direct_review(30000, 60000, 28000, 48000, 210000))

    def test_block_address_adapter(self):
        import json
        s = "<!-- bt-block:000001 -->\nA phrase.\n<!-- bt-block:000002 -->\nA phrase."
        t = "<!-- bt-block:000001 -->\nOld phrase.\n<!-- bt-block:000002 -->\nOld phrase."
        report = {"findings": [{"source_anchor": {"quote":"A phrase.", "occurrence":0, "block_id":"000002"},
                                "translation_anchor": {"quote":"Old phrase.", "occurrence":0, "block_id":"000002"},
                                "detail":"Do not change this"}]}
        raw = json.dumps(report)
        with patch.dict(os.environ, {"BOOK_TRANSLATOR_COHERENT_BATCHES":"on"}):
            normalized = json.loads(coherent.normalize_block_addresses(raw,s,t))
            self.assertEqual(normalized["findings"][0]["source_anchor"], {"quote":"A phrase.","occurrence":1})
            self.assertEqual(normalized["findings"][0]["translation_anchor"], {"quote":"Old phrase.","occurrence":1})
            self.assertEqual(normalized["findings"][0]["detail"], "Do not change this")
            self.assertIn("000", s)
            with self.assertRaises(ValueError):
                coherent.normalize_block_addresses(raw.replace("000002","000001"),s,t)
            with self.assertRaises(ValueError):
                coherent.normalize_block_addresses(raw,s,t.replace("000002","000003"))
            with self.assertRaises(ValueError):
                coherent.normalize_block_addresses(raw.replace("Old phrase.","Invented"),s,t)
        with patch.dict(os.environ, {"BOOK_TRANSLATOR_COHERENT_BATCHES":"off"}):
            self.assertEqual(coherent.normalize_block_addresses(raw,s,t), raw)

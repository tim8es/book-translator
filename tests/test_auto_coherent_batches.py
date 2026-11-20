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

    def test_full_context_output_budget_degrades_gradually_and_resumes(self):
        plan = [{"id":str(i),"source":"Narrative dialogue. "*75} for i in range(24)]
        stage={}
        status={}
        with patch.dict(os.environ,{"BOOK_TRANSLATOR_COHERENT_BATCHES":"off"}):
            sizes=[]
            for failure in range(4):
                budget,spans=coherent.context_batch_caps(status,"chapter-000001","hash1")
                chosen=coherent.structural_selection(Helper,plan,stage,24,
                    full_chapter=True,source_char_limit=budget,span_limit=spans)
                sizes.append((len(chosen),sum(len(x["source"]) for x in chosen)))
                self.assertLessEqual(len(chosen),spans)
                self.assertLessEqual(sizes[-1][1],budget)
                if failure==0:
                    self.assertGreater(len(chosen),1)
                if failure<3:
                    self.assertFalse(coherent.shrink_context_batch(status,"chapter-000001","hash1"))
            self.assertTrue(all(sizes[i+1][1]<=sizes[i][1] for i in range(3)))
            self.assertEqual(coherent.context_batch_caps(status,"chapter-000001","different-sha"),
                             (coherent.CONTEXT_INITIAL_SOURCE_CHARS,coherent.CONTEXT_INITIAL_SPANS))
            self.assertTrue(coherent.shrink_context_batch(status,"chapter-000001","hash1"))
            self.assertEqual(coherent.context_batch_caps(status,"chapter-000001","hash1"),
                             (coherent.CONTEXT_MIN_SOURCE_CHARS,coherent.CONTEXT_MIN_SPANS))
            one=coherent.structural_selection(Helper,plan,stage,24,full_chapter=True,
                 source_char_limit=coherent.CONTEXT_MIN_SOURCE_CHARS,span_limit=1)
            self.assertEqual(len(one),1)
        # A previously checkpointed valid output is never rescheduled.
        stage[one[0]["id"]]="Переведено"
        more=coherent.structural_selection(Helper,plan,stage,24,full_chapter=True)
        self.assertNotEqual(one[0]["id"],more[0]["id"])

    def test_sparse_valid_output_shrinks_next_batch_without_erasing_stage(self):
        status={}
        chapter="chapter-000001"
        source="hash"
        completed={"span-001":"Переведённое предложение."}
        changed=coherent.adjust_after_sparse_response(status,chapter,source,8,2)
        self.assertTrue(changed)
        self.assertEqual(coherent.context_batch_caps(status,chapter,source),(3000,4))
        self.assertFalse(coherent.adjust_after_sparse_response(status,chapter,source,4,3))
        self.assertEqual(coherent.context_batch_caps(status,chapter,source),(3000,4))
        self.assertTrue(coherent.adjust_after_sparse_response(status,chapter,source,4,1))
        self.assertEqual(coherent.context_batch_caps(status,chapter,source),(1800,2))
        self.assertEqual(completed,{"span-001":"Переведённое предложение."})
        for req,returned in [(0,1),(3,0),(2,3)]:
            with self.assertRaises(ValueError):
                coherent.adjust_after_sparse_response(status,chapter,source,req,returned)

    def test_full_context_progress_rejects_corrupt_caps(self):
        status={coherent.CONTEXT_PROGRESS_KEY:{"u":{
            "source_sha256":"hash","max_source_chars":900000,"max_spans":8}}}
        with self.assertRaises(ValueError):
            coherent.context_batch_caps(status,"u","hash")

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

    def test_fixture_deterministic_and_local_state_layout(self):
        import tempfile
        import json
        from unittest.mock import patch
        import coherent_live_benchmark as live
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            one, two = root/"one.epub", root/"two.epub"
            live.make_epub(one)
            live.make_epub(two)
            self.assertEqual(one.read_bytes(), two.read_bytes())
            import zipfile
            with zipfile.ZipFile(one) as z:
                self.assertEqual(z.testzip(), None)
                self.assertEqual(z.read("mimetype"), b"application/epub+zip")
            original = live.subprocess.run
            def run(args, **kwargs):
                if str(args[0]) == sys.executable:
                    self.assertTrue((kwargs["cwd"]/"books"/".gitkeep").is_file())
                    status = kwargs["cwd"]/"automation"/"status"/(live.JOB+".json")
                    status.parent.mkdir(parents=True, exist_ok=True)
                    status.write_text(json.dumps({"state":"blocked","phase":"test","model_calls_started":0}))
                    from types import SimpleNamespace
                    return SimpleNamespace(returncode=0)
                return original(args, **kwargs)
            with patch.object(live.subprocess,"run",side_effect=run):
                row = live.run_arm(root,one.read_bytes(),"off",3)
            self.assertEqual(row["calls_reserved"],0)
            self.assertFalse(row["epub_verified"])

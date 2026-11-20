import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"automation"))
import mapped_correction as mc
import span_map
import book_runner
import single_review_gate
from unittest.mock import patch

def fixture():
    s="She might leave. She never promised.\n\nThe train arrived.\n"
    t="Она точно уйдёт. Она обещала.\n\nПоезд прибыл.\n"
    a=s.index("The train");b=t.index("Поезд")
    m=span_map.make(s,t,[([0,a],[0,b]),([a,len(s)],[b,len(t)])])
    def f(fid,src,tgt,cat="meaning"):
        return {"id":fid,"category":cat,"severity":"major","detail":"Exact source discrepancy",
                "source_anchor":{"quote":src,"occurrence":0},"translation_anchor":{"quote":tgt,"occurrence":0},"resolved":False}
    findings=[f("a","She might leave.","Она точно уйдёт.","modality"),
              f("b","She never promised.","Она обещала."),
              f("c","The train arrived.","Поезд прибыл.")]
    return s,t,m,findings

class MappedCorrectionTests(unittest.TestCase):
    def test_one_context_per_owner_keeps_all_findings(self):
        s,t,m,f=fixture()
        p=mc.packet(m,s,t,f,book_runner._anchor_position)
        self.assertEqual(len(p),2)
        self.assertEqual([x["id"] for x in p[0]["findings"]],["a","b"])
        self.assertIn("She never promised.",p[0]["source"]["block"])
        self.assertEqual(mc.selection(m,s,t,f,f[:1],[],[],"u","r",book_runner._anchor_position),f)
    def test_overlap_and_known_unsafe_not_expanded(self):
        s,t,m,f=fixture()
        duplicate={**f[0],"id":"d"}
        result=mc.selection(m,s,t,[f[0],duplicate,f[1],f[2]],f[:1],[],
            [{"unit_id":"u","review_record_id":"r","finding_id":"c"}],"u","r",book_runner._anchor_position)
        self.assertEqual([x["id"] for x in result],["a","b"])
        self.assertEqual(mc.selection(m,s,t,f,f[:1],[],[],"u","r",book_runner._anchor_position,pressure=1),f[:1])
    def test_combined_veto_checks_real_candidate_and_all_edits(self):
        s,t,m,f=fixture()
        patches=[{"finding_id":"a","operation":"replace","target_anchor":f[0]["translation_anchor"],"replacement":"Возможно, она уйдёт."},
                 {"finding_id":"b","operation":"replace","target_anchor":f[1]["translation_anchor"],"replacement":"Она не обещала."}]
        candidate=book_runner.apply_correction_patches(t,patches)
        cases=mc.cases(m,s,t,candidate,patches,f,book_runner._anchor_position)
        self.assertEqual(len(cases),1)
        self.assertEqual(cases[0]["patch_ids"],["a","b"])
        self.assertEqual(cases[0]["candidate_translation_excerpt"]["block"],"Возможно, она уйдёт. Она не обещала.\n\n")
        self.assertEqual(cases[0]["original_excerpt"]["block"],"She might leave. She never promised.\n\n")
        with patch.dict("os.environ",{"BOOK_TRANSLATOR_SINGLE_REVIEW_GATE":"on"}):
            self.assertEqual(single_review_gate.select_cases(cases,patches,f),cases)
    def test_cross_owner_or_unanchored_falls_back(self):
        s,t,m,f=fixture();f[0]["translation_anchor"]=f[2]["translation_anchor"]
        self.assertIsNone(mc.packet(m,s,t,f,book_runner._anchor_position))
        p={"finding_id":"a","operation":"replace","target_anchor":f[0]["translation_anchor"],"replacement":"Изменено."}
        self.assertIsNone(mc.cases(m,s,t,book_runner.apply_correction_patches(t,[p]),[p],f,book_runner._anchor_position))
    def test_addressed_protocol_failures_are_recoverable_not_pass(self):
        from omniroute import ModelError
        for message in ("reviewer evidence is invalid: mapped follow-up unknown or duplicate passage ID",
                        "reviewer evidence is invalid: addressed review noncontiguous passage selection"):
            self.assertEqual(book_runner.reviewer_model_output_code(ModelError(message)),"review_evidence_invalid")

if __name__=="__main__":unittest.main()

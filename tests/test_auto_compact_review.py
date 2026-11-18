import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"automation"))
import compact_review as c
import review_chunks as rc
import block_recovery as br
import book_runner
import adaptive_budget

def marked(*parts):
    return "".join("<!-- bt-block:%06d -->\n%s\n\n"%(i+1,p) for i,p in enumerate(parts))

class CompactReviewTests(unittest.TestCase):
    def setUp(self):
        self.source=marked("Mira went home. She sang.","Theo came home. He slept.")
        self.target=marked("Мира пошла домой. Она пела.","Тео пришел домой. Он спал.")
        self.chunk={"source_core":[0,len(self.source)],"translation_core":[0,len(self.target)],
                    "source_window":[0,len(self.source)],"translation_window":[0,len(self.target)]}
        self.item={"source_block":"000001","source_quote":"Mira went home.",
                   "target_block":"000001","target_quote":"Мира пошла домой.",
                   "category":"meaning","severity":"minor","detail":"test discrepancy",
                   "prior":None,"resolved":False}
    def payload(self,findings,outcome="CORRECTIONS_REQUIRED"):
        return json.dumps({"outcome":outcome,"checks":{k:True for k in c.CHECKS},
                           "findings":findings,"glossary":[],"style_guide":[]})
    def test_exact_complete_pair_coverage(self):
        p=c.packet(self.source,self.target,self.chunk)
        self.assertEqual("".join(x["source"] for x in p["pairs"]),self.source)
        self.assertEqual("".join(x["target"] for x in p["pairs"]),self.target)
    def test_compile_does_not_change_verdict(self):
        r=json.loads(c.compile_response(self.payload([self.item]),self.source,self.target,self.chunk,[]))
        self.assertEqual(r["outcome"],"CORRECTIONS_REQUIRED")
        self.assertEqual(r["findings"][0]["id"],"f001")
        self.assertEqual(r["findings"][0]["source_anchor"]["quote"],"Mira went home.")
        self.assertFalse(r["findings"][0]["resolved"])
    def test_false_pass_and_missing_prior_rejected(self):
        with self.assertRaises(c.CompactReviewError):
            c.compile_response(self.payload([self.item],"PASS"),self.source,self.target,self.chunk,[])
        with self.assertRaises(c.CompactReviewError):
            c.compile_response(self.payload([],"PASS"),self.source,self.target,self.chunk,
                               [{"id":"old","previous_record_id":"a"*32}])
    def test_invalid_quote_rejected(self):
        self.item["source_quote"]="not present"
        with self.assertRaises(c.CompactReviewError):
            c.compile_response(self.payload([self.item]),self.source,self.target,self.chunk,[])
    def test_repeated_quote_first_and_second(self):
        for i in range(2):
            intervals=c.ranges(self.source,self.chunk["source_core"])
            a=c.anchor(self.source,intervals["%06d"%(i+1)],"home.")
            pos=rc._evidence_offset(self.source,a)
            self.assertTrue(intervals["%06d"%(i+1)][0]<=pos<intervals["%06d"%(i+1)][1])
            self.assertIn("home.",a["quote"])
    def test_protected_marker_never_anchor(self):
        with self.assertRaises(c.CompactReviewError):
            c.anchor(self.source,(0,len(self.source)),"bt-block:000001")
    def test_prior_recheck_compiled(self):
        self.item.update(prior="old",resolved=True)
        r=json.loads(c.compile_response(self.payload([self.item],"PASS"),self.source,self.target,self.chunk,
                                       [{"id":"old","previous_record_id":"a"*32}]))
        self.assertEqual(r["findings"][0]["resolves"],{"record_id":"a"*32,"finding_id":"old"})
    def test_fast_first_size_pressure_not_rate_limit(self):
        s={"provider_retry":{"unit_id":"u","role":"reviewer","code":"http_503","attempts":1},
           "provider_call_metric":{"unit_id":"u","role":"reviewer","outcome":"http_503","duration_ms":250000}}
        with patch.dict(os.environ,{"BOOK_TRANSLATOR_COMPACT_REVIEW":"on"}):
            self.assertEqual(adaptive_budget.provider_pressure(s,"u","reviewer"),1)
            s["provider_retry"]["code"]="http_429"
            s["provider_call_metric"]["outcome"]="http_429"
            self.assertEqual(adaptive_budget.provider_pressure(s,"u","reviewer"),0)

class RecoveryTests(unittest.TestCase):
    def test_full_block_recovery_keeps_outside_bytes(self):
        source=marked("Mira went home.")
        target=marked("Мира пошла домой.")
        f={"id":"f1","source_anchor":{"quote":"Mira","occurrence":0},
           "translation_anchor":{"quote":"Мира","occurrence":0}}
        p=br.spec(source,target,f,book_runner._anchor_position)
        self.assertIsNotNone(p)
        reply={"finding_id":"f1","candidate_block":p["prefix"]+"Мирра"+p["suffix"],"glossary":[],"style_guide":[]}
        compiled=json.loads(br.compile_response(json.dumps(reply),p))
        self.assertEqual(compiled["patches"][0]["replacement"],"Мирра")
        reply["candidate_block"]="Мирра"
        with self.assertRaises(br.RecoveryError):
            br.compile_response(json.dumps(reply),p)

if __name__=="__main__":
    unittest.main()

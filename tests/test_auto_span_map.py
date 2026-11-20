import copy,json,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"automation"))
import span_map as sm
import addressed_review as ar
import book_runner

def fixture():
    a="He might have seen the station. It was still dark."
    b="The second train arrived. They waved."
    u="Он наверняка видел станцию. Было ещё темно."
    v="Прибыл второй поезд. Они помахали."
    s=a+"\n\n"+b+"\n\n";t=u+"\n\n"+v+"\n"
    plan={"source_sha256":sm.sha(s),"parts":[{"kind":"text","id":"s00001","before":"","after":"\n\n","original":a},
                                            {"kind":"text","id":"s00002","before":"","after":"\n\n","original":b}]}
    return s,t,sm.from_render(plan,{"s00001":u,"s00002":v},s,t)

def chunk(s,t):
    return {"source_core":[0,len(s)],"translation_core":[0,len(t)],"source_window":[0,len(s)],"translation_window":[0,len(t)]}

class SpanMapTests(unittest.TestCase):
    def test_exact_map_and_forgery(self):
        s,t,m=fixture();sm.validate(m,s,t)
        self.assertEqual(len(m["spans"]),2)
        bad=copy.deepcopy(m);bad["spans"][0]["target_sha256"]="0"*64
        with self.assertRaises(ValueError):sm.validate(bad,s,t)
    def test_local_patch_preserves_other_span(self):
        s,t,m=fixture()
        p={"finding_id":"f1","operation":"replace","target_anchor":{"quote":"наверняка","occurrence":0},"replacement":"возможно"}
        out=book_runner.apply_correction_patches(t,[p])
        new=sm.after_patches(m,s,t,out,[p],book_runner._anchor_position)
        self.assertEqual(new["spans"][1]["target_sha256"],m["spans"][1]["target_sha256"])
        sm.validate(new,s,out)
    def test_cross_owner_patch_falls_back(self):
        s,t,m=fixture();p={"operation":"replace","target_anchor":{"quote":"темно.\n\nПрибыл","occurrence":0},"replacement":"темно. Прибыл"}
        self.assertIsNone(sm.after_patches(m,s,t,book_runner.apply_correction_patches(t,[p]),[p],book_runner._anchor_position))
    def test_exact_id_evidence(self):
        s,t,m=fixture();c=chunk(s,t);data,_,_=ar.packet(s,t,c,m)
        self.assertEqual("".join(cell[1] for row in data["pairs"] for cell in row["source"]),s)
        self.assertEqual("".join(cell[1] for row in data["pairs"] for cell in row["target"]),t)
        f={"source_ids":["s00001.s1"],"target_ids":["s00001.t1"],"category":"modality","severity":"major",
           "detail":"Possibility became certainty.","prior":None,"resolved":False}
        raw={"outcome":"CORRECTIONS_REQUIRED","checks":dict.fromkeys(("coverage","meaning","terminology","style","structure"),True),
             "findings":[f],"glossary":[],"style_guide":[]}
        r=json.loads(ar.compile_response(json.dumps(raw),s,t,c,m,[]))
        self.assertEqual(r["findings"][0]["source_anchor"]["quote"],"He might have seen the station.")
        self.assertEqual(r["findings"][0]["translation_anchor"]["quote"],"Он наверняка видел станцию.")
        raw["outcome"]="PASS"
        with self.assertRaises(ValueError):ar.compile_response(json.dumps(raw),s,t,c,m,[])
        raw["outcome"]="CORRECTIONS_REQUIRED";f["target_ids"]=["invented"]
        with self.assertRaises(ValueError):ar.compile_response(json.dumps(raw),s,t,c,m,[])
    def test_addressed_wire_contract_and_diagnostics(self):
        s,t,m=fixture();c=chunk(s,t)
        contract=ar.response_contract()
        self.assertIn("not the pinned contract",contract)
        self.assertNotIn("FULL_REQUIRED",contract)
        self.assertIn("FULL_REQUIRED",ar.response_contract(followup=True))
        raw={"outcome":"PASS","checks":dict.fromkeys(("coverage","meaning","terminology","style","structure"),True),
             "findings":[],"glossary":[],"style_guide":[]}
        self.assertEqual(json.loads(ar.compile_response(json.dumps(raw),s,t,c,m,[]))["outcome"],"PASS")
        cases=[({**raw,"context":"private text"},"envelope fields"),
               ({**raw,"outcome":"FULL_REQUIRED"},"outcome"),
               ({**raw,"checks":{**raw["checks"],"meaning":False}},"checks"),
               ({**raw,"checks":{**raw["checks"],"meaning":1}},"checks"),
               ({**raw,"findings":{}},"findings"),
               ({**raw,"glossary":{}},"literary proposal arrays")]
        for invalid,code in cases:
            with self.subTest(code=code):
                with self.assertRaisesRegex(ValueError,code) as error:
                    ar.compile_response(json.dumps(invalid),s,t,c,m,[])
                self.assertNotIn("private text",str(error.exception))

    def test_prior_recheck_wrong_source_rejected(self):
        s,t,m=fixture();old={"id":"old","previous_record_id":"a"*32,"source_anchor":{"quote":"He might have seen the station.","occurrence":0}}
        f={"source_ids":["s00002.s1"],"target_ids":["s00002.t1"],"category":"modality","severity":"major",
           "detail":"fixed","prior":"old","resolved":True}
        raw={"outcome":"PASS","checks":dict.fromkeys(("coverage","meaning","terminology","style","structure"),True),"findings":[f],"glossary":[],"style_guide":[]}
        with self.assertRaises(ValueError):ar.compile_response(json.dumps(raw),s,t,chunk(s,t),m,[old])

if __name__=="__main__":unittest.main()

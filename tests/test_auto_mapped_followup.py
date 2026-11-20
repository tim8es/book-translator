import copy,json,os,sys,tempfile,unittest
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"automation"))
import span_map
import mapped_followup as mf
import addressed_review
import book_runner

def fixture():
    sources=[f"She might have seen station {i:02d}. "+("It was dark outside. "*24)+"\n\n" for i in range(20)]
    targets=[f"Она наверняка видела станцию {i:02d}. "+("На улице было темно. "*24)+"\n\n" for i in range(20)]
    s,t="".join(sources),"".join(targets);spans=[];a=b=0
    for x,y in zip(sources,targets):
        spans.append(([a,a+len(x)],[b,b+len(y)]));a+=len(x);b+=len(y)
    old=span_map.make(s,t,spans)
    p={"finding_id":"f1","operation":"replace","target_anchor":{"quote":"Она наверняка видела станцию 10.","occurrence":0},
        "replacement":"Возможно, она видела станцию 10."}
    current=book_runner.apply_correction_patches(t,[p]);new=span_map.after_patches(old,s,t,current,[p],book_runner._anchor_position)
    shared={"glossary":"a"*64,"style_guide":"b"*64}
    prev={"record_id":"c"*32,"outcome":"CORRECTIONS_REQUIRED","source_sha256":span_map.sha(s),
          "translation_sha256":span_map.sha(t),"shared_state_revisions":shared,
          "quality_report":{"evidence_version":2,"context":"independent_session","checks":dict.fromkeys(mf.CHECKS,True),
          "findings":[{"id":"old","category":"modality","severity":"major","detail":"Possibility became certainty.",
            "source_anchor":{"quote":"She might have seen station 10.","occurrence":0},
            "translation_anchor":p["target_anchor"],"resolved":False,"resolves":None}]}}
    delta={"mode":"focused_candidate","previous_record_id":prev["record_id"],"source_sha256":span_map.sha(s),
           "translation_sha256":span_map.sha(current)}
    return s,t,current,old,new,prev,shared,delta

class MappedFollowupTests(unittest.TestCase):
    def test_small_scope_includes_prior_and_adjacent_spans(self):
        args=fixture();result=mf.scope(*args,book_runner._anchor_position)
        self.assertIsNotNone(result)
        data,sr,tr,prior=result
        self.assertEqual([p["span"] for p in data["pairs"]],["s00010","s00011","s00012"])
        self.assertEqual(data["changed_spans"],["s00011"])
        self.assertEqual(len(prior),1)
        self.assertNotIn("previous_target",data["pairs"][0])
        self.assertIn("previous_target",data["pairs"][1])
        self.assertNotIn("previous_target",data["pairs"][2])
        r={"outcome":"PASS","checks":dict.fromkeys(mf.CHECKS,True),"glossary":[],"style_guide":[],
           "findings":[{"source_ids":["s00011.s1"],"target_ids":["s00011.t1"],"category":"modality",
                        "severity":"major","detail":"Possibility restored.","prior":"old","resolved":True}]}
        parsed=json.loads(addressed_review.compile_tables(json.dumps(r),args[0],args[2],sr,tr,prior))
        self.assertEqual(parsed["findings"][0]["resolves"]["finding_id"],"old")
        self.assertEqual(parsed["findings"][0]["translation_anchor"]["quote"],"Возможно, она видела станцию 10.")
    def test_memory_change_and_nonlocal_risk_force_full(self):
        args=list(fixture());args[6]={"glossary":"0"*64,"style_guide":"b"*64}
        self.assertIsNone(mf.scope(*args,book_runner._anchor_position))
        args=list(fixture());args[5]=copy.deepcopy(args[5]);args[5]["quality_report"]["findings"][0]["category"]="voice"
        self.assertIsNone(mf.scope(*args,book_runner._anchor_position))
    def test_no_chained_bounded_or_waived_canonical_scope(self):
        args=list(fixture());args[5]=copy.deepcopy(args[5]);args[5]["quality_report"]["context"]="bounded_context"
        self.assertIsNone(mf.scope(*args,book_runner._anchor_position))
        args=list(fixture());args[7]={**args[7],"mode":"full"}
        self.assertIsNone(mf.scope(*args,book_runner._anchor_position))

    def test_complete_mapped_review_keeps_all_passages_and_prior_findings(self):
        s,t,current,old,new,previous,shared,delta=fixture()
        chapter={"unit_id":"chapter-000001"}
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ,{
                "BOOK_TRANSLATOR_SPAN_MAP":"on","BOOK_TRANSLATOR_MAPPED_FULL_REVIEW":"on"}):
            stage=Path(d)/"stage.json"
            def prompt(engine,slug,chapter,source,target,part,total,prior,*,mapping):
                data,sr,tr=addressed_review.packet(source,target,part,mapping)
                self.assertEqual("".join(source[a:b] for a,b in sr.values()),source)
                self.assertEqual("".join(target[a:b] for a,b in tr.values()),target)
                self.assertEqual(total,1)
                return "system","ID-addressed independent review "+json.dumps(data,ensure_ascii=False)
            with patch.object(book_runner,"review_chunk_stage_path",return_value=stage), \
                    patch.object(book_runner,"read_span_map",return_value=new), \
                    patch.object(book_runner,"review_chunk_prompt",side_effect=prompt):
                packet=book_runner.mapped_full_review_context(Path(d),"test",chapter,previous,s,current,{})
                self.assertIsNotNone(packet)
                system,user,part,mapping,prior=packet
                self.assertEqual(part["source_core"],[0,len(s)])
                self.assertEqual(part["translation_core"],[0,len(current)])
                self.assertEqual(len(prior),1)
                self.assertIn("ENTIRE chapter",user)
                response={"outcome":"PASS","checks":dict.fromkeys(mf.CHECKS,True),"findings":[],
                          "glossary":[],"style_guide":[]}
                with self.assertRaises(ValueError):
                    addressed_review.compile_response(json.dumps(response),s,current,part,mapping,prior)
                self.assertIsNone(book_runner.mapped_full_review_context(Path(d),"test",chapter,previous,s,current,
                                  {"review_chunk_fallback_unit":chapter["unit_id"]}))
                stage.write_text("{}")
                self.assertIsNone(book_runner.mapped_full_review_context(Path(d),"test",chapter,previous,s,current,{}))

if __name__=="__main__":unittest.main()

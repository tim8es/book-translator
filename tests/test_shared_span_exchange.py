import copy
import importlib.util
import json,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"automation"))
import span_exchange as exchange
import span_map
import book_runner

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    return mod

helper=load("structural_shared_test",ROOT/"scripts/workflow/structural_translation.py")
bridge=load("chat_span_bridge_test",ROOT/"scripts/chat_ops_bridge.py")
RID="a"*32
CHECKS=dict.fromkeys(("coverage","meaning","terminology","style","structure"),True)

def fixture():
    s="She might return. She never promised.\n\nThe station was empty.\n"
    t="Она, возможно, вернётся. Она не обещала.\n\nСтанция была пустой.\n"
    m=exchange.translation_map(s,t,{"s00001":t.strip()},helper,book_runner.bounded_source_plan)
    return s,t,m

def pass_parts(s,t,m,prev=None):
    return [{"chunk_id":p["chunk_id"],"response":{"outcome":"PASS","checks":CHECKS,
             "findings":[],"glossary":[],"style_guide":[]}} for p in exchange.prepare(s,t,m,prev)["parts"]]

class SharedSpanExchangeTests(unittest.TestCase):
    def test_structural_map_exact_render_only(self):
        s,t,m=fixture();span_map.validate(m,s,t)
        with self.assertRaises(ValueError):
            exchange.translation_map(s,t+"Extra",{"s00001":t.strip()},helper,book_runner.bounded_source_plan)
        with tempfile.TemporaryDirectory() as d:
            book=Path(d);p=span_map.persist_artifact(book,"chapter-000001",m,s,t)
            self.assertEqual(p,span_map.artifact_path(book,"chapter-000001",s,t))
            self.assertEqual(span_map.read_artifact(book,"chapter-000001",s,t),m)
            self.assertIsNone(span_map.read_artifact(book,"chapter-000001",s,t+"!"))
            with self.assertRaises(ValueError):span_map.persist_artifact(book,"../bad",m,s,t)

    def test_full_addressed_report_requires_every_part(self):
        s,t,m=fixture();rows=pass_parts(s,t,m)
        q=exchange.compile_review(s,t,m,None,rows,"PASS")
        self.assertEqual(q,{"evidence_version":2,"context":"independent_session","checks":CHECKS,"findings":[]})
        for invalid in ([],rows+rows):
            with self.assertRaises(ValueError):exchange.compile_review(s,t,m,None,invalid,"PASS")
        with self.assertRaises(ValueError):exchange.compile_review(s,t,m,None,rows,"CORRECTIONS_REQUIRED")

    def test_prior_recheck_required_and_exact(self):
        s,t,m=fixture()
        prev={"record_id":"b"*32,"source_sha256":span_map.sha(s),"quality_report":{"evidence_version":2,
              "findings":[{"id":"f1","category":"modality","severity":"major","detail":"Check uncertainty.",
                 "resolved":False,"source_anchor":{"quote":"She might return.","occurrence":0},
                 "translation_anchor":{"quote":"Она, возможно, вернётся.","occurrence":0}}]}}
        rows=pass_parts(s,t,m,prev)
        with self.assertRaises(ValueError):exchange.compile_review(s,t,m,prev,rows,"PASS")
        rows[0]["response"]["findings"]=[{"source_ids":["s00001.s1"],"target_ids":["s00001.t1"],
                "category":"modality","severity":"major","detail":"Uncertainty retained.",
                "prior":"f1","resolved":True}]
        q=exchange.compile_review(s,t,m,prev,rows,"PASS")
        self.assertEqual(q["findings"][0]["resolves"],{"record_id":"b"*32,"finding_id":"f1"})

    def test_protected_cells_are_visible_but_not_edit_addresses(self):
        import addressed_review as ar
        marker="<!-- bt-block:000001 -->"
        s=marker+"\nThe train stopped. [Station](#station) Here.\n"
        t=marker+"\nПоезд остановился. [Станция](#station) Здесь.\n"
        m=span_map.make(s,t,[([0,len(s)],[0,len(t)])])
        with patch.dict(os.environ,{"BOOK_TRANSLATOR_ADDRESSED_EPUB":"off"}):
            with self.assertRaises(ValueError):exchange.prepare(s,t,m)
        with patch.dict(os.environ,{"BOOK_TRANSLATOR_ADDRESSED_EPUB":"on"}):
            p=exchange.parts(s,t,m)[0]
            data,sr,tr=ar.packet(s,t,p,m)
            self.assertEqual("".join(s[a:b] for a,b in sr.values()),s)
            self.assertEqual("".join(t[a:b] for a,b in tr.values()),t)
            self.assertEqual(len(data["protected_ids"]),4)
            locked=next(k for k in data["protected_ids"] if k in tr)
            with self.assertRaises(ValueError):ar.select(t,[locked],tr)
            sid=next(k for k,(a,b) in sr.items() if "The train" in s[a:b])
            self.assertEqual(ar.select(s,[sid],sr)[0]["quote"],"The train stopped.")
            q=exchange.compile_review(s,t,m,None,pass_parts(s,t,m),"PASS")
            self.assertEqual(q["findings"],[])
            bad=t.replace("#station","#other")
            bm=span_map.make(s,bad,[([0,len(s)],[0,len(bad)])])
            with self.assertRaises(RuntimeError):exchange.prepare(s,bad,bm)
            # A map splitting the marker cannot authorize addressed review.
            cut=8
            split=span_map.make(s,t,[([0,cut],[0,cut]),([cut,len(s)],[cut,len(t)])])
            with self.assertRaises(ValueError):exchange.prepare(s,t,split)

    def test_protected_canonical_fallback_requires_real_anchored_evidence(self):
        marker="<!-- bt-block:000001 -->"
        s=marker+"\nThe train stopped. [Station](#station) Here.\n"
        t=marker+"\nПоезд остановился. [Вокзал](#station) Здесь.\n"
        m=span_map.make(s,t,[([0,len(s)],[0,len(t)])])
        with patch.dict(os.environ,{"BOOK_TRANSLATOR_ADDRESSED_EPUB":"on"}):
            p=exchange.parts(s,t,m)[0]
            finding={"id":"f1","category":"meaning","severity":"minor",
                   "detail":"Check station label.","resolved":False,
                   "source_anchor":{"quote":"[Station](#station)","occurrence":0},
                   "translation_anchor":{"quote":"[Вокзал](#station)","occurrence":0},
                   "resolves":None}
            raw={"evidence_version":2,"outcome":"CORRECTIONS_REQUIRED","checks":CHECKS,
                 "findings":[finding],"glossary":[],"style_guide":[]}
            def compile_part(response,outcome):
                return exchange.compile_review(s,t,m,None,[{"chunk_id":p["id"],"response":response}],outcome)
            q=compile_part(raw,"CORRECTIONS_REQUIRED")
            self.assertEqual(q["findings"][0]["source_anchor"],finding["source_anchor"])
            cases=[
                ({**raw,"outcome":"PASS"},"PASS"),
                ({**raw,"outcome":"PASS","findings":[]},"PASS"),
                ({**raw,"findings":[{**finding,"translation_anchor":{"quote":"invented","occurrence":0}}]},"CORRECTIONS_REQUIRED"),
                ({**raw,"findings":[{**finding,"resolved":True}]},"CORRECTIONS_REQUIRED"),
                ({**raw,"findings":[{**finding,"source_anchor":{"quote":"The train stopped.","occurrence":0},"translation_anchor":{"quote":"Поезд остановился.","occurrence":0}}]},"CORRECTIONS_REQUIRED"),
            ]
            for response,outcome in cases:
                with self.subTest(response=response):
                    with self.assertRaises(ValueError):compile_part(response,outcome)

    def test_protected_interval_lookup_matches_full_reference_scan(self):
        import addressed_review as ar
        unit="<!-- bt-block:000001 -->\nSentence. [Station](#station) Next.\n"
        text=unit*100
        ranges=ar.protected_ranges(text)
        def reference(lo,hi):
            cursor=lo;out=[]
            for a,b in ranges:
                if a>=hi:break
                if b<=lo:continue
                out.extend((x,y,False) for x,y in ar.intervals(text,cursor,a))
                out.append((a,b,True));cursor=b
            out.extend((x,y,False) for x,y in ar.intervals(text,cursor,hi))
            return out
        for i in (0,1,50,99):
            lo=i*len(unit);hi=lo+len(unit)
            self.assertEqual(list(ar.protected_intervals(text,lo,hi,ranges)),reference(lo,hi))
        with self.assertRaises(ValueError):
            list(ar.protected_intervals(text,1,len(unit),ranges))

    def test_bridge_optional_schema_does_not_allow_both_review_formats(self):
        s,t,m=fixture()
        common={"schema_version":1,"request_id":RID,"book_slug":"test-book",
              "workflow_revision":"c"*40,"chapter":1,"session_id":"reviewer-1",
              "source_sha256":span_map.sha(s),"translation_sha256":span_map.sha(t),"claim_sha256":"d"*64}
        a={**common,"operation":"accept-translation","translation":t,"structural_segments":{"s00001":t.strip()}}
        self.assertEqual(bridge.validate(a,RID),a)
        r={**common,"operation":"review-record","outcome":"PASS","addressed_parts":pass_parts(s,t,m)}
        self.assertEqual(bridge.validate(r,RID),r)
        with self.assertRaises(bridge.RequestError):bridge.validate({**r,"quality_report":{}},RID)

    def test_chatops_uses_shared_map_and_pinned_review_record(self):
        s,t,m=fixture()
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);book=root/"books/test-book";book.mkdir(parents=True)
            source=book/"source.md";source.write_bytes(s.encode())
            unit={"unit_id":"chapter-000001","source_path":"source.md","translation_path":"target.md"}
            request={"book_slug":"test-book","operation":"accept-translation","workflow_revision":"c"*40,
                     "chapter":1,"session_id":"writer","translation":t,"structural_segments":{"s00001":t.strip()}}
            calls=[]
            def cli(*args):
                calls.append(args)
                if "review-record" in args:
                    evidence=Path(args[args.index("--quality-report")+1])
                    self.assertEqual(json.loads(evidence.read_text())["evidence_version"],2)
                return {"status":"ok"}
            (book/"review-ledger.json").write_text('{"records":[]}')
            with patch.object(bridge,"expected_book",return_value=(book,{})),patch.object(bridge,"pinned_engine",return_value=root),\
                    patch.object(bridge,"chapter_and_claim",return_value=unit),patch.object(bridge,"cli",side_effect=cli),\
                    patch.object(book_runner,"load_public_structural_helper",return_value=helper):
                bridge.execute(root,root,request)
                self.assertEqual(span_map.read_artifact(book,unit["unit_id"],s,t),m)
                review={**request,"operation":"review-record","session_id":"reviewer","translation_sha256":span_map.sha(t),
                        "addressed_parts":pass_parts(s,t,m),"outcome":"PASS"}
                bridge.execute(root,root,review)
            self.assertTrue(any("accept-translation" in c for c in calls))
            self.assertTrue(any("review-record" in c for c in calls))
            self.assertFalse(any("accept-review" in c for c in calls))


    def test_chat_correction_rebases_same_map_and_rejects_stale_or_unbound(self):
        s="She might return. She never promised.\n\nThe station was empty.\n"
        before="Она точно вернётся. Она не обещала.\n\nСтанция была пустой.\n"
        m=exchange.translation_map(s,before,{"s00001":before.strip()},helper,book_runner.bounded_source_plan)
        protocol=load("correction_shared_test",ROOT/"scripts/workflow/correction_protocol.py")
        acceptance={"source_sha256":span_map.sha(s),"translation_sha256":span_map.sha(before)}
        f={"id":"f1","category":"modality","severity":"major","resolved":False,
           "detail":"Possibility became certainty.",
           "source_anchor":{"quote":"She might return.","occurrence":0},
           "translation_anchor":{"quote":"Она точно вернётся.","occurrence":0}}
        record={"record_id":"b"*32,"outcome":"CORRECTIONS_REQUIRED",**acceptance,
                "quality_report":{"evidence_version":2,"findings":[f]}}
        edit={"finding_id":"f1","operation":"replace","replacement":"Она, возможно, вернётся."}
        new=before.replace(f["translation_anchor"]["quote"],edit["replacement"])
        mapped=exchange.correction_map(s,before,new,m,record,acceptance,[edit],protocol)
        span_map.validate(mapped,s,new)
        self.assertEqual(mapped["spans"][0]["source_sha256"],m["spans"][0]["source_sha256"])
        for current,p in ((new+"Extra",[edit]),(new,[{**edit,"finding_id":"invented"}]),
                          (before,[{**edit,"replacement":f["translation_anchor"]["quote"]}])):
            from omniroute import ModelError
            with self.assertRaises((ValueError,RuntimeError,ModelError)):
                exchange.correction_map(s,before,current,m,record,acceptance,p,protocol)
        with self.assertRaises(ValueError):
            exchange.correction_map(s,before,new,m,{**record,"translation_sha256":"0"*64},acceptance,[edit],protocol)
        # The existing bridge uses the same current ledger, claim and
        # accepted-translation boundary, and persists a newly bound map.
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);book=root/"books/test-book";book.mkdir(parents=True)
            (book/"source.md").write_bytes(s.encode());(book/"target.md").write_bytes(before.encode())
            unit={"unit_id":"chapter-000001","source_path":"source.md","translation_path":"target.md",
                  "translation_acceptance":acceptance}
            record.update({"unit_id":unit["unit_id"],"sequence":1})
            (book/"review-ledger.json").write_text(json.dumps({"records":[record]}))
            span_map.persist_artifact(book,unit["unit_id"],m,s,before)
            request={"schema_version":1,"request_id":RID,"book_slug":"test-book",
                     "workflow_revision":"c"*40,"chapter":1,"session_id":"corrector",
                     "operation":"accept-translation","translation":new,
                     "source_sha256":span_map.sha(s),"translation_sha256":span_map.sha(new),"claim_sha256":"d"*64,
                     "correction_patches":[edit],"previous_review_record_id":record["record_id"]}
            self.assertEqual(bridge.validate(request,RID),request)
            with self.assertRaises(bridge.RequestError):
                bridge.validate({**request,"structural_segments":{"s00001":new.strip()}},RID)
            with patch.object(bridge,"expected_book",return_value=(book,{})),patch.object(bridge,"pinned_engine",return_value=root),\
                    patch.object(bridge,"chapter_and_claim",return_value=unit),patch.object(bridge,"cli",return_value={}) as cli,\
                    patch.object(book_runner,"load_public_correction_helper",return_value=protocol):
                with self.assertRaises(bridge.RequestError):
                    bridge.execute(root,root,{**request,"previous_review_record_id":"0"*32})
                self.assertEqual((book/"target.md").read_text(),before)
                bridge.execute(root,root,request)
                self.assertEqual(span_map.read_artifact(book,unit["unit_id"],s,new),mapped)
                self.assertFalse(any("accept-review" in c.args for c in cli.call_args_list))

if __name__=="__main__":unittest.main()

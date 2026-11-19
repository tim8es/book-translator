"""Mapped retry/rebase uses actual source-owned boundaries and exact evidence."""
import copy,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"automation"))
import span_map
import review_chunks as rc
import incremental_review as ir
import addressed_review

def fixture():
    ss=[];tt=[];pairs=[];a=b=0
    for i in range(24):
        x=(f"Source {i:02d}. "+"The station was empty. "*37+"\n\n")
        y=(f"Target {i:02d}. "+"Тихая станция. "*(47+i%4)+"\n\n")
        ss.append(x);tt.append(y)
        pairs.append(([a,a+len(x)],[b,b+len(y)]));a+=len(x);b+=len(y)
    s,t="".join(ss),"".join(tt)
    return s,t,span_map.make(s,t,pairs)

class MappedRecoveryTests(unittest.TestCase):
    def test_split_preserves_valid_independent_evidence(self):
        s,t,m=fixture();p=rc.plan_mapped(s,t,m,6000);self.assertGreater(len(p["chunks"]),2)
        binding=ir.binding(None,"a"*64,"b"*64,"c"*64)
        stage=ir.new_stage(p,"chapter-000001","f"*40,binding)
        first=p["chunks"][0]
        stage["completed"][first["id"]]={"outcome":"PASS","report":{"findings":[]},"style":[],"glossary":[]}
        ir.record_chunk(stage,first,s,t)
        new=rc.refine_pending(p,p["chunks"][1]["id"],s,t)
        self.assertIsNotNone(new);self.assertEqual(rc.source_owned_map(new),m)
        rc.validate_plan(new,s,t)
        for chunk in new["chunks"]:
            addressed_review.packet(s,t,chunk,m)
        fresh=ir.refine_current(stage,new,s,t,binding,"chapter-000001","f"*40)
        self.assertEqual(fresh["completed"],stage["completed"])
        self.assertEqual(fresh["windows"],stage["windows"])
        with self.assertRaises(ValueError):rc.combine(fresh)
        bad=copy.deepcopy(new);bad["chunks"][1]["translation_core"][1]-=1
        with self.assertRaises(ValueError):rc.validate_plan(bad,s,t)

    def test_replay_refined_plan_after_local_edit(self):
        s,t,m=fixture();p=rc.plan_mapped(s,t,m,6000)
        new=rc.refine_pending(p,p["chunks"][1]["id"],s,t)
        # Change one exact byte in the last source-owned target span.
        target=t.replace("Target 23.","Target 23!",1)
        current=span_map.make(s,target,[(x["source"],x["target"]) for x in m["spans"]])
        rebuilt=rc.recreate_mapped_plan(new,s,target,current)
        self.assertEqual(rebuilt["helper_revision"],rc.REFINED_REVISION)
        self.assertEqual([x["source_core"] for x in rebuilt["chunks"]],[x["source_core"] for x in new["chunks"]])
        self.assertEqual(rc.source_owned_map(rebuilt),current)
        rc.validate_plan(rebuilt,s,target)

    def test_rebase_keeps_only_exact_clean_source_windows_after_partition_change(self):
        s,t,m=fixture();p=rc.plan_mapped(s,t,m,6000)
        bind=ir.binding(None,"a"*64,"b"*64,"c"*64)
        stage=ir.new_stage(p,"chapter-000001","f"*40,bind)
        last=p["chunks"][-1]
        stage["completed"][last["id"]]={"outcome":"PASS","report":{"findings":[]},"style":[],"glossary":[]}
        ir.record_chunk(stage,last,s,t)
        target=t.replace("Target 00.","Target 00!",1)
        current=span_map.make(s,target,[(x["source"],x["target"]) for x in m["spans"]])
        next_plan=rc.plan_mapped(s,target,current,6000)
        refined=rc.refine_pending(next_plan,next_plan["chunks"][0]["id"],s,target)
        fresh,n=ir.rebase(stage,refined,s,target,bind,None,"chapter-000001","f"*40)
        self.assertEqual(n,1)
        self.assertNotEqual(last["id"],refined["chunks"][-1]["id"])
        self.assertIn(refined["chunks"][-1]["id"],fresh["completed"])
        self.assertNotIn(refined["chunks"][0]["id"],fresh["completed"])
        wrong={**bind,"glossary_sha256":"0"*64}
        self.assertEqual(ir.rebase(stage,refined,s,target,wrong,None,"chapter-000001","f"*40)[1],0)

if __name__=="__main__":unittest.main()

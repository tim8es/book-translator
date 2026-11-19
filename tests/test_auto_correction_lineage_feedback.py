import hashlib,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"automation"))
import review_convergence as rc
class CorrectionLineageTests(unittest.TestCase):
    def test_source_bound_history(self):
        s="She might return. The train left."
        sha=hashlib.sha256(s.encode()).hexdigest()
        def pos(text,a):
            n=text.index(a["quote"]);return n,n+len(a["quote"])
        f={"id":"f1","resolved":False,"detail":"Uncertainty lost.","source_anchor":{"quote":"She might return.","occurrence":0},"translation_anchor":{"quote":"Она вернётся.","occurrence":0},"resolves":None}
        a={"record_id":"a"*32,"unit_id":"chapter-000001","source_sha256":sha,"translation_sha256":"a"*64,"sequence":1,"outcome":"CORRECTIONS_REQUIRED","quality_report":{"evidence_version":2,"findings":[f]}}
        g={**f,"id":"f2","resolves":{"record_id":a["record_id"],"finding_id":"f1"}}
        b={**a,"record_id":"b"*32,"translation_sha256":"b"*64,"sequence":2,"quality_report":{"evidence_version":2,"findings":[g]}}
        rows=rc.prior_attempts({"records":[a,b]},b,[g],s,pos)
        self.assertEqual(len(rows),1)
        self.assertIn("Она вернётся.",rc.prior_attempt_prompt(rows))
        self.assertEqual(rc.prior_attempts({"records":[a,b]},b,[{**g,"source_anchor":{"quote":"The train left.","occurrence":0}}],s,pos),[])
        self.assertEqual(rc.prior_attempts({"records":[{**a,"unit_id":"chapter-000002"},b]},b,[g],s,pos),[])
        counts=rc.round_counts({"findings":[g,{**g,"id":"f3","resolved":True}]},a,"CORRECTIONS_REQUIRED")
        self.assertEqual((counts["open_from_prior"],counts["closed_rechecks"],counts["new_findings"]),(1,1,0))

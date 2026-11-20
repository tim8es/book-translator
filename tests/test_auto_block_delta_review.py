import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"automation"))
import block_delta_review as d
import transactional_quality as q
from book_runner import _anchor_position

def marked(prefix,edited=False):
    return "".join("<!-- bt-block:%06d -->\n%s %d%s.\n\n"%(i,prefix,i," corrected" if edited and i==5 else "") for i in range(1,13))

class BlockDeltaTests(unittest.TestCase):
    def setUp(self):
        self.source=marked("Original")
        self.before=marked("Target")
        self.after=marked("Target",True)
        self.shared={"glossary":"a"*64,"style_guide":"b"*64}
        self.record={"record_id":"c"*32,"outcome":"CORRECTIONS_REQUIRED",
                     "source_sha256":q.digest(self.source),"translation_sha256":q.digest(self.before),
                     "shared_state_revisions":self.shared,
                     "quality_report":{"evidence_version":2,"context":"independent_session",
                                       "checks":{k:True for k in d.CHECKS},
                                       "findings":[{"id":"meaning","category":"meaning","severity":"major","resolved":False,
                                                    "source_anchor":{"quote":"Original 5.","occurrence":0},
                                                    "translation_anchor":{"quote":"Target 5.","occurrence":0}}]}}
    def scope(self):
        return d.scope(self.source,self.before,self.after,self.record,self.shared,_anchor_position)
    def test_exact_changed_and_neighbor_scopes(self):
        s=self.scope()
        self.assertEqual(s["changed_block_ids"],["000005"])
        self.assertEqual(s["scope_block_ids"],["000004","000005","000006"])
        self.assertEqual(s["unchanged_outside_scope_count"],9)
        self.assertIn("Original 5",s["changed_translation_sections"][1]["source"])
    def test_stale_hash_or_memory_rejected(self):
        self.record["source_sha256"]="f"*64
        self.assertIsNone(self.scope())
    def test_nonlocal_risk_requires_full(self):
        self.record["quality_report"]["findings"][0]["category"]="structure"
        self.assertIsNone(self.scope())
    def test_bounded_baseline_does_not_recurse(self):
        self.record["quality_report"]["context"]="bounded_context"
        self.assertIsNone(self.scope())
    def test_prior_unchanged_finding_in_scope(self):
        f=dict(self.record["quality_report"]["findings"][0])
        f.update(id="other",source_anchor={"quote":"Original 7.","occurrence":0},
                 translation_anchor={"quote":"Target 7.","occurrence":0})
        self.record["quality_report"]["findings"].append(f)
        self.assertIn("000007",self.scope()["scope_block_ids"])

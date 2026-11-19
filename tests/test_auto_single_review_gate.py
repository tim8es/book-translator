import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"automation"))
import single_review_gate as gate
import review_chunks as chunks
import compact_review
import book_runner

def corpus(prefix,n=120,extra=0):
    return "\n\n".join(f"{prefix} {i:03d}. "+("A different sentence about the station and the journey. "*(4+extra)) for i in range(n))+"\n"

class SingleReviewGateTests(unittest.TestCase):
    def test_risk_selection_preserves_combined_case(self):
        a={"finding_id":"a","operation":"replace","target_anchor":{"quote":"Старый оборот"},"replacement":"Новый оборот"}
        b={**a,"finding_id":"b"}
        f=[{"id":"a","category":"style","severity":"minor"},{"id":"b","category":"meaning","severity":"major"}]
        c=[{"patch_ids":["a"]},{"patch_ids":["b"]},{"patch_ids":["a","b"]}]
        with patch.dict(os.environ,{"BOOK_TRANSLATOR_SINGLE_REVIEW_GATE":"on"}):
            self.assertEqual(gate.select_cases(c,[a,b],f),c[1:])
        self.assertTrue(gate.risky({**a,"operation":"insert_after"},f[0]))
    def test_stable_ordinal_partitions_and_complete_coverage(self):
        s=corpus("Source");t=corpus("Target")
        plan=chunks.plan_paragraphs(s,t,wider=True)
        self.assertEqual(plan["helper_revision"],chunks.PARAGRAPH_STABLE_REVISION)
        self.assertEqual(len(plan["chunks"]),2)
        chunks.validate_plan(plan,s,t)
        changed=t.replace("Target 100.","Changed target 100.",1)
        new=chunks.plan_paragraphs(s,changed,count=2)
        self.assertEqual(plan["chunks"][0]["source_core"],new["chunks"][0]["source_core"])
        self.assertEqual(chunks.sha(t[slice(*plan["chunks"][0]["translation_window"])]),
                         chunks.sha(changed[slice(*new["chunks"][0]["translation_window"])]))
        self.assertIsNone(chunks.plan_paragraphs(s,corpus("Target",119),wider=True))
    def test_translation_missing_paragraph_needs_repair(self):
        with patch.dict(os.environ,{"BOOK_TRANSLATOR_PARAGRAPH_REVIEW":"on"}):
            self.assertEqual(book_runner.incomplete_structural_batch([{"id":"a","source":"One.\n\nTwo."}],{"a":"Один. Два."}),["a"])
    def test_overlap_addresses_compile_without_false_omission(self):
        s=corpus("Source");t=corpus("Target")
        c=chunks.plan_paragraphs(s,t,wider=True)["chunks"][0]
        with patch.dict(os.environ,{"BOOK_TRANSLATOR_PARAGRAPH_REVIEW":"on"}):
            data=compact_review.packet(s,t,c)
            self.assertTrue(data["addressed_target_overlap"])
            _sr,tr,_paired=compact_review.packet_ranges(s,t,c)
            chosen=data["addressed_target_overlap"][0]
            quote=chosen["text"].split(".")[0]+"."
            anchor=compact_review.anchor(t,tr[chosen["block_id"]],quote)
            self.assertGreaterEqual(chunks._evidence_offset(t,anchor),c["translation_core"][1])

if __name__=="__main__":
    unittest.main()

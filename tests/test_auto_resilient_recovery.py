import copy
from datetime import datetime,timedelta,timezone
from email.utils import format_datetime
import json,os,sys,unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"automation"))
import book_runner as runner
import review_chunks as chunks
import incremental_review as incr
import resilient_recovery as recovery
import omniroute

def corpus(prefix):
    return "\n\n".join(f"{prefix} {i:03d}. "+("Distinct sentence about a quiet lake and distant mountain. "*5) for i in range(100))+"\n"

class ResilientRecoveryTests(unittest.TestCase):
    def test_slow_gateway_retry_bounded_and_retry_after_honored(self):
        chapter={"number":1,"unit_id":"chapter-000001"}
        s={"provider_call_metric":{"unit_id":chapter["unit_id"],"role":"reviewer","outcome":"http_503","duration_ms":300000}}
        with patch.dict(os.environ,{"BOOK_TRANSLATOR_RESILIENT_RECOVERY":"on"}):
            e=omniroute.TransientModelError("503",code="http_503")
            runner.schedule_provider_retry(s,chapter,"reviewer",e)
            remain=(datetime.fromisoformat(s["provider_retry"]["not_before"])-datetime.now(timezone.utc)).total_seconds()
            self.assertAlmostEqual(remain,60,delta=2)
            e=omniroute.TransientModelError("503",code="http_503",retry_after_seconds=800)
            runner.schedule_provider_retry(s,chapter,"reviewer",e)
            remain=(datetime.fromisoformat(s["provider_retry"]["not_before"])-datetime.now(timezone.utc)).total_seconds()
            self.assertAlmostEqual(remain,800,delta=2)
            self.assertEqual(recovery.retry_delay(s,chapter,"translator",e,1,300),300)
            self.assertEqual(recovery.retry_delay(s,chapter,"reviewer",omniroute.TransientModelError("429",code="http_429"),1,300),300)
    def test_http_date_retry_after_not_ignored(self):
        headers={"Retry-After":format_datetime(datetime.now(timezone.utc)+timedelta(minutes=10),usegmt=True)}
        env={"OMNIROUTE_BASE_URL":"https://example.invalid/v1","OMNIROUTE_API_KEY":"dummy","OMNIROUTE_REVIEWER_MODEL":"dummy"}
        with patch.dict(os.environ,env),patch("omniroute.urlopen",side_effect=HTTPError("https://example.invalid",503,"busy",headers,None)):
            with self.assertRaises(omniroute.TransientModelError) as e:
                omniroute.complete("REVIEWER","system","user")
            self.assertGreaterEqual(e.exception.retry_after_seconds,598)
    def test_memory_overflow_does_not_discard_valid_span(self):
        rows=[{"original":f"Name {i}","translation":f"Имя {i}","type":"имя","notes":""} for i in range(45)]
        status={}
        with patch.dict(os.environ,{"BOOK_TRANSLATOR_RESILIENT_RECOVERY":"on"}):
            result=runner.merge_unique_correction_decisions(rows[:30],rows[30:],status=status)
            self.assertEqual(result,rows[:40])
            self.assertEqual(status["optional_memory_proposals_deferred"],5)
    def test_refine_pending_preserves_completed_evidence_and_full_coverage(self):
        s,t=corpus("Source"),corpus("Target")
        plan=chunks.plan_paragraphs(s,t,wider=True)
        self.assertIsNotNone(plan)
        binding=incr.binding(None,"a"*64,"b"*64,"c"*64)
        stage=incr.new_stage(plan,"chapter-000001","f"*40,binding)
        first=plan["chunks"][0]
        report={"context":"independent_session","evidence_version":2,"checks":dict.fromkeys(("coverage","meaning","terminology","style","structure"),True),"findings":[]}
        stage["completed"][first["id"]]={"outcome":"PASS","report":report,"style":[],"glossary":[]}
        incr.record_chunk(stage,first,s,t)
        refined=chunks.refine_pending(plan,plan["chunks"][1]["id"],s,t)
        self.assertIsNotNone(refined)
        self.assertEqual(len(refined["chunks"]),len(plan["chunks"])+1)
        chunks.validate_plan(refined,s,t)
        fresh=incr.refine_current(stage,refined,s,t,binding,"chapter-000001","f"*40)
        self.assertEqual(fresh["completed"],stage["completed"])
        self.assertEqual(fresh["windows"],stage["windows"])
        with self.assertRaises(ValueError):
            chunks.combine(fresh)
        forged=copy.deepcopy(refined)
        forged["chunks"][1]["source_core"][1]-=1
        with self.assertRaises(ValueError):
            chunks.validate_plan(forged,s,t)
        with self.assertRaises(ValueError):
            incr.refine_current(stage,refined,s,t,{**binding,"glossary_sha256":"0"*64},"chapter-000001","f"*40)
    def test_repeated_refine_reconstructs_from_immutable_base(self):
        s,t=corpus("Source"),corpus("Target")
        p=chunks.plan_paragraphs(s,t,wider=True)
        a=chunks.refine_pending(p,p["chunks"][1]["id"],s,t)
        b=chunks.refine_pending(a,a["chunks"][1]["id"],s,t)
        self.assertIsNotNone(b)
        self.assertEqual(b["base_plan"],p)
        chunks.validate_plan(b,s,t)

if __name__=="__main__":
    unittest.main()

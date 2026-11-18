"""Synthetic throughput regressions; no model/network calls."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "automation"))
import execution_scheduler as scheduler
import agent_orchestrator as agent
import review_chunks as chunks

class ThroughputTests(unittest.TestCase):
    def test_review_target_is_source_bound(self):
        status = {}
        scheduler.record_review_target(status, "one", "hash-a", 3000)
        self.assertEqual(scheduler.review_target(status, "one", "hash-a"), 3000)
        self.assertEqual(scheduler.review_target(status, "one", "hash-b"), 12000)

    def test_isolation_does_not_waive_or_leak_between_records(self):
        status = {}
        chapter = {"unit_id": "one"}
        record = {"record_id": "r", "translation_sha256": "h"}
        scheduler.record_isolated(status, chapter, record, {"bad"})
        self.assertEqual(scheduler.isolated_findings(status, chapter, record,
                         [{"id": "bad"}]), {"bad"})
        self.assertEqual(scheduler.isolated_findings(status, chapter,
                         {**record, "record_id": "new"}, [{"id": "bad"}]), set())
        with self.assertRaises(ValueError):
            scheduler.isolated_findings(status, chapter, record, [{"id": "other"}])

    def test_stable_marker_geometry_after_translation_change(self):
        source = "\n\n".join(f"<!-- bt-block:{i:06d} -->\n" + "source " * 200
                           for i in range(1, 31))
        target = "\n\n".join(f"<!-- bt-block:{i:06d} -->\n" + "target " * 220
                           for i in range(1, 31))
        first = chunks.plan_aligned(source, target, stable=True, target_chars=6000)
        changed = target.replace("target " * 220, "expanded " * 480, 1)
        second = chunks.plan_aligned(source, changed, stable=True, count=len(first["chunks"]))
        self.assertEqual([p["source_core"] for p in first["chunks"]],
                         [p["source_core"] for p in second["chunks"]])
        chunks.validate_plan(second, source, changed)

    def test_advice_is_allowlisted_and_metadata_only(self):
        system, user = agent.prompt("contract", [{"severity":"major", "detail":"secret"}], {"id"})
        self.assertNotIn("secret", user)
        self.assertEqual(agent.parse('{"strategy":"isolate_remaining"}'), "isolate_remaining")
        for bad in ('{"strategy":"PASS"}', '{"strategy":"batch_remaining","command":"git"}'):
            with self.assertRaises(ValueError):
                agent.parse(bad)

    def test_batch_publishes_only_final_outputs_and_stops_at_backoff(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            final = root / "outputs"
            outputs = iter(["continue=true\n", "continue_queue=true\nretry_at=tomorrow\n"])
            def run(args, cwd, env):
                Path(env["GITHUB_OUTPUT"]).write_text(next(outputs), "utf-8")
                return SimpleNamespace(returncode=0)
            with patch.dict(os.environ, {"GITHUB_OUTPUT": str(final)}), \
                 patch.object(scheduler.subprocess, "run", side_effect=run) as child, \
                 patch.object(scheduler.subprocess, "check_output", side_effect=["", "head", "head", ""]):
                self.assertEqual(scheduler.run_checkpoints(root, root/"runner.py", "a"*32), 0)
                self.assertEqual(child.call_count, 2)
            self.assertEqual(final.read_text("utf-8"), "continue_queue=true\nretry_at=tomorrow\n")


class QueueAndMetricsTests(unittest.TestCase):
    def test_proactive_size_uses_slow_reviewer_failures_not_other_roles(self):
        self.assertEqual(scheduler.proactive_review_target({}, 79000), 12000)
        self.assertEqual(scheduler.proactive_review_target({}, 81000), 6000)
        m = {"role": "reviewer", "outcome": "http_503",
             "duration_ms": 300000, "request_chars": 80000}
        self.assertEqual(scheduler.proactive_review_target({"provider_call_metrics":[m]}, 70000), 6000)
        self.assertEqual(scheduler.proactive_review_target(
            {"provider_call_metrics":[{**m, "role": "translator"}]}, 70000), 12000)

    def test_metrics_never_count_response_as_accepted_section(self):
        import execution_metrics as metrics
        s = {}
        metrics.provider(s, {"role":"reviewer","outcome":"response",
                             "duration_ms":123, "prompt_tokens":7, "content":"secret"})
        self.assertEqual(s["execution_metrics"]["accepted_operations"], {})
        metrics.accepted(s, "reviewer", "CORRECTIONS_REQUIRED")
        self.assertEqual(s["execution_metrics"]["accepted_operations"], {"review_rejected":1})
        metrics.accepted(s, "reviewer", "PASS")
        self.assertEqual(s["execution_metrics"]["accepted_operations"]["reviewed"], 1)
        self.assertNotIn("secret", json.dumps(s))

    def test_costly_queue_candidate_requires_distinct_canonical_artifacts_and_cost(self):
        import review_convergence as convergence
        unit = "chapter-000001"
        def review(record, target, n):
            return {"unit_id":unit,"record_id":record,"source_sha256":"source",
                    "translation_sha256":target,"outcome":"CORRECTIONS_REQUIRED",
                    "quality_report":{"evidence_version":2,"findings":[
                        {"severity":"major","resolved":False} for _ in range(n)]}}
        ledger = {"records":[review("r1","t1",1), review("r2","t2",2)]}
        chapter = {"unit_id":unit,"translation_acceptance":{
            "source_sha256":"source","translation_sha256":"t2"}}
        status = {"correction_rounds":{unit:1},"provider_call_metrics":[
            {"unit_id":unit,"role":"reviewer","outcome":"response","duration_ms":150000}
            for _ in range(10)]}
        self.assertEqual(convergence.costly_nonconvergence(ledger, chapter,status)["action"],
                         "operator-attention-no-pass")
        self.assertIsNone(convergence.costly_nonconvergence(ledger, chapter,
            {**status,"provider_call_metrics":status["provider_call_metrics"][:2]}))
        self.assertIsNone(convergence.costly_nonconvergence(ledger,
            {**chapter,"translation_acceptance":{"source_sha256":"source","translation_sha256":"stale"}},status))
        self.assertEqual(agent.parse('{"strategy":"defer_attention"}', agent.QUEUE_STRATEGIES),
                         "defer_attention")

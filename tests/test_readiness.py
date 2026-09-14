import copy
import datetime
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_runner import runner
from test_runner import EvaluationEvidenceTests

class ReadinessTests(unittest.TestCase):
    def bridge(self,directory):
        paths=EvaluationEvidenceTests()._write_bridge(directory)
        evidence,suite,workflow,report=paths
        suite.write_text(json.dumps({'version':1,'scenarios':[{'id':'lookup','assertions':{'max_steps':1}}]}))
        trace=[{'scenario_id':'lookup','tool':'read','args':{},'cost_usd':0}]
        result={'status':'PASSED','summary':{'scenarios':1,'passed':1,'failed':0}}
        report.write_text(json.dumps({'trace':trace,'evaluated_result':result}))
        e=json.loads(evidence.read_text());e.update(timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00','Z'),suite_sha256=runner.sha256_file(suite),report_sha256=runner.sha256_file(report),gate_trace_sha256=runner.gate_trace_digest(trace));evidence.write_text(json.dumps(e))
        return paths

    def test_contradictory_report_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            paths=self.bridge(d);e,s,w,r=paths
            report=json.loads(r.read_text());report['evaluated_result']['status']='FAILED';r.write_text(json.dumps(report))
            evidence=json.loads(e.read_text());evidence['report_sha256']=runner.sha256_file(r);e.write_text(json.dumps(evidence))
            with self.assertRaises(ValueError):runner.verify_evaluation_evidence(*paths)

    def test_suite_is_re_evaluated(self):
        with tempfile.TemporaryDirectory() as d:
            paths=self.bridge(d);e,s,w,r=paths
            suite=json.loads(s.read_text());suite['scenarios'][0]['assertions']={'tool_called':'absent'};s.write_text(json.dumps(suite))
            evidence=json.loads(e.read_text());evidence['suite_sha256']=runner.sha256_file(s);e.write_text(json.dumps(evidence))
            with self.assertRaises(ValueError):runner.verify_evaluation_evidence(*paths)

    def test_evaluation_age(self):
        for age in [901,-1]:
            with self.subTest(age=age),tempfile.TemporaryDirectory() as d:
                paths=self.bridge(d);e=paths[0];evidence=json.loads(e.read_text());evidence['timestamp']=datetime.datetime.fromtimestamp(2000-age,datetime.timezone.utc).isoformat().replace('+00:00','Z');e.write_text(json.dumps(evidence))
                with patch.object(runner.time,'time',return_value=2000),self.assertRaises(ValueError):runner.verify_evaluation_evidence(*paths)

    def test_large_request_rejected_before_network(self):
        with patch.object(runner.urllib.request,'build_opener') as opener:
            with self.assertRaises(ValueError):runner.submit_to_gate('https://gate.invalid','fake',{'trace':'x'*(128*1024)})
            opener.assert_not_called()

    def test_full_cycles_and_scenario_boundaries(self):
        step=lambda scenario,tool:{'scenario_id':scenario,'tool':tool,'args':{},'cost_usd':0}
        self.assertIsNone(runner.find_scenario_cycle([step('a','read'),step('b','read')]))
        cycle=[step('a',str(i)) for i in range(11)]
        self.assertIsNotNone(runner.find_scenario_cycle(cycle*2+[step('a','end')]))

    def test_bounded_age_configuration_and_boundary(self):
        for limit in [0,-1,86401,True,float('inf')]:
            with self.subTest(limit=limit),self.assertRaises(ValueError):runner.verify_fresh_timestamp('2026-09-14T00:00:00Z',max_age_seconds=limit)
        runner.verify_fresh_timestamp('2026-09-14T00:00:00Z',now=runner.parse_timestamp('2026-09-14T00:15:00Z'))

    def test_serialized_utf8_request_boundary(self):
        overhead=len(runner.serialize_gate_payload({'x':''}))
        self.assertEqual(len(runner.serialize_gate_payload({'x':'a'*(runner.MAX_GATE_BYTES-overhead)})),runner.MAX_GATE_BYTES)
        with self.assertRaises(ValueError):runner.serialize_gate_payload({'x':'a'*(runner.MAX_GATE_BYTES-overhead+1)})
        with self.assertRaises(ValueError):runner.serialize_gate_payload({'x':'é'*(runner.MAX_GATE_BYTES//2)})

import contextlib
import io
import json
import os
import tempfile
import hashlib
from unittest.mock import patch
import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('runner', Path(__file__).parents[1] / 'runner.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

class GateTests(unittest.TestCase):
    def setUp(self):
        self.suite = {'version': 1, 'scenarios': [{'id': 'lookup', 'assertions': {'tool_called': 'lookup', 'max_steps': 2, 'max_cost_usd': 0.05}}]}
        self.trace = [{'scenario_id': 'lookup', 'tool': 'lookup', 'args': {'id': 1}, 'cost_usd': 0.02}]

    def test_real_measurements(self):
        result = runner.evaluate(self.suite, self.trace)
        self.assertEqual(result, {'scenarios': 1, 'passed': 1, 'steps': 1, 'cost_usd': 0.02})

    def test_missing_and_invalid_evidence_fails(self):
        for trace in [[], {}, [{'tool': 'lookup'}], self.trace + [dict(self.trace[0], scenario_id='other')], [dict(self.trace[0], cost_usd=float('nan'))]]:
            with self.subTest(trace=trace), self.assertRaises(ValueError):
                runner.evaluate(self.suite, trace)
        for suite in [{}, {'version': 1, 'scenarios': []}, {'version': 1, 'scenarios': [{'id': 'lookup', 'assertions': {}}]}]:
            with self.subTest(suite=suite), self.assertRaises(ValueError):
                runner.evaluate(suite, self.trace)

    def test_assertions_block_bad_candidate(self):
        for changes in [{'tool': 'wrong'}, {'cost_usd': 0.1}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                runner.evaluate(self.suite, [dict(self.trace[0], **changes)])
        with self.assertRaises(ValueError):
            runner.evaluate(self.suite, self.trace * 3)

    def test_unknown_assertions_fail(self):
        self.suite['scenarios'][0]['assertions']['typo'] = True
        with self.assertRaises(ValueError):
            runner.evaluate(self.suite, self.trace)

    def test_only_explicit_approval_passes(self):
        for result in [{}, None, [], {'verdict': 'HOLD'}, {'verdict': 'UNKNOWN'}, {'verdict': 'approved'}, {'verdict': True}]:
            with self.subTest(result=result), self.assertRaises(ValueError):
                runner.require_approval(result)
        runner.require_approval({'verdict': 'APPROVED'})

    def test_main_blocks_malformed_remote_and_network_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            suite, trace = Path(directory) / 'suite.json', Path(directory) / 'trace.json'
            suite.write_text(json.dumps(self.suite))
            trace.write_text(json.dumps(self.trace))
            argv = ['--scenarios', str(suite), '--trace', str(trace)]
            with patch.dict(os.environ, {'RUNLOG_TOKEN': 'test-secret'}), contextlib.redirect_stderr(io.StringIO()):
                for verdict in [{}, {'verdict': 'HOLD'}, {'verdict': 'UNKNOWN'}, None]:
                    with patch.object(runner, 'submit_to_gate', return_value=verdict):
                        self.assertEqual(runner.main(argv), 1)
                with patch.object(runner, 'submit_to_gate', side_effect=TimeoutError):
                    self.assertEqual(runner.main(argv), 1)
                with patch.object(runner, 'submit_to_gate', return_value={'verdict': 'APPROVED'}), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(runner.main(argv), 0)

    def test_transport_rejects_insecure_urls_and_redirects(self):
        for url in ['http://runlog.cc/api/v1/gate', 'https://user:password@runlog.cc/gate']:
            with self.assertRaises(ValueError):
                runner.submit_to_gate(url, 'test-secret', {})
        with self.assertRaises(ValueError):
            runner.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.example')

    def test_no_token_cli_argument(self):
        with self.assertRaises(SystemExit):
            runner.parse_args(['--scenarios', 'suite.json', '--trace', 'trace.json', '--token', 'secret'])


class EvaluationEvidenceTests(unittest.TestCase):
    signed_fixture = {
        'jwk': {'kty': 'OKP', 'crv': 'Ed25519', 'x': 'A6EHv_POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg'},
        'signed_payload': '{"issuer":"https://gate.example","repo":"acme/widget","head_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","base_sha":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","issued_at":"2026-09-08T00:00:00Z","trace_sha256":"262b40663397d47a97258b671983c435263bbb0d7162add1c7b4b799e4257355","verdict":"APPROVED"}',
        'signature': '/xKK6u2agp2iOut+OmDy7lcFRg9giI+jQfNPlOfpeZ4/Vj3Vn4904EzihgJcTPPA8apxkqs6LN7sQzikbBIQBA==',
        'payload_sha256': '68e357fdbd94e2288377cc59ed9d4ce71eecdda1ffbecf6ab6a8350946dc124d',
        'signature_algorithm': 'Ed25519',
    }

    def _write_bridge(self, directory):
        root = Path(directory)
        suite, workflow, report = root / 'suite.json', root / 'workflow.yml', root / 'report.json'
        suite.write_text(json.dumps({'version':1,'scenarios':[{'id':'lookup','assertions':{'max_steps':1}}]}))
        workflow.write_text('name: measured evaluation\n')
        report.write_text(json.dumps({'evaluated_result': {'status':'PASSED','summary': {'scenarios': 1, 'passed': 1, 'failed': 0}}, 'trace': [{'scenario_id': 'lookup', 'tool':'read', 'args':{}, 'cost_usd':0}]}))
        digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
        evidence = {
            'schema': runner.EVIDENCE_SCHEMA, 'trace_encoding': runner.TRACE_ENCODING, 'repo': 'acme/widget',
            'head_sha': 'a' * 40, 'base_sha': 'b' * 40,
            'suite_sha256': digest(suite), 'workflow_sha256': digest(workflow),
            'report_sha256': digest(report), 'gate_trace_sha256': runner.gate_trace_digest([{'scenario_id': 'lookup', 'tool':'read', 'args':{}, 'cost_usd':0}]),
            'evaluated_result': {'status': 'PASSED', 'summary': {'scenarios': 1, 'passed': 1, 'failed': 0}},
            'policy_version': 'policy.v1', 'timestamp': __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat().replace('+00:00','Z'),
        }
        evidence_path = root / 'evidence.json'
        evidence_path.write_text(json.dumps(evidence))
        return evidence_path, suite, workflow, report

    def test_known_signed_fixture_and_bridge_succeeds(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence, suite, workflow, report = self._write_bridge(directory)
            parsed = runner.verify_evaluation_evidence(evidence, suite, workflow, report)
            self.assertEqual(parsed['repo'], 'acme/widget')
            # The known Ed25519 signature remains valid, but its legacy packet
            # cannot satisfy the v2 trace policy and artifact binding contract.
            import base64
            runner.verify_ed25519(self.signed_fixture['jwk'], self.signed_fixture['signed_payload'].encode(), base64.b64decode(self.signed_fixture['signature']))
            with self.assertRaises(ValueError):
                runner.verify_gate_decision(self.signed_fixture, self.signed_fixture['jwk'], 'https://gate.example', parsed, now=1788825600, max_age_seconds=60)

    def test_tampered_or_swapped_bridge_files_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence, suite, workflow, report = self._write_bridge(directory)
            report.write_text('{"replaced":true}')
            with self.assertRaises(ValueError):
                runner.verify_evaluation_evidence(evidence, suite, workflow, report)
            evidence, suite, workflow, report = self._write_bridge(directory)
            with self.assertRaises(ValueError):
                runner.verify_evaluation_evidence(evidence, workflow, suite, report)

    def test_signed_decision_rejects_stale_wrong_key_and_wrong_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence, suite, workflow, report = self._write_bridge(directory)
            parsed = runner.verify_evaluation_evidence(evidence, suite, workflow, report)
            with self.assertRaises(ValueError):
                runner.verify_gate_decision(dict(self.signed_fixture, signature_algorithm=None), self.signed_fixture['jwk'], 'https://gate.example', parsed, now=1788825600, max_age_seconds=60)
            with self.assertRaises(ValueError):
                runner.verify_gate_decision(dict(self.signed_fixture, payload_sha256='0' * 64), self.signed_fixture['jwk'], 'https://gate.example', parsed, now=1788825600, max_age_seconds=60)
            with self.assertRaises(ValueError):
                runner.verify_gate_decision(self.signed_fixture, self.signed_fixture['jwk'], 'https://gate.example', parsed, now=1788825800, max_age_seconds=60)
            wrong_jwk = dict(self.signed_fixture['jwk'], x='AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA')
            with self.assertRaises(ValueError):
                runner.verify_gate_decision(self.signed_fixture, wrong_jwk, 'https://gate.example', parsed, now=1788825600, max_age_seconds=60)
            parsed['head_sha'] = 'd' * 40
            with self.assertRaises(ValueError):
                runner.verify_gate_decision(self.signed_fixture, self.signed_fixture['jwk'], 'https://gate.example', parsed, now=1788825600, max_age_seconds=60)

if __name__ == '__main__':
    unittest.main()

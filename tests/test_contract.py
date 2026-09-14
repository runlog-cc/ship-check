import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_runner import runner


class CanonicalContractTests(unittest.TestCase):
    def test_float_and_key_order_vectors(self):
        vectors = [
            (None, '["null"]'),
            (True, '["boolean",true]'),
            (0.0, '["number","0000000000000000"]'),
            (-0.0, '["number","0000000000000000"]'),
            (1, '["number","3ff0000000000000"]'),
            (1e-6, '["number","3eb0c6f7a0b5ed8d"]'),
            ('é', '["string","c3a9"]'),
            ({'2': 0, '10': 1}, '["object",[["3130",["number","3ff0000000000000"]],["32",["number","0000000000000000"]]]]'),
        ]
        self.assertTrue(hasattr(runner, 'canonical_json'), 'versioned canonical JSON encoder is required')
        for value, expected in vectors:
            with self.subTest(value=value):
                self.assertEqual(runner.canonical_json(value).decode(), expected)
        self.assertEqual(runner.gate_trace_digest([{'b': 1.0, 'a': -0.0}]), runner.gate_trace_digest([{'a': 0, 'b': 1}]))

    def test_nonportable_values_fail_closed(self):
        self.assertTrue(hasattr(runner, 'canonical_json'), 'versioned canonical JSON encoder is required')
        for value in [float('inf'), float('nan'), 2**53, 1e30, '\ud800', {1: 'not a JSON key'}]:
            with self.subTest(value=repr(value)), self.assertRaises(ValueError):
                runner.canonical_json(value)

    def test_duplicate_json_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'evidence.json'
            path.write_text('{"head_sha":"old","head_sha":"new"}')
            with self.assertRaises(ValueError):
                runner.load_json(path)

    def test_expected_commit_context_is_required_and_independent(self):
        self.assertTrue(hasattr(runner, 'verify_expected_context'), 'trusted CI bindings must be checked')
        evidence = {'repo': 'acme/widget', 'head_sha': 'a' * 40, 'base_sha': 'b' * 40}
        runner.verify_expected_context(evidence, 'acme/widget', 'a' * 40, 'b' * 40)
        for values in [(None, None, None), ('acme/other', 'a'*40, 'b'*40), ('acme/widget', 'c'*40, 'b'*40), ('acme/widget', 'a'*40, 'c'*40)]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                runner.verify_expected_context(evidence, *values)

    def test_offline_cli_requires_no_token_and_never_submits(self):
        with tempfile.TemporaryDirectory() as directory:
            suite, trace = Path(directory)/'suite.json', Path(directory)/'trace.json'
            suite.write_text(json.dumps({'version': 1, 'scenarios': [{'id': 'test', 'assertions': {'tool_called': 'read'}}]}))
            trace.write_text(json.dumps([{'scenario_id': 'test', 'tool': 'read', 'args': {}, 'cost_usd': 0}]))
            out = io.StringIO()
            with patch.dict(os.environ, {}, clear=True), patch.object(runner, 'submit_to_gate') as submit, contextlib.redirect_stdout(out):
                try:
                    code = runner.main(['--offline', '--scenarios', str(suite), '--trace', str(trace)])
                except SystemExit:
                    self.fail('offline CLI must be supported')
                self.assertEqual(code, 0)
                self.assertIn('LOCAL PASS', out.getvalue())
                self.assertNotIn('APPROVED', out.getvalue())
                submit.assert_not_called()


class SignedContractTests(unittest.TestCase):
    def test_valid_signature_cannot_omit_scope_or_artifact_bindings(self):
        # The known fixture has a real valid signature. It must be rejected for
        # lacking a v2 contract even if its trace and metadata match the caller.
        from test_runner import EvaluationEvidenceTests
        fixture = EvaluationEvidenceTests.signed_fixture
        packet = json.loads(fixture['signed_payload'])
        evidence = {**packet, 'gate_trace_sha256': packet['trace_sha256']}
        with self.assertRaises(ValueError):
            runner.verify_gate_decision(fixture, fixture['jwk'], 'https://gate.example', evidence, now=1788825600)

    def test_main_rejects_swapped_ci_commit_before_any_gate_request(self):
        from test_runner import EvaluationEvidenceTests
        with tempfile.TemporaryDirectory() as directory:
            paths = EvaluationEvidenceTests()._write_bridge(directory)
            key = Path(directory) / 'key.json'
            key.write_text('{}')
            argv = [arg for pair in zip(('--evaluation-evidence','--evaluation-suite','--evaluation-workflow','--evaluation-report'), map(str, paths)) for arg in pair]
            argv += ['--issuer-public-key', str(key), '--expected-issuer', 'https://gate.example', '--expected-repo', 'acme/widget', '--expected-head-sha', 'c'*40, '--expected-base-sha', 'b'*40]
            with patch.dict(os.environ, {'RUNLOG_TOKEN': 'fake'}, clear=True), patch.object(runner, 'submit_to_gate') as submit, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(runner.main(argv), 1)
                submit.assert_not_called()

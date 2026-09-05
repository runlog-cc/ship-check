import contextlib
import io
import json
import os
import tempfile
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

if __name__ == '__main__':
    unittest.main()

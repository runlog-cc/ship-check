#!/usr/bin/env python3
"""Run a deterministic local tool; produce demo records without providers or keys."""
import argparse
import datetime
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import runner


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--fail', action='store_true', help='Execute an extra call to violate max_steps')
    parser.add_argument('--repo', default='example/demo')
    parser.add_argument('--head-sha', default='a' * 40)
    parser.add_argument('--base-sha', default='b' * 40)
    parser.add_argument('--workflow', type=Path, default=Path(__file__))
    args = parser.parse_args()
    root = Path(args.output)
    root.mkdir(parents=True, exist_ok=True)
    suite = {'version': 1, 'scenarios': [{'id': 'lookup', 'assertions': {'tool_called': 'lookup_order', 'max_steps': 1, 'max_cost_usd': 0}}]}
    trace = []

    def lookup_order(order_id):
        # This real in-memory tool has no provider cost. Do not substitute zero
        # when integrating a paid tool whose actual cost is unknown.
        result = {104: 'ready', 105: 'pending'}[order_id]
        trace.append({'scenario_id': 'lookup', 'tool': 'lookup_order', 'args': {'order_id': order_id}, 'cost_usd': 0.0})
        return result

    assert lookup_order(104) == 'ready'
    if args.fail:
        assert lookup_order(105) == 'pending'
    try:
        runner.evaluate(suite, trace)
        result = {'status': 'PASSED', 'summary': {'scenarios': 1, 'passed': 1, 'failed': 0}}
    except ValueError:
        result = {'status': 'FAILED', 'summary': {'scenarios': 1, 'passed': 0, 'failed': 1}}
    for name, value in (('suite.json', suite), ('trace.json', trace), ('report.json', {'trace': trace, 'evaluated_result': result, 'demo': True})):
        (root/name).write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    (root/'workflow.yml').write_bytes(args.workflow.read_bytes())
    evidence = {'schema': runner.EVIDENCE_SCHEMA, 'trace_encoding': runner.TRACE_ENCODING,
                'repo': args.repo, 'head_sha': args.head_sha, 'base_sha': args.base_sha,
                'suite_sha256': runner.sha256_file(root/'suite.json'),
                'workflow_sha256': runner.sha256_file(root/'workflow.yml'),
                'report_sha256': runner.sha256_file(root/'report.json'),
                'gate_trace_sha256': runner.gate_trace_digest(trace), 'evaluated_result': result,
                'policy_version': 'demo.lookup.v1',
                'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z')}
    (root/'evidence.json').write_text(json.dumps(evidence, indent=2) + '\n', encoding='utf-8')
    print('Demo records generated from a local tool; this is not an agent-quality benchmark.')


if __name__ == '__main__':
    main()

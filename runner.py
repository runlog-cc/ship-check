#!/usr/bin/env python3
"""Validate caller-recorded evidence and fail closed on the remote gate verdict.

No agent execution, trace generation, PR comments, or cryptographic signing.
"""
import argparse
import json
import math
import os
from pathlib import Path
import sys
import urllib.parse
import urllib.request

MAX_BYTES = 5 * 1024 * 1024


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenarios', required=True, help='Version 1 JSON assertion suite')
    parser.add_argument('--trace', required=True, help='Recorded candidate JSON trace array')
    parser.add_argument('--baseline-trace', help='Optional recorded baseline trace array')
    parser.add_argument('--gate-url', default=os.getenv('RUNLOG_GATE_URL', 'https://runlog.cc/api/v1/gate'))
    return parser.parse_args(argv)


def load_json(path):
    with Path(path).open('rb') as source:
        data = source.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError('Evidence exceeds 5 MiB limit')
    return json.loads(data, parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Non-finite JSON number')))


def number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def evaluate(suite, trace):
    if not isinstance(suite, dict) or type(suite.get('version')) is not int or suite['version'] != 1:
        raise ValueError('Expected version 1 JSON scenario suite')
    scenarios = suite.get('scenarios')
    if not isinstance(scenarios, list) or not scenarios or not isinstance(trace, list) or not trace:
        raise ValueError('Nonempty scenarios and trace required')
    indexed = {}
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            raise ValueError('Invalid scenario')
        sid, assertions = scenario.get('id'), scenario.get('assertions')
        if not isinstance(sid, str) or not sid.strip() or sid in indexed:
            raise ValueError('Scenario IDs must be nonempty and unique')
        if not isinstance(assertions, dict) or not assertions or set(assertions) - {'tool_called', 'max_steps', 'max_cost_usd'}:
            raise ValueError('Explicit supported assertions required')
        if 'tool_called' in assertions and (not isinstance(assertions['tool_called'], str) or not assertions['tool_called'].strip()):
            raise ValueError('Invalid tool_called assertion')
        if 'max_steps' in assertions and (type(assertions['max_steps']) is not int or assertions['max_steps'] < 1):
            raise ValueError('Invalid max_steps assertion')
        if 'max_cost_usd' in assertions and not number(assertions['max_cost_usd']):
            raise ValueError('Invalid max_cost_usd assertion')
        indexed[sid] = (assertions, [])
    for step in trace:
        if not isinstance(step, dict) or not isinstance(step.get('scenario_id'), str) or step['scenario_id'] not in indexed:
            raise ValueError('Every trace step must reference a declared scenario')
        if not isinstance(step.get('tool'), str) or not step['tool'].strip() or not isinstance(step.get('args'), dict) or not number(step.get('cost_usd')):
            raise ValueError('Each step requires tool, args object, and measured nonnegative cost_usd')
        indexed[step['scenario_id']][1].append(step)
    for assertions, steps in indexed.values():
        if not steps:
            raise ValueError('Missing scenario evidence')
        if 'tool_called' in assertions and not any(s['tool'] == assertions['tool_called'] for s in steps):
            raise ValueError('Required tool assertion failed')
        if 'max_steps' in assertions and len(steps) > assertions['max_steps']:
            raise ValueError('Step ceiling assertion failed')
        if 'max_cost_usd' in assertions and sum(s['cost_usd'] for s in steps) > assertions['max_cost_usd']:
            raise ValueError('Cost ceiling assertion failed')
    total = sum(s['cost_usd'] for s in trace)
    if not math.isfinite(total):
        raise ValueError('Invalid total cost')
    return {'scenarios': len(indexed), 'passed': len(indexed), 'steps': len(trace), 'cost_usd': total}


def require_approval(result):
    if not isinstance(result, dict) or result.get('verdict') != 'APPROVED':
        raise ValueError('Gate did not return APPROVED')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Gate redirects are forbidden')


def submit_to_gate(url, token, payload):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('Gate URL must be HTTPS without embedded credentials or fragment')
    req = urllib.request.Request(url, data=json.dumps(payload, allow_nan=False).encode(), headers={
        'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json', 'User-Agent': 'RunLog-ShipCheck/0.1.0'})
    with urllib.request.build_opener(NoRedirect).open(req, timeout=30) as response:
        raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError('Oversized gate response')
        return json.loads(raw)


def main(argv=None):
    args = parse_args(argv)
    try:
        token = os.getenv('RUNLOG_TOKEN') or os.getenv('RUNLOG_API_KEY')
        if not token:
            raise ValueError('RUNLOG_TOKEN or RUNLOG_API_KEY is required')
        suite, trace = load_json(args.scenarios), load_json(args.trace)
        measurements = {'candidate': evaluate(suite, trace)}
        if args.baseline_trace:
            measurements['baseline'] = evaluate(suite, load_json(args.baseline_trace))
        payload = {'trace': trace, 'scenarios': suite, 'measurements': measurements,
                   'repo': os.getenv('GITHUB_REPOSITORY', ''), 'head_sha': os.getenv('HEAD_SHA', ''),
                   'base_sha': os.getenv('BASE_SHA', '')}
        result = submit_to_gate(args.gate_url, token, payload)
        require_approval(result)
        print('APPROVED: recorded evidence passed local assertions and remote gate.')
        print(json.dumps(measurements, allow_nan=False))
        return 0
    except Exception as error:
        # Do not echo trace contents, API bodies, URLs, or credentials into CI logs.
        print('Ship-Check failed closed (' + type(error).__name__ + '). Check evidence, configuration, and gate availability.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
"""Validate caller-recorded evidence and fail closed on the remote gate verdict.

No agent execution, trace generation, PR comments, or cryptographic signing.
"""
import argparse
import base64
import datetime
import hashlib
import json
import math
import os
import re
import struct
from pathlib import Path
import sys
import time
import urllib.parse
import urllib.request

MAX_BYTES = 5 * 1024 * 1024
VERSION = '0.2.0'
EVIDENCE_SCHEMA = 'runlog.evaluation-evidence.v2'
TRACE_ENCODING = 'runlog.canonical-json.v1'
GATE_CONTRACT = 'runlog.trace-gate.v2'
GATE_POLICY = 'runlog.repeated-tool-cycle.v1'


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', action='version', version=VERSION)
    parser.add_argument('--offline', action='store_true', help='Check record-only assertions without network or gate approval')
    parser.add_argument('--expected-repo', default=os.getenv('RUNLOG_EXPECTED_REPO'), help='Repository from trusted CI context')
    parser.add_argument('--expected-head-sha', default=os.getenv('RUNLOG_EXPECTED_HEAD_SHA'), help='Candidate commit from trusted CI context')
    parser.add_argument('--expected-base-sha', default=os.getenv('RUNLOG_EXPECTED_BASE_SHA'), help='Baseline commit from trusted CI context')
    parser.add_argument('--scenarios', help='Version 1 JSON assertion suite (record-only mode)')
    parser.add_argument('--trace', help='Recorded candidate JSON trace array (record-only mode)')
    parser.add_argument('--baseline-trace', help='Optional recorded baseline trace array')
    parser.add_argument('--evaluation-evidence', help='Signed-bridge evidence artifact JSON')
    parser.add_argument('--evaluation-suite', help='Exact suite file referenced by evidence')
    parser.add_argument('--evaluation-workflow', help='Exact workflow file referenced by evidence')
    parser.add_argument('--evaluation-report', help='Exact measured report file referenced by evidence')
    parser.add_argument('--issuer-public-key', help='Trusted Ed25519 issuer public JWK JSON file')
    parser.add_argument('--expected-issuer', help='Expected HTTPS issuer origin')
    parser.add_argument('--decision-max-age-seconds', type=int, default=900,
                        help='Maximum accepted signed gate decision age (default: 900)')
    parser.add_argument('--gate-url', default=os.getenv('RUNLOG_GATE_URL', 'https://runlog.cc/api/v1/gate'))
    return parser.parse_args(argv)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON object key')
        result[key] = value
    return result


def load_json(path):
    with Path(path).open('rb') as source:
        data = source.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError('Evidence exceeds 5 MiB limit')
    return json.loads(data, object_pairs_hook=unique_object, parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Non-finite JSON number')))


def sha256_file(path):
    with Path(path).open('rb') as source:
        digest = hashlib.sha256()
        remaining = MAX_BYTES + 1
        while remaining:
            block = source.read(min(65536, remaining))
            if not block:
                break
            digest.update(block)
            remaining -= len(block)
    if Path(path).stat().st_size > MAX_BYTES:
        raise ValueError('Evidence bridge file exceeds 5 MiB limit')
    return digest.hexdigest()


def hex_digest(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def require_text(mapping, key):
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Missing required evidence field')
    return value


def verify_evaluation_evidence(evidence_path, suite_path, workflow_path, report_path):
    evidence = load_json(evidence_path)
    if not isinstance(evidence, dict) or evidence.get('schema') != EVIDENCE_SCHEMA:
        raise ValueError('Expected evaluation evidence v2')
    if evidence.get('trace_encoding') != TRACE_ENCODING:
        raise ValueError('Unsupported trace encoding')
    for key in ('repo', 'head_sha', 'base_sha', 'policy_version', 'timestamp'):
        require_text(evidence, key)
    for field, path in (('suite_sha256', suite_path), ('workflow_sha256', workflow_path), ('report_sha256', report_path)):
        if not hex_digest(evidence.get(field)) or not isinstance(path, (str, Path)):
            raise ValueError('Invalid evidence digest')
        if sha256_file(path) != evidence[field]:
            raise ValueError('Evidence digest did not match supplied file')
    if not hex_digest(evidence.get('gate_trace_sha256')):
        raise ValueError('Invalid gate trace digest')
    report = load_json(report_path)
    trace = report.get('trace') if isinstance(report, dict) else None
    if not isinstance(trace, list) or not trace or gate_trace_digest(trace) != evidence['gate_trace_sha256']:
        raise ValueError('Report trace did not match evaluation evidence')
    result = evidence.get('evaluated_result')
    if not isinstance(result, dict) or result.get('status') != 'PASSED':
        raise ValueError('Measured evaluation did not pass')
    summary = result.get('summary')
    if not isinstance(summary, dict) or any(type(summary.get(k)) is not int or summary[k] < 0 for k in ('scenarios', 'passed', 'failed')):
        raise ValueError('Invalid measured evaluation summary')
    if summary['scenarios'] != summary['passed'] + summary['failed'] or summary['passed'] < 1 or summary['failed'] != 0:
        raise ValueError('Measured evaluation summary did not pass')
    parse_timestamp(evidence['timestamp'])
    return evidence


def parse_timestamp(value):
    if not isinstance(value, str) or not value.endswith('Z'):
        raise ValueError('Timestamp must be UTC RFC3339')
    try:
        return datetime.datetime.fromisoformat(value[:-1] + '+00:00').timestamp()
    except ValueError as error:
        raise ValueError('Invalid timestamp') from error


def b64url_decode(value):
    if not isinstance(value, str) or not value or any(c not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_' for c in value):
        raise ValueError('Invalid base64url value')
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


# Minimal Ed25519 verifier, kept dependency-free for composite-action portability.
P = 2 ** 255 - 19
L = 2 ** 252 + 27742317777372353535851937790883648493
D = (-121665 * pow(121666, P - 2, P)) % P
I = pow(2, (P - 1) // 4, P)
B = (15112221349535400772501151409588531511454012693041857206046113283949847762202,
     46316835694926478169428394003475163141307993866256225615783033603165251855960)


def edwards_add(a, b):
    x1, y1 = a
    x2, y2 = b
    denominator = (1 + D * x1 * x2 * y1 * y2) % P
    x3 = ((x1 * y2 + x2 * y1) * pow(denominator, P - 2, P)) % P
    y3 = ((y1 * y2 + x1 * x2) * pow((1 - D * x1 * x2 * y1 * y2) % P, P - 2, P)) % P
    return x3, y3


def scalar_mult(scalar, point):
    result = (0, 1)
    while scalar:
        if scalar & 1:
            result = edwards_add(result, point)
        point = edwards_add(point, point)
        scalar >>= 1
    return result


def decode_point(encoded):
    if len(encoded) != 32:
        raise ValueError('Invalid Ed25519 point')
    value = int.from_bytes(encoded, 'little')
    sign, y = value >> 255, value & ((1 << 255) - 1)
    if y >= P:
        raise ValueError('Invalid Ed25519 point')
    x2 = ((y * y - 1) * pow((D * y * y + 1) % P, P - 2, P)) % P
    x = pow(x2, (P + 3) // 8, P)
    if (x * x - x2) % P:
        x = (x * I) % P
    if (x * x - x2) % P:
        raise ValueError('Invalid Ed25519 point')
    if (x & 1) != sign:
        x = P - x
    return x, y


def verify_ed25519(public_jwk, message, signature):
    if not isinstance(public_jwk, dict) or set(public_jwk) - {'kty', 'crv', 'x', 'alg', 'kid'}:
        raise ValueError('Invalid issuer public JWK')
    if public_jwk.get('kty') != 'OKP' or public_jwk.get('crv') != 'Ed25519' or public_jwk.get('alg') not in (None, 'Ed25519', 'EdDSA'):
        raise ValueError('Invalid issuer public JWK')
    key = b64url_decode(public_jwk.get('x'))
    if len(signature) != 64:
        raise ValueError('Invalid gate signature')
    r, s = signature[:32], int.from_bytes(signature[32:], 'little')
    if s >= L:
        raise ValueError('Invalid gate signature')
    public_point, r_point = decode_point(key), decode_point(r)
    h = int.from_bytes(hashlib.sha512(r + key + message).digest(), 'little') % L
    if scalar_mult(s, B) != edwards_add(r_point, scalar_mult(h, public_point)):
        raise ValueError('Invalid gate signature')


def verify_gate_decision(decision, issuer_public_key, expected_issuer, evidence, now=None, max_age_seconds=900, expected_artifact_binding=None):
    if not isinstance(decision, dict) or not isinstance(max_age_seconds, int) or max_age_seconds < 1:
        raise ValueError('Invalid signed gate decision')
    signed_payload, signature = decision.get('signed_payload'), decision.get('signature')
    if not isinstance(signed_payload, str) or not isinstance(signature, str) or decision.get('signature_algorithm') != 'Ed25519':
        raise ValueError('Missing signed gate decision')
    encoded = signed_payload.encode('utf-8')
    if decision.get('payload_sha256') != hashlib.sha256(encoded).hexdigest():
        raise ValueError('Signed payload digest mismatch')
    try:
        signature_bytes = base64.b64decode(signature, validate=True)
        packet = json.loads(signed_payload, object_pairs_hook=unique_object, parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Non-finite JSON number')))
    except (ValueError, UnicodeError) as error:
        raise ValueError('Malformed signed gate decision') from error
    verify_ed25519(issuer_public_key, encoded, signature_bytes)
    if not isinstance(packet, dict) or packet.get('issuer') != expected_issuer or packet.get('verdict') != 'APPROVED':
        raise ValueError('Gate decision was not approved by expected issuer')
    if (packet.get('gate_contract') != GATE_CONTRACT or packet.get('trace_encoding') != TRACE_ENCODING
            or packet.get('decision_scope') != 'trace-loop-policy' or packet.get('policy_version') != GATE_POLICY):
        raise ValueError('Unexpected signed gate contract or policy scope')
    required_hashes = {'suite_sha256', 'workflow_sha256', 'report_sha256', 'evidence_sha256'}
    if (not isinstance(expected_artifact_binding, dict) or set(expected_artifact_binding) != required_hashes
            or any(not hex_digest(value) for value in expected_artifact_binding.values())
            or packet.get('artifact_binding') != expected_artifact_binding):
        raise ValueError('Signed artifact binding mismatch')
    for key in ('repo', 'head_sha', 'base_sha'):
        if packet.get(key) != evidence.get(key):
            raise ValueError('Gate decision commit binding mismatch')
    if packet.get('trace_sha256') != evidence.get('gate_trace_sha256'):
        raise ValueError('Gate decision trace digest mismatch')
    issued_at = parse_timestamp(packet.get('issued_at'))
    current = time.time() if now is None else now
    if not isinstance(current, (int, float)) or issued_at > current + 60 or current - issued_at > max_age_seconds:
        raise ValueError('Gate decision is stale')
    return packet


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
        'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json', 'User-Agent': 'RunLog-ShipCheck/' + VERSION})
    with urllib.request.build_opener(NoRedirect).open(req, timeout=30) as response:
        raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError('Oversized gate response')
        return json.loads(raw)


def canonical_json(value):
    """runlog.canonical-json.v1: tagged JSON with binary64 numbers and UTF-8 hex.

    This deliberately is not JSON.stringify/JCS. Tagged values prevent collisions,
    and the output alphabet avoids runtime differences in escaping or key order.
    """
    def text_hex(text):
        try:
            return text.encode('utf-8').hex()
        except UnicodeError as error:
            raise ValueError('Unpaired Unicode surrogate') from error

    def encode(item, depth=0):
        if depth > 64:
            raise ValueError('Canonical JSON exceeds 64 nesting levels')
        if item is None:
            return ['null']
        if type(item) is bool:
            return ['boolean', item]
        if type(item) in (int, float):
            if not math.isfinite(item) or (item == math.trunc(item) and abs(item) > 2**53 - 1):
                raise ValueError('Nonportable JSON number')
            return ['number', struct.pack('>d', 0.0 if item == 0 else item).hex()]
        if isinstance(item, str):
            return ['string', text_hex(item)]
        if isinstance(item, list):
            return ['array', [encode(child, depth + 1) for child in item]]
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ValueError('JSON keys must be strings')
            pairs = sorted((text_hex(key), child) for key, child in item.items())
            return ['object', [[key, encode(child, depth + 1)] for key, child in pairs]]
        raise ValueError('Unsupported JSON value')

    return json.dumps(encode(value), separators=(',', ':'), allow_nan=False).encode('ascii')


def gate_trace_digest(trace):
    return hashlib.sha256(canonical_json(trace)).hexdigest()


def verify_expected_context(evidence, repo, head_sha, base_sha):
    if not isinstance(repo, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise ValueError('Expected repository is required from trusted CI context')
    for sha in (head_sha, base_sha):
        if not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{40}', sha):
            raise ValueError('Expected full commit SHAs are required from trusted CI context')
    if any(evidence.get(key) != value for key, value in (('repo', repo), ('head_sha', head_sha), ('base_sha', base_sha))):
        raise ValueError('Evidence does not match trusted CI context')


def artifact_binding(evidence, evidence_path):
    return {**{key: evidence[key] for key in ('suite_sha256', 'workflow_sha256', 'report_sha256')},
            'evidence_sha256': sha256_file(evidence_path)}


def main(argv=None):
    args = parse_args(argv)
    try:
        token = os.getenv('RUNLOG_TOKEN') or os.getenv('RUNLOG_API_KEY')
        if not token and not args.offline:
            raise ValueError('RUNLOG_TOKEN or RUNLOG_API_KEY is required')
        evidence_mode = any((args.evaluation_evidence, args.evaluation_suite, args.evaluation_workflow,
                             args.evaluation_report, args.issuer_public_key, args.expected_issuer))
        if evidence_mode:
            if args.offline:
                raise ValueError('Offline mode supports record-only assertions')
            if not all((args.evaluation_evidence, args.evaluation_suite, args.evaluation_workflow,
                        args.evaluation_report, args.issuer_public_key, args.expected_issuer)):
                raise ValueError('Complete evaluation evidence bridge inputs are required')
            expected = urllib.parse.urlsplit(args.expected_issuer)
            if expected.scheme != 'https' or not expected.hostname or expected.path or expected.query or expected.fragment or expected.username or expected.password:
                raise ValueError('Expected issuer must be an HTTPS origin')
            evidence = verify_evaluation_evidence(args.evaluation_evidence, args.evaluation_suite,
                                                   args.evaluation_workflow, args.evaluation_report)
            verify_expected_context(evidence, args.expected_repo, args.expected_head_sha, args.expected_base_sha)
            binding = artifact_binding(evidence, args.evaluation_evidence)
            report = load_json(args.evaluation_report)
            trace = report['trace']
            payload = {'trace': trace, 'repo': evidence['repo'], 'head_sha': evidence['head_sha'],
                       'base_sha': evidence['base_sha'], 'gate_contract': GATE_CONTRACT,
                       'trace_encoding': TRACE_ENCODING, 'artifact_binding': binding}
            result = submit_to_gate(args.gate_url, token, payload)
            verify_gate_decision(result, load_json(args.issuer_public_key), args.expected_issuer, evidence,
                                 max_age_seconds=args.decision_max_age_seconds, expected_artifact_binding=binding)
            print('APPROVED: signed trace-loop policy decision and supplied artifact hashes verified; evaluator provenance is not attested.')
            print(json.dumps(evidence['evaluated_result'], allow_nan=False))
        else:
            if not args.scenarios or not args.trace:
                raise ValueError('Record-only mode requires scenarios and trace')
            suite, trace = load_json(args.scenarios), load_json(args.trace)
            measurements = {'candidate': evaluate(suite, trace)}
            if args.baseline_trace:
                measurements['baseline'] = evaluate(suite, load_json(args.baseline_trace))
            if args.offline:
                print('LOCAL PASS: recorded assertions passed; no gate request or release approval.')
                print(json.dumps(measurements, allow_nan=False))
                return 0
            payload = {'trace': trace, 'scenarios': suite, 'measurements': measurements,
                       'repo': os.getenv('GITHUB_REPOSITORY', ''), 'head_sha': os.getenv('HEAD_SHA', ''),
                       'base_sha': os.getenv('BASE_SHA', '')}
            result = submit_to_gate(args.gate_url, token, payload)
            require_approval(result)
            print('APPROVED: record-only caller trace passed local assertions and remote gate.')
            print(json.dumps(measurements, allow_nan=False))
        return 0
    except Exception as error:
        # Do not echo trace contents, API bodies, URLs, or credentials into CI logs.
        print('Ship-Check failed closed (' + type(error).__name__ + '). Check evidence, configuration, and gate availability.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())

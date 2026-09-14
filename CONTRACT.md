# Ship-Check 0.2 contract

## Canonical JSON: `runlog.canonical-json.v1`

This is a tagged JSON encoding, not RFC 8785 or JSON.stringify. Encode values as
below, then serialize the encoded tree as compact UTF-8 JSON (no whitespace).
Its strings contain only ASCII type labels and lowercase hex, avoiding runtime
escaping and number-format differences. The digest is SHA-256 of these bytes.

| Original value | Encoded JSON |
| --- | --- |
| null | `["null"]` |
| boolean | `["boolean",true]` or `["boolean",false]` |
| number | `["number","<16 lowercase hex digits>"]` |
| string | `["string","<UTF-8 bytes in lowercase hex>"]` |
| array | `["array",[<encoded values in original order>]]` |
| object | `["object",[["<key UTF-8 hex>",<encoded value>],...]]` |

Numbers use IEEE754 binary64 big-endian bytes. Normalize negative zero to positive
zero. Reject nonfinite numbers and integral numbers outside ±(2^53−1), even if
representable in binary64. Reject unpaired Unicode surrogates, nonstring object
keys and nesting deeper than 64 edges (root depth zero). Sort object pairs by
lexicographic key hex. Do not Unicode-normalize strings. Input JSON must not have
duplicate object keys. Vectors are in `tests/fixtures/canonical-json-v1.json`.

## Request and response

The existing legacy gate request remains unchanged. A new signed-mode request
adds:

```json
{
  "gate_contract": "runlog.trace-gate.v2",
  "trace_encoding": "runlog.canonical-json.v1",
  "trace": [{"tool":"read","args":{},"cost_usd":0.002}],
  "repo": "owner/repo",
  "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "base_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "artifact_binding": {
    "suite_sha256": "64 lowercase hex characters",
    "workflow_sha256": "64 lowercase hex characters",
    "report_sha256": "64 lowercase hex characters",
    "evidence_sha256": "64 lowercase hex characters"
  }
}
```

For v2 the Worker must validate the encoding, repository, full lowercase 40-hex
commits and exactly these four digest keys, before quota or audit mutation.
Unknown gate contracts must fail closed. SHA-256 trace digest uses the encoding
above. Legacy requests without gate_contract retain their existing JSON.stringify
trace digest and response semantics.

In addition to issuer, repo, head_sha, base_sha, issued_at, trace_sha256 and verdict,
the **signed payload** includes these exact values:

```json
{
  "gate_contract":"runlog.trace-gate.v2",
  "trace_encoding":"runlog.canonical-json.v1",
  "decision_scope":"trace-loop-policy",
  "policy_version":"runlog.repeated-tool-cycle.v1",
  "artifact_binding":{"suite_sha256":"...","workflow_sha256":"...","report_sha256":"...","evidence_sha256":"..."}
}
```

The response envelope retains signed_payload (exact UTF-8 string), payload_sha256,
standard-base64 signature and signature_algorithm `Ed25519`. The client uses an
independently provisioned public JWK, verifies the signature, compares all bindings,
and requires a fresh signed APPROVED decision. It does not trust response public_key.

The gate attests **only** its trace-loop policy result and the hashes supplied by
the caller. It has not read those files, run their harness, checked their evaluation
summary, or established provenance. A valid signature does not upgrade a fabricated
trace into trustworthy evidence. The client checks file hashes locally and requires
repo/head/base to match independent trusted CI inputs; callers must protect those
inputs, the evaluator, suite, policy, artifact handoff and public key.

`runlog.repeated-tool-cycle.v1` detects two consecutive equal tool+args cycles,
lengths 1–10, across the submitted flat 1–1000-step trace. It is scenario-agnostic;
legitimate repeated calls can be held. It makes no semantic-quality or cost claim.

## Evidence file

Use schema `runlog.evaluation-evidence.v2` and trace_encoding above; retain repo,
head_sha, base_sha, suite_sha256, workflow_sha256, report_sha256,
gate_trace_sha256, evaluated_result, policy_version and timestamp. The first three
hashes are exact file-byte hashes; gate_trace_sha256 is canonical encoded trace
hash. Evidence summary is a caller declaration checked for balanced, nonempty
PASSED counts, not an authenticated evaluation result. evidence_sha256 is calculated
over the complete evidence file by the client (never placed recursively inside it).

The unreleased v1 evidence bridge is intentionally rejected: it lacked a portable
hash and policy/artifact binding. Released v0.1 record-only users remain compatible.

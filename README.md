# RunLog Ship-Check 0.3.0

Ship-Check checks recorded tool assertions and can verify a signed **trace-loop
policy** decision bound to your repository, commits and supplied artifact hashes.
It does not execute your agent, prove evaluator provenance, score answer quality,
create PR comments, or compare baseline cost drift. The gate signs hashes supplied
by you; it has not independently inspected the corresponding files or evaluator.

Python 3.12, no runtime dependencies. The GitHub composite action supports the
same modes. Version 0.3.0 adds native evidence reconciliation, evaluation freshness,
request preflight bounds, a pinned evaluator import and scenario-aware cycle checks. Released 0.1.0 record-only requests remain compatible with the
gate; the unreleased v1 evidence bridge is rejected. Signed mode needs a gate that
supports `runlog.trace-gate.v3`; older gates fail closed. See [CONTRACT.md](CONTRACT.md).

## Install and run offline

Download the release archive and checksum file from the
[v0.3.0 release](https://github.com/runlog-cc/ship-check/releases/tag/v0.3.0), then:

```sh
curl -fLO https://github.com/runlog-cc/ship-check/releases/download/v0.3.0/ship-check-0.3.0-source.tar.gz
curl -fLO https://github.com/runlog-cc/ship-check/releases/download/v0.3.0/SHA256SUMS
awk '$2 == "ship-check-0.3.0-source.tar.gz" {print; found=1} END {if (!found) exit 1}' SHA256SUMS > ship-check.SHA256SUMS
sha256sum --check ship-check.SHA256SUMS
tar -xzf ship-check-0.3.0-source.tar.gz
cd ship-check-0.3.0
python3 runner.py --version
python3 examples/harness.py --output /tmp/ship-check-demo
python3 runner.py --offline --scenarios /tmp/ship-check-demo/suite.json --trace /tmp/ship-check-demo/trace.json
```

Expected: `0.3.0`, then `LOCAL PASS` and a measured summary, exit 0. No account,
key, network, or model call is needed after download. The example executes a real
in-memory lookup tool, records its calls and legitimately records zero provider
cost. It demonstrates wiring, not your agent's evaluation quality.

A deliberately failing example executes one extra call:

```sh
python3 examples/harness.py --output /tmp/ship-check-demo --fail
python3 runner.py --offline --scenarios /tmp/ship-check-demo/suite.json --trace /tmp/ship-check-demo/trace.json
```

Expected: exit 1 and a fail-closed message because the recorded step count exceeds
the suite ceiling. Offline success is never remote gate or release approval.

## Supply your recorded assertions

Run your real harness first. A JSON suite and candidate trace look like:

```json
{"version":1,"scenarios":[{"id":"lookup","assertions":{"tool_called":"lookup_order","max_steps":4,"max_cost_usd":0.05}}]}
```

```json
[{"scenario_id":"lookup","tool":"lookup_order","args":{"order_id":104},"cost_usd":0.002}]
```

These snippets illustrate the schema. Do not use fabricated traces for release
decisions. Each scenario requires at least one supported assertion and one step.
Unknown assertions, missing scenarios, nonfinite/negative costs and malformed
records fail. Capture actual USD cost; unknown cost must fail the harness rather
than becoming zero. `--baseline-trace` checks the same assertions and displays a
separate summary; it has no automatic regression/delta threshold.

Omit `--offline` and supply `RUNLOG_TOKEN` or `RUNLOG_API_KEY` securely to send the
legacy recorded trace to `https://runlog.cc/api/v1/gate`. Legacy mode requires an
APPROVED response over HTTPS but does not verify a signature. Redact credentials
and customer data from tool arguments before submission.

## Signed trace policy with artifact and commit binding

Create a report with a nonempty `trace` array and an evidence file following
[CONTRACT.md](CONTRACT.md). `examples/harness.py` produces all five example files
and shows the exact file-byte and canonical trace digest calculations. Its summary
is explicitly a caller declaration. The signed decision establishes only the
trace-loop policy verdict and supplied hash bindings, not that declaration's truth.

Provision the issuer's public Ed25519 JWK through a trusted administrative channel.
Store only `kty`, `crv`, `x` and optional `alg`/`kid` in the JSON file. Never adopt a
public key from a gate response or untrusted PR. For rotation, review and update
the configured public key independently; an unexpected signer fails closed.

Supply expected repository/head/base from trusted CI metadata or your own trusted
checkout; never derive these expectations from the evidence file:

```sh
# RUNLOG_TOKEN comes from your secret environment, not a command-line argument.
export RUNLOG_EXPECTED_REPO=owner/repository
export RUNLOG_EXPECTED_HEAD_SHA="$(git rev-parse HEAD)"
export RUNLOG_EXPECTED_BASE_SHA="$(git rev-parse HEAD^)"
python3 runner.py --evaluation-evidence evidence.json --evaluation-suite suite.json \
  --evaluation-workflow workflow.yml --evaluation-report report.json \
  --issuer-public-key trusted-gate-public.jwk --expected-issuer https://runlog.cc
```

The equivalent flags are `--expected-repo`, `--expected-head-sha` and
`--expected-base-sha`. All three are mandatory in signed mode. Successful output
explicitly says `signed trace-loop policy` and that evaluator provenance is not
attested. The signature binds canonical trace digest and exact suite, workflow,
report and evidence file hashes; commit mismatch blocks before any network call.
Decisions must be APPROVED, no older than 900 seconds by default, and no more than
60 seconds in the future. Files/responses are capped at 5 MiB; the gate accepts
1–1000 steps. HTTPS, no redirects, 30-second timeout; errors omit sensitive content.

The loop policy detects two consecutive repeated tool+args cycles of length 1–10
across the flat trace. Legitimate repeats and separate scenarios can cause HOLD.
It does not enforce cost or semantic quality; your trusted evaluator must do that.

## Complete CI starter

Copy [examples/ship-check.yml](examples/ship-check.yml) to
`.github/workflows/ship-check.yml` in a repository with a `main` branch and at
least two commits. It runs the local demo without secrets on pull requests, then
runs signed trace verification only on pushes to protected `main`.

1. Protect `main` and review changes to the harness, suite and workflow. Replace
   the demo harness with your real evaluator before treating this as release gating.
2. Create environment `runlog-ci`, restrict it to protected `main`, and require
   approval if that is your release policy. Add `RUNLOG_API_KEY` as an environment
   secret and the independently verified public JWK as environment variable
   `RUNLOG_GATE_PUBLIC_JWK`.
3. Set repository variable `RUNLOG_SHIP_CHECK_COMMIT` to the reviewed full release
   commit SHA. The starter rejects non-SHA values and checks out that exact source.
   Other actions already use reviewed commit SHAs.
4. Require `recorded-assertions` on PRs once it tests your real application. Make
   release/deploy jobs depend on `signed-trace-policy`; a post-merge push job does
   not itself prevent merging a PR.

No PR job gets gate credentials. Do not execute untrusted PR code with secrets or
use `pull_request_target` for a PR checkout. This starter intentionally does not
consume fork-produced artifacts in a privileged job. If you build a `workflow_run`
handoff, independently validate the originating run/repository/head/base and
immutable artifact identity; do not use the verifier workflow's `github.sha` as
the candidate head. Pass all three `expected-*` action inputs explicitly there.
A signed hash is not a replacement for that trusted artifact/evaluator handoff.

## Verification

```sh
python3 -m unittest discover -s tests
```

For maintainers, the full actual-CLI/HTTPS/Worker/Ed25519 integration runs against
an in-memory Worker database and fake auth, with no external provider calls. It
requires Node with `node:sqlite`, OpenSSL and the companion site's source:

```sh
RUNLOG_SITE_ROOT=/path/to/projects/runlog-site node tests/worker_integration.mjs "$PWD"
```

## 0.3.0 validation boundaries

Signed mode re-runs the native suite against the report trace and requires identical
`evaluated_result` status/summary in report and evidence. Missing or contradictory
reports fail. This consistency check does not establish evaluator provenance.
Evaluation timestamps and signed decisions default to a 900-second maximum age;
`--evaluation-max-age-seconds` and `--decision-max-age-seconds` accept 1–86400.
Future timestamps fail, with no clock-skew allowance. Keep CI clocks synchronized.
The exact compact serialized gate request must fit 128 KiB before any HTTP call.

V3 scans every adjacent repeated tool+args cycle within each exact scenario ID,
including periods longer than ten and cycles followed by unrelated steps. At most
1000 total steps are accepted. Independent scenarios cannot form a cycle together.
It remains a pattern policy, not a cost or answer-quality gate. Explicit v2 packet
verification is available through `verify_gate_decision(..., expected_contract=
'runlog.trace-gate.v2')`; normal v3 CLI requests reject downgraded v2 decisions.

## Pinned evaluator import

See [PROMPTFOO.md](PROMPTFOO.md) for the explicitly supported Promptfoo 0.117.0
JSON export subset and the offline import command. The importer validates actual
text equality assertions as well as success/grading/count declarations and native
trace assertions. It requires caller-recorded trace evidence and does not invent
trace steps or costs from model text.

## Immutable CI installation

Set the protected repository variable `RUNLOG_SHIP_CHECK_COMMIT` to the reviewed
full 40-character source commit for the 0.3.0 release. Obtain that commit from the
release provenance, review it, and freeze it in your CI configuration. Do not use
`main`, a tag, or an untrusted PR-supplied value. The complete workflow in
`examples/ship-check.yml` validates the pin, checks out that exact commit and uses
its local composite action. This avoids inventing a release commit before publish.
Protect the workflow, variable, suite, evaluator and issuer key. A deployment job
must explicitly `needs: signed-trace-policy` to depend on this check; this example
does not deploy anything or grant release assurance by itself.

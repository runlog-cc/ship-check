# RunLog Ship-Check 0.1.0

A fail-closed GitHub composite action for **caller-recorded** JSON traces. It does
not execute your agent, authenticate trace provenance, sign verdicts, or post PR
comments. The job succeeds only when every local assertion passes and the HTTPS
gate returns exactly `{"verdict":"APPROVED"}`. Errors and other verdicts block it.

Run your real scenario harness before this action; never use demonstration traces
for a release decision. Protect the harness, assertion suite, workflow and baseline
against unreviewed PR changes. Do not run untrusted PR code with production secrets
or use `pull_request_target` to execute a PR checkout. Trace args are sent to the
configured endpoint: redact credentials/customer data first.

## Workflow after the standalone repository is published

The distribution repository is `runlog-cc/ship-check`; the release tag is `v0.1.0`.
Use the reviewed release commit SHA in production (replace `RELEASE_COMMIT_SHA`).
This documentation does not imply that the repository/tag is already public.

```yaml
permissions:
  contents: read
steps:
  - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2
  # Run your project's real agent test harness here to write candidate.json.
  - uses: runlog-cc/ship-check@RELEASE_COMMIT_SHA
    with:
      token: ${{ secrets.RUNLOG_API_KEY }}
      scenarios: .runlog/scenarios.json
      trace: .runlog/candidate.json
      # baseline-trace: .runlog/baseline.json
```

Required suite shape (JSON only):

```json
{"version":1,"scenarios":[{"id":"lookup","assertions":{"tool_called":"lookup_order","max_steps":4,"max_cost_usd":0.05}}]}
```

Trace shape, illustrated with **synthetic schema data only**:

```json
[{"scenario_id":"lookup","tool":"lookup_order","args":{"order_id":104},"cost_usd":0.002}]
```

Each scenario needs at least one supported assertion and at least one recorded
step. Unknown assertions, duplicate IDs, unknown scenarios, malformed evidence,
negative/nonfinite cost, empty suites/traces and missing files fail. Record actual
step costs in USD; if unavailable, fail the harness instead of substituting zero.
An optional baseline is evaluated against the same assertions and its measured
summary is displayed separately; no automatic cost-drift threshold is claimed.
Recursion policy belongs to the remote gate; local supported assertions are only
`tool_called`, `max_steps`, and `max_cost_usd`.

Local usage (Python 3.12; no dependencies):

```sh
export RUNLOG_TOKEN # supplied securely by your environment
python runner.py --scenarios scenarios.json --trace candidate.json
python -m unittest discover -s tests
```

The token is read only from `RUNLOG_TOKEN` or `RUNLOG_API_KEY`, never a CLI flag.
Requests require HTTPS, reject redirects, time out after 30 seconds and limit
files/responses to 5 MiB. No API error bodies or evidence are echoed to CI logs.
Use a required status check in repository rules to enforce the job's exit status.

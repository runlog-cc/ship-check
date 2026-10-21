# Promptfoo import profile

Ship-Check 0.3.1 supports the explicit profile
`promptfoo-0.117.0-summary-v3-equals`. The contract is based on the pinned
`promptfoo@0.117.0` package's JSON `writeOutput` exporter and
`EvaluateSummaryV3` / `EvaluateResult` types. It does not assume every Promptfoo
export is compatible. Other versions, table/v2 exports, multi-provider runs,
multiple prompts, external test files, custom graders, transforms, red-team runs,
and assertion types other than literal text `equals` are unsupported and fail.

Generate JSON with the pinned evaluator (`promptfoo eval -o export.json`) in your
own evaluation job. Install exactly `promptfoo@0.117.0` with your package lockfile;
do not use an unpinned `npx promptfoo`. Protect the evaluator configuration and
trace capture. The importer itself makes no network requests and runs no models.

The accepted root keys are `evalId`, `results`, `config`, optional `shareableUrl`.
`results` must contain version 3, a fresh UTC timestamp, `prompts`, `results` rows
and `stats`. Config supports `prompts`, `providers`, `tests`, optional description,
with exactly one prompt and provider. Provider configuration is a nonblank string ID; prompt configuration is a nonblank string or an exact `{raw,label}` string object. Provider objects and transform configuration are rejected. Successful row, grading and component scores must equal numeric `1`. Each test has explicit equals assertions and
metadata `runlog_scenario_id` naming exactly one scenario in the native suite:

```json
{
  "prompts": ["Return the order status"],
  "providers": ["your-local-provider"],
  "tests": [{
    "assert": [{"type":"equals","value":"ready"}],
    "metadata": {"runlog_scenario_id":"lookup"}
  }]
}
```

There must be one row per declared test and native scenario, no duplicates or
missing rows. All row success flags, failure reasons, grading results/components,
aggregate successes/failures/errors and actual text equality must agree. Native
suite assertions are also executed against your separately supplied recorded
trace. Tool steps and costs cannot be inferred from Promptfoo response text.

```sh
python3 promptfoo_import.py \
  --format promptfoo-0.117.0-summary-v3-equals --export export.json \
  --suite suite.json --trace trace.json --workflow .github/workflows/evaluate.yml \
  --repo owner/repository --head-sha "$TRUSTED_HEAD_SHA" --base-sha "$TRUSTED_BASE_SHA" \
  --output .runlog/imported
python3 runner.py --offline \
  --scenarios .runlog/imported/suite.json --trace .runlog/imported/trace.json
```

Use the generated evidence/suite/workflow/report files with the signed-mode CLI
shown in README. The report embeds the original export and profile; signed-mode
local verification rechecks it and preserves its original timestamp. The remote
gate receives only trace/context/hashes, not model output or grading text. The
report is still sensitive local evidence: manage its artifact access accordingly.
The signature binds supplied hashes; it does not attest Promptfoo execution or
prove the evaluator was trustworthy. Offline `LOCAL PASS` is not release approval.

## Reproduce the real local echo export

`tests/fixtures/promptfoo-0.117.0-echo.json` was captured from the real pinned
Promptfoo evaluator and JSON exporter, using its local `echo` provider. It is a
compatibility fixture, not a customer evaluation. The test checks its original
capture time explicitly; the importer does not waive freshness in normal use.
The package needed explicit installation of undeclared interactive dependencies
in this Node 24.15.0 environment. This command reproduces the tested setup without
provider keys (network is needed only for npm installation):

```sh
npm install --prefix /tmp/ship-promptfoo --ignore-scripts --save-exact \
  promptfoo@0.117.0 @inquirer/input@4.2.0 @inquirer/editor@4.2.15 \
  @inquirer/confirm@5.1.14 @inquirer/select@4.3.4
NODE_PATH=/tmp/ship-promptfoo/node_modules \
  node examples/promptfoo_echo.cjs /tmp/promptfoo-echo.json
```

The real export contains normalized empty `vars` and `options` on test rows;
these are permitted. Nonempty options are rejected. Config may include exporter
`outputPath` and `writeLatestResults` fields; neither grants evaluation approval.
Use scenario `case1` in your accompanying native suite and trace for this example.

The script records its actual free local evaluator invocation in
`/tmp/echo-trace.json` and writes `/tmp/echo-suite.json`. Complete the import with:

```sh
python3 promptfoo_import.py --format promptfoo-0.117.0-summary-v3-equals \
  --export /tmp/promptfoo-echo.json --suite /tmp/echo-suite.json \
  --trace /tmp/echo-trace.json --workflow examples/promptfoo_echo.cjs \
  --repo example/demo --head-sha aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  --base-sha bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb --output /tmp/echo-import
python3 runner.py --offline --scenarios /tmp/echo-import/suite.json \
  --trace /tmp/echo-import/trace.json
```

Those demo commit IDs are explicitly synthetic. Use independent actual CI commit
IDs for signed real-workload evaluation; never promote the demo to a quality gate.

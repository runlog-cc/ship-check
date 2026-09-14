#!/usr/bin/env python3
"""Import the explicit Promptfoo 0.117.0 summary-v3 equals-only profile offline.

Source contract: promptfoo@0.117.0 dist/src/util/index.js writeOutput and
 dist/src/types/index.d.ts EvaluateSummaryV3/EvaluateResult. No provider execution.
"""
import argparse
import json
from pathlib import Path
import sys
import runner

PROFILE = 'promptfoo-0.117.0-summary-v3-equals'


def object_fields(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or not set(required) <= set(value):
        raise ValueError('Unsupported Promptfoo export fields')


def validate_export(export, suite, now=None, max_age_seconds=900):
    object_fields(export, ('evalId','results','config','shareableUrl'), ('evalId','results','config'))
    if not isinstance(export['evalId'],str) or not export['evalId']:
        raise ValueError('Missing Promptfoo evaluation ID')
    summary=export['results']
    object_fields(summary, ('version','timestamp','results','prompts','stats'), ('version','timestamp','results','prompts','stats'))
    if type(summary['version']) is not int or summary['version'] != 3:
        raise ValueError('Only Promptfoo summary v3 is supported')
    runner.verify_fresh_timestamp(summary['timestamp'],now,max_age_seconds)
    config=export['config']
    object_fields(config, ('prompts','providers','tests','description','outputPath','writeLatestResults'), ('prompts','providers','tests'))
    if not isinstance(config['prompts'],list) or len(config['prompts']) != 1 or not isinstance(config['providers'],list) or len(config['providers']) != 1:
        raise ValueError('Profile requires one prompt and one provider')
    provider = config['providers'][0]
    if not isinstance(provider, str) or not provider.strip():
        raise ValueError('Profile requires a plain provider identifier, not provider configuration')
    prompt = config['prompts'][0]
    if isinstance(prompt, str):
        if not prompt.strip():
            raise ValueError('Empty prompt is unsupported')
    else:
        object_fields(prompt, ('raw', 'label'), ('raw', 'label'))
        if any(not isinstance(prompt[key], str) or not prompt[key].strip() for key in ('raw', 'label')):
            raise ValueError('Profile requires plain prompt text and label')
    if not isinstance(suite,dict) or suite.get('version') != 1 or not isinstance(suite.get('scenarios'),list):
        raise ValueError('Native version 1 suite required')
    scenario_ids=[scenario.get('id') for scenario in suite['scenarios'] if isinstance(scenario,dict)]
    if len(scenario_ids)!=len(suite['scenarios']) or any(not isinstance(sid,str) or not sid.strip() for sid in scenario_ids) or len(set(scenario_ids))!=len(scenario_ids):
        raise ValueError('Invalid native scenario IDs')
    if not isinstance(summary['prompts'],list) or len(summary['prompts']) != 1 or not isinstance(summary['prompts'][0],dict):
        raise ValueError('One summary prompt is required')
    rows=summary['results']; tests=config['tests']
    if not isinstance(rows,list) or not 1 <= len(rows) <= 1000 or not isinstance(tests,list) or len(rows)!=len(scenario_ids) or len(tests)!=len(rows):
        raise ValueError('Require exactly one evaluator row per native scenario')
    stats=summary['stats']
    object_fields(stats, ('successes','failures','errors','tokenUsage'), ('successes','failures','errors'))
    if any(type(stats[k]) is not int for k in ('successes','failures','errors')) or (stats['successes'],stats['failures'],stats['errors']) != (len(rows),0,0):
        raise ValueError('Promptfoo statistics did not pass')
    seen=set(); indices=set()
    allowed_row=('id','description','promptIdx','testIdx','testCase','promptId','provider','prompt','vars','response','error','failureReason','success','score','latencyMs','gradingResult','namedScores','cost','metadata','tokenUsage')
    for row in rows:
        object_fields(row,allowed_row,('promptIdx','testIdx','testCase','response','failureReason','success','score','gradingResult'))
        if type(row['promptIdx']) is not int or row['promptIdx']!=0 or type(row['testIdx']) is not int or not 0<=row['testIdx']<len(tests) or row['testIdx'] in indices:
            raise ValueError('Unsupported or duplicate evaluator row index')
        indices.add(row['testIdx'])
        if row['success'] is not True or type(row['failureReason']) is not int or row['failureReason']!=0 or row.get('error') not in (None,'') or (not runner.number(row['score']) or row['score'] != 1):
            raise ValueError('Evaluator row did not pass')
        case=row['testCase']
        object_fields(case,('assert','metadata','vars','description','options'),('assert','metadata'))
        declared=tests[row['testIdx']]
        object_fields(declared,('assert','metadata','vars','description','options'),('assert','metadata'))
        if case.get('options',{}) != {} or declared.get('options',{}) != {}:
            raise ValueError('Evaluator transforms and options are unsupported')
        normalized=lambda test: {**test,'vars':test.get('vars',{}),'options':{}}
        if normalized(case)!=normalized(declared):
            raise ValueError('Evaluator row differs from declared test')
        object_fields(case['metadata'],('runlog_scenario_id',),('runlog_scenario_id',))
        sid=case['metadata']['runlog_scenario_id']
        if not isinstance(sid,str) or sid not in scenario_ids or sid in seen:
            raise ValueError('Evaluator scenario mapping mismatch')
        seen.add(sid)
        assertions=case['assert']; response=row['response']
        if not isinstance(assertions,list) or not assertions or not isinstance(response,dict) or not isinstance(response.get('output'),str) or response.get('error'):
            raise ValueError('Explicit text equality assertions and output required')
        for assertion in assertions:
            object_fields(assertion,('type','value'),('type','value'))
            if assertion['type']!='equals' or not isinstance(assertion['value'],str) or response['output']!=assertion['value']:
                raise ValueError('Unsupported or failed equals assertion')
        grading=row['gradingResult']
        object_fields(grading,('pass','score','reason','namedScores','tokensUsed','componentResults','assertion'),('pass','score','reason'))
        if grading['pass'] is not True or (not runner.number(grading['score']) or grading['score'] != 1) or not isinstance(grading['reason'],str):
            raise ValueError('Contradictory grading result')
        components=grading.get('componentResults',[])
        if not isinstance(components,list) or any(not isinstance(c,dict) or c.get('pass') is not True or not runner.number(c.get('score')) or c['score'] != 1 for c in components):
            raise ValueError('Contradictory grading components')
    return len(rows)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--format',choices=[PROFILE],required=True)
    parser.add_argument('--export',required=True)
    parser.add_argument('--suite',required=True)
    parser.add_argument('--trace',required=True)
    parser.add_argument('--workflow',required=True)
    parser.add_argument('--repo',required=True)
    parser.add_argument('--head-sha',required=True)
    parser.add_argument('--base-sha',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--evaluation-max-age-seconds',type=int,default=900)
    args=parser.parse_args(argv)
    try:
        export,suite,trace=runner.load_json(args.export),runner.load_json(args.suite),runner.load_json(args.trace)
        count=validate_export(export,suite,max_age_seconds=args.evaluation_max_age_seconds)
        runner.evaluate(suite,trace)
        runner.find_scenario_cycle(trace)  # Validate v3 structure; gate decides cycles separately.
        context={'repo':args.repo,'head_sha':args.head_sha,'base_sha':args.base_sha}
        runner.verify_expected_context(context,args.repo,args.head_sha,args.base_sha)
        result={'status':'PASSED','summary':{'scenarios':count,'passed':count,'failed':0}}
        report={'trace':trace,'evaluated_result':result,'promptfoo_profile':PROFILE,'promptfoo_export':export}
        root=Path(args.output);root.mkdir(parents=True,exist_ok=True)
        (root/'suite.json').write_bytes(Path(args.suite).read_bytes())
        (root/'workflow.yml').write_bytes(Path(args.workflow).read_bytes())
        (root/'report.json').write_text(json.dumps(report,allow_nan=False,indent=2)+'\n')
        (root/'trace.json').write_text(json.dumps(trace,allow_nan=False,indent=2)+'\n')
        evidence={**context,'schema':runner.EVIDENCE_SCHEMA,'trace_encoding':runner.TRACE_ENCODING,
                  'suite_sha256':runner.sha256_file(root/'suite.json'),'workflow_sha256':runner.sha256_file(root/'workflow.yml'),
                  'report_sha256':runner.sha256_file(root/'report.json'),'gate_trace_sha256':runner.gate_trace_digest(trace),
                  'evaluated_result':result,'policy_version':PROFILE,'timestamp':export['results']['timestamp']}
        (root/'evidence.json').write_text(json.dumps(evidence,allow_nan=False,indent=2)+'\n')
        print('Imported local evaluator records; provenance and release approval are not attested.')
        return 0
    except Exception as error:
        print('Promptfoo import failed closed ('+type(error).__name__+').',file=sys.stderr)
        return 1

if __name__=='__main__':
    sys.exit(main())

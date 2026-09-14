import copy
import unittest
import runner

class PromptfooTests(unittest.TestCase):
    def fixture(self):
        suite={'version':1,'scenarios':[{'id':'case1','assertions':{'tool_called':'lookup'}}]}
        case={'assert':[{'type':'equals','value':'ready'}],'metadata':{'runlog_scenario_id':'case1'}}
        export={'evalId':'eval-local','config':{'prompts':['status'],'providers':['echo'],'tests':[case]},'results':{'version':3,'timestamp':'2026-09-14T00:00:00Z','prompts':[{}],'stats':{'successes':1,'failures':0,'errors':0,'tokenUsage':{}},'results':[{'promptIdx':0,'testIdx':0,'testCase':case,'success':True,'failureReason':0,'score':1,'gradingResult':{'pass':True,'score':1,'reason':'equal'},'response':{'output':'ready'}}]}}
        return export,suite

    def test_supported_profile_and_contradictions(self):
        from promptfoo_import import validate_export
        export,suite=self.fixture()
        now=runner.parse_timestamp(export['results']['timestamp'])
        self.assertEqual(validate_export(export,suite,now=now),1)
        for change in ['failed','stats','output','unknown_version','no_assertions','scenario','duplicate']:
            bad=copy.deepcopy(export);row=bad['results']['results'][0]
            if change=='failed':row['gradingResult']['pass']=False
            elif change=='stats':bad['results']['stats']['failures']=1
            elif change=='output':row['response']['output']='not ready'
            elif change=='unknown_version':bad['results']['version']=4
            elif change=='no_assertions':row['testCase']['assert']=[]
            elif change=='scenario':row['testCase']['metadata']['runlog_scenario_id']='other'
            else:bad['results']['results'].append(row)
            with self.subTest(change=change),self.assertRaises(ValueError):validate_export(bad,suite,now=now)

    def test_import_cli_and_native_bridge(self):
        import datetime
        import json
        import tempfile
        from pathlib import Path
        from promptfoo_import import main, PROFILE
        export,suite=self.fixture();export['results']['timestamp']=datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00','Z')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name,value in [('export.json',export),('suite.json',suite),('trace.json',[{'scenario_id':'case1','tool':'lookup','args':{},'cost_usd':0}])]:
                (root/name).write_text(json.dumps(value))
            (root/'workflow.yml').write_text('name: controlled evaluator\n')
            args=['--format',PROFILE,'--export',str(root/'export.json'),'--suite',str(root/'suite.json'),'--trace',str(root/'trace.json'),'--workflow',str(root/'workflow.yml'),'--repo','acme/test','--head-sha','a'*40,'--base-sha','b'*40,'--output',str(root/'out')]
            self.assertEqual(main(args),0)
            paths=[root/'out'/name for name in ('evidence.json','suite.json','workflow.yml','report.json')]
            self.assertEqual(runner.verify_evaluation_evidence(*paths)['policy_version'],PROFILE)
            report=json.loads(paths[3].read_text());report['promptfoo_export']['results']['results'][0]['success']=False;paths[3].write_text(json.dumps(report))
            evidence=json.loads(paths[0].read_text());evidence['report_sha256']=runner.sha256_file(paths[3]);paths[0].write_text(json.dumps(evidence))
            with self.assertRaises(ValueError):runner.verify_evaluation_evidence(*paths)

    def test_captured_real_promptfoo_echo_export(self):
        from pathlib import Path
        from promptfoo_import import validate_export
        export=runner.load_json(Path(__file__).parent/'fixtures/promptfoo-0.117.0-echo.json')
        suite=self.fixture()[1]
        self.assertEqual(validate_export(export,suite,now=runner.parse_timestamp(export['results']['timestamp'])),1)

    def test_equals_profile_requires_consistent_perfect_scores(self):
        import copy
        from promptfoo_import import validate_export
        export,suite=self.fixture();now=runner.parse_timestamp(export['results']['timestamp'])
        export['results']['results'][0]['gradingResult']['componentResults']=[{'pass':True,'score':1,'reason':'equal','assertion':{'type':'equals','value':'ready'}}]
        for field in ['row','grading','component']:
            for score in [0,0.5,2,True,None]:
                bad=copy.deepcopy(export);row=bad['results']['results'][0]
                if field=='row':row['score']=score
                elif field=='grading':row['gradingResult']['score']=score
                else:row['gradingResult']['componentResults'][0]['score']=score
                with self.subTest(field=field,score=score),self.assertRaises(ValueError):validate_export(bad,suite,now=now)

    def test_rejects_provider_and_prompt_configuration_outside_profile(self):
        import copy
        from promptfoo_import import validate_export
        export,suite=self.fixture();now=runner.parse_timestamp(export['results']['timestamp'])
        for field,entry in [('providers',{'id':'echo','transform':'output'}),('providers',{'id':'echo'}),('providers',None),('providers',''),('prompts',{'raw':'ready','label':'ready','config':{'transform':'output'}}),('prompts',{'raw':'ready','label':'ready','transform':'output'}),('prompts',None),('prompts',5)]:
            bad=copy.deepcopy(export);bad['config'][field]=[entry]
            with self.subTest(field=field,entry=entry),self.assertRaises(ValueError):validate_export(bad,suite,now=now)

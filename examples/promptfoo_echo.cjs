// Run with exactly promptfoo@0.117.0 installed. All evaluation is local echo.
process.env.PROMPTFOO_DISABLE_TELEMETRY='1';
process.env.PROMPTFOO_DISABLE_UPDATE='1';
process.env.PROMPTFOO_CACHE_ENABLED='false';
const packageFile=require('path').resolve(require('path').dirname(require.resolve('promptfoo')),'../../package.json');
if(JSON.parse(require('fs').readFileSync(packageFile,'utf8')).version!=='0.117.0')throw Error('Expected promptfoo 0.117.0');
const {evaluate}=require('promptfoo');
const outputPath=process.argv[2];
if(!outputPath)throw Error('Supply an output JSON path');
evaluate({prompts:['ready'],providers:['echo'],tests:[{assert:[{type:'equals',value:'ready'}],metadata:{runlog_scenario_id:'case1'}}],outputPath,writeLatestResults:false},{cache:false}).then(()=>{
  // Record this actual local evaluator invocation, whose echo provider is free.
  // Do not substitute zero for unknown paid-provider costs in your own harness.
  const fs=require('fs'),path=require('path'),root=path.dirname(outputPath);
  fs.writeFileSync(path.join(root,'echo-suite.json'),JSON.stringify({version:1,scenarios:[{id:'case1',assertions:{tool_called:'local_echo_evaluation',max_steps:1,max_cost_usd:0}}]}));
  fs.writeFileSync(path.join(root,'echo-trace.json'),JSON.stringify([{scenario_id:'case1',tool:'local_echo_evaluation',args:{provider:'echo'},cost_usd:0}]));
}).catch(()=>{console.error('Local Promptfoo evaluation failed');process.exitCode=1;});

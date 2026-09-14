// Full CLI -> local HTTPS -> actual Worker -> real Ed25519 verification.
// No provider or external auth calls. Run from the monorepo or set RUNLOG_SITE_ROOT.
import assert from 'node:assert/strict';
import {mkdtemp, readFile, writeFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {spawn, execFileSync} from 'node:child_process';
import https from 'node:https';
const root = path.resolve(process.argv[2] || fileURLToPath(new URL('..', import.meta.url)));
const site = process.env.RUNLOG_SITE_ROOT || path.resolve(root, '../runlog-site');
const {default: worker} = await import(pathToFileURL(path.join(site, 'worker.js')));
const {database} = await import(pathToFileURL(path.join(site, 'tests/helpers/d1.mjs')));
const temp = await mkdtemp(path.join(tmpdir(), 'ship-worker-'));
const user={id:'11111111-1111-4111-8111-111111111111',email:'audit@example.invalid',email_confirmed_at:'2026-09-01'};
const oldFetch=globalThis.fetch;
globalThis.fetch=async url => {
 assert.ok(String(url).startsWith('https://identity.supabase.co/'), 'unexpected external call');
 return Response.json(String(url).endsWith('/settings')?{external:{email:true},disable_signup:false,mailer_autoconfirm:false}:String(url).endsWith('/user')?user:{access_token:'fake-access',refresh_token:'fake-refresh',expires_in:3600,user});
};
const pair=await crypto.subtle.generateKey({name:'Ed25519'},true,['sign','verify']);
const publicJwk=await crypto.subtle.exportKey('jwk',pair.publicKey);
const env={ASSETS:{fetch:async()=>new Response('asset')},PLATFORM_DB:database(),PLATFORM_ENV:'staging',SUPABASE_URL:'https://identity.supabase.co',SUPABASE_SERVICE_KEY:'fake-service',SESSION_ENCRYPTION_KEY:Buffer.alloc(32,7).toString('base64'),ADMIN_USER:'admin',ADMIN_PASS:'fake-password',RUNLOG_DATA:{get:async()=>null},REQUEST_LIMITER:{limit:async()=>({success:true})},AUTH_LIMITER:{limit:async()=>({success:true})},SECURITY_LIMITS_REQUIRED:'true',ED25519_PRIVATE_KEY_JWK:JSON.stringify(await crypto.subtle.exportKey('jwk',pair.privateKey))};
const req=(route,data,headers={})=>new Request('https://runlog.cc'+route,{method:'POST',headers:{Origin:'https://runlog.cc','Content-Type':'application/json',...headers},body:JSON.stringify(data)});
const login=await worker.fetch(req('/api/auth/login',{email:user.email,password:'fake-password'}),env);
assert.equal(login.status,200,await login.clone().text());
const cookie={Cookie:login.headers.get('set-cookie').split(';')[0]};
const keyResponse=await worker.fetch(req('/api/customer/keys',{name:'local CI integration'},cookie),env);
assert.equal(keyResponse.status,201);
const key=(await keyResponse.json()).apiKey;
let calls=0, mode='normal';
const python=process.env.PYTHON || 'python3';
const run = (args, extra={}) => new Promise((resolve,reject)=>{
 const child=spawn(python,args,{env:{...process.env,RUNLOG_TOKEN:key,SSL_CERT_FILE:path.join(temp,'cert.pem'),...extra}});
 let stdout='',stderr='';child.stdout.on('data',data=>stdout+=data);child.stderr.on('data',data=>stderr+=data);child.on('error',reject);child.on('close',code=>resolve({code,stdout,stderr}));
});
let server;
try {
 execFileSync('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-keyout',path.join(temp,'key.pem'),'-out',path.join(temp,'cert.pem'),'-days','1','-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost,IP:127.0.0.1'],{stdio:'ignore'});
 await writeFile(path.join(temp,'issuer.json'),JSON.stringify({kty:publicJwk.kty,crv:publicJwk.crv,x:publicJwk.x}));
 server=https.createServer({key:await readFile(path.join(temp,'key.pem')),cert:await readFile(path.join(temp,'cert.pem'))},async (incoming,outgoing)=>{
  try {
   calls++;let body='';for await(const chunk of incoming)body+=chunk;
   if(mode==='v2-downgrade'){const request=JSON.parse(body);request.gate_contract='runlog.trace-gate.v2';body=JSON.stringify(request);}
   const response=await worker.fetch(new Request('https://runlog.cc/api/v1/gate',{method:'POST',headers:incoming.headers,body}),env);
   const result=await response.json();
   if(mode==='v2-downgrade')await writeFile(path.join(temp,'v2-packet.json'),JSON.stringify(result));
   if(mode==='swap-binding' && result.signed_payload){
    const packet=JSON.parse(result.signed_payload);packet.artifact_binding.report_sha256='0'.repeat(64);
    result.signed_payload=JSON.stringify(packet);
    result.payload_sha256=Buffer.from(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(result.signed_payload))).toString('hex');
    result.signature=Buffer.from(await crypto.subtle.sign('Ed25519',pair.privateKey,new TextEncoder().encode(result.signed_payload))).toString('base64');
   }
   outgoing.writeHead(response.status,{'Content-Type':'application/json'});outgoing.end(JSON.stringify(result));
  } catch(error) {outgoing.writeHead(500);outgoing.end(JSON.stringify({error:String(error)}));}
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const generated=await run([path.join(root,'examples/harness.py'),'--output',temp]);assert.equal(generated.code,0,generated.stderr);
 const args=[path.join(root,'runner.py'),'--evaluation-evidence',path.join(temp,'evidence.json'),'--evaluation-suite',path.join(temp,'suite.json'),'--evaluation-workflow',path.join(temp,'workflow.yml'),'--evaluation-report',path.join(temp,'report.json'),'--issuer-public-key',path.join(temp,'issuer.json'),'--expected-issuer','https://runlog.cc','--expected-repo','example/demo','--expected-head-sha','a'.repeat(40),'--expected-base-sha','b'.repeat(40),'--gate-url',`https://127.0.0.1:${server.address().port}/api/v1/gate`];
 const approved=await run(args);assert.equal(approved.code,0,approved.stderr);assert.match(approved.stdout,/signed trace-loop policy/);assert.equal(calls,1);
 const swapped=[...args];swapped[swapped.indexOf('--expected-head-sha')+1]='c'.repeat(40);assert.equal((await run(swapped)).code,1);assert.equal(calls,1,'CI mismatch must fail before HTTP');
 mode='swap-binding';assert.equal((await run(args)).code,1,'validly signed wrong artifact must fail');assert.equal(calls,2);
 mode='v2-downgrade';assert.equal((await run(args)).code,1,'v3 client must reject validly signed v2 downgrade');assert.equal(calls,3);
 const verifyV2 = `import sys; sys.path.insert(0,sys.argv[1]); import runner; from pathlib import Path; p=Path(sys.argv[2]); e=runner.load_json(p/'evidence.json'); runner.verify_gate_decision(runner.load_json(p/'v2-packet.json'),runner.load_json(p/'issuer.json'),'https://runlog.cc',e,expected_artifact_binding=runner.artifact_binding(e,p/'evidence.json'),expected_contract='runlog.trace-gate.v2')`;
 const compatible=await run(['-c',verifyV2,root,temp]);assert.equal(compatible.code,0,compatible.stderr);
 mode='normal';const legacy=await run([path.join(root,'runner.py'),'--scenarios',path.join(temp,'suite.json'),'--trace',path.join(temp,'trace.json'),'--gate-url',`https://127.0.0.1:${server.address().port}/api/v1/gate`]);assert.equal(legacy.code,0,legacy.stderr);assert.equal(calls,4);
 console.log('PASS: installed CLI/local HTTPS/Worker signing; CI mismatch; signed artifact mismatch; v3 downgrade rejection; explicit signed v2 and legacy compatibility');
} finally {
 if(server)await new Promise(resolve=>server.close(resolve));
 globalThis.fetch=oldFetch;env.PLATFORM_DB.sql.close();await rm(temp,{recursive:true,force:true});
}

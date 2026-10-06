const cp=require('child_process'),fs=require('fs');
const root='/root/rpg-engine-audit-20261003';
const chrome='/root/.cache/ms-playwright/chromium_headless_shell-1223/chrome-headless-shell-linux64/chrome-headless-shell';
const child=cp.spawn(chrome,['--no-sandbox','--disable-gpu','--disable-dev-shm-usage','--remote-debugging-pipe','about:blank'],{stdio:['ignore','ignore','pipe','pipe','pipe']});
let seq=0,buffer='',pending=new Map(),errors=[],stderr='';
child.stderr.on('data',chunk=>{stderr+=chunk.toString();});
for(const stream of [child.stdio[3],child.stdio[4]])stream.on('error',()=>{});
child.on('exit',(code,signal)=>{
 if(pending.size){
  const result={status:'blocked',code,signal,reason:stderr.slice(-1500),scope:'Chromium could not start; no browser rendering claim'};
  fs.writeFileSync(root+'/evidence/browser-check.json',JSON.stringify(result,null,2));
  console.log(JSON.stringify(result));
  for(const {reject} of pending.values())reject(new Error('Chromium process unavailable'));
  pending.clear();
 }
});
child.stdio[4].on('data',chunk=>{
 buffer+=chunk.toString();let pos;
 while((pos=buffer.indexOf('\0'))!==-1){
  const line=buffer.slice(0,pos);buffer=buffer.slice(pos+1);if(!line)continue;
  const msg=JSON.parse(line);
  if(msg.id&&pending.has(msg.id)){const {resolve,reject}=pending.get(msg.id);pending.delete(msg.id);msg.error?reject(new Error(JSON.stringify(msg.error))):resolve(msg.result);}
  if(msg.method==='Runtime.exceptionThrown')errors.push(msg.params.exceptionDetails.text);
 }
});
function call(method,params={},sessionId){return new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});child.stdio[3].write(JSON.stringify({id,method,params,...(sessionId?{sessionId}:{})})+'\0');});}
function pause(ms){return new Promise(r=>setTimeout(r,ms));}
(async()=>{
 const {targetId}=await call('Target.createTarget',{url:'about:blank'});
 const {sessionId}=await call('Target.attachToTarget',{targetId,flatten:true});
 const run=(m,p={})=>call(m,p,sessionId);
 await run('Page.enable');await run('Runtime.enable');
 const evaluate=async expression=>(await run('Runtime.evaluate',{expression,returnByValue:true})).result.value;
 const outputs=[];
 for(const [name,width,height] of [['desktop',1440,1150],['mobile',390,844]]){
  await run('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:name==='mobile'});
  await run('Page.navigate',{url:'file://'+root+'/report.html'});await pause(550);
  const stats=await evaluate(`(()=>{const ids=[...document.querySelectorAll('[id]')].map(e=>e.id);return {width:innerWidth,bodyWidth:document.documentElement.scrollWidth,duplicateIds:ids.filter((id,i)=>ids.indexOf(id)!==i),sections:document.querySelectorAll('main section').length,decisions:document.querySelectorAll('.decision').length,svgCount:document.querySelectorAll('svg').length}})()`);
  if(stats.bodyWidth>width)throw Error('Unexpected page overflow '+JSON.stringify(stats));
  if(stats.duplicateIds.length)throw Error('Duplicate IDs remain');
  await evaluate(`document.querySelector('[data-filter="reject"]').click()`);
  const rejected=await evaluate(`document.querySelectorAll('.decision:not([hidden])').length`);
  if(rejected!==2)throw Error('Filter regression: '+rejected);
  await evaluate(`document.querySelector('[data-filter="all"]').click();scrollTo(0,0)`);await pause(100);
  const shot=await run('Page.captureScreenshot',{format:'png'});
  fs.writeFileSync(root+'/figures/report-'+name+'.png',Buffer.from(shot.data,'base64'));
  outputs.push({name,...stats,rejectFilterVisible:rejected});
 }
 await run('Emulation.setDeviceMetricsOverride',{width:1440,height:1100,deviceScaleFactor:1,mobile:false});
 for(const section of ['architecture','experiments','target']){
  await evaluate(`document.querySelector('#${section}').scrollIntoView()`);await pause(450);
  const shot=await run('Page.captureScreenshot',{format:'png'});fs.writeFileSync(root+'/figures/report-'+section+'.png',Buffer.from(shot.data,'base64'));
 }
 if(errors.length)throw Error('Browser JS errors '+errors.join(';'));
 const result={status:'passed',javascriptErrors:errors,viewports:outputs};fs.writeFileSync(root+'/evidence/browser-check.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 await call('Browser.close');
})().catch(error=>{console.error(error.stack);child.kill();process.exitCode=1;});
setTimeout(()=>{if(child.exitCode===null){child.kill();process.exitCode=1;}},25000).unref();

// Reproducible visual evidence: actual product components/CSS, isolated local
// fixtures, fake SDK document, no devices or external network.
import { createServer } from 'node:http';
import assert from 'node:assert/strict';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { mkdir, writeFile } from 'node:fs/promises';
import { build } from 'esbuild';
const browserEngine = process.env.ZOOM_UI_BROWSER || 'chromium';
const playwright = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : 'playwright');
const now = Date.now();
const interview = { id: 'fixture-interview', status: 'booked', start: new Date(now + 5 * 60_000).toISOString(), end: new Date(now + 35 * 60_000).toISOString(), recruiterName: 'Тестовый рекрутер', callProvider: 'daily', callProviderLocked: false, videoConfigured: true, callProviderOptions: [{ id: 'daily', available: true }, { id: 'zoom', available: true }] };
const person = { id: 'fixture-candidate', name: 'Тестовый кандидат', email: null, city: '', about: '', desiredRole: '', telegram: '', geographies: [], availability: [], consentReuse: false, autoApprove: false, interview, applications: [], videoConfigured: true };
const bundle = await build({ stdin: { contents: `import React,{useState} from 'react'; import {createRoot} from 'react-dom/client'; import './src/base.css';
import {RecruiterInterviewWorkspace} from './src/scenes/recruiter-portal/RecruiterInterviewWorkspace'; import {RecruiterZoomCall} from './src/scenes/recruiter-portal/RecruiterZoomCall';
function Fixture(){const [call,setCall]=useState(null);return call?<RecruiterZoomCall active={{kind:'interview',callProvider:call.provider,recordingConsent:call.recordingConsent,candidateId:'fixture-candidate',interviewId:'fixture-interview',title:'Тестовый кандидат'}} token="fixture-token" onClose={()=>setCall(null)} onJoined={()=>{}} onCompleted={()=>setCall(null)}/>:<RecruiterInterviewWorkspace candidate={${JSON.stringify(person)}} token="fixture-token" videoConfigured={true} onJoin={(provider,recordingConsent)=>setCall({provider,recordingConsent})} onClose={()=>{}} onUpdated={async()=>{}}/>};createRoot(document.querySelector('#root')).render(<Fixture/>);`, resolveDir: fileURLToPath(new URL('../', import.meta.url)), loader: 'tsx' }, bundle: true, write: false, outdir: '/virtual', format: 'esm', jsx: 'automatic', loader: { '.svg': 'dataurl' }, plugins: [{ name: 'fixture-worker', setup(builder) { builder.onResolve({ filter: /\?worker&url$/ }, args => ({ path: args.path, namespace: 'fixture' })); builder.onLoad({ filter: /.*/, namespace: 'fixture' }, () => ({ contents: 'export default "/unused-worker.js"', loader: 'js' })); } }] });
const assets = new Map(bundle.outputFiles.map(file => [file.path.split('/').pop(), file.contents]));
const server = createServer((request,response) => {
  const filename = request.url.slice(1);
  if(assets.has(filename)){response.setHeader('Content-Type', filename.endsWith('.css')?'text/css':'text/javascript');response.end(assets.get(filename));return;}
  response.setHeader('Content-Type','text/html');
  if(request.url==='/call/zoom/client.html'){response.end(`<!doctype html><style>html,body{height:100%;margin:0;background:#11141a;color:#dadce5;font:16px system-ui}main{height:100%;display:grid;place-content:center;text-align:center;gap:18px}.badge{margin:auto;width:80px;height:80px;border-radius:50%;background:#304b89;display:grid;place-items:center;font-size:28px}small{color:#8c94a3}</style><main><div class="badge">ТК</div><strong>Тестовый SDK Zoom</strong><small>Макет подключения · камера и звук не используются</small></main><script>addEventListener('message',e=>{if(e.source===parent&&e.origin===location.origin&&e.data.type==='join')parent.postMessage({source:'hr-zoom-client',type:'status',phase:'joined'},location.origin)});parent.postMessage({source:'hr-zoom-client',type:'ready'},location.origin)</script>`);return;}
  response.end('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/stdin.css"><div id="root"></div><script type="module" src="/stdin.js"></script>');
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const origin=`http://127.0.0.1:${server.address().port}`;
const browser=await playwright[browserEngine].launch({headless:true,...(browserEngine==='chromium'?{channel:process.env.PLAYWRIGHT_CHANNEL}:{})});
try {
 const page=await browser.newPage({viewport:{width:1440,height:960},deviceScaleFactor:1});
 await page.route('**/*',route=>route.request().url().startsWith(origin+'/')?route.continue():route.abort());
 await page.route('**/api/hr/recruiter/**',async route=>{
 const path=route.request().url().split('/api/hr/recruiter/')[1];
 const json=path==='interview/coach/state'?{interviewStatus:'live',active:{revision:1,questions:[{id:'q1',topic:'Опыт',question:'Расскажите о вашем опыте работы',why:'',followups:['Какие задачи вам нравятся?']}]},progress:{answers:{},general:''},revision:1}
 :path==='interview/coach/history'?{versions:[],hasMore:false,nextCursor:null}
 :path==='interview/provider'?{candidates:[{...person,interview:{...interview,callProvider:'zoom'}}]}
 :path==='interview/join'?{provider:'zoom',meetingNumber:'12345678901',passWord:'fixture',userName:'Тестовый рекрутер',role:1,signature:'fixture.fixture.fixture',zak:'fixture-zak',sdkVersion:'6.10.0'}:{};
 await route.fulfill({json});});
 await page.goto(origin);await page.getByRole('combobox',{name:'Видеосервис встречи'}).waitFor();
 await page.getByRole('combobox',{name:'Видеосервис встречи'}).selectOption('zoom');
 await page.getByRole('button',{name:'Подключиться к звонку',exact:true}).waitFor();
 const output=process.env.ZOOM_UI_OUTPUT || fileURLToPath(new URL('../../output/zoom-parallel-2026-10-25/',import.meta.url));await mkdir(output,{recursive:true});
 await page.screenshot({path:output+'prejoin-provider-selector.png',fullPage:true});
 assert.equal(await page.getByRole('button',{name:'Подключиться к звонку',exact:true}).isDisabled(),true);
 await page.getByRole('checkbox',{name:'Согласен на запись встречи и сохранение видео в сервисе.'}).check();
 await page.getByRole('button',{name:'Подключиться к звонку',exact:true}).click();
 await page.frameLocator('iframe[title="Встреча Zoom"]').getByText('Тестовый SDK Zoom',{exact:true}).waitFor();
 const stage = await page.locator('.rcc-stage').boundingBox();
 const frame = await page.locator('iframe[title="Встреча Zoom"]').boundingBox();
 assert.deepEqual(frame, stage, 'Zoom must fill the entire video stage');
 assert.equal(await page.locator('.rcc-stage button, .rcc-stage header, .rcc-stage footer').count(), 0);
 assert.equal(await page.locator('.rcc-sidebar').isVisible(), true);
 await page.screenshot({path:output+'zoom-shell-test-sdk.png',fullPage:true});
 const initialFrame=await page.locator('iframe[title="Встреча Zoom"]').elementHandle();
 await page.setViewportSize({width:390,height:844});
 await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
 const phoneStage=await page.locator('.rcc-stage').boundingBox();
 const phoneFrame=await page.locator('iframe[title="Встреча Zoom"]').boundingBox();
 assert.deepEqual(phoneFrame,phoneStage,'Phone Zoom must occupy the full stage');
 assert.equal(phoneStage.width,390);assert.equal(phoneStage.height,844);
 assert.equal(await page.locator('.rcc-root').evaluate(node=>node.scrollWidth<=node.clientWidth),true);
 await page.screenshot({path:output+'zoom-phone-test-sdk.png'});
 await page.locator('.rcc-root').evaluate(node=>node.scrollTop=844);
 const profile=page.getByRole('button',{name:'Профиль: Тестовый кандидат',exact:true});
 await profile.scrollIntoViewIfNeeded();
 assert.equal(await profile.isVisible(),true);
 assert.equal(await page.locator('iframe[title="Встреча Zoom"]').evaluate((node,old)=>node===old,initialFrame),true);
 await profile.click();
 await page.getByRole('dialog',{name:'Тестовый кандидат',exact:true}).waitFor();
 await page.getByRole('button',{name:'Закрыть профиль',exact:true}).click();
 await page.screenshot({path:output+'zoom-phone-notes.png'});
 await writeFile(output+'responsive-proof.json',JSON.stringify({browserEngine,proof:'fixture CSS and React only; no real Zoom/media or physical device',desktop:{stage,frame},phone:{stage:phoneStage,frame:phoneFrame,iframePreserved:true,profileAccessible:true}},null,2)+'\n');
 console.log(JSON.stringify({browserEngine,desktopStageFills:true,phoneFullViewport:true,notesBelow:true,profileAccessible:true,iframePreserved:true,realMedia:false}));
}finally{await browser.close();await new Promise(resolve=>server.close(resolve));}

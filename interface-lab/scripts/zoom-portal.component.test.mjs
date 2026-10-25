// Real portal + shared call window; local API and SDK fixtures, no external calls.
import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { createServer } from 'node:http';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : 'playwright');
let browser, server, origin;
const access = { provider: 'zoom', meetingNumber: '12345678901', passWord: 'fixture', userName: 'Host', role: 1, signature: 'fixture', zak: 'fixture', sdkVersion: '6.10.0' };
const person = () => ({ id: 'c1', name: 'Кандидат', email: null, telegram: '', accountActive: true,
  interview: { id: 'i1', callProvider: 'zoom', callProviderLocked: false, callProviderOptions: [{ id: 'zoom', available: true }, { id: 'daily', available: true }],
    videoConfigured: true, status: 'live', start: new Date(Date.now() - 60000).toISOString(), end: new Date(Date.now() + 1800000).toISOString() } });
before(async () => {
  const bundle = await build({ stdin: { contents: `import React from 'react'; import {createRoot} from 'react-dom/client';
    import {RecruiterPortal} from './src/scenes/recruiter-portal/RecruiterPortal';
    navigator.mediaDevices.getUserMedia=()=>{throw Error('unexpected outer capture')};
    createRoot(document.querySelector('#root')).render(<RecruiterPortal/>);`, loader: 'tsx', resolveDir: fileURLToPath(new URL('../', import.meta.url)) },
    bundle: true, write: false, format: 'esm', jsx: 'automatic', loader: { '.css': 'empty', '.svg': 'dataurl' },
    plugins: [{ name: 'fixture-worker', setup(b) { b.onResolve({filter:/\?worker&url$/}, a=>({path:a.path,namespace:'worker'})); b.onLoad({filter:/.*/,namespace:'worker'},()=>({contents:'export default "/worker.js"',loader:'js'})); } }] });
  server = createServer((req,res) => {
    res.setHeader('Content-Type',req.url==='/component.js'?'text/javascript':'text/html');
    res.end(req.url==='/component.js'?bundle.outputFiles[0].contents:req.url==='/call/zoom/client.html'
      ? `<!doctype html><button>Leave</button><script>const send=p=>parent.postMessage({source:'hr-zoom-client',type:'status',phase:p},location.origin);window.onmessage=e=>{if(e.source===parent&&e.data.type==='join')send('joined')};document.querySelector('button').onclick=()=>send('left');parent.postMessage({source:'hr-zoom-client',type:'ready'},location.origin)</script>`
      : '<div id="root"></div><script type="module" src="/component.js"></script>');
  });
  await new Promise(r=>server.listen(0,'127.0.0.1',r)); origin=`http://127.0.0.1:${server.address().port}`;
  browser=await chromium.launch({headless:true,channel:process.env.PLAYWRIGHT_CHANNEL});
});
after(async()=>{await browser?.close();if(server)await new Promise(r=>server.close(r));});
async function fixture(t, overrides={}) {
  const page=await browser.newPage();page.setDefaultTimeout(6000);t.after(()=>page.close());
  let candidate=person(); const calls=[];
  await page.route('**/*',r=>r.request().url().startsWith(origin+'/')?r.continue():r.abort());
  await page.route('**/api/hr/recruiter/**',async route=>{
    const path=route.request().url().split('/api/hr/recruiter/')[1];calls.push(path);
    if(overrides[path])return overrides[path](route);
    const state={candidates:[candidate],slots:[],vacancies:[],videoConfigured:true};
    const coach={candidateId:'c1',interviewId:'i1',interviewStatus:'live',active:{revision:1,questions:[]},revision:1,progress:{general:'Сохранённая заметка',answers:{}}};
    const json=path==='auth/session'?{authenticated:true,recruiter:{id:'r1',name:'Рекрутер',email:'fixture@example.invalid'},token:'fixture'}
      :path==='interview/join'?access:path==='interview/coach/state'?coach:path==='interview/coach/history'?{versions:[]}:state;
    return route.fulfill({json});
  });
  await page.goto(origin);
  await page.getByRole('button',{name:'Открыть интервью',exact:true}).waitFor();
  return {page,calls,complete:()=>{candidate={...candidate,interview:{...candidate.interview,status:'completed'}};}};
}
async function join(page){await page.getByRole('button',{name:'Открыть интервью',exact:true}).click();await page.getByRole('checkbox').check();await page.getByRole('button',{name:'Присоединиться',exact:true}).click();await page.locator('.md-zoom-call[data-phase="joined"]').waitFor();}

test('native leave returns before reconciliation, then confirmed completion updates the cabinet',async t=>{
  let release, received;const pending=new Promise(r=>release=r),started=new Promise(r=>received=r);
  const {page,calls,complete}=await fixture(t,{'interview/reconcile':async route=>{received();await pending;await route.fulfill({json:{completed:true}});}});
  await join(page);await page.frameLocator('iframe').getByRole('button',{name:'Leave'}).click();await started;
  assert.equal(await page.locator('iframe').count(),0);
  assert.equal(await page.locator('.md-zoom-entry-dialog').count(),0);
  assert.equal(await page.locator('.rp-shell').evaluate(e=>e.inert),false);
  complete();release();await page.getByText('Интервью завершено.',{exact:true}).waitFor();
  assert.equal(calls.includes('interview/complete'),false);
});

test('failed coach API cannot gate the call and is not requested again on entry',async t=>{
  const {page,calls}=await fixture(t,{'interview/coach/state':r=>r.fulfill({status:503,json:{error:'Fixture coach unavailable'}})});
  await page.getByText('Статус скрипта недоступен',{exact:true}).waitFor();
  const before=calls.filter(p=>p.includes('coach')).length;
  await join(page);
  assert.equal(calls.filter(p=>p.includes('coach')).length,before);
  assert.equal(await page.locator('.ric-approved-shell, .rcc-sidebar').count(),0);
});

test('live interview notes remain available separately without a room or second consent',async t=>{
  const {page,calls}=await fixture(t);
  await page.getByRole('button',{name:'Материалы и заметки',exact:true}).click();
  await page.getByText('Материалы интервью',{exact:true}).waitFor();
  assert.equal(calls.includes('interview/join'),false);
  assert.equal(await page.locator('iframe').count(),0);
  assert.equal(await page.getByRole('checkbox').count(),0);
});

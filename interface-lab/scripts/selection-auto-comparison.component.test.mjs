// Real components; loopback synthetic APIs only. No model/scoring POST on view.
// Prerequisites: npm ci; externally provisioned Playwright module + Chromium.
// PLAYWRIGHT_MODULE=/absolute/playwright/index.mjs CHROMIUM_EXECUTABLE_PATH=/absolute/chromium node --test scripts/selection-auto-comparison.component.test.mjs
import assert from 'node:assert/strict';
import { before, after, test } from 'node:test';
import { createServer } from 'node:http';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
import { makeFixture } from './selection-vacancy.fixtures.mjs';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : 'playwright');
let browser, server, origin;
before(async () => {
  const bundle = await build({ stdin: { contents: `import React from 'react';import {createRoot} from 'react-dom/client';import {SelectionWorkbench} from './src/scenes/selection-workbench/SelectionWorkbench';createRoot(document.getElementById('root')).render(<SelectionWorkbench/>);`, resolveDir: fileURLToPath(new URL('../', import.meta.url)), loader: 'tsx' }, bundle: true, write: false, format: 'esm', jsx: 'automatic', outfile: 'component.js', loader: { '.svg': 'dataurl' } });
  const assets = new Map(bundle.outputFiles.map(file => [file.path.split('/').at(-1), file.contents]));
  server = createServer((req,res) => { res.setHeader('Content-Type',req.url.endsWith('.js')?'text/javascript':req.url.endsWith('.css')?'text/css':'text/html');res.end(assets.get(req.url.slice(1)) || '<!doctype html><html lang="ru"><meta charset="utf-8"><link rel="stylesheet" href="/component.css"><div id="root"></div><script type="module" src="/component.js"></script>'); });
  await new Promise(resolve => server.listen(0,'127.0.0.1',resolve));origin=`http://127.0.0.1:${server.address().port}`;
  browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.CHROMIUM_EXECUTABLE_PATH}:{})});
});
after(async()=>{await browser?.close();if(server)await new Promise(resolve=>server.close(resolve));});
const until=async check=>{for(let i=0;i<180;i++){if(await check())return;await new Promise(resolve=>setTimeout(resolve,15));}assert.fail('Expected transition did not complete');};
function fixture() {
  const state=makeFixture();
  for(const item of state.recommendations.result.items){
    item.score_points=item.status==='scored'?(item.candidate_id==='candidate-2'?11.1:93.7):null;
    for(const p of item.parameters) {p.utility_mode=p.excluded?null:'required_level_tanh';p.weight_share=p.excluded?0:.5;p.score_points_contribution=p.excluded?0:7.25;}
  }
  state.recommendations.automation={state:'ready',updatedAt:1793016000};
  return state;
}
async function setup(t,state=fixture(),intercept) {
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[],unexpected=[],requests=[];
  page.setDefaultTimeout(6000);await page.clock.install();
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*',async route=>{
    const req=route.request(),url=new URL(req.url());
    if(url.origin!==origin){unexpected.push(req.url());return route.abort();}
    if(!url.pathname.startsWith('/api/'))return route.continue();
    requests.push({method:req.method(),path:url.pathname+url.search});
    const body=req.method()==='POST'?req.postDataJSON():null;
    let value=structuredClone(state.request(req.method(),url.pathname+url.search,body));
    if(intercept)value=await intercept({req,url,value,requests})??value;
    await route.fulfill({status:value.httpStatus??200,json:value});
  });
  t.after(async()=>{await page.close();assert.deepEqual(errors,[]);assert.deepEqual(unexpected,[]);});
  await page.goto(origin+'/?tab=vacancies&vacancyId='+state.vacancy.id);
  const reads=()=>requests.filter(r=>r.method==='GET'&&r.path.endsWith('/recommendations')).length;
  await until(()=>reads()>0);
  const poll=async()=>{const n=reads();await page.clock.runFor(15100);await until(()=>reads()>n);};
  const row=name=>page.locator('tr.md-recommendation-row').filter({has:page.getByRole('button',{name,exact:true})});
  return {page,state,requests,reads,poll,row};
}
const noCompute=requests=>assert.equal(requests.some(r=>r.method==='POST'&&/\/(recommendations|profile|assessment)(\/|$)/.test(r.path)),false);

test('not_started and queued automatically become server points; order and null preserved without POST',async t=>{
  const state=fixture(),ready=structuredClone(state.recommendations);
  Object.assign(state.recommendations,{state:'not_started',result:null,recommendationId:null,pendingCandidates:[],automation:{state:'waiting_profile'}});
  const {page,poll,row,requests,reads}=await setup(t,state);
  await until(async()=> (await row('Борис Соколов').innerText()).includes('Ожидает требования вакансии'));
  Object.assign(state.recommendations,{state:'queued',automation:{state:'queued'}});
  const waitingReads=reads();await page.clock.runFor(3100);await until(()=>reads()>waitingReads);
  await until(async()=> (await row('Борис Соколов').innerText()).includes('В очереди'));
  Object.assign(state.recommendations,ready);await poll();
  await until(async()=> (await row('Борис Соколов').innerText()).includes('11,1 / 100'));
  const names=await page.locator('tr.md-recommendation-row .sl-person').allTextContents();
  assert.deepEqual(names.slice(0,2),['Борис Соколов','Анна Романова']); // score_points never re-sorts the owner result
  assert.match(await row('Анна Романова').innerText(),/93,7 \/ 100/);
  assert.doesNotMatch(await row('Дарья Орлова').innerText(),/0 \/ 100|Место/);
  const readyReads=reads();await page.clock.runFor(3100);assert.equal(reads(),readyReads);
  noCompute(requests);assert.deepEqual(state.writes,[]);
});

test('same version polls retain selection; stale then new version clears approvals and hides old score',async t=>{
  const {page,state,poll,row,requests}=await setup(t);
  await page.getByRole('textbox',{name:'Найти кандидата в вакансии'}).fill('Борис');
  await page.getByRole('checkbox',{name:'Подтвердить неизвестные условия: Борис Соколов',exact:true}).check();
  await page.getByRole('checkbox',{name:'Пригласить: Борис Соколов',exact:true}).check();
  await poll();
  assert.equal(await page.getByRole('checkbox',{name:'Пригласить: Борис Соколов',exact:true}).isChecked(),true);
  Object.assign(state.recommendations,{stale:true,state:'stale',automation:{state:'processing'}});await poll();
  await until(async()=> (await row('Борис Соколов').innerText()).includes('Нужно обновить'));
  assert.doesNotMatch(await row('Борис Соколов').innerText(),/11,1 \/ 100/);
  assert.equal(await page.getByRole('button',{name:/^Отправить приглашения/}).isDisabled(),true);
  assert.equal(await page.getByRole('checkbox',{name:'Подтвердить неизвестные условия: Борис Соколов',exact:true}).isChecked(),false);
  Object.assign(state.recommendations,{stale:false,state:'ready',recommendationId:'next-version',automation:{state:'ready'}});
  state.recommendations.result.recommendation_id='next-version';state.recommendations.result.items[0].score_points=42.4;
  await poll();await until(async()=> (await row('Борис Соколов').innerText()).includes('42,4 / 100'));
  assert.equal(await page.getByRole('checkbox',{name:'Пригласить: Борис Соколов',exact:true}).isChecked(),false);
  assert.equal(await page.getByRole('textbox',{name:'Найти кандидата в вакансии'}).inputValue(),'Борис');
  noCompute(requests);
});

test('503 backs off; slow peer GET finishes before any next batch; recovery needs no click',async t=>{
  let failed=false,release,hold=false;const waiting=new Promise(resolve=>release=resolve);
  t.after(()=>release());
  const {page,reads,requests,row}=await setup(t,fixture(),async({url,value})=>{
    if(hold&&url.pathname.endsWith('/profile'))await waiting;
    if(hold&&url.pathname.endsWith('/recommendations')&&!failed){failed=true;return {httpStatus:503,error:'Временно недоступно'};}
    return value;
  });
  await until(async()=> (await row('Борис Соколов').innerText()).includes('11,1 / 100'));
  hold=true;await page.clock.runFor(15100);await until(()=>failed);const count=reads();
  await page.clock.runFor(20000);assert.equal(reads(),count);
  hold=false;release();await until(async()=> (await page.locator('.sl-error').count())>0);
  await page.clock.runFor(3000);assert.equal(reads(),count);
  await page.clock.runFor(3100);await until(()=>reads()>count);
  await until(async()=> (await row('Борис Соколов').innerText()).includes('11,1 / 100'));
  noCompute(requests);
});

test('hidden screen pauses GET, visibility resumes once; leaving vacancy stops polling',async t=>{
  const {page,reads,requests,row}=await setup(t);
  await until(async()=> (await row('Борис Соколов').innerText()).includes('11,1 / 100'));
  await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,get:()=>window.__hiddenForTest===true});window.__hiddenForTest=true;document.dispatchEvent(new Event('visibilitychange'));});
  const before=reads();await page.clock.runFor(20000);assert.equal(reads(),before);
  await page.evaluate(()=>{window.__hiddenForTest=false;document.dispatchEvent(new Event('visibilitychange'));});
  await until(()=>reads()>before);await page.getByRole('button',{name:'Все вакансии',exact:true}).click();
  const left=reads();await page.clock.runFor(20000);assert.equal(reads(),left);noCompute(requests);
});

test('401 and 403 stop polling and clear the operational screen',async t=>{
  for(const status of [401,403]){
    let denied=false;
    const {page,reads,requests,row}=await setup(t,fixture(),async({url,value})=>denied&&url.pathname.endsWith('/recommendations')?{httpStatus:status,error:'Нет доступа'}:value);
    await until(async()=> (await row('Борис Соколов').innerText()).includes('11,1 / 100'));
    denied=true;await page.clock.runFor(15100);await page.getByText('Вход для команды',{exact:true}).waitFor();
    const count=reads();await page.clock.runFor(20000);assert.equal(reads(),count);noCompute(requests);await page.close();
  }
});

test('known weights with null target render higher-better details, raw probabilities and allow saving null',async t=>{
  const state=fixture(),parameter=state.profileEnvelope.profile.parameters[0];
  parameter.required_level_id=null;parameter.status='needs_review';state.profileEnvelope.state='needs_review';
  for(const item of state.recommendations.result.items){const p=item.parameters.find(p=>p.parameter_id===parameter.parameter_id);p.required_level_id=null;p.utility_mode='higher_level_linear';}
  const {page,row,requests}=await setup(t,state);
  await until(async()=> (await row('Борис Соколов').innerText()).includes('11,1 / 100'));
  await row('Борис Соколов').getByRole('button',{name:'Почему такой балл',exact:true}).click();
  const explanation=page.locator('.sl-match-explanation');await explanation.locator('.md-skill-disclosure > summary').first().click();await page.clock.runFor(50);
  await until(async()=> (await explanation.innerText()).includes('Чем выше уровень кандидата'));
  assert.match(await explanation.innerText(),/Чем выше уровень кандидата/);
  assert.match(await explanation.innerText(),/7,3 балла/);
  assert.match(await explanation.innerText(),/30\s?%/);
  assert.equal(await explanation.locator('.md-required').count(),0);
  await page.locator('.sl-requirements > summary').click();await page.getByRole('button',{name:'Изменить требования',exact:true}).click();
  assert.equal(await page.getByRole('button',{name:'Сохранить требования',exact:true}).isEnabled(),true);
  await page.getByRole('button',{name:'Сохранить требования',exact:true}).click();
  await until(()=>state.writes.length>0);
  assert.equal(state.writes[0].body.changes[0].required_level_id,null);
  assert.equal(requests.filter(r=>r.method==='POST'&&r.path.endsWith('/recommendations')).length,0);
});

test('unknown importance remains a clarification and cannot authorize an old score',async t=>{
  const state=fixture();state.profileEnvelope.profile.parameters[0].importance='insufficient_data';
  state.profileEnvelope.state='needs_review';state.recommendations.automation={state:'needs_review',errorCode:'importance_required'};
  const {page,row,requests}=await setup(t,state);
  await page.getByText(/Уточните важность навыков в требованиях вакансии/).first().waitFor();
  assert.doesNotMatch(await row('Борис Соколов').innerText(),/11,1 \/ 100/);
  assert.equal(await page.getByRole('button',{name:/^Отправить приглашения/}).isDisabled(),true);
  assert.equal(await row('Борис Соколов').getByRole('button',{name:'Почему такой балл',exact:true}).count(),0);
  noCompute(requests);
});

test('late GET from a previous vacancy cannot replace the newly opened result',async t=>{
  const first=fixture(),second=fixture();second.vacancy.id='vacancy-2';second.vacancy.title='Вторая вакансия';
  second.profileEnvelope.vacancyId='vacancy-2';second.profileEnvelope.profile.vacancy_id='vacancy-2';
  second.recommendations.vacancyId='vacancy-2';second.recommendations.result.vacancy_id='vacancy-2';
  second.recommendations.result.items[0].score_points=17.2;
  let holding=false,held=false,release;const deferred=new Promise(resolve=>release=resolve);t.after(()=>release());
  const {page,requests,row}=await setup(t,first,async({url,value})=>{
    if(url.pathname==='/api/hr/selection/vacancies')return {vacancies:[first.vacancy,second.vacancy]};
    if(url.pathname.includes('/vacancies/vacancy-2/'))return structuredClone(second.request('GET',url.pathname,null));
    if(holding&&url.pathname.endsWith('/recommendations')){held=true;await deferred;}
    return value;
  });
  await until(async()=> (await row('Борис Соколов').innerText()).includes('11,1 / 100'));
  first.recommendations.result.items[0].score_points=95.9;holding=true;await page.clock.runFor(15100);await until(()=>held);
  await page.getByRole('button',{name:'Все вакансии',exact:true}).click();
  await page.getByRole('button',{name:'Открыть вакансию: Вторая вакансия',exact:true}).click();
  await until(async()=> (await row('Борис Соколов').innerText()).includes('17,2 / 100'));
  release();await page.clock.runFor(100);
  assert.match(await row('Борис Соколов').innerText(),/17,2 \/ 100/);
  assert.doesNotMatch(await row('Борис Соколов').innerText(),/95,9 \/ 100/);
  assert.equal(new URL(page.url()).searchParams.get('vacancyId'),'vacancy-2');noCompute(requests);
});

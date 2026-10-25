// Local React integration only: no production API, devices or external SDK.
import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { createServer } from 'node:http';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : 'playwright');
let browser, server, origin;
const active = { kind: 'interview', callProvider: 'zoom', candidateId: 'candidate-fixture', interviewId: 'interview-fixture', title: 'Кандидат' };
const credentials = { provider: 'zoom', meetingNumber: '12345678901', passWord: 'pass', userName: 'Host fixture', role: 1, signature: 'fixture.signature.value', zak: 'fixture-host-zak', sdkVersion: '6.10.0' };
const coach = { candidateId: active.candidateId, interviewId: active.interviewId, interviewStatus: 'live', active: { revision: 1, questions: [{ id: 'q1', topic: 'Опыт', question: 'Расскажите об опыте', why: '', followups: [] }] }, revision: 1, progress: { answers: {}, general: '' } };
before(async () => {
  const bundle = await build({ stdin: { contents: `import React from 'react'; import { createRoot } from 'react-dom/client'; import { RecruiterZoomCall } from './src/scenes/recruiter-portal/RecruiterZoomCall';
    window.receipts=[]; window.counts={joined:0,closed:0,completed:0,capture:0};
    navigator.mediaDevices.getUserMedia = () => { window.counts.capture++; throw Error('forbidden outer capture'); };
    createRoot(document.querySelector('#root')).render(<RecruiterZoomCall active={{...${JSON.stringify(active)}, recordingConsent: !new URLSearchParams(location.search).has("without-consent")}} token="test-recruiter" onClose={() => window.counts.closed++} onJoined={() => window.counts.joined++} onCompleted={() => window.counts.completed++}/>);`, loader: 'tsx', resolveDir: fileURLToPath(new URL('../', import.meta.url)) },
    bundle: true, write: false, format: 'esm', jsx: 'automatic', plugins: [{ name: 'fixture-worker-url', setup(build) { build.onResolve({ filter: /\?worker&url$/ }, args => ({ path: args.path, namespace: 'fixture-worker' })); build.onLoad({ filter: /.*/, namespace: 'fixture-worker' }, () => ({ contents: 'export default "/fixture-worker.js"', loader: 'js' })); } }], loader: { '.css': 'empty', '.svg': 'dataurl' } });
  server = createServer((request, response) => {
    response.setHeader('Content-Type', request.url === '/component.js' ? 'text/javascript' : 'text/html');
    response.end(request.url === '/component.js' ? bundle.outputFiles[0].contents
      : request.url === '/call/zoom/client.html' ? `<!doctype html><p>Fixture native Zoom</p><script>window.addEventListener('message',e=>{if(e.source!==parent||e.origin!==location.origin)return;parent.receipts.push(e.data.type);if(e.data.type==='join'){window.joined=true;parent.postMessage({source:'hr-zoom-client',type:'status',phase:'joined'},location.origin)}if(e.data.type==='leave')parent.postMessage({source:'hr-zoom-client',type:'status',phase:'left'},location.origin)});parent.postMessage({source:'hr-zoom-client',type:'ready'},location.origin)</script>`
      : '<!doctype html><div id="root"></div><script type="module" src="/component.js"></script>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve)); origin = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL });
});
after(async () => { await browser?.close(); if (server) await new Promise(resolve => server.close(resolve)); });
async function fixture(t, overrides = {}, query = '') {
  const page = await browser.newPage(); page.setDefaultTimeout(6000); t.after(() => page.close());
  const calls = [], commands = [];
  await page.route('**/*', route => route.request().url().startsWith(origin + '/') ? route.continue() : route.abort());
  await page.route('**/api/hr/recruiter/**', async route => {
    const path = route.request().url().split('/api/hr/recruiter/')[1]; calls.push(path);
    commands.push({ path, body: route.request().postDataJSON() });
    if (overrides[path]) return overrides[path](route);
    let json = path === 'interview/join' ? credentials : path === 'interview/reconcile' ? { completed: false, status: 'live' } : path === 'interview/coach/state' ? coach
      : path === 'interview/coach/history' ? { versions: [], hasMore: false, nextCursor: null } : {};
    await route.fulfill({ json });
  });
  await page.goto(origin + query);
  if (!query.includes('without-consent')) await page.waitForFunction(() => window.receipts.includes('join'));
  return { page, calls, commands };
}

test('coach render failure cannot prevent Zoom entry or remount the call', async t => {
  const { page, calls } = await fixture(t, { 'interview/coach/state': route => route.fulfill({ json: { ...coach, active: { questions: {} } } }) });
  await page.getByText('Материалы интервью временно недоступны. Звонок продолжается.').waitFor();
  const element = await page.locator('iframe[title="Встреча Zoom"]').elementHandle();
  await page.getByRole('button', { name: 'Открыть повторно', exact: true }).click();
  assert.equal(await page.locator('iframe[title="Встреча Zoom"]').evaluate((node, old) => node === old, element), true);
  assert.deepEqual(await page.evaluate(() => window.receipts), ['join']);
  assert.equal(await page.evaluate(() => window.counts.capture), 0);
  assert.ok(calls.includes('interview/connected'));
  await nativeLeave(page);
  await page.getByText('Вы вышли из Zoom', { exact: true }).first().waitFor();
  assert.equal(calls.includes('interview/complete'), false);
});

test('failed notes save does not block local exit, draft remains exportable', async t => {
  const { page, calls } = await fixture(t, { 'interview/coach/progress': route => route.fulfill({ status: 503, json: { error: 'Fixture notes failure' } }) });
  await page.getByRole('textbox', { name: 'Заметка к текущему вопросу' }).fill('Unsaved fixture note');
  await page.locator('[role=alert]').filter({ hasText: 'Fixture notes failure' }).waitFor();
  await nativeLeave(page);
  await page.getByText('Вы вышли из Zoom', { exact: true }).first().waitFor();
  assert.equal(await page.locator('iframe').count(), 0);
  assert.equal(await page.getByRole('textbox', { name: 'Заметка к текущему вопросу' }).inputValue(), 'Unsaved fixture note');
  assert.ok(await page.getByRole('button', { name: 'Скачать заметки', exact: true }).count());
  assert.equal(calls.includes('interview/complete'), false);
});

async function nativeLeave(page) {
  await page.frames().find(frame => frame.url().endsWith('/call/zoom/client.html')).evaluate(() => parent.postMessage({ source: 'hr-zoom-client', type: 'status', phase: 'left' }, location.origin));
}

test('Zoom owns the entire video stage; live host leave only reconciles and never ends everyone', async t => {
  const { page, calls } = await fixture(t);
  assert.equal(await page.locator('.rcc-stage .rcc-header, .rcc-stage .rcc-call-controls, .rcc-stage .rcc-stage-toolbar, .rcc-stage .rcc-call-notice').count(), 0);
  assert.equal(await page.locator('.rcc-stage button').count(), 0);
  assert.equal(await page.locator('.rcc-native-provider').count(), 1);
  await nativeLeave(page);
  await page.getByText('Вы вышли из Zoom', { exact: true }).first().waitFor();
  await page.waitForFunction(() => document.querySelector('.rcc-native-notice')?.textContent?.includes('Проверяем'));
  assert.equal(calls.includes('interview/reconcile'), true);
  assert.equal(calls.includes('interview/complete'), false);
  assert.equal(await page.getByRole('heading', { name: 'Интервью завершено' }).count(), 0);
  assert.equal(await page.locator('iframe').count(), 0);
  await page.getByRole('button', { name: 'Вернуться к интервью', exact: true }).click();
  assert.equal(await page.evaluate(() => window.counts.closed), 1);
});

test('only server-confirmed ended session opens the completed state after native end', async t => {
  const { page, calls } = await fixture(t, { 'interview/reconcile': route => route.fulfill({ json: { completed: true, status: 'completed' } }) });
  await nativeLeave(page);
  await page.getByRole('heading', { name: 'Интервью завершено' }).waitFor();
  assert.equal(calls.filter(path => path === 'interview/reconcile').length, 1);
  assert.equal(calls.includes('interview/complete'), false);
  assert.equal(await page.locator('iframe').count(), 0);
  await page.getByRole('button', { name: 'Вернуться', exact: true }).click();
  assert.equal(await page.evaluate(() => window.counts.completed), 1);
});

test('transient host receipt failure retries without another join or replacing the frame', async t => {
  let attempts = 0;
  const { page } = await fixture(t, { 'interview/connected': route => route.fulfill(++attempts === 1
    ? { status: 503, json: { error: 'Session not propagated' } } : { json: { connected: true } }) });
  await page.getByText('Обновляем статус встречи. Звонок продолжается.', { exact: true }).waitFor();
  const original = await page.locator('iframe[title="Встреча Zoom"]').elementHandle();
  await page.getByText('Обновляем статус встречи. Звонок продолжается.', { exact: true }).waitFor({ state: 'hidden' });
  assert.equal(attempts, 2);
  assert.equal(await page.locator('iframe[title="Встреча Zoom"]').evaluate((node, old) => node === old, original), true);
  assert.deepEqual(await page.evaluate(() => window.receipts), ['join']);
});

test('guest credentials cannot issue host receipt or complete the meeting', async t => {
  const { page, calls } = await fixture(t, { 'interview/join': route => route.fulfill({ json: { ...credentials, role: 0, zak: undefined } }) });
  assert.equal(await page.getByRole('button', { name: 'Завершить интервью для всех', exact: true }).count(), 0);
  await nativeLeave(page);
  await page.getByText('Вы вышли из Zoom', { exact: true }).first().waitFor();
  assert.equal(calls.includes('interview/reconcile'), false);
  assert.equal(calls.includes('interview/connected'), false);
  assert.equal(calls.includes('interview/complete'), false);
});


test('prejoin consent is submitted with access request; Zoom recording needs no post-join start command', async t => {
  const { page, calls, commands } = await fixture(t);
  assert.deepEqual(commands.find(command => command.path === 'interview/join')?.body,
    { candidateId: active.candidateId, interviewId: active.interviewId, consent: true });
  assert.equal(calls.includes('interview/start'), false);
  assert.equal(await page.locator('.rcc-stage button, .rcc-stage input').count(), 0);
  assert.deepEqual(await page.evaluate(() => window.receipts), ['join']);
  assert.equal(await page.locator('iframe').count(), 1);
  assert.equal(await page.evaluate(() => window.counts.capture), 0);
});

test('direct recruiter entry cannot request Zoom access before explicit recording consent', async t => {
  const { page, calls, commands } = await fixture(t, {}, '/?without-consent=1');
  const join = page.getByRole('button', { name: 'Подключиться к звонку', exact: true });
  assert.equal(await join.isDisabled(), true);
  assert.equal(calls.includes('interview/join'), false);
  assert.equal(await page.locator('iframe').count(), 0);
  await page.getByRole('checkbox', { name: 'Согласен на запись встречи и сохранение видео в сервисе.' }).check();
  assert.equal(calls.includes('interview/join'), false);
  await join.click();
  await page.waitForFunction(() => window.receipts.includes('join'));
  assert.equal(commands.find(command => command.path === 'interview/join')?.body.consent, true);
  assert.equal(calls.includes('interview/start'), false);
  assert.equal(await page.locator('.rcc-stage button, .rcc-stage input').count(), 0);
});

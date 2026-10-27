// Local React integration only: no production API, devices or external SDK.
import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { createServer } from 'node:http';
import { readFileSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : 'playwright');
let browser, server, origin;
const active = { kind: 'interview', callProvider: 'zoom', candidateId: 'candidate-fixture', interviewId: 'interview-fixture', title: 'Кандидат' };
const credentials = { provider: 'zoom', meetingNumber: '12345678901', passWord: 'pass', userName: 'Host fixture', role: 1, signature: 'fixture.signature.value', zak: 'fixture-host-zak', sdkVersion: '6.10.0' };
const coach = { candidateId: active.candidateId, interviewId: active.interviewId, interviewStatus: 'live', active: { revision: 1, questions: [{ id: 'q1', topic: 'Опыт', question: 'Расскажите об опыте', why: '', followups: [] }] }, revision: 1, progress: { answers: {}, general: '' } };
before(async () => {
  const bundle = await build({ stdin: { contents: `import React from 'react'; import { createRoot } from 'react-dom/client'; import { RecruiterZoomCall } from './src/scenes/recruiter-portal/RecruiterZoomCall';
    window.receipts=[]; window.counts={joined:0,closed:0,completed:0,capture:0,hostLeft:0};
    navigator.mediaDevices.getUserMedia = () => { window.counts.capture++; throw Error('forbidden outer capture'); };
    createRoot(document.querySelector('#root')).render(<RecruiterZoomCall active={{...${JSON.stringify(active)}, recordingConsent: !new URLSearchParams(location.search).has("without-consent")}} token="test-recruiter" onClose={() => window.counts.closed++} onJoined={() => window.counts.joined++} onCompleted={() => window.counts.completed++} onZoomHostLeft={() => window.counts.hostLeft++}/>);`, loader: 'tsx', resolveDir: fileURLToPath(new URL('../', import.meta.url)) },
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
  await page.waitForFunction(() => window.receipts.includes('prepare'));
  if (!query.includes('without-consent')) {
    await page.getByRole('checkbox').check();
    await page.getByRole('button', { name: 'Присоединиться', exact: true }).click();
    await page.waitForFunction(() => window.receipts.includes('join'));
  }
  return { page, calls, commands };
}

test('call has no coach, notes or resume dependency even when those APIs fail', async t => {
  const { page, calls } = await fixture(t, { 'interview/coach/state': route => route.fulfill({ status: 503, json: { error: 'Unavailable' } }) });
  assert.equal(calls.some(path => path.includes('coach') || path.includes('resume')), false);
  assert.equal(await page.getByRole('textbox').count(), 0);
  assert.equal(await page.locator('.rcc-panel, .rcc-call-controls, video').count(), 0);
  assert.equal(await page.evaluate(() => window.counts.capture), 0);
  assert.deepEqual(await page.evaluate(() => window.receipts), ['prepare', 'join']);
});

async function nativeLeave(page) {
  await page.frames().find(frame => frame.url().endsWith('/call/zoom/client.html')).evaluate(() => parent.postMessage({ source: 'hr-zoom-client', type: 'status', phase: 'left' }, location.origin));
}

test('native host leave returns immediately and delegates status to the cabinet', async t => {
  const { page, calls } = await fixture(t);
  await nativeLeave(page);
  await page.waitForFunction(() => window.counts.closed === 1);
  await page.locator('iframe').waitFor({ state: 'hidden' });
  assert.equal(await page.evaluate(() => window.receipts.filter(command => command === 'join').length), 1);
  assert.equal(await page.evaluate(() => window.counts.hostLeft), 1);
  assert.equal(calls.includes('interview/complete'), false);
  assert.equal(calls.includes('interview/reconcile'), false);
});

test('transient session receipt failure retries without another join or replacing the frame', async t => {
  let attempts = 0;
  const { page } = await fixture(t, { 'interview/connected': route => route.fulfill(++attempts === 1
    ? { status: 503, json: { error: 'Session not propagated' } } : { json: { connected: true } }) });
  await page.getByText('Обновляем статус встречи. Звонок продолжается.', { exact: true }).waitFor();
  const original = await page.locator('iframe[title="Встреча Zoom"]').elementHandle();
  await page.getByText('Обновляем статус встречи. Звонок продолжается.', { exact: true }).waitFor({ state: 'hidden' });
  assert.equal(attempts, 2);
  assert.equal(await page.locator('iframe').evaluate((node, old) => node === old, original), true);
  assert.deepEqual(await page.evaluate(() => window.receipts), ['prepare', 'join']);
});

test('server guest role cannot pin or complete the host session', async t => {
  const { page, calls } = await fixture(t, { 'interview/join': route => route.fulfill({ json: { ...credentials, role: 0, zak: undefined } }) });
  await nativeLeave(page);
  await page.waitForFunction(() => window.counts.closed === 1);
  assert.equal(await page.evaluate(() => window.counts.hostLeft), 0);
  assert.equal(calls.includes('interview/connected'), false);
  assert.equal(calls.includes('interview/complete'), false);
});

test('one explicit consent is required even if the old entry sent recordingConsent', async t => {
  const { page, calls, commands } = await fixture(t, {}, '?without-consent');
  assert.equal(calls.length, 0);
  assert.deepEqual(await page.evaluate(() => window.receipts), ['prepare']);
  const preparedFrame = await page.locator('iframe').elementHandle();
  assert.equal(await page.getByRole('button', { name: 'Присоединиться', exact: true }).isDisabled(), true);
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: 'Присоединиться', exact: true }).click();
  await page.waitForFunction(() => window.receipts.includes('join'));
  assert.equal(await preparedFrame.evaluate(node => node === document.querySelector('iframe')), true);
  assert.deepEqual(commands.find(command => command.path === 'interview/join')?.body,
    { candidateId: active.candidateId, interviewId: active.interviewId, consent: true });
  assert.equal(calls.includes('interview/start'), false);
  assert.equal(await page.getByRole('dialog').locator('button, input').count(), 0);
});

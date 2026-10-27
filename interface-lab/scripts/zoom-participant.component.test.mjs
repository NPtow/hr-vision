// Actual candidate/employer components, isolated Chromium, mocked API and Zoom
// child document. Never contacts Zoom, uses real credentials, or captures media.
import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { createServer } from 'node:http';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE
  ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : 'playwright');
let browser, server, origin;
const sdkDocument = `<!doctype html><button id="leave">Leave fixture</button><script>
  const send = data => parent.postMessage({ source: 'hr-zoom-client', ...data }, location.origin);
  window.addEventListener('message', event => {
    if (event.source !== parent || event.origin !== location.origin || event.data?.source !== 'hr-zoom-parent') return;
    if (event.data.type === 'join') {
      document.body.dataset.joined = 'yes';
      document.body.dataset.joinCount = String(Number(document.body.dataset.joinCount || 0) + 1);
      send({ type: 'status', phase: 'joined' });
    }
    if (event.data.type === 'leave') send({ type: 'status', phase: 'left' });
  });
  document.querySelector('#leave').onclick = () => send({ type: 'status', phase: 'left' });
  send({ type: 'ready' });
</script>`;

before(async () => {
  const bundle = await build({
    stdin: { contents: `import React from 'react';
      import { createRoot } from 'react-dom/client';
      import { InterviewRoom } from './src/scenes/candidate-portal/InterviewRoom';
      import { MeetingPanel } from './src/scenes/connected-journey/MeetingPanel';
      import { useJourney } from './src/scenes/connected-journey/api';
      window.__dailyImports = 0; window.__mediaCalls = 0; window.__refreshes = 0; window.__completions = 0;
      navigator.mediaDevices.getUserMedia = () => { window.__mediaCalls++; throw new Error('Unexpected capture'); };
      const mode = new URLSearchParams(location.search).get('screen');
      function Employer() {
        const journey = useJourney('sfera', { employer: true });
        const person = journey.state?.candidates['candidate-1'];
        return <div className="cj-app">{journey.error && <p role="alert">{journey.error}</p>}
          {person ? <MeetingPanel person={person} journey={journey} onCallChange={() => {}}
            onComplete={() => { window.__completions++; }}/> : <p>Loading fixture</p>}</div>;
      }
      createRoot(document.querySelector('#root')).render(mode === 'employer' ? <Employer/> : <div className="cp-root">
        <InterviewRoom title="Интервью с рекрутером" start="2026-10-25T15:00:00Z" completed={false}
          configured callProvider="zoom" guestToken={mode === 'guest' ? 'fixture-guest-invite' : undefined}
          onRefresh={async () => { window.__refreshes++; }} onBack={() => {}}/>
      </div>);`, resolveDir: fileURLToPath(new URL('../', import.meta.url)), loader: 'tsx' },
    bundle: true, write: false, format: 'esm', jsx: 'automatic', loader: { '.css': 'empty', '.svg': 'dataurl' },
    plugins: [{ name: 'daily-import-guard', setup(builder) {
      builder.onResolve({ filter: /^@daily-co\/daily-js$/ }, () => ({ path: 'daily-guard', namespace: 'fixture' }));
      builder.onLoad({ filter: /.*/, namespace: 'fixture' }, () => ({ contents: 'window.__dailyImports++; throw new Error("Daily imported in Zoom branch"); export default {};', loader: 'js' }));
    } }],
  });
  server = createServer((request, response) => {
    response.setHeader('Content-Type', request.url === '/component.js' ? 'text/javascript' : 'text/html');
    response.end(request.url === '/component.js' ? bundle.outputFiles[0].contents : request.url === '/call/zoom/client.html'
      ? sdkDocument : '<!doctype html><div id="root"></div><script type="module" src="/component.js"></script>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL });
});
after(async () => { await browser?.close(); if (server) await new Promise(resolve => server.close(resolve)); });

function credentials(index = 1, role = 0) {
  return { provider: 'zoom', meetingNumber: '123456789', passWord: 'fixture-only', userName: 'Fixture', role,
    signature: `fixture-signature-${index}`, sdkVersion: '6.10.0', ...(role ? { zak: 'fixture-zak' } : {}) };
}
async function pageFor(t) {
  const page = await browser.newPage();
  page.setDefaultTimeout(5000);
  t.after(() => page.close());
  await page.route('**/*', route => route.request().url().startsWith(origin + '/') ? route.continue() : route.abort());
  return page;
}
const settle = page => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
async function assertNoLegacyMedia(page) {
  assert.deepEqual(await page.evaluate(() => [window.__dailyImports, window.__mediaCalls]), [0, 0]);
  assert.equal(await page.locator('video').count(), 0);
}
async function waitJoined(page) {
  await page.locator('.md-zoom-call[data-phase="joined"]').waitFor();
  assert.equal(await page.frameLocator('iframe').locator('body').getAttribute('data-joined'), 'yes');
}
async function assertNativeOnlyOverlay(page) {
  const overlay = page.getByRole('dialog');
  assert.equal(await overlay.locator('iframe').count(), 1);
  assert.equal(await overlay.locator('.cp-call-bar, .cj-call-bar').count(), 0);
  assert.equal(await overlay.getByRole('button').count(), 0);
  assert.equal(await overlay.getByRole('checkbox').count(), 0);
  assert.equal(await overlay.getByRole('alert').count(), 0);
}
async function enterCandidate(page) {
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: 'Присоединиться', exact: true }).click();
  await waitJoined(page);
}

test('candidate consent gates access; Zoom joins without Daily or outer device capture and keeps credentials out of URLs', async t => {
  const page = await pageFor(t);
  const commands = [], urls = [];
  page.on('request', request => urls.push(request.url()));
  await page.route('**/api/hr/candidate/**', async route => {
    commands.push({ path: new URL(route.request().url()).pathname, body: route.request().postDataJSON() });
    await route.fulfill({ json: credentials() });
  });
  await page.goto(origin);
  assert.equal(await page.getByRole('button', { name: 'Присоединиться', exact: true }).isDisabled(), true);
  assert.equal(commands.length, 0);
  assert.equal(await page.locator('iframe').count(), 0);
  await assertNoLegacyMedia(page);
  await enterCandidate(page);
  await assertNativeOnlyOverlay(page);
  assert.deepEqual(commands, [{ path: '/api/hr/candidate/interview/join', body: { consent: true } }]);
  assert.equal(await page.locator('iframe').getAttribute('src'), '/call/zoom/client.html');
  assert.equal(urls.some(url => /fixture-signature|fixture-only|zak=/.test(url)), false);
  assert.deepEqual(await page.evaluate(() => [Object.keys(localStorage), Object.keys(sessionStorage)]), [[], []]);
  await assertNoLegacyMedia(page);
});

test('guest native leave only disconnects locally and rejoin requests fresh access', async t => {
  const page = await pageFor(t);
  const commands = [];
  await page.route('**/api/hr/candidate/**', async route => {
    commands.push({ path: new URL(route.request().url()).pathname, body: route.request().postDataJSON() });
    await route.fulfill({ json: credentials(commands.length) });
  });
  await page.goto(origin + '/?screen=guest');
  assert.equal(await page.getByRole('checkbox').isChecked(), false);
  assert.equal(await page.getByRole('button', { name: 'Присоединиться', exact: true }).isDisabled(), true);
  assert.equal(commands.length, 0);
  await enterCandidate(page);
  await assertNativeOnlyOverlay(page);
  const refreshesBeforeLeave = await page.evaluate(() => window.__refreshes);
  await page.frameLocator('iframe').getByRole('button', { name: 'Leave fixture' }).click();
  await page.locator('iframe').waitFor({ state: 'detached' });
  assert.equal(await page.evaluate(() => window.__refreshes), refreshesBeforeLeave + 1);
  await page.getByRole('button', { name: 'Присоединиться', exact: true }).click();
  await waitJoined(page);
  assert.equal(commands.length, 2);
  for (const command of commands) assert.deepEqual(command, { path: '/api/hr/candidate/guest/join', body: { token: 'fixture-guest-invite', consent: true } });
  await assertNoLegacyMedia(page);
});

test('a late join response after local cancellation cannot remount the SDK or capture devices', async t => {
  const page = await pageFor(t);
  let release, received;
  const waiting = new Promise(resolve => { release = resolve; });
  const captured = new Promise(resolve => { received = resolve; });
  await page.route('**/api/hr/candidate/interview/join', async route => {
    received(); await waiting; await route.fulfill({ json: credentials() });
  });
  await page.goto(origin);
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: 'Присоединиться', exact: true }).click();
  await captured;
  assert.equal(await page.getByRole('dialog').locator('iframe').count(), 0);
  await page.getByRole('dialog').getByRole('status').getByText('Подключаемся…', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Отменить', exact: true }).click();
  const finished = page.waitForResponse(response => response.url().endsWith('/interview/join'));
  release(); await finished; await settle(page);
  assert.equal(await page.locator('iframe').count(), 0);
  assert.equal(await page.getByRole('button', { name: 'Присоединиться', exact: true }).isEnabled(), true);
  await assertNoLegacyMedia(page);
});

function employerState(provider = 'daily', locked = false) {
  return { mode: 'employer', workspaceId: 'workspace-1', scope: 'sfera', generation: 'fixture-generation', revision: 1,
    actor: 'manager', actorName: 'Fixture Manager', videoConfigured: true, closedBy: null,
    vacancy: { id: 'vacancy-1', company: 'Fixture Company', role: 'Fixture' },
    candidates: { 'candidate-1': { id: 'candidate-1', candidateId: 'candidate-1', applicationId: 'application-1', vacancyId: 'vacancy-1',
      name: 'Fixture Candidate', meeting: { id: 'meeting-1', start: '2026-10-25T15:00:00Z', status: locked ? 'live' : 'confirmed',
        callProvider: provider, callProviderLocked: locked, videoConfigured: true, callProviderOptions: [{ id: 'daily', available: true }, { id: 'zoom', available: true }] } } } };
}

test('employer selector saves exact meeting; bookkeeping errors stay outside the native Zoom overlay and live reconciliation cannot complete it', async t => {
  const page = await pageFor(t);
  let state = employerState(), command;
  const commands = [];
  await page.route('**/api/hr/employer/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() === 'POST') commands.push({ path, body: route.request().postDataJSON() });
    if (path.endsWith('/session')) return route.fulfill({ json: { token: 'fixture-employer-token', state } });
    if (path.endsWith('/call-provider')) {
      command = route.request().postDataJSON(); state = employerState(command.provider);
      return route.fulfill({ json: state });
    }
    if (path.endsWith('/room')) { state = employerState('zoom', true); return route.fulfill({ json: credentials(1, 1) }); }
    if (path.endsWith('/call-connected')) return route.fulfill({ status: 503, json: { error: 'Fixture bookkeeping failure' } });
    if (path.endsWith('/call-reconcile')) return route.fulfill({ json: { completed: false, status: 'live' } });
    return route.fulfill({ json: state });
  });
  await page.goto(origin + '/?screen=employer');
  const selector = page.getByRole('combobox', { name: 'Видеосервис встречи' });
  await selector.selectOption('zoom');
  await page.waitForFunction(() => document.querySelector('select')?.value === 'zoom');
  assert.deepEqual(command, { vacancyId: 'vacancy-1', applicationId: 'application-1', candidate: 'candidate-1',
    meetingId: 'meeting-1', generation: 'fixture-generation', provider: 'zoom' });
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: 'Войти в видеовстречу', exact: true }).click();
  await waitJoined(page);
  await page.locator('.cj-meeting').getByText('Не удалось обновить статус встречи. Разговор продолжается.', { exact: true }).waitFor();
  await assertNativeOnlyOverlay(page);
  await page.waitForFunction(() => document.querySelector('select')?.disabled === true);
  // A native host leave/end report reconciles the exact meeting, never ends it.
  const reconciliation = page.waitForResponse(response => response.url().endsWith('/call-reconcile'));
  await page.frameLocator('iframe').getByRole('button', { name: 'Leave fixture' }).click();
  await reconciliation;
  await page.locator('iframe').waitFor({ state: 'detached' });
  await settle(page);
  assert.deepEqual(commands.find(command => command.path.endsWith('/call-reconcile')),
    { path: '/api/hr/employer/call-reconcile', body: { vacancyId: 'vacancy-1', applicationId: 'application-1',
      candidate: 'candidate-1', meetingId: 'meeting-1', generation: 'fixture-generation' } });
  assert.equal(commands.some(command => command.body?.action === 'meeting.finish'), false);
  assert.ok(commands.filter(command => command.path.endsWith('/call-connected')).length >= 1);
  assert.equal(await page.evaluate(() => window.__completions), 0);
  await assertNoLegacyMedia(page);
});

test('employer retries only session receipt after temporary failure and keeps the exact joined iframe', async t => {
  const page = await pageFor(t);
  let state = employerState('zoom'), receipts = 0, joins = 0;
  await page.route('**/api/hr/employer/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/session')) return route.fulfill({ json: { token: 'fixture-employer-token', state } });
    if (path.endsWith('/room')) { joins++; state = employerState('zoom', true); return route.fulfill({ json: credentials(1, 1) }); }
    if (path.endsWith('/call-connected')) {
      receipts++;
      return route.fulfill({ status: receipts === 1 ? 503 : 200, json: receipts === 1 ? { error: 'Temporary failure' } : state });
    }
    return route.fulfill({ json: state });
  });
  await page.goto(origin + '/?screen=employer');
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: 'Войти в видеовстречу', exact: true }).click();
  await waitJoined(page);
  const pending = page.locator('.cj-meeting').getByText('Не удалось обновить статус встречи. Разговор продолжается.', { exact: true });
  await pending.waitFor();
  await assertNativeOnlyOverlay(page);
  await page.evaluate(() => { window.__joinedFrame = document.querySelector('iframe'); });
  await pending.waitFor({ state: 'detached' });
  assert.equal(receipts, 2);
  assert.equal(joins, 1);
  assert.equal(await page.evaluate(() => window.__joinedFrame === document.querySelector('iframe')), true);
  assert.equal(await page.frameLocator('iframe').locator('body').getAttribute('data-join-count'), '1');
  await assertNoLegacyMedia(page);
});

test('an employer joining with guest credentials never pins or completes the host session', async t => {
  const page = await pageFor(t);
  let state = employerState('zoom');
  const commands = [];
  await page.route('**/api/hr/employer/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() === 'POST') commands.push({ path, body: route.request().postDataJSON() });
    if (path.endsWith('/session')) return route.fulfill({ json: { token: 'fixture-employer-token', state } });
    if (path.endsWith('/room')) { state = employerState('zoom', true); return route.fulfill({ json: credentials(1, 0) }); }
    return route.fulfill({ json: state });
  });
  await page.goto(origin + '/?screen=employer');
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: 'Войти в видеовстречу', exact: true }).click();
  await waitJoined(page);
  await assertNativeOnlyOverlay(page);
  assert.equal(await page.getByRole('button', { name: 'Завершить встречу', exact: true }).count(), 0);
  await page.frameLocator('iframe').getByRole('button', { name: 'Leave fixture' }).click();
  await page.locator('iframe').waitFor({ state: 'detached' });
  assert.equal(await page.getByRole('button', { name: 'Завершить встречу', exact: true }).count(), 0);
  assert.equal(commands.some(command => command.path.endsWith('/call-connected') || command.path.endsWith('/call-reconcile') || command.body?.action === 'meeting.finish'), false);
  assert.equal(await page.evaluate(() => window.__completions), 0);
  await assertNoLegacyMedia(page);
});

test('native host leave polls the exact meeting and completes only after server confirmation', async t => {
  const page = await pageFor(t);
  let state = employerState('zoom'), reconciliations = 0, joins = 0, refreshesAfterCompletion = 0;
  const commands = [];
  await page.route('**/api/hr/employer/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() === 'POST') commands.push({ path, body: route.request().postDataJSON() });
    if (path.endsWith('/session')) return route.fulfill({ json: { token: 'fixture-employer-token', state } });
    if (path.endsWith('/room')) { joins++; state = employerState('zoom', true); return route.fulfill({ json: credentials(1, 1) }); }
    if (path.endsWith('/call-reconcile')) {
      const completed = ++reconciliations === 2;
      if (completed) state.candidates['candidate-1'].meeting.status = 'completed';
      return route.fulfill({ json: { completed, status: completed ? 'ended' : 'live' } });
    }
    if (path.endsWith('/state') && state.candidates['candidate-1'].meeting.status === 'completed') refreshesAfterCompletion++;
    return route.fulfill({ json: state });
  });
  await page.goto(origin + '/?screen=employer');
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: 'Войти в видеовстречу', exact: true }).click();
  await waitJoined(page);
  await assertNativeOnlyOverlay(page);
  const firstReconciliation = page.waitForResponse(response => response.url().endsWith('/call-reconcile'));
  await page.frameLocator('iframe').getByRole('button', { name: 'Leave fixture' }).click();
  await firstReconciliation;
  await page.locator('iframe').waitFor({ state: 'detached' });
  await settle(page);
  assert.equal(reconciliations, 1);
  assert.equal(await page.evaluate(() => window.__completions), 0);
  await page.waitForFunction(() => window.__completions === 1);
  assert.equal(reconciliations, 2);
  assert.equal(joins, 1);
  assert.ok(refreshesAfterCompletion >= 1);
  for (const command of commands.filter(command => command.path.endsWith('/call-reconcile'))) {
    assert.deepEqual(command.body, { vacancyId: 'vacancy-1', applicationId: 'application-1',
      candidate: 'candidate-1', meetingId: 'meeting-1', generation: 'fixture-generation' });
  }
  assert.equal(commands.some(command => command.body?.action === 'meeting.finish'), false);
  await assertNoLegacyMedia(page);
});

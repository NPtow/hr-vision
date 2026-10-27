// Permanent regression for the production employer task workspace. Runs only
// the real component graph in a loopback server, never the Storybook set.
// Uses the same esbuild/Chromium boundary as the Zoom component tests. CSS is
// intentionally excluded: this is an interaction gate, not a visual snapshot.
// All fixture API calls are local; Chromium cannot reach external hosts or media.
// Run: PLAYWRIGHT_MODULE=/absolute/path/to/playwright/index.mjs node --test scripts/employer-task-states.component.test.mjs
import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { readFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE
  ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : 'playwright');
const root = fileURLToPath(new URL('../', import.meta.url));
let browser, server, origin;
const entry = `import React from 'react';
import { createRoot } from 'react-dom/client';
import { ProductionTaskStates } from './src/scenes/employer-task-states/ProductionTaskStates.tsx';
const query = new URLSearchParams(location.search);
createRoot(document.getElementById('root')).render(React.createElement(ProductionTaskStates, {
  scenario: query.get('scenario') || 'completed', provider: query.get('provider') || 'daily'
}));`;

before(async () => {
  const bundle = await build({
    stdin: { contents: entry, resolveDir: root, loader: 'jsx' },
    bundle: true, write: false, format: 'esm', jsx: 'automatic',
    loader: { '.css': 'empty', '.svg': 'dataurl', '.png': 'dataurl', '.jpg': 'dataurl' },
    plugins: [{ name: 'fixture-worker', setup(builder) {
      builder.onResolve({ filter: /\?worker&url$/ }, args => ({ path: args.path, namespace: 'fixture-worker' }));
      builder.onLoad({ filter: /.*/, namespace: 'fixture-worker' }, () => ({ contents: 'export default "/fixture-worker.js"', loader: 'js' }));
      builder.onResolve({ filter: /^@daily-co\/daily-js$/ }, () => ({ path: 'daily-guard', namespace: 'fixture-daily' }));
      builder.onLoad({ filter: /.*/, namespace: 'fixture-daily' }, () => ({ contents: 'window.__taskDailyImports++; throw Error("Unexpected Daily SDK import"); export default {};', loader: 'js' }));
    } }],
  });
  const media = await readFile(new URL('../public/task-meeting-preview/interview.webm', import.meta.url));
  server = createServer((request, response) => {
    const path = new URL(request.url, 'http://fixture').pathname;
    if (path === '/component-entry.js') { response.setHeader('Content-Type', 'text/javascript'); response.end(bundle.outputFiles[0].contents); }
    else if (path === '/task-meeting-preview/interview.webm') { response.setHeader('Content-Type', 'video/webm'); response.end(media); }
    else if (path === '/') { response.setHeader('Content-Type', 'text/html'); response.end('<!doctype html><html lang="ru"><meta charset="UTF-8"><div id="root"></div><script type="module" src="/component-entry.js"></script></html>'); }
    else { response.statusCode = 404; response.end('Fixture resource not found'); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL });
});
after(async () => {
  await browser?.close();
  if (server) await new Promise(resolve => server.close(resolve));
});

async function pageFor(t, scenario = 'completed', provider = 'daily') {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [], leakedApi = [], blockedExternal = [];
  page.setDefaultTimeout(10_000);
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    window.__taskMediaCalls = 0; window.__taskDailyImports = 0; window.__taskViolations = [];
    // The story owns a fetch mock. Audit every replacement as well as real fetch,
    // otherwise an external URL or unknown route could be silently caught by it.
    const audited = implementation => async (input, init) => {
      const url = new URL(input instanceof Request ? input.url : String(input), location.href);
      if (url.origin !== location.origin) window.__taskViolations.push(`external:${url.origin}`);
      const path = url.pathname.replace(/^\/api\/hr\/(employer\/)?/, '');
      if (url.pathname.startsWith('/api/hr/') && !['materials', 'state', 'room', 'action', 'interview-assistant'].includes(path)) window.__taskViolations.push(`unknown:${path}`);
      const result = await implementation(input, init);
      // Make historical fixtures distinguishable. This changes provider data only;
      // every production component still runs unchanged.
      if (path !== 'materials' || new URLSearchParams(location.search).get('scenario') !== 'completed') return result;
      const body = JSON.parse(init?.body || '{}');
      const first = body.meetingId === 'task-preview-completed-meeting';
      const data = await result.json();
      data.recordings = [{ id: `recording:${body.meetingId}`, url: `/task-meeting-preview/interview.webm?meeting=${body.meetingId}` }];
      data.transcripts = data.transcripts.map(item => ({ ...item, text: item.text.replace('Это синтетическая запись для проверки интерфейса.', first ? 'Материал первой встречи.' : 'Материал второй встречи.') }));
      return new Response(JSON.stringify(data), { status: result.status, headers: { 'Content-Type': 'application/json' } });
    };
    let currentFetch = audited(window.fetch.bind(window));
    Object.defineProperty(window, 'fetch', { configurable: true, get: () => currentFetch, set: implementation => { currentFetch = audited(implementation); } });
    navigator.mediaDevices.getUserMedia = () => { window.__taskMediaCalls++; throw Error('Unexpected device capture'); };
  });
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) { blockedExternal.push(url.href); return route.abort(); }
    if (url.pathname.startsWith('/api/hr/')) { leakedApi.push(url.pathname); return route.fulfill({ status: 501, json: { error: 'Unmocked fixture API' } }); }
    return route.continue();
  });
  t.after(async () => {
    const counters = page.isClosed() ? null : await page.evaluate(() => ({ media: window.__taskMediaCalls, daily: window.__taskDailyImports, violations: window.__taskViolations }));
    await page.close();
    assert.deepEqual(errors, [], 'component must not throw');
    assert.deepEqual(leakedApi, [], 'API requests must remain inside the fixture boundary');
    assert.deepEqual(blockedExternal, [], 'components must not attempt external requests');
    assert.deepEqual(counters, { media: 0, daily: 0, violations: [] }, 'the regression must not capture devices, load SDKs or hide invalid API attempts');
  });
  await page.goto(`${origin}/?scenario=${scenario}&provider=${provider}`);
  await page.getByRole('tablist', { name: 'Этапы кандидата', exact: true }).waitFor();
  return page;
}
const button = (page, name) => page.getByRole('button', { name, exact: true });
const accessibleName = name => new RegExp('^' + name.replace(/\s+/g, '\\s*') + '$');
const tab = (page, name) => page.getByRole('tab', { name: accessibleName(name) });
const logged = async page => JSON.parse(await page.getByTestId('qa-requests').textContent());
const actions = async page => (await logged(page)).filter(item => item.path.startsWith('action:'));
const next = async page => { await button(page, 'Добавить следующий этап').click(); await page.getByRole('group', { name: 'Следующий шаг', exact: true }).waitFor(); };
const settled = page => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));

// This specifically fails if historical meetings regress to the legacy player,
// screening examples, a notes-only body, or materials from the latest meeting.
test('historical meetings use the common player, their own transcript and scoped persistent comments', async t => {
  const page = await pageFor(t);
  await tab(page, 'Встреча 14 октября').click();
  await button(page, 'Воспроизвести интервью').waitFor();
  assert.equal(await page.locator('.iv-player').count(), 1, 'use CandidateInterviewPlayer');
  assert.equal(await page.locator('.ir-player').count(), 0, 'legacy meeting player must not replace it');
  await button(page, 'Воспроизвести интервью').click();
  await page.waitForFunction(() => document.querySelector('video')?.currentTime > 0.1);
  await button(page, '00:04 Кандидат: Материал первой встречи.').click();
  await page.waitForFunction(() => { const time = document.querySelector('video')?.currentTime; return time >= 4 && time < 8; });
  assert.equal(await page.locator('video').evaluate(video => video.error), null);
  assert.equal(await page.locator('video').evaluate(video => video.duration), 20);
  assert.ok((await page.locator('video').getAttribute('src')).endsWith('/task-meeting-preview/interview.webm?meeting=task-preview-completed-meeting'));
  await tab(page, 'Комментарии').click();
  await button(page, 'Написать').click();
  await page.getByPlaceholder('Написать комментарий…', { exact: true }).fill('Комментарий только первой встречи');
  await button(page, 'Сохранить комментарий').click();
  await page.getByText('Комментарий только первой встречи', { exact: true }).waitFor();
  const [command] = (await actions(page)).filter(item => item.path === 'action:meeting.comment.add');
  assert.equal(command.body.meetingId, 'task-preview-completed-meeting');
  assert.equal(command.body.recordingId, 'recording:task-preview-completed-meeting');
  assert.ok(command.body.seconds >= 4 && command.body.seconds < 8);
  assert.ok(command.body.requestId);
  await tab(page, 'Встреча 17 октября').click();
  await button(page, '00:04 Кандидат: Материал второй встречи.').waitFor();
  assert.ok((await page.locator('video').getAttribute('src')).endsWith('?meeting=task-preview-completed-second-meeting'));
  await tab(page, 'Комментарии').click();
  assert.equal(await page.getByText('Комментарий только первой встречи', { exact: true }).count(), 0, 'comments must not leak to another meeting');
  await tab(page, 'Встреча 14 октября').click();
  await button(page, '00:04 Кандидат: Материал первой встречи.').waitFor();
  await tab(page, 'Комментарии').click();
  await page.getByText('Комментарий только первой встречи', { exact: true }).waitFor();
  const materialRequests = (await logged(page)).filter(item => item.path.endsWith('/materials'));
  assert.ok(materialRequests.some(item => item.body.meetingId === 'task-preview-completed-meeting'));
  assert.ok(materialRequests.every(item => item.body.candidate === 'qa-task-application' && item.body.generation === 'qa-task-generation'));
});

for (const provider of ['daily', 'zoom']) test(`${provider}: upcoming stays compact and consent gates the room request`, async t => {
  const page = await pageFor(t, 'scheduled', provider);
  const panel = page.getByRole('tabpanel', { name: accessibleName('Предстоящая встреча 27 октября') });
  assert.equal(await panel.getByRole('heading', { name: 'Предстоящая встреча', exact: true }).count(), 1);
  assert.equal(await panel.getByRole('button').count(), 1);
  assert.equal(await panel.getByRole('checkbox').count(), 0);
  assert.equal(await panel.getByText('27 октября в 13:00 МСК', { exact: true }).count(), 1);
  await button(page, 'Подключиться').click();
  const dialog = page.getByRole('dialog', { name: 'Подключение к встрече', exact: true });
  await dialog.waitFor();
  assert.equal(await button(page, 'Продолжить').isDisabled(), true);
  assert.equal((await logged(page)).filter(item => item.path.endsWith('/room')).length, 0);
  await button(page, 'Отмена').click(); await settled(page);
  assert.equal(await button(page, 'Подключиться').evaluate(element => element === document.activeElement), true);
  await button(page, 'Подключиться').click();
  await dialog.getByRole('checkbox').check();
  if (provider === 'zoom') {
    await dialog.getByRole('combobox').selectOption('daily');
    assert.equal(await dialog.getByRole('checkbox').isChecked(), false, 'provider change requires fresh consent');
    assert.equal(await button(page, 'Продолжить').isDisabled(), true);
    await dialog.getByRole('combobox').selectOption('zoom');
    await dialog.getByRole('checkbox').check();
  }
  await button(page, 'Продолжить').click();
  await page.waitForFunction(() => document.querySelector('[data-testid="qa-requests"]')?.textContent.includes('/room'));
  const rooms = (await logged(page)).filter(item => item.path.endsWith('/room'));
  assert.equal(rooms.length, 1);
  assert.equal(rooms[0].body.consent, true);
  assert.equal(rooms[0].body.meetingId, 'task-preview-upcoming-meeting');
  // The room fixture deliberately fails before loading either SDK.
  await page.waitForFunction(() => !document.querySelector('.cj-app[inert]'));
  assert.equal(await page.locator('iframe').count(), 0);
  assert.equal(await page.locator('video').count(), 0);
});

test('next step has exactly three actions and active-stage navigation restores completed materials', async t => {
  const page = await pageFor(t, 'no-offer');
  await next(page);
  assert.deepEqual(await page.getByRole('group', { name: 'Следующий шаг', exact: true }).getByRole('button').allTextContents(), ['Назначить встречу', 'Сделать оффер', 'Отказать']);
  await tab(page, 'Встреча 17 октября').click();
  await button(page, 'Воспроизвести интервью').waitFor();
  await next(page); await button(page, 'Сделать оффер').click();
  await page.getByLabel('Дата выхода', { exact: true }).fill('2099-11-01');
  await button(page, 'Сохранить оффер').click();
  await tab(page, 'Оффер Черновик').waitFor();
  assert.equal((await actions(page)).filter(item => item.path === 'action:offer.save').length, 1);
  assert.equal((await actions(page)).filter(item => item.path === 'action:offer.send').length, 0);
  assert.ok(new URL(page.url()).searchParams.get('stageId').startsWith('offer:'));
});

test('an existing offer opens from next step without creating another draft', async t => {
  const page = await pageFor(t, 'offer');
  await next(page); await button(page, 'Сделать оффер').click();
  await page.getByRole('tabpanel', { name: accessibleName('Оффер 19 октября') }).waitFor();
  assert.equal(await page.locator('form').count(), 0);
  assert.deepEqual(await actions(page), []);
});

test('an active meeting cannot be bypassed by another meeting, offer or rejection', async t => {
  const page = await pageFor(t, 'scheduled');
  for (const action of ['Назначить встречу', 'Сделать оффер', 'Отказать']) {
    await next(page); await button(page, action).click();
    await button(page, 'Подключиться').waitFor();
    assert.equal(await page.locator('form').count(), 0);
  }
  assert.deepEqual(await actions(page), []);
  await page.getByText('Сначала завершите или отмените назначенную встречу.', { exact: true }).waitFor();
});

test('closed history preserves notes, feedback and recorded comments without edit controls', async t => {
  const page = await pageFor(t, 'read-only');
  assert.equal(await button(page, 'Добавить следующий этап').count(), 0);
  await button(page, 'Действия с кандидатом').click();
  await page.getByRole('menuitem', { name: 'Заметки и фидбек', exact: true }).click();
  await page.getByText('Сохранённая заметка: уточнить дату выхода.', { exact: true }).waitFor();
  await page.getByText('Разобрали тестовое. Согласовали ожидания на первые три месяца.', { exact: true }).waitFor();
  assert.equal(await button(page, 'Изменить заметку').count(), 0);
  await button(page, 'К встрече').click(); await tab(page, 'Комментарии').click();
  await page.getByText('Сохранённый комментарий к записи.', { exact: true }).waitFor();
  assert.equal(await button(page, 'Написать').count(), 0);
  assert.equal(await button(page, 'Написать комментарий').count(), 0);
  assert.deepEqual(await actions(page), []);
});

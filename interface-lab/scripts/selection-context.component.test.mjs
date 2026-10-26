// Actual production component graph with loopback-only synthetic API responses.
// Tests state preservation and invalidation, never publishes the fixture UI.
// Prerequisites: npm ci (repository-locked esbuild/React) and an explicitly
// provisioned Playwright module + compatible Chromium; no global packages.
// Run from interface-lab:
// PLAYWRIGHT_MODULE=/absolute/playwright/index.mjs CHROMIUM_EXECUTABLE_PATH=/absolute/chromium node --test scripts/selection-context.component.test.mjs
// Verified: Node 26.7.0, esbuild 0.28.2, Playwright 1.62.1, Chromium 153.0.8010.12.
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
  server = createServer((req, res) => { res.setHeader('Content-Type', req.url.endsWith('.js') ? 'text/javascript' : req.url.endsWith('.css') ? 'text/css' : 'text/html'); res.end(assets.get(req.url.slice(1)) || '<!doctype html><html lang="ru"><meta charset="utf-8"><link rel="stylesheet" href="/component.css"><div id="root"></div><script type="module" src="/component.js"></script>'); });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve)); origin = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({ headless: true, ...(process.env.CHROMIUM_EXECUTABLE_PATH ? { executablePath: process.env.CHROMIUM_EXECUTABLE_PATH } : {}) });
});
after(async () => { await browser?.close(); if (server) await new Promise(resolve => server.close(resolve)); });
const until = async check => { for (let i = 0; i < 100; i++) { if (await check()) return; await new Promise(resolve => setTimeout(resolve, 20)); } assert.fail('Expected UI transition did not complete'); };
async function setup(t) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(5000);
  const state = makeFixture(), errors = [], unexpected = [], requests = [];
  let hold = false, resume;
  let recheck = Promise.resolve();
  const deferRecheck = () => { hold = true; recheck = new Promise(resolve => { resume = resolve; }); };
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url());
    if (url.origin !== origin) { unexpected.push(req.url()); return route.abort(); }
    if (!url.pathname.startsWith('/api/')) return route.continue();
    requests.push(req.method() + ' ' + url.pathname + url.search);
    const body = req.method() === 'POST' ? req.postDataJSON() : null;
    let value = structuredClone(state.request(req.method(), url.pathname + url.search, body));
    // The owner endpoint returns its updated person DTO, not an empty receipt.
    if (req.method() === 'POST' && url.pathname.endsWith('/candidates/update')) value = { candidate: structuredClone(state.people.find(p => p.id === body.candidateId)) };
    if (hold && req.method() === 'GET' && url.pathname.endsWith('/recommendations')) await recheck;
    await route.fulfill({ status: value?.httpStatus ?? 200, json: value });
  });
  page.on('pageerror', error => errors.push(error.message));
  t.after(async () => { resume?.(); await page.close(); assert.deepEqual(errors, []); assert.deepEqual(unexpected, []); });
  await page.goto(origin + '/?tab=vacancies&vacancyId=' + state.vacancy.id);
  await page.getByRole('button', { name: 'Борис Соколов', exact: true }).waitFor();
  const search = page.getByRole('textbox', { name: 'Найти кандидата в вакансии', exact: true });
  const invite = page.getByRole('button', { name: /^Отправить приглашения/ });
  async function chooseAndOpen() {
    await search.fill('Борис');
    await page.getByRole('checkbox', { name: 'Подтвердить неизвестные условия: Борис Соколов', exact: true }).check();
    await page.getByRole('checkbox', { name: 'Пригласить: Борис Соколов', exact: true }).check();
    assert.equal(await invite.isEnabled(), true);
    await page.getByRole('button', { name: 'Борис Соколов', exact: true }).click();
    await page.getByRole('dialog').waitFor();
    await page.getByRole('region', { name: 'Оценка по параметрам' }).waitFor();
  }
  return { page, state, search, invite, requests, chooseAndOpen, deferRecheck, release: () => { hold = false; resume?.(); } };
}

test('directory spelling save keeps the mounted vacancy search, selection and approval', async t => {
  const { page, state, search, invite, requests, chooseAndOpen } = await setup(t);
  await chooseAndOpen();
  const reads = requests.length;
  await page.getByRole('button', { name: 'Изменить Имя', exact: true }).click();
  await page.getByRole('textbox', { name: 'Имя', exact: true }).fill('Борис Соколов новый');
  await page.getByRole('textbox', { name: 'Имя', exact: true }).press('Enter');
  await page.getByRole('heading', { name: 'Борис Соколов новый', exact: true }).waitFor();
  await page.getByRole('button', { name: 'Закрыть карточку', exact: true }).click();
  assert.equal(await search.inputValue(), 'Борис');
  assert.equal(await page.getByRole('checkbox', { name: 'Пригласить: Борис Соколов новый', exact: true }).isChecked(), true);
  assert.equal(await page.getByRole('checkbox', { name: 'Подтвердить неизвестные условия: Борис Соколов новый', exact: true }).isChecked(), true);
  assert.equal(await invite.isEnabled(), true);
  assert.equal(await page.getByRole('button', { name: /^Вакансии/ }).getAttribute('aria-current'), 'page');
  assert.equal(new URL(page.url()).searchParams.get('vacancyId'), state.vacancy.id);
  assert.equal(requests.slice(reads).some(r => r.startsWith('GET ') && /selection\/(candidates\?|vacancies$)/.test(r)), false);
  assert.deepEqual(state.writes.map(w => w.action), ['candidates/update']);
});

test('facts save clears approvals and blocks the previous index before delayed version reread', async t => {
  const { page, state, search, invite, chooseAndOpen, deferRecheck, release, requests } = await setup(t);
  await chooseAndOpen();
  const originalId = state.recommendations.recommendationId;
  await page.getByRole('dialog').locator('.sl-facts > summary').click();
  await page.getByRole('button', { name: 'Изменить условия', exact: true }).click();
  await page.getByRole('listbox', { name: 'География поиска', exact: true }).selectOption('moscow');
  deferRecheck();
  await page.getByRole('button', { name: 'Сохранить условия', exact: true }).click();
  await until(() => requests.filter(r => r.startsWith('GET ') && r.includes('/recommendations')).length >= 2);
  await page.getByRole('button', { name: 'Закрыть карточку', exact: true }).click();
  assert.equal(await search.inputValue(), 'Борис');
  assert.equal(await invite.isDisabled(), true);
  assert.equal(await page.locator('.sl-warning').count(), 1);
  assert.equal(await page.getByRole('button', { name: 'Почему такой балл', exact: true }).count(), 0);
  release();
  await page.getByRole('checkbox', { name: 'Подтвердить неизвестные условия: Борис Соколов', exact: true }).waitFor();
  assert.equal(await page.getByRole('checkbox', { name: 'Подтвердить неизвестные условия: Борис Соколов', exact: true }).isChecked(), false);
  assert.equal(await page.getByRole('checkbox', { name: 'Пригласить: Борис Соколов', exact: true }).isChecked(), false);
  assert.equal(await invite.isDisabled(), true);
  assert.equal(await search.inputValue(), 'Борис');
  assert.equal(state.recommendations.recommendationId, originalId);
  assert.deepEqual(state.writes.map(w => w.action), ['candidates/candidate-2/required-fields']);
});

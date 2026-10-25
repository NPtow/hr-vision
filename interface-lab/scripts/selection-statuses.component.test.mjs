// Real selection component, synthetic local API, no production data or messages.
// Run with PLAYWRIGHT_MODULE=/absolute/path/to/playwright/index.mjs node --test scripts/selection-statuses.component.test.mjs
import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { createServer } from 'node:http';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE
  ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : 'playwright');
const person = (id, name, interviewStatus, telegramConnected, emailConnected, accountActive = false) => ({
  id, name, interviewStatus, telegramConnected, emailConnected, accountActive,
  email: `${id}@example.test`, desiredRole: 'Менеджер', city: 'Москва', about: '',
  revision: id, sourceRevision: id, searching: true, consentReuse: false,
  interviewId: interviewStatus ? 'interview-' + id : null, resumeAvailable: true,
  inCohort: true, canAdd: true, assessment: null, application: null, nomination: null, deliveries: [],
});
const people = [
  person('a', 'Анна', 'completed', true, true),
  person('b', 'Борис', 'completed', false, true, true),
  person('v', 'Вера', 'booked', true, false),
  person('g', 'Глеб', null, false, false),
  person('e', 'Елена', null, true, true, true),
];
let browser, server, origin;
before(async () => {
  const bundle = await build({
    stdin: { contents: `import React from 'react'; import { createRoot } from 'react-dom/client';
      import { SelectionWorkbench } from './src/scenes/selection-workbench/SelectionWorkbench';
      createRoot(document.getElementById('root')).render(<SelectionWorkbench/>);`,
      resolveDir: fileURLToPath(new URL('../', import.meta.url)), loader: 'tsx' },
    bundle: true, write: false, format: 'esm', jsx: 'automatic',
    outfile: 'component.js', loader: { '.svg': 'dataurl' },
  });
  const assets = new Map(bundle.outputFiles.map(file => [file.path.split('/').at(-1), file.contents]));
  server = createServer((req, res) => {
    const file = assets.get(req.url.slice(1));
    res.setHeader('Content-Type', req.url.endsWith('.js') ? 'text/javascript'
      : req.url.endsWith('.css') ? 'text/css' : 'text/html');
    res.end(file || '<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/component.css"><div id="root"></div><script type="module" src="/component.js"></script>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({ headless: true,
    ...(process.env.CHROMIUM_EXECUTABLE_PATH ? { executablePath: process.env.CHROMIUM_EXECUTABLE_PATH } : {}) });
});
after(async () => { await browser?.close(); if (server) await new Promise(resolve => server.close(resolve)); });

async function pageFor(t, viewport = { width: 1280, height: 800 }) {
  const page = await browser.newPage({ viewport });
  page.setDefaultTimeout(5000);
  const unexpected = [], errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => {
    const req = route.request(), url = new URL(req.url());
    const responses = {
      '/api/hr/access': { ready: true }, '/api/hr/session': { token: 'local-fixture-only' },
      '/api/hr/selection/vacancies': { vacancies: [{ id: 'vac-1', title: 'Продажи', company: 'Тест', city: 'Москва', salary: '' }] },
      '/api/hr/selection/candidates': { candidates: people, count: people.length },
    };
    if (url.origin !== origin || (req.method() !== 'GET' && url.pathname !== '/api/hr/session')) {
      unexpected.push(req.method() + ' ' + url.pathname); return route.abort();
    }
    if (url.pathname in responses) return route.fulfill({ json: responses[url.pathname] });
    if (url.pathname.startsWith('/api/')) { unexpected.push(url.pathname); return route.abort(); }
    return route.continue();
  });
  t.after(async () => { await page.close(); assert.deepEqual(errors, []); assert.deepEqual(unexpected, []); });
  await page.goto(origin + '/?tab=candidates');
  await page.getByRole('button', { name: 'Анна', exact: true }).waitFor();
  return page;
}
const names = page => page.locator('tbody .sl-person').allTextContents();
const openFilters = page => page.getByRole('button', { name: /^Фильтры/ }).click();
const choose = (page, name, value) => page.getByRole('group', { name: 'Статусы кандидатов' }).getByLabel(name, { exact: true }).selectOption(value);

test('channel columns remain independent from cabinet activation and scheduled is not completed', async t => {
  const page = await pageFor(t);
  for (const name of ['Интервью', 'Telegram', 'Почта']) assert.equal(await page.getByRole('columnheader', { name, exact: true }).count(), 1);
  assert.equal(await page.getByText('Не активирован', { exact: true }).count(), 0);
  const anna = page.getByRole('row').filter({ has: page.getByRole('button', { name: 'Анна', exact: true }) });
  assert.match(await anna.innerText(), /Проведено\s+Подключён\s+Подключена/);
  await openFilters(page); await choose(page, 'Интервью', 'yes');
  assert.deepEqual(await names(page), ['Анна', 'Борис']);
  await choose(page, 'Интервью', 'no');
  assert.deepEqual(await names(page), ['Вера', 'Глеб', 'Елена']);
});

test('filters combine, clear hidden selection and reset the empty state', async t => {
  const page = await pageFor(t);
  await page.getByRole('checkbox', { name: 'Выбрать: Анна', exact: true }).check();
  assert.equal(await page.getByRole('button', { name: 'Добавить · 1', exact: true }).isEnabled(), true);
  await openFilters(page); await choose(page, 'Интервью', 'yes'); await choose(page, 'Telegram', 'no'); await choose(page, 'Почта', 'yes');
  assert.deepEqual(await names(page), ['Борис']);
  assert.equal(await page.getByRole('button', { name: 'Добавить', exact: true }).isEnabled(), false);
  await choose(page, 'Почта', 'no');
  await page.getByRole('heading', { name: 'Ничего не найдено' }).waitFor();
  await page.getByRole('button', { name: 'Показать всех' }).click();
  assert.deepEqual(await names(page), people.map(p => p.name));
  assert.equal(await page.getByRole('button', { name: 'Сбросить', exact: true }).count(), 0);
});

test('narrow layout keeps filters usable and horizontal scrolling inside the table', async t => {
  const page = await pageFor(t, { width: 390, height: 844 });
  await openFilters(page); await choose(page, 'Telegram', 'yes'); await choose(page, 'Почта', 'yes');
  assert.deepEqual(await names(page), ['Анна', 'Елена']);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.getByRole('button', { name: 'Сбросить', exact: true }).click();
  await openFilters(page);
  assert.equal(await page.getByRole('group', { name: 'Статусы кандидатов' }).count(), 0);
});

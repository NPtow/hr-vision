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
people[1].workflow = { interview: { state: 'closed' }, resume: { state: 'available' },
  assessment: { state: 'saved' }, match: { state: 'comparison_saved' } };
const coreRun = {
  run_id: 'fixture-new-core', status: 'completed', error: null,
  corpus_revision: 'fixture-corpus', rubric_version: 'demo-v1', model: 'jev-1.14.0',
  assessment: { run_id: 'fixture-new-core', candidate_id: 'v', contract_version: 'full-corpus-level-choice-v1',
    corpus_sha256: 'fixture-corpus', rubric_id: 'demo', rubric_version: 'demo-v1', rubric_sha256: 'fixture-rubric',
    model: 'jev-1.14.0', chunk_count: 3, usage: {}, sources: [
      { source_id: 'cv', version: '1', kind: 'cv', sha256: 'cv-hash' },
      { source_id: 'interview-part-1', version: '1', kind: 'interview', sha256: 'part-1-hash' },
      { source_id: 'interview-part-2', version: '1', kind: 'interview', sha256: 'part-2-hash' },
    ], parameters: [
      { parameter_id: 'objection', title: 'Работа с возражениями', status: 'assessed', selected_level_id: 'L3',
        levels: [{ level_id: 'L1', label: 'Ответ без уточнения', description: 'Описание первого уровня', probability: .1 },
          { level_id: 'L2', label: 'Уточнение и ответ', description: 'Описание второго уровня', probability: .2 },
          { level_id: 'L3', label: 'Проверка результата', description: 'Описание третьего уровня', probability: .6 }],
        insufficient_data_probability: .1, model_confidence: .47 },
      { parameter_id: 'coaching', title: 'Развитие сотрудников', status: 'insufficient_data', selected_level_id: null,
        levels: [{ level_id: 'L1', label: 'Общий совет', description: 'Описание первого уровня', probability: .1 },
          { level_id: 'L2', label: 'Разбор затруднений', description: 'Описание второго уровня', probability: .2 }],
        insufficient_data_probability: .7, model_confidence: .6 },
    ] },
};
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
    for (const p of people) responses['/api/hr/selection/candidates/' + p.id + '/readiness'] = {
      candidateId: p.id,
      materials: { state: p.id === 'a' ? 'processing' : 'unavailable', recordings: p.id === 'a' ? 2 : null, transcripts: p.id === 'a' ? 0 : null, items: [] },
      assessment: { state: p.id === 'a' ? 'waiting_sources' : p.id === 'b' ? 'stale' : 'not_started', result: null,
        match: { state: p.id === 'b' ? 'stale' : 'not_compared' } },
    };
    responses['/api/hr/selection/candidates/e/readiness'].assessment = {
      state: 'insufficient_data', match: { state: 'compared' },
      result: { revisionId: 'fixture-profile', feedback: [], criteria: [{ id: 'discovery', label: 'Диагностика',
        state: 'unknown', level: null, target: null, summary: '', evidence: [], missingReason: 'Нет примера из интервью' }],
        vacancyMatch: { revisionId: 'fixture-comparison', requiresReview: true,
          criteria: [{ id: 'complex-sales', label: 'Сложные продажи', status: 'clarify' }] } },
    };
    responses['/api/hr/selection/candidates/v/readiness'].assessment = {
      state: 'ready', result: null, coreRun,
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

test('closed room is distinct from a conducted interview and linked channels are independent', async t => {
  const page = await pageFor(t);
  for (const name of ['Интервью', 'Telegram', 'Почта']) assert.equal(await page.getByRole('columnheader', { name, exact: true }).count(), 1);
  assert.equal(await page.getByText('Не активирован', { exact: true }).count(), 0);
  const anna = page.getByRole('row').filter({ has: page.getByRole('button', { name: 'Анна', exact: true }) });
  assert.match(await anna.innerText(), /Встреча закрыта\s+Подключён\s+Подключена/);
  assert.equal(await page.getByText('Проведено', { exact: true }).count(), 0);
  await openFilters(page); await choose(page, 'Интервью', 'yes');
  assert.deepEqual(await names(page), ['Анна', 'Борис']);
  await choose(page, 'Интервью', 'no');
  assert.deepEqual(await names(page), ['Вера', 'Глеб', 'Елена']);
});

test('selection is the same people, with assessment and vacancy comparison kept separate', async t => {
  const page = await pageFor(t);
  await page.getByRole('button', { name: /^Подборка/ }).click();
  assert.deepEqual(await names(page), people.map(p => p.name));
  assert.equal(await page.getByRole('columnheader', { name: 'Оценка навыков', exact: true }).count(), 1);
  assert.equal(await page.getByRole('columnheader', { name: 'По вакансии', exact: true }).count(), 1);
  assert.equal(await page.getByText('Не сравнивали', { exact: true }).count(), 4);
  assert.equal(await page.getByText('Сравнение сохранено', { exact: true }).count(), 1);
  assert.equal(await page.getByText('Нет рекомендации', { exact: true }).count(), 0);
  await openFilters(page);
  await choose(page, 'Оценка навыков', 'not_started');
  assert.deepEqual(await names(page), ['Анна', 'Вера', 'Глеб', 'Елена']);
});

test('opening details verifies metadata only; source failure is never zero and stale scores are hidden', async t => {
  const page = await pageFor(t);
  await page.getByRole('button', { name: 'Анна', exact: true }).click();
  const drawer = page.getByRole('dialog');
  await drawer.getByText('Обрабатываются', { exact: true }).waitFor();
  assert.match(await drawer.innerText(), /Записей в каталоге: 2 · основных текстов: 0/);
  assert.match(await drawer.innerText(), /Это не подтверждает участие/);
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: 'Борис', exact: true }).click();
  await drawer.getByText('Результат устарел', { exact: true }).waitFor();
  assert.equal(await drawer.getByText('Сравнение устарело', { exact: true }).count(), 1);
  assert.equal(await drawer.getByText('Сравнение сохранено', { exact: true }).count(), 0);
  assert.equal(await drawer.getByText('Не удалось проверить', { exact: true }).count(), 1);
  assert.doesNotMatch(await drawer.innerText(), /Записей в каталоге: 0/);
  assert.equal(await drawer.getByRole('region', { name: 'Сохранённая оценка навыков' }).count(), 0);
  await drawer.getByRole('button', { name: 'Проверить актуальность', exact: true }).click();
  await drawer.getByText('Результат устарел', { exact: true }).waitFor();
  // pageFor rejects every mutation except the local test session.
});

test('insufficient evidence stays unscored and vacancy requirements are shown separately', async t => {
  const page = await pageFor(t);
  await page.getByRole('button', { name: 'Елена', exact: true }).click();
  const drawer = page.getByRole('dialog');
  await drawer.getByText('Недостаточно данных', { exact: true }).waitFor();
  assert.match(await drawer.innerText(), /Это не нулевой балл/);
  const criterion = drawer.getByRole('row').filter({ hasText: 'Диагностика' });
  assert.deepEqual(await criterion.getByRole('cell').allTextContents(), ['Диагностика', '—', 'Нет примера из интервью']);
  assert.equal(await drawer.getByText('Сравнение готово', { exact: true }).count(), 1);
  assert.match(await drawer.innerText(), /Сложные продажи\s+Нужно уточнить/);
});

test('new-core choices retain own levels and every probability without a derived score', async t => {
  const page = await pageFor(t);
  await page.getByRole('button', { name: 'Вера', exact: true }).click();
  const view = page.getByRole('region', { name: 'Оценка по параметрам', exact: true });
  await view.getByRole('heading', { name: 'Работа с возражениями' }).waitFor();
  assert.equal(await view.locator('.fc-level').allTextContents().then(JSON.stringify), JSON.stringify(['Проверка результата', 'Недостаточно данных']));
  await view.locator('summary').filter({ hasText: 'Вероятности уровней' }).first().click();
  assert.deepEqual(await view.locator('.fc-parameter').first().locator('dd').allTextContents(), ['10 %', '20 %', '60 %', '10 %']);
  assert.equal(await page.getByRole('region', { name: 'Сохранённая оценка навыков' }).count(), 0);
  await view.locator('summary').filter({ hasText: 'Версия и состав материалов' }).click();
  assert.match(await view.innerText(), /Резюме: 1 · частей интервью: 2/);
  assert.doesNotMatch(await view.innerText(), /Итоговый балл|Оценка: 0/);
});

test('new-core view rejects mismatched candidate identity and corpus revision', async t => {
  const page = await pageFor(t);
  for (const patch of [{ candidate_id: 'another-person' }, { corpus_sha256: 'another-corpus' }]) {
    await page.route('**/api/hr/selection/candidates/v/readiness*', route => route.fulfill({ json: {
      candidateId: 'v', materials: { state: 'ready', recordings: 2, transcripts: 2, items: [] },
      assessment: { state: 'ready', result: null, coreRun: { ...coreRun, assessment: { ...coreRun.assessment, ...patch } } },
    } }));
    await page.getByRole('button', { name: 'Вера', exact: true }).click();
    await page.getByRole('alert').filter({ hasText: 'Не удалось подтвердить версию оценки' }).waitFor();
    assert.equal(await page.getByRole('region', { name: 'Оценка по параметрам' }).count(), 0);
    await page.keyboard.press('Escape');
  }
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

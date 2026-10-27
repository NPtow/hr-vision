// Isolated browser checks of the actual selection UI. Synthetic service output;
// no real data, mail/TG, model calls, or production APIs.
import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { createServer } from 'node:http';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
import { makeFixture } from './selection-vacancy.fixtures.mjs';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : 'playwright');
let browser, server, origin;
before(async () => {
  const bundle = await build({ stdin: { contents: `import React from 'react';import {createRoot} from 'react-dom/client';import {SelectionWorkbench} from './src/scenes/selection-workbench/SelectionWorkbench';createRoot(document.getElementById('root')).render(<SelectionWorkbench/>);`, resolveDir: fileURLToPath(new URL('../', import.meta.url)), loader: 'tsx' }, bundle: true, write: false, format: 'esm', jsx: 'automatic', outfile: 'component.js', loader: { '.svg': 'dataurl' } });
  const assets = new Map(bundle.outputFiles.map(file => [file.path.split('/').at(-1), file.contents]));
  server = createServer((req, res) => { const file = assets.get(req.url.slice(1));res.setHeader('Content-Type', req.url.endsWith('.js') ? 'text/javascript' : req.url.endsWith('.css') ? 'text/css' : 'text/html');res.end(file || '<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/component.css"><div id="root"></div><script type="module" src="/component.js"></script>'); });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve)); origin = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({ headless: true, ...(process.env.CHROMIUM_EXECUTABLE_PATH ? { executablePath: process.env.CHROMIUM_EXECUTABLE_PATH } : {}) });
});
after(async () => { await browser?.close(); if (server) await new Promise(resolve => server.close(resolve)); });
async function setup(t, { viewport = { width: 1440, height: 1000 }, state = makeFixture(), query = '?tab=candidates' } = {}) {
  const page = await browser.newPage({ viewport }), errors = [], unexpected = [];
  page.setDefaultTimeout(6500); page.on('pageerror', err => errors.push(err.message));
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url());
    if (url.origin !== origin) { unexpected.push(req.method() + ' ' + url.origin); return route.abort(); }
    if (!url.pathname.startsWith('/api/')) return route.continue();
    const value = state.request(req.method(), url.pathname + url.search, req.method() === 'POST' ? req.postDataJSON() : null);
    if (state.delay && req.method() === 'POST' && url.pathname.endsWith('/publish')) await new Promise(resolve => setTimeout(resolve, state.delay));
    return route.fulfill({ status: value?.httpStatus ?? 200, json: value });
  });
  t.after(async () => { await page.close(); assert.deepEqual(errors, []); assert.deepEqual(unexpected, []); });
  await page.goto(origin + '/' + query);
  await page.getByRole('button', { name: /^Кандидаты/ }).waitFor();
  await page.getByRole('button', { name: 'Анна Романова', exact: true }).waitFor();
  return { page, state };
}
const names = p => p.locator('tbody .sl-person').allTextContents();
const vacancy = async p => { await p.getByRole('button', { name: /^Вакансии/ }).click(); await p.getByRole('button', { name: 'Открыть вакансию: Менеджер по продажам' }).click(); await p.getByRole('button', { name: 'Борис Соколов', exact: true }).waitFor(); };
const invite = p => p.getByRole('button', { name: /^Отправить приглашения/ });
const tick = async () => new Promise(resolve => setTimeout(resolve, 50));

test('default is a real HR interview: recording AND complete primary transcript, never closed room or employer meeting', async t => {
  const { page, state } = await setup(t);
  assert.deepEqual(await names(page), state.people.slice(0, 6).map(p => p.name));
  assert.equal(await page.getByLabel('HR-интервью', { exact: true }).inputValue(), 'ready');
  assert.equal(await page.getByRole('button', { name: /^Подборка/ }).count(), 0);
  assert.equal(await invite(page).count(), 0);
  await page.getByLabel('HR-интервью', { exact: true }).selectOption('all');
  assert.equal((await names(page)).length, 12);
  await page.getByRole('button', { name: 'По умолчанию', exact: true }).click();
  assert.equal((await names(page)).length, 6);
  assert.deepEqual(state.writes, []);
});

test('assessment opens in Candidates, preserves raw probabilities and insufficient data', async t => {
  const { page, state } = await setup(t);
  await page.getByRole('button', { name: 'Открыть оценку: Анна Романова' }).click();
  await page.getByRole('region', { name: 'Оценка по параметрам' }).waitFor();
  await page.locator('.fc-parameter summary').first().click();
  assert.match(await page.locator('.fc-parameter').first().innerText(), /20\s?%/);
  assert.equal(await page.getByText('По выбранной вакансии', { exact: true }).count(), 0);
  await page.getByRole('button', { name: 'Закрыть карточку' }).click();
  await page.getByRole('button', { name: 'Дарья Орлова', exact: true }).click();
  await page.locator('.fc-level').first().waitFor();
  assert.equal(await page.locator('.fc-level').first().innerText(), 'Недостаточно данных');
  assert.deepEqual(state.writes, []);
});

test('Telegram, mail and interview filters combine and reset to the HR-interview baseline', async t => {
  const { page } = await setup(t);
  await page.getByLabel('Telegram', { exact: true }).selectOption('no');
  await page.getByLabel('Почта', { exact: true }).selectOption('no');
  assert.deepEqual(await names(page), ['Елена Иванова']);
  await page.getByRole('button', { name: 'По умолчанию' }).click();
  assert.equal((await names(page)).length, 6);
  await page.getByLabel('HR-интервью', { exact: true }).selectOption('unknown');
  assert.deepEqual(await names(page), ['Закрытая комната', 'Статус неизвестен']);
});

test('vacancy uses engine order, excludes hard failures and leaves pending/unscored without a fake rank', async t => {
  const { page, state } = await setup(t); await vacancy(page);
  assert.deepEqual(await names(page), ['Борис Соколов', 'Анна Романова', 'Елена Иванова', 'Дарья Орлова', 'Фёдор Смирнов']);
  assert.match(await page.locator('tr').filter({ has: page.getByRole('button', { name: 'Фёдор Смирнов', exact: true }) }).innerText(), /Ожидает оценки|Ещё не рассчитано/);
  assert.doesNotMatch(await page.locator('tr').filter({ has: page.getByRole('button', { name: 'Дарья Орлова', exact: true }) }).innerText(), /Место/);
  await page.getByRole('button', { name: 'Расчёт', exact: true }).first().click();
  await page.locator('.sl-match-explanation details').first().locator('summary').click();
  const text = await page.locator('.sl-match-explanation').innerText();
  assert.match(text, /30\s?%/); assert.match(text, /33,3\s?%/); assert.match(text, /Индекс.*\+0,508/);
  assert.match(text, /Учтено 2 навыков/); assert.match(text, /а не вероятность найма/);
  assert.deepEqual(state.writes, []);
  await page.getByRole('button', { name: 'Анна Романова', exact: true }).click();
  await page.getByRole('dialog').waitFor();
  assert.equal(await page.getByRole('button', { name: /^Кандидаты/ }).getAttribute('aria-current'), 'page');
});

test('send requires an explicit unknown-conditions checkbox and uses only the current recommendation', async t => {
  const { page, state } = await setup(t); await vacancy(page);
  const boris = page.getByRole('checkbox', { name: 'Пригласить: Борис Соколов', exact: true });
  assert.equal(await boris.isEnabled(), false);
  assert.equal(await page.getByRole('checkbox', { name: 'Пригласить: Елена Иванова' }).isEnabled(), false);
  await page.getByRole('checkbox', { name: 'Подтвердить неизвестные условия: Борис Соколов' }).check();
  await boris.check();
  state.delay = 150;
  await invite(page).dblclick({ delay: 10 }).catch(() => {});
  await page.getByText(/Заявки приняты: 1/).waitFor();
  assert.equal(state.writes.length, 1);
  const body = state.writes[0].body;
  assert.deepEqual(body.candidateIds, ['candidate-2']); assert.deepEqual(body.unknownApprovals, ['candidate-2']);
  assert.equal(body.recommendationId, state.recommendations.recommendationId);
  assert.equal(body.revision, state.vacancy.contentHash); assert.ok(body.requestId);
  assert.match(await page.getByRole('status').innerText(), /после согласия/);
});

test('lost publish response retains requestId for an explicit retry; search cannot send hidden selections', async t => {
  const { page, state } = await setup(t); await vacancy(page);
  await page.getByRole('checkbox', { name: 'Пригласить: Анна Романова' }).check();
  await page.getByLabel('Найти кандидата в вакансии').fill('Борис');
  assert.equal(await invite(page).isEnabled(), false);
  await page.getByLabel('Найти кандидата в вакансии').fill('');
  await page.getByRole('checkbox', { name: 'Пригласить: Анна Романова' }).check();
  state.failPublishOnce = true;
  await invite(page).click(); await page.getByRole('alert').waitFor();
  assert.equal(await invite(page).isEnabled(), false);
  await page.getByRole('button', { name: 'Обновить', exact: true }).click();
  await page.getByRole('checkbox', { name: 'Пригласить: Анна Романова' }).waitFor();
  await invite(page).click(); await page.getByText(/Заявки приняты: 1/).waitFor();
  assert.equal(state.writes.length, 2); assert.equal(state.writes[0].body.requestId, state.writes[1].body.requestId);
});

test('stale or mismatched result cannot enable invitations or show old score as current', async t => {
  const state = makeFixture(); state.recommendations.stale = true;
  const { page } = await setup(t, { state }); await vacancy(page);
  assert.equal(await page.getByRole('checkbox', { name: 'Пригласить: Анна Романова' }).isEnabled(), false);
  assert.equal(await page.getByRole('button', { name: 'Расчёт', exact: true }).count(), 0);
  assert.match(await page.getByRole('status').innerText(), /Прежние баллы/);
  state.recommendations.stale = false; state.recommendations.result.vacancy_id = 'other-vacancy';
  await page.getByRole('button', { name: 'Обновить', exact: true }).click();
  await page.getByRole('alert').waitFor(); assert.match(await page.getByRole('alert').innerText(), /этой вакансии/);
  assert.equal(await invite(page).isEnabled(), false); assert.deepEqual(state.writes, []);
});

test('requirements editor preserves unknown versus zero and versions the explicit correction', async t => {
  const { page, state } = await setup(t); await vacancy(page);
  await page.locator('.sl-requirements > summary').click();
  await page.getByRole('button', { name: 'Изменить требования', exact: true }).click();
  await page.getByLabel('Важность: Работа с возражениями').selectOption('0');
  await page.getByLabel('Важность: Диагностика потребностей').selectOption('insufficient_data');
  await page.getByRole('button', { name: 'Сохранить требования' }).click();
  await page.getByRole('button', { name: 'Изменить требования' }).waitFor();
  assert.equal(state.writes.length, 1);
  assert.deepEqual(state.writes[0].body.changes.map(c => c.importance), [0, 'insufficient_data']);
  assert.match(state.writes[0].action, /profile\/revisions$/); assert.equal(state.writes[0].body.profileId, 'a'.repeat(64));
  assert.equal(await invite(page).isEnabled(), false);
});

test('candidate facts do not infer work location from residence or insert an invented zero experience', async t => {
  const { page, state } = await setup(t);
  await page.getByRole('button', { name: 'Анна Романова', exact: true }).click();
  await page.locator('.sl-facts > summary').click();
  await page.getByRole('button', { name: 'Изменить условия' }).click();
  await page.getByLabel('Добавить отрасль', { exact: true }).selectOption('logistics');
  assert.equal(await page.getByRole('button', { name: 'Добавить опыт', exact: true }).isEnabled(), false);
  assert.equal(await page.getByRole('button', { name: 'Сохранить условия' }).isEnabled(), false);
  await page.getByLabel('Подтверждённый стаж, месяцев').fill('12');
  await page.getByRole('button', { name: 'Добавить опыт', exact: true }).click();
  await page.getByRole('button', { name: 'Сохранить условия' }).click();
  await page.getByRole('button', { name: 'Изменить условия' }).waitFor();
  assert.equal(state.writes.length, 1); assert.deepEqual(state.writes[0].body.fields, { work_locations: null, employment_types: null, industry_months: { logistics: 12 } });
});

test('service unavailable is not zero matches, does not start models or delivery', async t => {
  const state = makeFixture(); state.responses.set('/api/hr/selection/vacancies/vacancy-1/recommendations', { httpStatus: 503, error: 'Сервис временно недоступен' });
  const { page } = await setup(t, { state });
  await page.getByRole('button', { name: /^Вакансии/ }).click(); await page.getByRole('button', { name: 'Открыть вакансию: Менеджер по продажам' }).click();
  await page.getByRole('alert').waitFor(); assert.match(await page.getByRole('alert').innerText(), /недоступен/);
  assert.equal(await invite(page).isEnabled(), false); assert.deepEqual(state.writes, []);
});

test('legacy assessments URL resolves to Candidates and read does not start calculation', async t => {
  const { page, state } = await setup(t, { query: '?tab=assessments&vacancyId=vacancy-1' });
  assert.equal(new URL(page.url()).searchParams.get('tab'), 'candidates');
  assert.equal(new URL(page.url()).searchParams.has('vacancyId'), false); assert.deepEqual(state.writes, []);
});

test('390px layout keeps the page contained and keyboard can leave the candidate dialog', async t => {
  const { page } = await setup(t, { viewport: { width: 390, height: 844 } });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.getByRole('button', { name: 'Анна Романова', exact: true }).click();
  await page.locator('.fc-parameter').first().waitFor();
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.keyboard.press('Escape'); await tick(); assert.equal(await page.getByRole('dialog').count(), 0);
  await vacancy(page); assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
});


test('pending verification is shown honestly; acceptance does not imply delivery or employer access', async t => {
  const state = makeFixture(); state.pendingVerificationMode = true;
  const { page } = await setup(t, { state }); await vacancy(page);
  await page.getByRole('checkbox', { name: 'Пригласить: Анна Романова' }).check();
  await invite(page).click(); await page.getByText('Проверяется перед передачей', { exact: true }).waitFor();
  assert.match(await page.locator('.sl-notice').innerText(), /На проверке перед передачей: 1/);
  assert.doesNotMatch(await page.locator('.sl-notice').innerText(), /Письмо отправлено|Приглашения отправлены/);
  assert.equal(state.writes.length, 1);
});

test('failed verification can only be retried explicitly against the saved recommendation', async t => {
  const state = makeFixture(); state.people[0].nomination = { id: 'nom-1', state: 'verification_failed', error: 'source_unavailable', attempts: 5, retryAt: 0 };
  state.recommendations.invitationEligibility[0].canInvite = false;
  const { page } = await setup(t, { state }); await vacancy(page);
  await page.getByText('Проверка не завершена', { exact: true }).waitFor(); assert.deepEqual(state.writes, []);
  await page.getByRole('button', { name: 'Повторить проверку', exact: true }).click();
  await page.getByText('Проверяется перед передачей', { exact: true }).waitFor();
  assert.deepEqual(state.writes, [{ action: 'nominations/nom-1/retry', body: { revision: state.vacancy.contentHash, recommendationId: state.recommendations.recommendationId } }]);
});

// Node timers + React server rendering only. No browser or live service calls.
import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { makeFixture } from './selection-vacancy.fixtures.mjs';

const folder = await mkdtemp(join(tmpdir(), 'selection-automation-'));
after(() => rm(folder, { recursive: true, force: true }));
const built = await build({ stdin: { contents: `
export * from './src/scenes/selection-workbench/assessment-automation';
export * from './src/scenes/selection-workbench/readiness-poll';
export * from './src/scenes/selection-workbench/review-state';
export { FieldConflicts } from './src/scenes/selection-workbench/RequiredFieldsEditor';
export * from './src/scenes/selection-workbench/required-fields-values';
export { recommendationCandidateIds } from './src/scenes/selection-workbench/directory';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
export const render = (component, props) => renderToStaticMarkup(createElement(component, props));
`, resolveDir: fileURLToPath(new URL('../', import.meta.url)), loader: 'tsx' }, bundle: true, platform: 'node', format: 'cjs', write: false, jsx: 'automatic', loader: { '.css': 'empty' } });
const bundle = join(folder, 'components.cjs');
await writeFile(bundle, built.outputFiles[0].contents);
const ui = createRequire(import.meta.url)(bundle);
const settle = () => new Promise(resolve => setImmediate(resolve));
const fixture = makeFixture();
const readiness = (automation, assessment = { state: 'not_started', result: null }) => ({
  candidateId: 'candidate-1', hrInterview: fixture.people[0].hrInterview,
  materials: { state: 'ready', recordings: 1, transcripts: 1, items: [] }, assessment,
  ...(automation ? { automation } : {}),
});
function observe(t, response, candidateId = 'candidate-1') {
  const calls = [], values = [], errors = []; let exhausted = 0;
  t.mock.timers.enable({ apis: ['setTimeout'] });
  t.mock.method(globalThis, 'fetch', async (input, init) => {
    calls.push({ input, init });
    assert.equal(input, '/api/hr/selection/candidates/' + encodeURIComponent(candidateId) + '/readiness');
    assert.equal(init.method, 'GET'); assert.equal(init.body, undefined);
    assert.equal(init.headers.Authorization, 'Bearer synthetic-only');
    const value = await response(calls.length);
    return value instanceof Response ? value : Response.json(value);
  });
  const start = () => ui.startReadinessPolling(candidateId, 'synthetic-only', {
    onValue: value => values.push(value), onError: cause => errors.push(cause), onExhausted: () => exhausted++,
  });
  const stop = start(); t.after(stop);
  return { calls, values, errors, start, stop, get exhausted() { return exhausted; } };
}

test('explicit assessment entry opens the assessment independently of a not-compared vacancy state', () => {
  const person = fixture.people[0];
  const markup = ui.render(ui.AssessmentEntry, { person, onOpen() {} });
  assert.match(markup, /Открыть оценку: Анна Романова/);
  assert.match(markup, />Открыть оценку<\/button>/);
  assert.match(markup, /Готово/); assert.doesNotMatch(markup, /Не сравнивали/);
});

test('every public automation state is explicit for absent results, without using raw automaticState', () => {
  for (const [state, label] of Object.entries(ui.automationLabels)) {
    const person = structuredClone(fixture.people[5]);
    person.workflow.automation = { state, automaticState: 'raw-submitted', errorCode: 'internal-secret-code' };
    const markup = ui.render(ui.AssessmentEntry, { person, onOpen() {} });
    assert.ok(markup.includes(label), state);
    assert.doesNotMatch(markup, /raw-submitted|internal-secret-code/);
    assert.equal(person.workflow.assessment.state, 'not_started');
  }
});

test('ready assessment is authoritative and missing automation preserves old adapter behavior', () => {
  assert.equal(ui.assessmentStatus('ready', { state: 'queued' }), 'ready');
  assert.equal(ui.assessmentStatus('stale', { state: 'ready' }), 'stale');
  assert.equal(ui.assessmentStatus('not_started'), 'not_started');
  const html = ui.render(ui.ReviewState, { person: fixture.people[0], readiness: readiness({ state: 'queued' }), loading: false, error: '' });
  assert.match(html, /В очереди/); assert.doesNotMatch(html, /Не рассчитано/);
});

test('a ready full-corpus result keeps all 16 parameters and raw probabilities during delayed automation', () => {
  const assessment = structuredClone(fixture.assessment['candidate-1']);
  const original = assessment.coreRun.assessment.parameters;
  assessment.coreRun.assessment.parameters = Array.from({ length: 16 }, (_, i) => ({ ...original[i % original.length], parameter_id: 'param-' + i, title: 'Параметр ' + (i + 1) }));
  const html = ui.render(ui.ReviewState, { person: fixture.people[0], readiness: readiness({ state: 'processing_delayed' }, assessment), loading: false, error: '' });
  assert.equal((html.match(/class="fc-parameter"/g) ?? []).length, 16);
  assert.match(html, /Параметр 16/); assert.match(html, /20\s?%/);
  assert.match(html, /Расчёт завершён/); assert.match(html, /Ожидает результат/);
});

test('HR-ready candidates remain in the pool before assessment or automation enrollment', () => {
  const people = ['not_enrolled', 'queued', 'dependency_unavailable', 'error'].map((state, i) => ({ ...fixture.people[5], id: 'ready-' + i, workflow: { ...fixture.people[5].workflow, automation: { state } } }));
  people.push({ ...people[0], id: 'text-only', hrInterview: { ...people[0].hrInterview, recordingCount: 0 } });
  people.push({ ...people[0], id: 'recording-only', hrInterview: { ...people[0].hrInterview, transcriptCount: 0 } });
  people.push({ ...people[0], id: 'partial', hrInterview: { ...people[0].hrInterview, allPartsReady: false } });
  assert.deepEqual(ui.recommendationCandidateIds(people), ['ready-0', 'ready-1', 'ready-2', 'ready-3']);
});

test('queued automation without coreRun refreshes with GET and stops at a completed result', async t => {
  const responses = [readiness({ state: 'queued' }), readiness({ state: 'dispatching' }), readiness({ state: 'processing' }), readiness({ state: 'ready' }, fixture.assessment['candidate-1'])];
  const run = observe(t, n => responses[n - 1]); await settle();
  for (const delay of ui.READINESS_POLL_DELAYS.slice(0, 3)) { t.mock.timers.tick(delay); await settle(); }
  t.mock.timers.tick(300000); await settle();
  assert.equal(run.calls.length, 4); assert.equal(run.values.length, 4);
  assert.equal(run.exhausted, 0); assert.deepEqual(run.errors, []);
});

test('persistent pending state exhausts a finite GET budget; only an explicit check restarts it', async t => {
  const run = observe(t, () => readiness({ state: 'dependency_unavailable' })); await settle();
  for (const delay of ui.READINESS_POLL_DELAYS) { t.mock.timers.tick(delay); await settle(); }
  assert.equal(run.calls.length, 6); assert.equal(run.exhausted, 1);
  t.mock.timers.tick(300000); await settle(); assert.equal(run.calls.length, 6);
  run.stop(); const secondStop = run.start(); await settle(); secondStop();
  assert.equal(run.calls.length, 7); assert.deepEqual(run.errors, []);
});

test('old coreRun polling remains supported but terminal and ambiguous states never loop', () => {
  assert.equal(ui.shouldPollReadiness(readiness(undefined, { state: 'processing', coreRun: { status: 'sent' } })), true);
  assert.equal(ui.shouldPollReadiness(readiness({ state: 'dependency_unavailable' }, { state: 'unavailable' })), true);
  assert.equal(ui.shouldPollReadiness(readiness(undefined, { state: 'unavailable' })), false);
  assert.equal(ui.shouldPollReadiness(readiness()), false);
  for (const state of ['not_enrolled', 'ready', 'error', 'stale', 'dispatch_unknown', 'outcome_unknown', 'unrecognized']) {
    assert.equal(ui.shouldPollReadiness(readiness({ state })), false, state);
  }
  for (const state of ['ready', 'stale', 'cancelled', 'expired', 'error']) {
    assert.equal(ui.shouldPollReadiness(readiness({ state: 'queued' }, { state })), false, state);
  }
});

test('network failure is surfaced once and does not silently restart reads or model dispatch', async t => {
  const run = observe(t, n => { if (n === 2) throw new Error('network failed'); return readiness({ state: 'queued' }); });
  await settle(); t.mock.timers.tick(ui.READINESS_POLL_DELAYS[0]); await settle();
  t.mock.timers.tick(300000); await settle();
  assert.equal(run.calls.length, 2); assert.equal(run.errors.length, 1); assert.equal(run.values.length, 1);
});

test('401 still reaches the expiry handler and stops refreshes', async t => {
  const run = observe(t, () => Response.json({ error: 'expired' }, { status: 401 })); await settle();
  t.mock.timers.tick(300000); await settle();
  assert.equal(run.errors[0].status, 401); assert.equal(run.calls.length, 1); assert.equal(run.values.length, 0);
});

test('closing the card ignores an in-flight response and cancels future polling', async t => {
  let resolve;
  const run = observe(t, () => new Promise(done => { resolve = done; }));
  await settle(); run.stop(); resolve(readiness({ state: 'queued' })); await settle();
  t.mock.timers.tick(300000); await settle();
  assert.equal(run.calls.length, 1); assert.deepEqual(run.values, []); assert.deepEqual(run.errors, []);
});

test('slow reads never overlap and another candidate cannot replace the current card', async t => {
  let resolve;
  const run = observe(t, n => n === 1 ? new Promise(done => { resolve = done; }) : { ...readiness(), candidateId: 'wrong-id' });
  await settle(); t.mock.timers.tick(300000); await settle(); assert.equal(run.calls.length, 1);
  resolve(readiness({ state: 'queued' })); await settle();
  t.mock.timers.tick(ui.READINESS_POLL_DELAYS[0]); await settle();
  assert.equal(run.calls.length, 2); assert.equal(run.errors.length, 1); assert.equal(run.values.length, 1);
  assert.equal(run.values[0].candidateId, 'candidate-1');
});

test('retryAt is optional Unix seconds and never changes known result or causes a POST', () => {
  assert.equal(ui.automationRetryLabel(null), null); assert.equal(ui.automationRetryLabel(0), null);
  assert.equal(ui.automationRetryLabel(NaN), null);
  assert.equal(ui.automationRetryLabel(1793000000), new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }).format(new Date(1793000000 * 1000)));
  const html = ui.render(ui.AutomationState, { automation: { state: 'dispatch_unknown', retryAt: 1793000000, runId: 'do-not-expose-run' } });
  assert.match(html, /Повторная проверка сервисом/); assert.match(html, /Запуск не подтверждён/);
  assert.doesNotMatch(html, /do-not-expose-run/);
});

test('known vacancy values stay visible when mandatory status is unknown, without coercing null', () => {
  const choices = [{ id: 'ru:moscow', label: 'Москва' }], value = { required: null, allowed_ids: ['ru:moscow'] };
  assert.equal(ui.vacancyConditionLabel(value, choices), 'Москва · Обязательность не уточнена');
  assert.equal(value.required, null);
  assert.equal(ui.vacancyConditionLabel({ ...value, required: false }, choices), 'Москва · Не требуется');
  assert.equal(ui.vacancyConditionLabel({ required: true, allowed_ids: null }, choices), 'Значение не уточнено · Обязательно');
  assert.equal(ui.vacancyIndustryLabel({ required: null, industry_id: 'logistics', minimum_months: 12 }, [{ id: 'logistics', label: 'Логистика' }]), 'Логистика · от 12 мес. · Обязательность не уточнена');
});

test('empty industry maps mean unknown; zero belongs only to an explicitly named industry', () => {
  const options = [{ id: 'logistics', label: 'Логистика' }, { id: 'sales', label: 'Продажи' }];
  assert.equal(ui.candidateIndustryLabel(null, options), 'Не уточнён');
  assert.equal(ui.candidateIndustryLabel({}, options), 'Не уточнён');
  assert.equal(ui.candidateIndustryLabel({ logistics: 0 }, options), 'Логистика · 0 мес.');
  assert.doesNotMatch(ui.candidateIndustryLabel({ logistics: 0 }, options), /Продажи|отсутствует|опыта нет/);
});

test('conflicts expose both human values for correction without leaking technical revisions', () => {
  const evidence = { conflicts: {
    'location.allowed_ids': { manualValue: ['moscow'], sourceValue: ['spb'], manualSourceRevision: 'private-hash-old', sourceRevision: 'private-hash-new' },
    'employment.required': { manualValue: true, sourceValue: null, manualSourceRevision: 'private-hash-old', sourceRevision: 'private-hash-new' },
    industry_months: { manualValue: {}, sourceValue: { logistics: 0 }, manualSourceRevision: 'private-hash-old', sourceRevision: 'private-hash-new' },
  } };
  const options = fixture.required.options;
  const html = ui.render(ui.FieldConflicts, { evidence, options });
  assert.equal((html.match(/Ответ изменился — проверьте условие\./g) ?? []).length, 3);
  assert.match(html, /Москва/); assert.match(html, /Санкт-Петербург/);
  assert.match(html, /Ваше уточнение/); assert.match(html, /Новые данные/);
  assert.match(html, /Не уточнено/); assert.match(html, /Не уточнён/); assert.match(html, /Логистика · 0 мес\./);
  assert.doesNotMatch(html, /private-hash|sourceRevision|manualValue|Не требуется|Подтверждено: опыта нет/);
});

test('only an explicit conflict choice resolves inherited null; ordinary neighboring edits do not', () => {
  const fields = structuredClone(fixture.required.fields);
  fields.employment.required = null;
  const normal = ui.factsUpdateBody('current', { ...fields, location: { required: true, allowed_ids: ['spb'] } }, []);
  assert.equal(Object.hasOwn(normal, 'resolveConflicts'), false);
  const chosen = ui.withResolvedFact(fields, 'employment.required', null);
  const body = ui.factsUpdateBody('current', chosen, ['employment.required']);
  assert.deepEqual(body.resolveConflicts, ['employment.required']);
  assert.equal(body.fields.employment.required, null);
  assert.deepEqual(body.fields.location, fields.location);
  assert.notEqual(chosen, fields);
  const sourceChoice = ui.withResolvedFact(chosen, 'employment.required', true);
  assert.equal(sourceChoice.employment.required, true);
  assert.equal(chosen.employment.required, null);
});

test('resolving one industry conflict preserves other facts and accepts explicit zero only by industry', () => {
  const fields = { work_locations: null, employment_types: ['full_time'], industry_months: null };
  const chosen = ui.withResolvedFact(fields, 'industry_months', { logistics: 0 });
  assert.deepEqual(ui.factsUpdateBody('revision', chosen, ['industry_months']), {
    revision: 'revision', fields: { ...fields, industry_months: { logistics: 0 } }, resolveConflicts: ['industry_months'],
  });
  assert.throws(() => ui.withResolvedFact(fields, 'employment.required', false));
  assert.throws(() => ui.withResolvedFact(fields, '__proto__', {}));
  assert.throws(() => ui.withResolvedFact(fields, 'industry_months', { logistics: '0' }));
});

test('conflict resolution choices are explicit and require saving rather than implying an immediate write', () => {
  const html = ui.render(ui.FieldConflicts, { evidence: { conflicts: { 'employment.required': { manualValue: true, sourceValue: null } } }, options: fixture.required.options, resolvedPaths: ['employment.required'], onResolve() {} });
  assert.match(html, /Принять новые данные/); assert.match(html, /Оставить моё уточнение/);
  assert.match(html, /Оставить неуточнённым/); assert.match(html, /Сохраните условия/);
  assert.equal((html.match(/type="button"/g) ?? []).length, 3);
});

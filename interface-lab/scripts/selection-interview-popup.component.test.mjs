// Real Workbench + existing CandidateInterviewPlayer, loopback synthetic APIs/media.
// npm ci supplies locked esbuild/React. Provision Playwright + compatible Chromium explicitly:
// PLAYWRIGHT_MODULE=/absolute/playwright/index.mjs CHROMIUM_EXECUTABLE_PATH=/absolute/chromium node --test scripts/selection-interview-popup.component.test.mjs
// Verified toolchain: Node 26.7.0, esbuild 0.28.2, Playwright 1.62.1, Chromium 153.0.8010.12.
import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
import { makeFixture } from './selection-vacancy.fixtures.mjs';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : 'playwright');
let browser, server, origin;
before(async () => {
  const bundle = await build({ stdin: { contents: `import React,{useState,StrictMode} from 'react';import {createRoot} from 'react-dom/client';import {SelectionWorkbench} from './src/scenes/selection-workbench/SelectionWorkbench';import {CandidateInterviewPlayer} from './src/scenes/interview-review/CandidateInterviewPlayer';function SourceRefresh(){const [revision,setRevision]=useState(1);return <><button onClick={()=>setRevision(v=>v+1)}>Refresh signed source</button><CandidateInterviewPlayer sourceId="same-interview" name="Source refresh" kind="screening" chapters={[]} media={{src:'/fixture.webm?revision='+revision}}/></>};createRoot(document.getElementById('root')).render(location.search.includes('playerRefresh')?<StrictMode><SourceRefresh/></StrictMode>:<SelectionWorkbench/>);`, resolveDir: fileURLToPath(new URL('../', import.meta.url)), loader: 'tsx' }, bundle: true, write: false, format: 'esm', jsx: 'automatic', outfile: 'component.js', loader: { '.svg': 'dataurl' } });
  const assets = new Map(bundle.outputFiles.map(file => [file.path.split('/').at(-1), file.contents]));
  const media = await readFile(new URL('../public/task-meeting-preview/interview.webm', import.meta.url));
  server = createServer((req, res) => {
    const path = new URL(req.url, 'http://fixture').pathname;
    if (path === '/fixture.webm') { res.setHeader('Content-Type', 'video/webm'); res.end(media); return; }
    res.setHeader('Content-Type', path.endsWith('.js') ? 'text/javascript' : path.endsWith('.css') ? 'text/css' : 'text/html');
    res.end(assets.get(path.slice(1)) || '<!doctype html><html lang="ru"><meta charset="utf-8"><link rel="stylesheet" href="/component.css"><div id="root"></div><script type="module" src="/component.js"></script>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve)); origin = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({ headless: true, ...(process.env.CHROMIUM_EXECUTABLE_PATH ? { executablePath: process.env.CHROMIUM_EXECUTABLE_PATH } : {}) });
});
after(async () => { await browser?.close(); if (server) await new Promise(resolve => server.close(resolve)); });
const until = async check => { for (let i = 0; i < 150; i++) { if (await check()) return; await new Promise(resolve => setTimeout(resolve, 20)); } assert.fail('Expected UI transition did not complete'); };
function material(candidateId, interviewId, count = 1) {
  const parts = Array.from({ length: count }, (_, n) => ({ id: `${interviewId}-part-${n}`, recordingId: `${interviewId}-part-${n}`, kind: 'video', state: 'ready', partIndex: n, transcriptStatus: 'ready', transcriptId: `${interviewId}-text-${n}` }));
  const transcripts = parts.map((part, n) => ({ id: part.transcriptId, recordingId: part.id, text: `WEBVTT\n\n00:00.000 --> 00:08.000\n${candidateId} ${interviewId} часть ${n + 1} сохранённый ответ.` }));
  return { candidateId, interviewId, parts, status: 'ready', recordings: parts.map(part => ({ id: part.id, partIndex: part.partIndex, url: '/fixture.webm' })), transcripts, partTranscripts: transcripts, video: { status: 'ready' }, transcription: { status: 'ready' }, checkedAt: '2026-10-26' };
}
async function setup(t, { vacancy = false, respond } = {}) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } }); page.setDefaultTimeout(7000);
  const state = makeFixture(), calls = [], errors = [], external = [];
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url());
    if (url.origin !== origin) { external.push(req.url()); return route.abort(); }
    if (!url.pathname.startsWith('/api/')) return route.continue();
    calls.push({ method: req.method(), path: url.pathname });
    let value;
    const match = url.pathname.match(/\/selection\/candidates\/([^/]+)\/interviews(?:\/([^/]+)\/materials)?$/);
    if (match) {
      const [, cid, iid] = match;
      value = await respond?.({ cid, iid, calls });
      value ??= iid ? material(cid, iid) : { candidateId: cid, interviews: [{ id: 'interview-1', status: 'ready', parts: [] }] };
    } else value = state.request(req.method(), url.pathname + url.search, req.method() === 'POST' ? req.postDataJSON() : null);
    await route.fulfill({ status: value?.httpStatus ?? 200, json: value }).catch(() => {});
  });
  page.on('pageerror', error => errors.push(error.message));
  t.after(async () => { await page.close(); assert.deepEqual(errors, []); assert.deepEqual(external, []); assert.deepEqual(calls.filter(call => call.method !== 'GET' && call.path !== '/api/hr/session'), []); });
  await page.goto(origin + (vacancy ? '/?tab=vacancies&vacancyId=' + state.vacancy.id : '/'));
  await page.getByRole('button', { name: 'Борис Соколов', exact: true }).waitFor();
  return { page, state, calls, close: () => page.getByRole('button', { name: 'Закрыть карточку', exact: true }).click() };
}

test('candidate opens familiar viewer, real media stops on Escape, focus and directory filters return', async t => {
  const { page, close } = await setup(t);
  const search = page.getByPlaceholder('Найти кандидата', { exact: true }); await search.fill('Борис');
  const opener = page.getByRole('button', { name: 'Борис Соколов', exact: true }); await opener.click();
  await page.getByText('candidate-2 interview-1 часть 1 сохранённый ответ.', { exact: true }).waitFor();
  const video = page.getByLabel('Видеозапись: Борис Соколов', { exact: true });
  await until(() => video.evaluate(node => node.readyState >= 2));
  await page.getByRole('button', { name: 'Воспроизвести интервью', exact: true }).click();
  await until(() => video.evaluate(node => !node.paused && node.currentTime > 0));
  await video.evaluate(node => { window.closedVideo = node; });
  await page.keyboard.press('Escape'); await until(async () => await page.getByRole('dialog').count() === 0);
  assert.equal(await page.evaluate(() => window.closedVideo.paused), true);
  assert.equal(await page.evaluate(() => window.closedVideo.getAttribute('src')), null);
  assert.equal(await opener.evaluate(node => node === document.activeElement), true);
  assert.equal(await search.inputValue(), 'Борис');
  assert.equal(await page.getByLabel('HR-интервью', { exact: true }).inputValue(), 'ready');
  await page.getByRole('button', { name: 'Интервью: Борис Соколов', exact: true }).click(); await video.waitFor(); await close();
});

test('shared player survives StrictMode ref reattachment and a new signed src on the same mounted video', async t => {
  const { page } = await setup(t);
  await page.goto(origin + '/?playerRefresh');
  const video = page.getByLabel('Видеозапись: Source refresh', { exact: true });
  await until(() => video.evaluate(node => node.readyState >= 2 && node.currentSrc.endsWith('?revision=1')));
  await video.evaluate(node => { window.originalVideo = node; });
  await page.getByRole('button', { name: 'Refresh signed source', exact: true }).click();
  await until(() => video.evaluate(node => node.readyState >= 2 && node.currentSrc.endsWith('?revision=2')));
  assert.equal(await video.evaluate(node => node === window.originalVideo), true);
  await page.getByRole('button', { name: 'Воспроизвести интервью', exact: true }).click();
  await until(() => video.evaluate(node => !node.paused && node.currentTime > 0));
});

test('shortlist popup preserves vacancy search, selected candidate and explicit unknown approval', async t => {
  const { page, state, close } = await setup(t, { vacancy: true });
  const search = page.getByRole('textbox', { name: 'Найти кандидата в вакансии', exact: true }); await search.fill('Борис');
  await page.getByRole('checkbox', { name: 'Подтвердить неизвестные условия: Борис Соколов', exact: true }).check();
  await page.getByRole('checkbox', { name: 'Пригласить: Борис Соколов', exact: true }).check();
  const opener = page.getByRole('button', { name: 'Интервью: Борис Соколов', exact: true }); await opener.click();
  await page.getByLabel('Видеозапись: Борис Соколов', { exact: true }).waitFor(); await close();
  assert.equal(await search.inputValue(), 'Борис');
  assert.equal(await page.getByRole('checkbox', { name: 'Пригласить: Борис Соколов', exact: true }).isChecked(), true);
  assert.equal(await page.getByRole('checkbox', { name: 'Подтвердить неизвестные условия: Борис Соколов', exact: true }).isChecked(), true);
  assert.equal(new URL(page.url()).searchParams.get('vacancyId'), state.vacancy.id);
  assert.equal(await opener.evaluate(node => node === document.activeElement), true);
});

test('every interview and missing part remains selectable; saved primary ready text is exact per part', async t => {
  const { page } = await setup(t, { respond: ({ cid, iid }) => {
    if (!iid) return { candidateId: cid, interviews: [{ id: 'first', status: 'ready' }, { id: 'second', status: 'ready' }] };
    const value = material(cid, iid, 2);
    if (iid === 'first') {
      value.parts[1].state = 'missing'; value.parts[1].transcriptStatus = 'processing';
      value.recordings.pop(); value.transcription.status = 'processing'; value.transcripts = []; value.partTranscripts.pop();
    }
    return value;
  } });
  await page.getByRole('button', { name: 'Борис Соколов', exact: true }).click();
  await page.getByText('candidate-2 first часть 1 сохранённый ответ.', { exact: true }).waitFor();
  assert.equal(await page.getByLabel('Часть интервью', { exact: true }).locator('option').count(), 2);
  await until(() => page.locator('video').evaluate(node => node.readyState >= 2));
  await page.getByRole('button', { name: 'Воспроизвести интервью', exact: true }).click();
  await until(() => page.locator('video').evaluate(node => !node.paused));
  await page.locator('video').evaluate(node => { window.previousPart = node; });
  await page.getByLabel('Часть интервью', { exact: true }).selectOption('first-part-1');
  await page.getByText('Запись этой части интервью отсутствует.', { exact: true }).waitFor();
  assert.equal(await page.locator('video').count(), 0);
  assert.equal(await page.evaluate(() => window.previousPart.paused && window.previousPart.getAttribute('src') === null), true);
  assert.equal(await page.getByText('candidate-2 first часть 1 сохранённый ответ.', { exact: true }).count(), 0);
  await page.getByLabel('Интервью кандидата', { exact: true }).selectOption('second');
  await page.getByLabel('Часть интервью', { exact: true }).selectOption('second-part-1');
  await page.getByText('candidate-2 second часть 2 сохранённый ответ.', { exact: true }).waitFor();
});

test('saved primary transcript survives missing media, and explicit GET refresh reissues playback without processing', async t => {
  let ready = false;
  const { page, calls } = await setup(t, { respond: ({ cid, iid }) => {
    if (!iid) return undefined;
    const value = material(cid, iid);
    if (!ready) { value.recordings = []; value.parts[0].state = 'error'; value.video.status = 'error'; }
    return value;
  } });
  await page.getByRole('button', { name: 'Интервью: Борис Соколов', exact: true }).click();
  await page.getByText('candidate-2 interview-1 часть 1 сохранённый ответ.', { exact: true }).waitFor();
  await page.getByText('Не удалось получить запись этой части интервью.', { exact: true }).waitFor();
  assert.equal(await page.locator('video').count(), 0);
  ready = true; await page.getByRole('button', { name: 'Обновить материалы интервью', exact: true }).click();
  await until(() => page.locator('video').evaluate(node => node.readyState >= 2));
  assert.equal(await page.locator('video').evaluate(node => node.paused), true);
  assert.equal(calls.filter(call => call.path.endsWith('/interview-1/materials')).length, 2);
});

test('late candidate/interview responses cannot replace current identity or reopen closed popup', async t => {
  let release; const held = new Promise(resolve => { release = resolve; }); t.after(() => release());
  const { page, calls, close } = await setup(t, { respond: async ({ cid, iid }) => {
    if (!iid) return { candidateId: cid, interviews: [{ id: 'slow', status: 'ready' }, { id: 'fast', status: 'ready' }] };
    if (iid === 'slow') await held;
    return material(cid, iid);
  } });
  await page.getByRole('button', { name: 'Борис Соколов', exact: true }).click();
  await until(() => calls.some(call => call.path.endsWith('/slow/materials')));
  assert.equal(await page.getByText('Загружаем запись и расшифровку…', { exact: true }).count(), 1);
  assert.equal(await page.locator('video').count(), 0);
  await page.getByLabel('Интервью кандидата', { exact: true }).selectOption('fast');
  await page.getByText('candidate-2 fast часть 1 сохранённый ответ.', { exact: true }).waitFor();
  await close(); await page.getByRole('button', { name: 'Анна Романова', exact: true }).click();
  await page.getByLabel('Интервью кандидата', { exact: true }).selectOption('fast');
  await page.getByText('candidate-1 fast часть 1 сохранённый ответ.', { exact: true }).waitFor();
  release();
  await page.waitForTimeout(100);
  assert.equal(await page.getByText('candidate-2 slow часть 1 сохранённый ответ.', { exact: true }).count(), 0);
  assert.equal(await page.getByRole('dialog').getByRole('heading', { name: 'Анна Романова', exact: true }).count(), 1);
  await close(); assert.equal(await page.locator('video').count(), 0);
});

test('loading, empty, media failures, API failures and mismatched identities are honest; 403 expires access', async t => {
  let mode = 'empty';
  const { page, close } = await setup(t, { respond: ({ cid, iid }) => {
    if (mode === 'empty') return { candidateId: cid, interviews: [] };
    if (mode === 'denied') return { httpStatus: 403, error: 'forbidden' };
    if (!iid) return { candidateId: cid, interviews: [{ id: 'one', status: 'ready' }] };
    if (mode === 'error') return { httpStatus: 503, error: 'private_provider_diagnostic' };
    if (mode === 'wrong') return material('another-candidate', iid);
    const value = material(cid, iid); value.recordings[0].url = '/broken.webm'; return value;
  } });
  const open = () => page.getByRole('button', { name: 'Борис Соколов', exact: true }).click();
  await open(); await page.getByText('HR-интервью пока нет.', { exact: true }).waitFor(); await close();
  mode = 'error'; await open(); await page.getByText('Не удалось загрузить интервью. Повторите загрузку.', { exact: true }).waitFor();
  assert.equal(await page.getByText('private_provider_diagnostic').count(), 0);
  mode = 'wrong'; await page.getByRole('button', { name: 'Повторить загрузку интервью', exact: true }).click();
  await until(async () => await page.getByText('Не удалось загрузить интервью. Повторите загрузку.', { exact: true }).count() === 1);
  assert.equal(await page.locator('video').count(), 0); await close();
  mode = 'media'; await open(); await page.getByRole('button', { name: /Не удалось открыть запись/ }).waitFor(); await close();
  mode = 'denied'; await open(); await page.getByRole('heading', { name: 'Вход для команды', exact: true }).waitFor();
  assert.equal(await page.getByRole('dialog').count(), 0);
});

// Real hook + transport, synthetic loopback responses. No external requests.
import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { createServer } from 'node:http';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : 'playwright');
let browser, server, origin;
before(async () => {
  const result = await build({ stdin: { contents: `
    import React, { useState } from 'react'; import { createRoot } from 'react-dom/client';
    import { useInterviewAssistant } from './src/scenes/interview-review/useInterviewAssistant';
    import { interviewAssistant } from './src/scenes/interview-review/interview-assistant-api';
    function Test() {
      const [id, setId] = useState('alpha');
      const assistant = useInterviewAssistant(interviewAssistant('synthetic-test-token', { source: 'intake', id, index: 0, vacancyId: 'synthetic' }));
      return <><button onClick={() => setId(id === 'alpha' ? 'beta' : 'alpha')}>Switch</button>
        <button disabled={!!assistant.busy} onClick={() => assistant.submit('chat', 'Почему?')}>Ask</button>
        <div data-testid="scope">{id}</div><div data-testid="history">{assistant.messages.map(m => <p key={m.id}>{m.query}: {m.answer?.text || 'pending'}</p>)}</div>
        <div data-testid="error">{assistant.historyError}</div></>;
    } createRoot(document.getElementById('root')).render(<React.StrictMode><Test/></React.StrictMode>);
  `, resolveDir: fileURLToPath(new URL('../', import.meta.url)), loader: 'jsx' }, bundle: true, write: false, format: 'esm', jsx: 'automatic' });
  server = createServer((req, res) => {
    if (req.url === '/app.js') { res.setHeader('Content-Type', 'text/javascript'); res.end(result.outputFiles[0].contents); }
    else { res.setHeader('Content-Type', 'text/html'); res.end('<div id="root"></div><script type="module" src="/app.js"></script>'); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || undefined });
});
after(async () => { await browser?.close(); await new Promise(resolve => server?.close(resolve)); });
const answer = text => ({ mode: 'chat', text, parts: [{ text, sourceIds: [] }], evidence: [], supported: false,
  coverage: { selected: 1, omitted: 0 }, model: 'synthetic', version: 'test' });
test('history survives reload and candidate switching never delivers a stale answer', async () => {
  const page = await browser.newPage(); const errors = []; page.on('pageerror', e => errors.push(e.message));
  const saved = { alpha: [{ id: 'saved-alpha', query: 'Первый вопрос', answer: answer('Контекст Альфы') }], beta: [] };
  let release, inFlight;
  const started = new Promise(resolve => { inFlight = resolve; });
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    assert.equal(url.origin, origin);
    if (url.pathname !== '/api/hr/employer/interview-assistant') return route.continue();
    const body = route.request().postDataJSON();
    if (body.action === 'history') return route.fulfill({ json: { mode: 'history', messages: saved[body.id] } });
    inFlight(); await new Promise(resolve => { release = resolve; });
    saved[body.id].push({ id: body.requestId, query: body.query, answer: answer('Ответ только Альфы') });
    await route.fulfill({ json: answer('Ответ только Альфы') }).catch(() => {});
  });
  await page.goto(origin); await page.getByText('Первый вопрос: Контекст Альфы', { exact: true }).waitFor();
  await page.reload(); await page.getByText('Первый вопрос: Контекст Альфы', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Ask', exact: true }).click(); await started;
  await page.getByRole('button', { name: 'Switch', exact: true }).click();
  await page.getByTestId('scope').filter({ hasText: 'beta' }).waitFor(); release();
  await page.getByRole('button', { name: 'Ask', exact: true }).waitFor();
  assert.equal(await page.getByTestId('history').textContent(), '');
  await page.getByRole('button', { name: 'Switch', exact: true }).click();
  await page.getByText('Почему?: Ответ только Альфы', { exact: true }).waitFor();
  assert.equal(await page.getByTestId('error').textContent(), '');
  assert.deepEqual(errors, []); await page.close();
});

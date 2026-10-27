// Run with PLAYWRIGHT_MODULE pointing to an installed playwright package if it
// is not resolvable locally. Only a temporary loopback server and mocked API.
import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { createServer } from 'node:http';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE
  ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : 'playwright');
let browser, server, origin;
before(async () => {
  const bundle = await build({
    stdin: { contents: `import React from 'react';
      import { createRoot } from 'react-dom/client';
      import { RecruiterTelegramAction } from './src/scenes/recruiter-portal/RecruiterTelegramAction';
      window.mountAccount = token => root.render(<RecruiterTelegramAction key={token} token={token} disabled={false} onError={error => { document.querySelector('#error').textContent = error; }}/>);
      const root = createRoot(document.querySelector('#root'));
      window.mountAccount('fixture-account');`,
      resolveDir: fileURLToPath(new URL('../', import.meta.url)), loader: 'tsx' },
    bundle: true, write: false, format: 'esm', jsx: 'automatic',
  });
  server = createServer((request, response) => {
    response.setHeader('Content-Type', request.url === '/component.js' ? 'text/javascript' : 'text/html');
    response.end(request.url === '/component.js' ? bundle.outputFiles[0].contents
      : '<!doctype html><div id="root"></div><div id="error"></div><script type="module" src="/component.js"></script>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL });
});
after(async () => {
  await browser?.close();
  if (server) await new Promise(resolve => server.close(resolve));
});

async function pageFor(t) {
  const page = await browser.newPage();
  page.setDefaultTimeout(5000);
  t.after(() => page.close());
  // Keep a regression from accidentally calling any external service.
  await page.route('**/*', route => route.request().url().startsWith(origin + '/')
    ? route.continue() : route.abort());
  return page;
}

test('rejected initial status stays visible and explicit retry restores connection action', async t => {
  const page = await pageFor(t);
  let statusRequests = 0;
  const commands = [];
  await page.route('**/api/hr/recruiter/**', async route => {
    const request = route.request();
    assert.equal(request.headers().authorization, 'Bearer fixture-account');
    if (request.url().endsWith('/notifications')) {
      statusRequests++;
      await route.fulfill({ status: statusRequests === 1 ? 503 : 200, json: statusRequests === 1
        ? { error: 'Fixture outage' } : { telegram: { linked: false, canLink: true } } });
    } else {
      commands.push(request.url());
      await route.fulfill({ json: { url: 'https://t.me/FixtureBot?start=fixture' } });
    }
  });
  await page.goto(origin);
  await page.getByText('Не удалось проверить Telegram.', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Повторить', exact: true }).click();
  await page.getByRole('button', { name: 'Подключить Telegram', exact: true }).click();
  const link = page.getByRole('link', { name: 'Открыть бота и подключить', exact: true });
  await link.waitFor();
  assert.equal(await link.getAttribute('href'), 'https://t.me/FixtureBot?start=fixture');
  assert.equal(statusRequests, 2);
  assert.equal(commands.length, 1);
  assert.ok(commands[0].endsWith('/notifications/telegram/link'));
  assert.equal(await page.getByText('Не удалось проверить Telegram.', { exact: true }).count(), 0);
});

test('a late failed status from an unmounted account cannot hide the new account action', async t => {
  const page = await pageFor(t);
  let releaseOld;
  const oldResponse = new Promise(resolve => { releaseOld = resolve; });
  let capturedOld;
  const oldRequested = new Promise(resolve => { capturedOld = resolve; });
  await page.route('**/api/hr/recruiter/notifications', async route => {
    if (route.request().headers().authorization === 'Bearer fixture-account') {
      capturedOld();
      await oldResponse;
      await route.fulfill({ status: 503, json: { error: 'Old account failed' } });
    } else {
      await route.fulfill({ json: { telegram: { linked: true, canLink: true } } });
    }
  });
  await page.goto(origin);
  await oldRequested;
  await page.evaluate(() => window.mountAccount('another-fixture-account'));
  await page.getByRole('button', { name: 'Telegram подключён · отключить', exact: true }).waitFor();
  const oldFinished = page.waitForResponse(response => response.status() === 503);
  releaseOld();
  await oldFinished;
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  assert.equal(await page.getByRole('button', { name: 'Telegram подключён · отключить', exact: true }).count(), 1);
  assert.equal(await page.getByText('Не удалось проверить Telegram.', { exact: true }).count(), 0);
});

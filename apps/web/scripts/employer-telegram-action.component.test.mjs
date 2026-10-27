// Uses the actual React component and Radix menu with a temporary loopback
// server and intercepted API only. Supports the recruiter harness overrides.
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
      import { DropdownMenu } from 'radix-ui';
      import { EmployerTelegramAction } from './src/scenes/employer-workspace/EmployerTelegramAction';
      const root = createRoot(document.querySelector('#root'));
      window.mountAccount = token => root.render(<DropdownMenu.Root defaultOpen>
        <DropdownMenu.Trigger>Account</DropdownMenu.Trigger>
        <DropdownMenu.Portal><DropdownMenu.Content>
          <EmployerTelegramAction token={token} onError={error => { document.querySelector('#error').textContent = error; }}/>
          <DropdownMenu.Item>Logout</DropdownMenu.Item>
        </DropdownMenu.Content></DropdownMenu.Portal>
      </DropdownMenu.Root>);
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
  await page.route('**/*', route => route.request().url().startsWith(origin + '/')
    ? route.continue() : route.abort());
  return page;
}
function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}
const item = (page, name) => page.getByRole('menuitem', { name, exact: true });
const settle = page => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));

test('initial status failure stays in the account menu and explicit retry restores connect and deep link', async t => {
  const page = await pageFor(t);
  const releaseRetry = deferred();
  let statusRequests = 0;
  const commands = [];
  await page.route('**/api/hr/employer/telegram**', async route => {
    const request = route.request();
    assert.equal(request.headers().authorization, 'Bearer fixture-account');
    if (request.method() === 'GET') {
      statusRequests++;
      if (statusRequests === 2) await releaseRetry.promise;
      await route.fulfill({ status: statusRequests === 1 ? 503 : 200, json: statusRequests === 1
        ? { error: 'Fixture outage' } : { linked: false, available: true } });
    } else {
      commands.push(request.url());
      await route.fulfill({ json: { url: 'https://t.me/FixtureBot?start=fixture' } });
    }
  });
  await page.goto(origin);
  await page.getByText('Не удалось проверить Telegram.', { exact: true }).waitFor();
  await item(page, 'Повторить').click();
  await item(page, 'Проверяем Telegram…').waitFor();
  releaseRetry.resolve();
  await item(page, 'Подключить Telegram').click();
  const link = item(page, 'Открыть бота и подключить');
  await link.waitFor();
  assert.equal(await link.getAttribute('href'), 'https://t.me/FixtureBot?start=fixture');
  assert.equal(statusRequests, 2);
  assert.equal(commands.length, 1);
  assert.ok(commands[0].endsWith('/telegram/link'));
  assert.equal(await page.getByText('Не удалось проверить Telegram.', { exact: true }).count(), 0);
  assert.equal(await page.getByRole('menu').count(), 1);
});

for (const failed of [false, true]) test(`a stale ${failed ? 'failed' : 'successful'} GET cannot replace a completed disconnect`, async t => {
  const page = await pageFor(t);
  const captured = deferred(), release = deferred();
  let statusRequests = 0, disconnects = 0;
  await page.route('**/api/hr/employer/telegram**', async route => {
    if (route.request().method() === 'GET') {
      statusRequests++;
      if (statusRequests === 2) { captured.resolve(); await release.promise; }
      await route.fulfill({ status: failed && statusRequests === 2 ? 503 : 200,
        json: failed && statusRequests === 2 ? { error: 'Stale failure' } : { linked: true, available: true } });
    } else {
      assert.ok(route.request().url().endsWith('/disconnect'));
      disconnects++;
      await route.fulfill({ json: { linked: false, available: true } });
    }
  });
  await page.goto(origin);
  await item(page, 'Telegram подключён · отключить').waitFor();
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await captured.promise;
  await item(page, 'Telegram подключён · отключить').click();
  await item(page, 'Подключить Telegram').waitFor();
  const finished = page.waitForResponse(response => response.request().method() === 'GET' && response.url().endsWith('/telegram'));
  release.resolve();
  await finished; await settle(page);
  assert.equal(await item(page, 'Подключить Telegram').count(), 1);
  assert.equal(await page.getByText('Не удалось проверить Telegram.', { exact: true }).count(), 0);
  assert.equal(statusRequests, 2);
  assert.equal(disconnects, 1);
});

test('a pending mutation blocks duplicate selections and focus/visibility refreshes', async t => {
  const page = await pageFor(t);
  const captured = deferred(), release = deferred();
  let statusRequests = 0, disconnects = 0;
  await page.route('**/api/hr/employer/telegram**', async route => {
    if (route.request().method() === 'GET') {
      statusRequests++;
      await route.fulfill({ json: { linked: true, available: true } });
    } else {
      disconnects++; captured.resolve(); await release.promise;
      await route.fulfill({ json: { linked: false, available: true } });
    }
  });
  await page.goto(origin);
  await item(page, 'Telegram подключён · отключить').waitFor();
  await item(page, 'Telegram подключён · отключить').evaluate(element => { element.click(); element.click(); });
  await captured.promise;
  await item(page, 'Подождите…').waitFor();
  await page.evaluate(() => {
    window.dispatchEvent(new Event('focus'));
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await settle(page);
  assert.equal(statusRequests, 1);
  assert.equal(disconnects, 1);
  release.resolve();
  await item(page, 'Подключить Telegram').waitFor();
});

for (const failed of [false, true]) test(`a late ${failed ? 'failed' : 'successful'} status cannot leak across a token change without a parent remount`, async t => {
  const page = await pageFor(t);
  const captured = deferred(), release = deferred();
  await page.route('**/api/hr/employer/telegram', async route => {
    const old = route.request().headers().authorization === 'Bearer fixture-account';
    if (old) { captured.resolve(); await release.promise; }
    await route.fulfill({ status: old && failed ? 503 : 200,
      json: old && failed ? { error: 'Old account failed' } : { linked: !old, available: true } });
  });
  await page.goto(origin);
  await captured.promise;
  await page.evaluate(() => window.mountAccount('another-fixture-account'));
  await item(page, 'Telegram подключён · отключить').waitFor();
  const finished = page.waitForResponse(response => response.request().headers().authorization === 'Bearer fixture-account');
  release.resolve();
  await finished; await settle(page);
  assert.equal(await item(page, 'Telegram подключён · отключить').count(), 1);
  assert.equal(await page.getByText('Не удалось проверить Telegram.', { exact: true }).count(), 0);
});

for (const operation of ['link', 'disconnect', 'failed-link']) test(`late ${operation} results from the previous account cannot change the current account`, async t => {
  const page = await pageFor(t);
  const captured = deferred(), release = deferred();
  await page.route('**/api/hr/employer/telegram**', async route => {
    const request = route.request();
    if (request.method() === 'GET') {
      await route.fulfill({ json: { linked: operation === 'disconnect' || request.headers().authorization !== 'Bearer fixture-account', available: true } });
    } else {
      captured.resolve(); await release.promise;
      await route.fulfill({ status: operation === 'failed-link' ? 503 : 200, json: operation === 'failed-link'
        ? { error: 'Old account mutation failed' } : operation === 'link'
          ? { url: 'https://t.me/FixtureBot?start=old-fixture' } : { linked: false, available: true } });
    }
  });
  await page.goto(origin);
  await item(page, operation === 'disconnect' ? 'Telegram подключён · отключить' : 'Подключить Telegram').click();
  await captured.promise;
  await page.evaluate(() => window.mountAccount('another-fixture-account'));
  await item(page, 'Telegram подключён · отключить').waitFor();
  const finished = page.waitForResponse(response => response.request().method() === 'POST');
  release.resolve();
  await finished; await settle(page);
  assert.equal(await item(page, 'Telegram подключён · отключить').count(), 1);
  assert.equal(await item(page, 'Открыть бота и подключить').count(), 0);
  assert.equal(await page.locator('#error').textContent(), '');
});

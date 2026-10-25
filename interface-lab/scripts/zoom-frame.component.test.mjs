// Real React DOM and iframe message boundary, local fixture Zoom document only.
// No provider calls, real credentials, device capture, or external navigation.
import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { createServer } from 'node:http';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE
  ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : 'playwright');
let browser, server, origin;
const childDocument = `<!doctype html><p id="state">Fixture ready</p><script>
  window.commands = [];
  window.sendFixture = data => parent.postMessage({ source: 'hr-zoom-client', ...data }, location.origin);
  window.addEventListener('message', event => {
    if (event.source !== parent || event.origin !== location.origin || event.data?.source !== 'hr-zoom-parent') return;
    window.commands.push(event.data);
    if (event.data.type === 'join') {
      document.querySelector('#state').textContent = 'Fixture joined';
      window.sendFixture({ type: 'status', phase: 'joined' });
    }
    if (event.data.type === 'leave') {
      document.querySelector('#state').textContent = 'Fixture left';
      window.sendFixture({ type: 'status', phase: 'left' });
      window.sendFixture({ type: 'status', phase: 'left' });
    }
  });
  if (!new URL(parent.location.href).searchParams.has('manualReady')) window.sendFixture({ type: 'ready' });
</script>`;

before(async () => {
  const bundle = await build({
    stdin: { contents: `import React, { useRef, useState } from 'react';
      import { createRoot } from 'react-dom/client';
      import { ZoomCallFrame } from './src/components/call/ZoomCallFrame';
      import { OptionalCallPanel } from './src/components/call/OptionalCallPanel';
      function OptionalContent({ broken, revision }) {
        if (broken) throw new Error('Fixture optional panel render failure');
        return <p>Notes revision {revision}</p>;
      }
      function Fixture() {
        const call = useRef(null);
        const [revision, setRevision] = useState(0);
        const [collapsed, setCollapsed] = useState(false);
        const [broken, setBroken] = useState(false);
        const [joined, setJoined] = useState(0);
        const [left, setLeft] = useState(0);
        const [errors, setErrors] = useState(0);
        const credentials = { provider: 'zoom', sdkVersion: '6.10.0', meetingNumber: '12345678901',
          signature: 'fixture.signature.' + revision, passWord: 'fixture-passcode', userName: 'Fixture', role: 1,
          zak: 'fixture-host-token' };
        return <>
          <button onClick={() => setRevision(value => value + 1)}>Change optional props</button>
          <button onClick={() => setCollapsed(value => !value)}>Toggle notes</button>
          <button onClick={() => setBroken(true)}>Break notes</button>
          <button onClick={() => call.current.leave()}>Leave locally</button>
          <output id="callbacks">{JSON.stringify({ joined, left, errors })}</output>
          <div className="call-shell">
            <ZoomCallFrame ref={call} credentials={credentials} onJoined={() => setJoined(value => value + 1)}
              onLeft={() => setLeft(value => value + 1)} onError={() => setErrors(value => value + 1)}/>
            <OptionalCallPanel label="Заметки">
              {!collapsed && <OptionalContent broken={broken} revision={revision}/>}
            </OptionalCallPanel>
          </div>
        </>;
      }
      createRoot(document.querySelector('#root')).render(<Fixture/>);`,
      resolveDir: fileURLToPath(new URL('../', import.meta.url)), loader: 'tsx' },
    bundle: true, write: false, format: 'esm', jsx: 'automatic', loader: { '.css': 'empty' },
  });
  server = createServer((request, response) => {
    if (request.url === '/component.js') {
      response.setHeader('Content-Type', 'text/javascript');
      response.end(bundle.outputFiles[0].contents);
    } else if (request.url === '/call/zoom/client.html') {
      response.setHeader('Content-Type', 'text/html');
      response.end(childDocument);
    } else if (request.url === '/spoof.html') {
      response.setHeader('Content-Type', 'text/html');
      response.end('<!doctype html><p>Untrusted sibling</p>');
    } else if (request.url.startsWith('/api/')) {
      response.writeHead(500);
      response.end('No provider API exists in this component fixture');
    } else {
      response.setHeader('Content-Type', 'text/html');
      response.end('<!doctype html><div id="root"></div><script type="module" src="/component.js"></script>');
    }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL });
});
after(async () => { await browser?.close(); if (server) await new Promise(resolve => server.close(resolve)); });

async function pageFor(t) {
  const page = await browser.newPage();
  page.setDefaultTimeout(5000);
  t.after(() => page.close());
  const requests = [];
  page.on('request', request => requests.push(request.url()));
  await page.route('**/*', route => route.request().url().startsWith(origin + '/') ? route.continue() : route.abort());
  return { page, requests };
}
const settle = page => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
const callbacks = async page => JSON.parse(await page.locator('#callbacks').textContent());
const commands = page => page.locator('iframe[title="Встреча Zoom"]').evaluate(frame => frame.contentWindow.commands);
async function joined(page) {
  await page.locator('.md-zoom-call[data-phase="joined"]').waitFor();
  await page.frameLocator('iframe[title="Встреча Zoom"]').getByText('Fixture joined', { exact: true }).waitFor();
}

test('frame accepts exact origin and source only; sibling or spoofed readiness cannot receive credentials', async t => {
  const { page } = await pageFor(t);
  await page.goto(origin + '/?manualReady=1');
  await page.frameLocator('iframe').getByText('Fixture ready', { exact: true }).waitFor();
  await page.evaluate(() => {
    const child = document.querySelector('iframe').contentWindow;
    const data = { source: 'hr-zoom-client', type: 'ready' };
    window.dispatchEvent(new MessageEvent('message', { data, source: child, origin: 'https://attacker.example' }));
    window.postMessage(data, location.origin);
    const sibling = document.createElement('iframe');
    sibling.id = 'spoof'; sibling.src = '/spoof.html'; document.body.appendChild(sibling);
  });
  const sibling = page.frameLocator('#spoof');
  await sibling.getByText('Untrusted sibling', { exact: true }).waitFor();
  await page.frames().find(frame => frame.url().endsWith('/spoof.html')).evaluate(() => {
    parent.postMessage({ source: 'hr-zoom-client', type: 'ready' }, location.origin);
  });
  await settle(page);
  assert.deepEqual(await commands(page), []);
  assert.deepEqual(await callbacks(page), { joined: 0, left: 0, errors: 0 });
  await page.locator('iframe[title="Встреча Zoom"]').evaluate(frame => frame.contentWindow.sendFixture({ type: 'ready' }));
  await joined(page);
  assert.equal((await commands(page)).length, 1);
  assert.deepEqual(await callbacks(page), { joined: 1, left: 0, errors: 0 });
});

test('optional prop changes, panel collapse and duplicate ready keep one frame and one credential delivery', async t => {
  const { page, requests } = await pageFor(t);
  await page.goto(origin);
  await joined(page);
  const frame = await page.locator('iframe').elementHandle();
  const first = await commands(page);
  await page.getByRole('button', { name: 'Change optional props', exact: true }).click();
  await page.getByText('Notes revision 1', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Toggle notes', exact: true }).click();
  assert.equal(await page.getByText('Notes revision 1', { exact: true }).count(), 0);
  await page.getByRole('button', { name: 'Toggle notes', exact: true }).click();
  await page.getByText('Notes revision 1', { exact: true }).waitFor();
  await page.locator('iframe').evaluate(frame => {
    frame.contentWindow.sendFixture({ type: 'ready' });
    frame.contentWindow.sendFixture({ type: 'status', phase: 'joined' });
  });
  await settle(page);
  assert.equal(await frame.evaluate(node => node === document.querySelector('iframe')), true);
  assert.deepEqual(await commands(page), first);
  assert.equal(first[0].credentials.signature, 'fixture.signature.0');
  assert.deepEqual(await callbacks(page), { joined: 1, left: 0, errors: 0 });
  assert.equal(requests.filter(url => url.endsWith('/call/zoom/client.html')).length, 1);
});

test('deliberate optional render failure preserves the same joined iframe and media document', async t => {
  const { page } = await pageFor(t);
  await page.goto(origin);
  await joined(page);
  const frame = await page.locator('iframe').elementHandle();
  await page.getByRole('button', { name: 'Break notes', exact: true }).click();
  await page.getByText('Заметки временно недоступны. Звонок продолжается.', { exact: true }).waitFor();
  assert.equal(await frame.evaluate(node => node === document.querySelector('iframe')), true);
  await joined(page);
  assert.equal((await commands(page)).length, 1);
  assert.deepEqual(await callbacks(page), { joined: 1, left: 0, errors: 0 });
});

test('local leave sends only child leave command; duplicate left is idempotent and credentials never reach URLs/storage', async t => {
  const { page, requests } = await pageFor(t);
  await page.goto(origin);
  await joined(page);
  assert.equal(await page.locator('iframe').getAttribute('src'), '/call/zoom/client.html');
  await page.getByRole('button', { name: 'Leave locally', exact: true }).click();
  await page.locator('.md-zoom-call[data-phase="left"]').waitFor();
  await settle(page);
  const received = await commands(page);
  assert.deepEqual(received.map(command => command.type), ['join', 'leave']);
  assert.deepEqual(received[1], { source: 'hr-zoom-parent', type: 'leave' });
  assert.deepEqual(await callbacks(page), { joined: 1, left: 1, errors: 0 });
  assert.equal(requests.some(url => url.includes('/api/')), false);
  assert.equal(requests.some(url => /fixture\.signature|fixture-passcode|fixture-host-token|12345678901/.test(url)), false);
  assert.deepEqual(await page.evaluate(() => [Object.keys(localStorage), Object.keys(sessionStorage)]), [[], []]);
});

test('expired readiness deadline rejects a late ready and cannot begin a hidden call', async t => {
  const { page } = await pageFor(t);
  // Intercept only this adapter's 15-second readiness deadline. No shortened
  // production timer or test that sleeps for the whole timeout is needed.
  await page.addInitScript(() => {
    const original = window.setTimeout.bind(window);
    window.setTimeout = (callback, delay, ...args) => {
      if (delay === 15000) { window.expireZoomReady = () => callback(...args); return 0; }
      return original(callback, delay, ...args);
    };
  });
  await page.goto(origin + '/?manualReady=1');
  await page.frameLocator('iframe').getByText('Fixture ready', { exact: true }).waitFor();
  await page.waitForFunction(() => typeof window.expireZoomReady === 'function');
  await page.evaluate(() => window.expireZoomReady());
  await page.getByRole('alert').getByText('Окно Zoom не загрузилось. Выйдите и повторите подключение.', { exact: true }).waitFor();
  await page.locator('iframe').evaluate(frame => {
    frame.contentWindow.sendFixture({ type: 'ready' });
    frame.contentWindow.sendFixture({ type: 'status', phase: 'joined' });
  });
  await settle(page);
  assert.deepEqual(await commands(page), []);
  assert.deepEqual(await callbacks(page), { joined: 0, left: 0, errors: 1 });
  assert.equal(await page.locator('.md-zoom-call').getAttribute('data-phase'), 'error');
});

test('local leave before child readiness cannot authorize a delayed join even if frame remains mounted', async t => {
  const { page, requests } = await pageFor(t);
  await page.goto(origin + '/?manualReady=1');
  await page.frameLocator('iframe').getByText('Fixture ready', { exact: true }).waitFor();
  // Simulate the leave command being lost before the child installed its
  // listener: suppress its left acknowledgment, then let ready arrive late.
  await page.locator('iframe').evaluate(frame => { frame.contentWindow.sendFixture = () => {}; });
  await page.getByRole('button', { name: 'Leave locally', exact: true }).click();
  await page.frameLocator('iframe').getByText('Fixture left', { exact: true }).waitFor();
  const frame = page.frames().find(frame => frame.url().endsWith('/call/zoom/client.html'));
  await frame.evaluate(() => {
    parent.postMessage({ source: 'hr-zoom-client', type: 'ready' }, location.origin);
    parent.postMessage({ source: 'hr-zoom-client', type: 'status', phase: 'joined' }, location.origin);
  });
  await settle(page);
  assert.deepEqual((await commands(page)).map(command => command.type), ['leave']);
  assert.equal(await page.locator('iframe').count(), 1);
  assert.deepEqual(await callbacks(page), { joined: 0, left: 0, errors: 0 });
  assert.equal(requests.some(url => url.includes('/api/')), false);
  await frame.evaluate(() => {
    parent.postMessage({ source: 'hr-zoom-client', type: 'status', phase: 'left' }, location.origin);
  });
  await page.locator('.md-zoom-call[data-phase="left"]').waitFor();
  assert.deepEqual(await callbacks(page), { joined: 0, left: 1, errors: 0 });
});

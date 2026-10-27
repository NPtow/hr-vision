// Real, built Zoom SDK with every external request blocked. No meeting is joined.
import assert from 'node:assert/strict';
import { before, after, test } from 'node:test';
import { createServer } from 'node:http';
import { readFileSync, existsSync } from 'node:fs';
import { gzipSync } from 'node:zlib';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';
import { build } from 'esbuild';

const playwright = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : 'playwright');
const browserName = process.env.PLAYWRIGHT_BROWSER || 'chromium';
const root = fileURLToPath(new URL('../zoom-client/dist/', import.meta.url));
const mime = { '.js': 'text/javascript', '.json': 'application/json', '.html': 'text/html', '.css': 'text/css', '.wasm': 'application/wasm', '.woff2': 'font/woff2', '.png': 'image/png', '.svg': 'image/svg+xml' };
let browser, server, origin, isolated = false, fault = '', requests = [], held = [], holdWasm = false;
const compressed = new Map();

before(async () => {
  assert.ok(existsSync(path.join(root, 'call/zoom/bootstrap.json')), 'Run npm run zoom:build first');
  const bundle = await build({ stdin: { contents: `import React, { useRef, useState } from 'react';
    import { createRoot } from 'react-dom/client';
    import { ZoomCallFrame } from './src/components/call/ZoomCallFrame';
    window.statuses=[]; window.joins=0;
    window.addEventListener('message', event => { if (event.origin===location.origin && event.data?.source==='hr-zoom-client') window.statuses.push(event.data); });
    function Fixture() { const [shown,setShown]=useState(true); const call=useRef(null);
      return <><button onClick={()=>{ call.current?.leave(); setShown(false); }}>Cancel preparation</button>
        <div style={{height:500}}>{shown && <ZoomCallFrame ref={call} credentials={null} onJoined={()=>window.joins++}/>}</div></>; }
    createRoot(document.getElementById('root')).render(<Fixture/>);`, loader: 'tsx', resolveDir: fileURLToPath(new URL('../', import.meta.url)) },
    bundle: true, write: false, format: 'esm', jsx: 'automatic', loader: { '.css': 'empty' } });
  server = createServer((request, response) => {
    const name = new URL(request.url, 'http://local').pathname;
    requests.push(name);
    if (isolated) { response.setHeader('Cross-Origin-Opener-Policy', 'same-origin'); response.setHeader('Cross-Origin-Embedder-Policy', 'require-corp'); }
    response.setHeader('Cache-Control', 'no-store');
    if (name === '/component.js') { response.setHeader('Content-Type', 'text/javascript'); return response.end(bundle.outputFiles[0].contents); }
    if (name === '/') { response.setHeader('Content-Type', 'text/html'); return response.end('<!doctype html><div id="root"></div><script type="module" src="/component.js"></script>'); }
    if (name === '/favicon.ico') { response.writeHead(204); return response.end(); }
    const file = path.resolve(root, '.' + name);
    if (!file.startsWith(root) || !existsSync(file)) { response.writeHead(404); return response.end('Not found'); }
    if ((fault === 'css' && name.endsWith('.css')) || (fault === 'wasm' && name.endsWith('.wasm'))) { response.writeHead(503); return response.end('Fixture asset outage'); }
    if (fault === 'invalid-wasm' && name.endsWith('.wasm')) { response.setHeader('Content-Type', 'application/wasm'); return response.end('not a WebAssembly module'); }
    const send = () => {
      if (response.destroyed) return;
      if (!compressed.has(file)) compressed.set(file, gzipSync(readFileSync(file)));
      response.setHeader('Content-Type', mime[path.extname(file)] || 'application/octet-stream');
      response.setHeader('Content-Encoding', 'gzip');
      response.end(compressed.get(file));
    };
    if (holdWasm && name.endsWith('.wasm')) held.push(send); else send();
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
  browser = await playwright[browserName].launch({ headless: true, ...(browserName === 'chromium' ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) });
});
after(async () => { held.forEach(send => send()); await browser?.close(); if (server) await new Promise(resolve => server.close(resolve)); });

async function fixture(t, options = {}) {
  requests = []; fault = options.fault || ''; isolated = options.isolated || false; holdWasm = options.holdWasm || false; held = [];
  const page = await browser.newPage(); page.setDefaultTimeout(30000); t.after(() => page.close());
  const external = [], errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    window.captureRequests = 0;
    navigator.mediaDevices.getUserMedia = () => { window.captureRequests++; return Promise.reject(new Error('No device capture in preparation')); };
  });
  await page.route('**/*', route => {
    if (route.request().url().startsWith(origin + '/')) return route.continue();
    external.push(route.request().url()); return route.abort();
  });
  await page.goto(origin);
  return { page, external, errors };
}
async function prepared(page) { await page.waitForFunction(() => window.statuses.some(message => message.phase === 'prepared')); }
async function noJoin(page) {
  assert.equal(await page.evaluate(() => window.joins), 0);
  assert.equal(await page.evaluate(() => window.statuses.some(message => ['joining', 'joined'].includes(message.phase))), false);
  assert.equal(await page.frames().find(frame => frame.url().endsWith('/call/zoom/client.html')).evaluate(() => window.captureRequests), 0);
  assert.equal(requests.some(name => name.startsWith('/api/')), false);
}

for (const isolation of [false, true]) test(`local SDK prepares without a CDN, credentials or media capture (isolation=${isolation})`, async t => {
  const start = performance.now();
  const { page, external, errors } = await fixture(t, { isolated: isolation });
  await prepared(page);
  assert.equal(await page.evaluate(() => crossOriginIsolated), isolation);
  assert.ok(requests.some(name => name.endsWith('/ui/zoom-meetingsdk.css')));
  assert.ok(requests.some(name => name.endsWith('/lang/ru-RU.json')));
  assert.ok(requests.some(name => name.endsWith('/audio.simd.wasm')));
  assert.ok(requests.some(name => name.endsWith(isolation ? '/video.mtsimd.wasm' : '/video.simd.wasm')));
  assert.deepEqual(external, []); assert.deepEqual(errors, []);
  await noJoin(page);
  t.diagnostic(`SDK prepared in ${Math.round(performance.now() - start)} ms on localhost; ${new Set(requests).size} local paths. This is not live-call latency.`);
});

for (const kind of ['css', 'wasm', 'invalid-wasm']) test(`a ${kind} failure is not ready; explicit retry recovers in a new document`, async t => {
  const { page, external } = await fixture(t, { fault: kind });
  await page.getByRole('button', { name: 'Повторить загрузку', exact: true }).waitFor();
  assert.equal(await page.evaluate(() => window.statuses.some(message => message.phase === 'prepared')), false);
  assert.equal(await page.evaluate(() => window.statuses.at(-1).code), 'sdk_assets');
  await noJoin(page);
  const old = await page.locator('iframe').elementHandle(); fault = '';
  await page.getByRole('button', { name: 'Повторить загрузку', exact: true }).click();
  await prepared(page);
  assert.equal(await old.evaluate(node => node.isConnected), false);
  assert.deepEqual(external, []);
  await noJoin(page);
});

test('cancelled slow WASM cannot report readiness or initiate a call later', async t => {
  const { page } = await fixture(t, { holdWasm: true });
  await page.waitForRequest(request => request.url().endsWith('.wasm'));
  await page.getByRole('button', { name: 'Cancel preparation', exact: true }).click();
  await page.locator('iframe').waitFor({ state: 'detached' });
  holdWasm = false; held.forEach(send => send());
  await page.waitForTimeout(200);
  assert.equal(await page.evaluate(() => window.statuses.some(message => ['prepared', 'joining', 'joined'].includes(message.phase))), false);
  assert.equal(await page.evaluate(() => window.joins), 0);
});

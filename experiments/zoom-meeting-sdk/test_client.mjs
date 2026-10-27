import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import vm from 'node:vm';

const source = await fs.readFile(new URL('./web/client.js', import.meta.url), 'utf8');
const tick = () => new Promise((resolve) => setImmediate(resolve));

async function harness({ search = '?meeting=test-meeting&mode=guest', fetcher, config = {}, storageError = false } = {}) {
  const calls = [], messages = [], events = {}, elements = {};
  const defaults = { meetingNumber: '12345678901', signature: 'test-only-signature', userName: 'Test guest', passWord: 'test-only-passcode', role: 0 };
  const sdk = {
    preLoadWasm: () => calls.push('wasm'),
    prepareWebSDK: () => calls.push('prepare'),
    inMeetingServiceListener: (name, fn) => { events[name] = fn; },
    i18n: { load: () => {}, onLoad: (fn) => fn() },
    init: (options) => { calls.push('init'); options.success(); },
    join: (options) => { calls.push({ join: options }); options.success(); },
  };
  const context = vm.createContext({
    URLSearchParams, AbortController, setTimeout, clearTimeout,
    location: { origin: 'http://127.0.0.1:4321', search },
    window: { isSecureContext: true, crossOriginIsolated: false, addEventListener: (name, fn) => { events[name] = fn; } },
    document: { getElementById: (id) => elements[id] ??= { hidden: true, textContent: '' } },
    parent: { postMessage: (value) => messages.push(value) },
    sessionStorage: { getItem: () => { if (storageError) throw Error('storage blocked'); return 'Test guest'; } },
    fetch: fetcher ?? (async () => ({ ok: true, json: async () => ({ ...defaults, ...config }) })),
  });
  let imports = 0;
  const module = new vm.SourceTextModule(source, {
    context,
    importModuleDynamically: async () => {
      imports++;
      const dependency = new vm.SyntheticModule(['ZoomMtg'], function () { this.setExport('ZoomMtg', sdk); }, { context });
      await dependency.link(() => {});
      await dependency.evaluate();
      return dependency;
    },
  });
  await module.link(() => {});
  await module.evaluate();
  await tick();
  return { calls, messages, events, elements, imports: () => imports };
}

test('guest follows one documented SDK lifecycle and does not receive host ZAK', async () => {
  const h = await harness();
  assert.equal(h.imports(), 1);
  assert.deepEqual(h.calls.slice(0, 3), ['wasm', 'prepare', 'init']);
  assert.equal(h.calls.filter((c) => c.join).length, 1);
  assert.equal('zak' in h.calls[3].join, false);
  assert.equal(h.messages.at(-1).phase, 'joined'); // SDK acknowledgment, not media proof.
});

test('host uses server-provided ZAK; guest refuses a host response', async () => {
  const h = await harness({ search: '?meeting=test-meeting&mode=host', config: { role: 1, zak: 'test-only-zak' } });
  assert.equal(h.calls.find((c) => c.join).join.zak, 'test-only-zak');
  const invalid = await harness({ config: { role: 1, zak: 'must-not-reach-guest' } });
  assert.equal(invalid.imports(), 0);
  assert.equal(invalid.messages.at(-1).phase, 'error');
});

test('failed authorization never initializes SDK', async () => {
  const h = await harness({ fetcher: async () => ({ ok: false }) });
  assert.equal(h.imports(), 0);
  assert.equal(h.messages.at(-1).phase, 'error');
});

test('late authorization after leaving cannot start a session', async () => {
  let resolve;
  const response = new Promise((done) => { resolve = done; });
  const h = await harness({ fetcher: () => response });
  h.events.pagehide();
  resolve({ ok: true, json: async () => ({ meetingNumber: '12345678901', signature: 'test', userName: 'Test', passWord: '', role: 0 }) });
  await tick();
  assert.equal(h.imports(), 0);
  assert.equal(h.calls.length, 0);
});

test('storage failure cannot gate the call', async () => {
  const h = await harness({ storageError: true });
  assert.equal(h.messages.at(-1).phase, 'joined');
});

test('leave destination does not request a token or initialize another SDK', async () => {
  const h = await harness({ search: '?left=1', fetcher: () => { throw Error('must not fetch'); } });
  assert.equal(h.imports(), 0);
  assert.equal(h.messages.at(-1).phase, 'left');
});

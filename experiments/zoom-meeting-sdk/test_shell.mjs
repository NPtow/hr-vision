// Synthetic tests of our shell boundary only. These do not test Zoom media.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('./web/app.js', import.meta.url), 'utf8');
const html = readFileSync(new URL('./web/index.html', import.meta.url), 'utf8');
const optionalIds = [
  'side-panel', 'notes-tab', 'questions-tab', 'export-notes', 'notes-workspace',
  'notes-view', 'general-notes', 'questions-view', 'questions-list', 'add-question',
  'notes-recovery', 'restore-notes', 'export-recovery', 'draft-status',
  'recordings-section', 'refresh-recordings', 'recordings-status', 'recording-files', 'fail-notes',
];

class Element {
  constructor(tag = 'div') {
    this.tag = tag; this.children = []; this.handlers = {}; this.attributes = {};
    this.dataset = {}; this.value = ''; this.textContent = ''; this.hidden = false;
    this.contentWindow = {};
    const classes = new Set();
    this.classList = { toggle(name, force) { const on = force ?? !classes.has(name); on ? classes.add(name) : classes.delete(name); return on; } };
  }
  addEventListener(name, callback) { this.handlers[name] = callback; }
  fire(name, event = {}) { return this.handlers[name]?.({ preventDefault() {}, ...event }); }
  click() { return this.fire('click'); }
  setAttribute(name, value) { this.attributes[name] = value; }
  getAttribute(name) { return this.attributes[name]; }
  append(...items) { for (const item of items) { item.parent = this; this.children.push(item); } }
  replaceChildren(...items) { this.children = []; this.append(...items); }
  querySelectorAll(tag) { return this.children.flatMap(item => [...(item.tag === tag ? [item] : []), ...item.querySelectorAll(tag)]); }
  querySelector(tag) { return this.querySelectorAll(tag)[0]; }
  get lastElementChild() { return this.children.at(-1); }
  remove() { this.parent.children = this.parent.children.filter(item => item !== this); }
  focus() {}
  setCustomValidity() {}
  reportValidity() {}
}

async function shell({ omit = [], configured = true, storageFails = false, response, breakBootstrap } = {}) {
  const elements = Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)]
    .filter(match => !omit.includes(match[1])).map(match => [match[1], new Element()]));
  const calls = [], events = {}, stored = new Map();
  const storage = {
    getItem(key) { if (storageFails) throw new Error('Synthetic unavailable storage'); return stored.get(key) || null; },
    setItem(key, value) { if (storageFails) throw new Error('Synthetic unavailable storage'); stored.set(key, value); },
  };
  breakBootstrap?.(elements);
  const context = vm.createContext({
    URL, URLSearchParams, Blob, AbortController, setTimeout, clearTimeout,
    crypto: { randomUUID }, location: new URL('http://127.0.0.1:4321/'),
    localStorage: storage, sessionStorage: storage,
    document: { getElementById: id => elements[id] || null, createElement: tag => new Element(tag) },
    window: { addEventListener: (name, handler) => events[name] = handler },
    navigator: { clipboard: { writeText: async () => {} } },
  });
  context.history = { replaceState(_state, _title, url) { context.location = new URL(url, context.location); } };
  context.fetch = async (path, options) => {
    const call = { path, options }; calls.push(call);
    const supplied = await response?.(call);
    const data = supplied ?? (path === '/api/config'
      ? { configured, hostSession: true, missing: configured ? [] : ['ZOOM_HOST_USER_ID'] }
      : path.endsWith('/recordings') ? { status: 'absent', files: [] }
        : { meeting: { id: 'synthetic-meeting', topic: 'Synthetic test only' } });
    return { ok: true, status: 200, json: async () => data };
  };
  let bootstrapError;
  try { vm.runInContext(source, context); } catch (error) { bootstrapError = error; }
  for (let i = 0; i < 8; i++) await new Promise(resolve => setImmediate(resolve));
  return { elements, calls, events, context, bootstrapError, read: expression => vm.runInContext(expression, context) };
}

async function createAndJoin(app) {
  app.elements['display-name'].value = 'Synthetic participant';
  await app.elements['join-form'].fire('submit');
  assert.equal(app.read('state.meeting.id'), 'synthetic-meeting');
  await app.elements['join-form'].fire('submit');
  const frame = app.read('state.frame');
  assert.ok(frame); assert.match(frame.src, /meeting=synthetic-meeting&mode=host/);
  return frame;
}

for (const missing of [['general-notes'], ['draft-status'], ['questions-list'], optionalIds]) {
  test(`missing optional DOM (${missing.length === 1 ? missing[0] : 'entire panel'}) cannot block bootstrap/create/join/exit`, async () => {
    const app = await shell({ omit: missing });
    assert.equal(app.bootstrapError, undefined);
    assert.equal(app.calls.filter(call => call.path === '/api/config').length, 1);
    assert.equal(app.read('state.panelFailed'), true);
    assert.equal(app.elements['join-form'].hidden, false);
    const frame = await createAndJoin(app);
    app.events.message({ origin: 'http://127.0.0.1:4321', source: frame.contentWindow, data: { source: 'zoom-poc-client', type: 'status', phase: 'left' } });
    await app.elements['return-to-join'].click();
    assert.equal(app.read('state.frame'), null);
    assert.equal(app.elements['connection-card'].hidden, false);
  });
}

test('throwing optional event registration remains local', async () => {
  const app = await shell({ breakBootstrap(elements) {
    elements['notes-tab'].addEventListener = () => { throw new Error('Synthetic malformed panel'); };
  } });
  assert.equal(app.bootstrapError, undefined);
  assert.equal(app.read('state.panelFailed'), true);
  assert.equal(app.calls[0].path, '/api/config');
  await createAndJoin(app);
});

test('required call DOM failure is not classified as a notes failure', async () => {
  const app = await shell({ omit: ['join-form'] });
  assert.ok(app.bootstrapError);
  assert.equal(app.read('state.panelFailed'), false);
});

test('runtime notes error, storage refusal, tabs, collapse and recording read retain the SDK iframe', async () => {
  const app = await shell({ storageFails: true });
  const frame = await createAndJoin(app);
  app.elements['general-notes'].value = 'Synthetic draft';
  await app.elements['general-notes'].fire('input');
  assert.match(app.elements['draft-status'].textContent, /в памяти страницы/);
  await app.elements['questions-tab'].click(); await app.elements['add-question'].click();
  await app.elements['fail-notes'].click(); assert.equal(app.elements['notes-recovery'].hidden, false);
  await app.elements['toggle-panel'].click(); await app.elements['toggle-panel'].click();
  await app.elements['restore-notes'].click(); await app.elements['refresh-recordings'].click();
  Object.defineProperty(app.elements['draft-status'], 'textContent', { set() { throw new Error('Synthetic damaged status DOM'); } });
  assert.doesNotThrow(() => app.elements['general-notes'].fire('input'));
  assert.equal(app.read('state.panelFailed'), true);
  assert.equal(app.read('state.frame'), frame);
  assert.equal(app.elements['sdk-container'].children.length, 1);
  assert.equal(app.read('state.draft.general'), 'Synthetic draft');
});

test('unconfigured account does not fabricate a room', async () => {
  const app = await shell({ configured: false });
  assert.equal(app.elements['join-form'].hidden, true);
  assert.equal(app.elements['sdk-container'].children.length, 0);
  assert.match(app.elements['connection-description'].textContent, /нужно подключить аккаунт/);
});

test('create retries preserve operation identity even when session storage is unavailable', async () => {
  const ids = [];
  const app = await shell({ storageFails: true, response({ path, options }) {
    if (path !== '/api/meetings') return;
    ids.push(JSON.parse(options.body).requestId);
    if (ids.length === 1) throw new TypeError('Synthetic lost response');
  } });
  app.elements['display-name'].value = 'Synthetic participant';
  await app.elements['join-form'].fire('submit'); await app.elements['join-form'].fire('submit');
  assert.equal(ids.length, 2); assert.equal(ids[0], ids[1]);
});

test('a second recording download cannot queue behind an active download', async () => {
  let releaseDownload;
  const app = await shell({ async response({ path }) {
    if (path.endsWith('/recordings')) return { status: 'ready', files: ['a', 'b'].map(id => ({ id, fileType: 'MP4', status: 'completed' })) };
    if (path.endsWith('/download')) { await new Promise(resolve => releaseDownload = resolve); return { status: 'downloaded' }; }
  } });
  const frame = await createAndJoin(app);
  await app.elements['refresh-recordings'].click();
  const buttons = app.elements['recording-files'].querySelectorAll('button');
  const first = buttons[0].click();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(buttons[1].disabled, true); await buttons[1].click();
  assert.equal(app.calls.filter(call => call.path.endsWith('/download')).length, 1);
  releaseDownload(); await first;
  assert.equal(buttons[0].textContent, 'Сохранено локально');
  assert.equal(buttons[0].disabled, true); assert.equal(buttons[1].disabled, false);
  assert.equal(app.read('state.frame'), frame);
});

import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import vm from 'node:vm';

const source = await fs.readFile(new URL('../zoom-client/src/client.js', import.meta.url), 'utf8');
const leftSource = await fs.readFile(new URL('../zoom-client/src/left.js', import.meta.url), 'utf8');
const tick = () => new Promise((done) => setImmediate(done));
const credentials = (extra = {}) => ({
  provider: 'zoom', sdkVersion: '6.10.0', meetingNumber: '12345678901',
  signature: 'test.signature.only', passWord: 'test-passcode', userName: 'Тест', role: 0,
  ...extra,
});

async function harness({ sdkOverrides = {}, importGate, importFailure = false, secure = true,
  standalone = false, script = source } = {}) {
  const calls = [], messages = [], events = {}, elements = {}, timers = new Map(), navigations = [];
  let imports = 0, timerId = 0;
  const sdk = {
    preLoadWasm: () => calls.push('wasm'),
    prepareWebSDK: () => calls.push('prepare'),
    inMeetingServiceListener: (name, fn) => { events[name] = fn; },
    i18n: { load: (locale) => calls.push({ locale }), onLoad: (fn) => { events.localeReady = fn; fn(); } },
    init: (options) => { calls.push({ init: options }); events.init = options; options.success(); },
    join: (options) => { calls.push({ join: options }); events.join = options; options.success(); },
    leaveMeeting: () => { throw new Error('must not end meeting for host'); },
    ...sdkOverrides,
  };
  const parent = { postMessage: (message, origin) => messages.push({ ...message, targetOrigin: origin }) };
  const window = {
    parent, isSecureContext: secure, crossOriginIsolated: false,
    addEventListener: (name, fn) => { events[name] = fn; },
  };
  if (standalone) window.parent = window;
  const context = vm.createContext({
    window, AbortController,
    location: { origin: 'https://mashadasha.tech', replace: (path) => navigations.push(path) },
    document: { getElementById: (id) => elements[id] ??= { hidden: true, textContent: '' } },
    setTimeout: (fn, ms) => { timers.set(++timerId, { fn, ms }); return timerId; },
    clearTimeout: (id) => timers.delete(id),
    fetch: () => { throw new Error('frame must not request credentials'); },
    localStorage: { setItem: () => { throw new Error('must not persist credentials'); } },
    sessionStorage: { setItem: () => { throw new Error('must not persist credentials'); } },
    console: { log: () => { throw new Error('must not log SDK credentials'); }, error: () => { throw new Error('must not log SDK errors'); } },
  });
  const module = new vm.SourceTextModule(script, { context });
  await module.link(async specifier => {
    assert.equal(specifier, './sdk-loader.js');
    return new vm.SyntheticModule(['prepareSdk'], function () { this.setExport('prepareSdk', async signal => {
      imports++;
      if (importGate) await importGate;
      if (importFailure) throw new Error('SECRET_RAW_SDK_ERROR');
      if (signal.aborted) throw new Error('cancelled');
      return sdk;
    }); }, { context });
  });
  await module.evaluate();
  await tick();
  const send = (data, overrides = {}) => events.message({ source: parent, origin: 'https://mashadasha.tech', data, ...overrides });
  const join = (value = credentials(), overrides) => send({ source: 'hr-zoom-parent', type: 'join', credentials: value }, overrides);
  return { calls, messages, events, elements, timers, navigations, imports: () => imports, send, join };
}

test('frame waits for authorized parent and pins the reply origin', async () => {
  const h = await harness();
  assert.equal(h.imports(), 0);
  assert.deepEqual(h.messages, [{ source: 'hr-zoom-client', type: 'ready', targetOrigin: 'https://mashadasha.tech' }]);
  h.join(credentials(), { origin: 'https://attacker.example' });
  h.join(credentials(), { source: {} });
  h.send({ source: 'other-frame', type: 'join', credentials: credentials() });
  await tick();
  assert.equal(h.imports(), 0);
  assert.equal(h.calls.length, 0);
  h.join();
  await tick();
  assert.equal(h.imports(), 1);
  assert.equal(h.messages.at(-1).phase, 'joined');
});

test('one iframe initializes and joins only once despite duplicate messages and callbacks', async () => {
  const h = await harness();
  h.join();
  h.join();
  await tick();
  h.events.init.success();
  h.join();
  assert.equal(h.imports(), 1);
  assert.equal(h.calls.filter((call) => call.init).length, 1);
  assert.equal(h.calls.filter((call) => call.join).length, 1);
  const options = h.calls.find((call) => call.init).init;
  assert.equal(options.disablePreview, false);
  assert.equal(options.enableWaitingRoomPreview, true);
  assert.equal(options.disableCORP, true);
  assert.equal(options.leaveUrl, 'https://mashadasha.tech/call/zoom/left.html');
  assert.equal(h.timers.size, 0);
});

test('credentials only reach SDK join; guest has no host ZAK and no credentials in messages or URL', async () => {
  for (const role of [0, 1]) {
    const h = await harness();
    h.join(credentials(role === 1 ? { role, zak: 'private-host-token' } : {}));
    await tick();
    const options = h.calls.find((call) => call.join).join;
    assert.equal(options.signature, 'test.signature.only');
    assert.equal('zak' in options, role === 1);
    if (role === 1) assert.equal(options.zak, 'private-host-token');
    const publicData = JSON.stringify({ messages: h.messages, navigations: h.navigations, leaveUrl: h.events.init.leaveUrl });
    for (const secret of ['test.signature.only', 'test-passcode', 'private-host-token', '12345678901']) {
      assert.equal(publicData.includes(secret), false);
    }
  }
});

test('malformed, unsupported or host-escalating credentials never load the SDK', async () => {
  for (const value of [null, credentials({ provider: 'daily' }), credentials({ sdkVersion: '6.9.0' }),
    credentials({ meetingNumber: 'https://invalid' }), credentials({ signature: 'not-a-jwt' }),
    credentials({ role: 2 }), credentials({ role: 1 }), credentials({ zak: 'host-secret' }),
    credentials({ userName: '' }), credentials({ passWord: '\n' })]) {
    const h = await harness();
    h.join(value);
    await tick();
    assert.equal(h.imports(), 0);
    assert.equal(h.messages.at(-1).code, 'invalid_credentials');
  }
});

test('standalone and insecure pages do not initialize the SDK', async () => {
  const direct = await harness({ standalone: true });
  direct.join();
  assert.equal(direct.imports(), 0);
  assert.equal(direct.messages.length, 0);
  const insecure = await harness({ secure: false });
  insecure.join();
  assert.equal(insecure.imports(), 0);
  assert.equal(insecure.messages.at(-1).code, 'insecure_context');
});

test('leave during SDK load prevents late initialization and never invokes host end-meeting', async () => {
  let resolve;
  const h = await harness({ importGate: new Promise((done) => { resolve = done; }) });
  h.join(credentials({ role: 1, zak: 'private-host-token' }));
  h.send({ source: 'hr-zoom-parent', type: 'leave' });
  h.send({ source: 'hr-zoom-parent', type: 'leave' });
  assert.equal(h.messages.at(-1).phase, 'left');
  assert.deepEqual(h.navigations, ['/call/zoom/left.html']);
  resolve();
  await tick();
  assert.equal(h.calls.length, 0);
  assert.equal(h.timers.size, 0);
});

test('pagehide prevents late init and join callbacks from reopening session', async () => {
  let init;
  const h = await harness({ sdkOverrides: { init: (options) => { init = options; } } });
  h.join();
  await tick();
  h.events.pagehide();
  const count = h.messages.length;
  init.success();
  init.error({ errorCode: 500 });
  h.events.onMeetingStatus({ meetingStatus: 2 });
  assert.equal(h.calls.filter((call) => call.join).length, 0);
  assert.equal(h.messages.length, count);
});

test('SDK loading has a deadline but user prejoin and reconnect do not', async () => {
  let resolve;
  const h = await harness({ importGate: new Promise((done) => { resolve = done; }) });
  h.join();
  const deadline = [...h.timers.values()][0];
  assert.equal(deadline.ms, 90000);
  deadline.fn();
  await tick();
  assert.equal(h.messages.at(-1).code, 'sdk_load_timeout');
  resolve();
  await tick();
  assert.equal(h.calls.length, 0);
  const waiting = await harness({ sdkOverrides: { join: () => {} } });
  waiting.join();
  await tick();
  assert.equal(waiting.messages.at(-1).phase, 'joining');
  assert.equal(waiting.timers.size, 0);
  waiting.events.onMeetingStatus({ meetingStatus: 4 });
  assert.equal(waiting.messages.at(-1).phase, 'joining');
  assert.equal(waiting.timers.size, 0);
});

test('SDK failures surface only sanitized error code and fixed copy', async () => {
  const load = await harness({ importFailure: true });
  load.join();
  await tick();
  assert.equal(load.messages.at(-1).code, 'sdk_load');
  assert.equal(JSON.stringify(load.messages).includes('SECRET_RAW_SDK_ERROR'), false);
  for (const stage of ['init', 'join']) {
    const h = await harness({ sdkOverrides: { [stage]: (options) => options.error({ errorCode: 3712, message: 'test.signature.only', signature: 'private' }) } });
    h.join();
    await tick();
    assert.equal(h.messages.at(-1).phase, 'error');
    assert.equal(h.messages.at(-1).code, '3712');
    assert.equal(JSON.stringify(h.messages).includes('test.signature.only'), false);
    assert.equal(h.elements['connection-notice'].hidden, false);
  }
});

test('native disconnect is terminal and left destination cannot trigger a rejoin', async () => {
  const h = await harness();
  h.join();
  await tick();
  h.events.onMeetingStatus({ meetingStatus: 3 });
  h.events.onMeetingStatus({ meetingStatus: 2 });
  h.join();
  assert.equal(h.messages.at(-1).phase, 'left');
  assert.equal(h.imports(), 1);
  const left = await harness({ script: leftSource });
  assert.equal(left.imports(), 0);
  assert.deepEqual(left.messages, [{ source: 'hr-zoom-client', type: 'status', phase: 'left', targetOrigin: 'https://mashadasha.tech' }]);
});

test('preparation downloads the SDK without credentials or starting a meeting; join reuses it', async () => {
  const h = await harness();
  h.send({ source: 'hr-zoom-parent', type: 'prepare' });
  h.send({ source: 'hr-zoom-parent', type: 'prepare' });
  await tick();
  assert.equal(h.imports(), 1);
  assert.equal(h.calls.length, 0);
  assert.equal(h.messages.at(-1).phase, 'prepared');
  h.join();
  await tick();
  assert.equal(h.imports(), 1);
  assert.equal(h.calls.filter(call => call.join).length, 1);
});

test('an init promise rejection is handled even when the SDK never calls its error callback', async () => {
  const h = await harness({ sdkOverrides: { init: () => Promise.reject({ errorCode: 500, message: 'private' }) } });
  h.join();
  await tick();
  assert.equal(h.messages.at(-1).phase, 'error');
  assert.equal(h.calls.filter(call => call.join).length, 0);
  assert.equal(JSON.stringify(h.messages).includes('private'), false);
});

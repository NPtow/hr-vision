import assert from 'node:assert/strict';
import test from 'node:test';
import { CallConnectionError, PreviewTimeoutError, deviceAccessMessage, directCallUrl, releaseCall, waitForCall, waitForPreview } from '../src/scenes/candidate-portal/call-connection.ts';

test('a call that never resolves can be cancelled without waiting for the provider', async () => {
  const controller = new AbortController();
  const pending = waitForCall(new Promise<void>(() => {}), controller.signal);
  const reason = new CallConnectionError('connection deadline');
  controller.abort(reason);
  await assert.rejects(pending, error => error === reason);
});

test('late provider success cannot revive a cancelled connection', async () => {
  const controller = new AbortController();
  let finish!: (value: string) => void;
  let connected = false;
  const pending = waitForCall(new Promise<string>(resolve => { finish = resolve; }), controller.signal);
  pending.then(() => { connected = true; }, () => {});
  controller.abort(new Error('left'));
  finish('joined');
  await assert.rejects(pending, /left/);
  assert.equal(connected, false);
});

test('a successful connection returns its result and is unaffected by later abort', async () => {
  const controller = new AbortController();
  assert.equal(await waitForCall(Promise.resolve('joined'), controller.signal), 'joined');
  controller.abort();
});

test('already cancelled attempts consume late provider rejection', async () => {
  const controller = new AbortController();
  controller.abort(new Error('unmounted'));
  await assert.rejects(waitForCall(Promise.reject('SDK load failed'), controller.signal), /unmounted/);
});

test('an unresponsive iframe cannot block closing indefinitely', async () => {
  await assert.rejects(releaseCall({ destroy: () => new Promise<void>(() => {}) }, 5), CallConnectionError);
});

test('normal frame cleanup and rejected cleanup both settle', async () => {
  await releaseCall({ destroy: () => Promise.resolve() }, 100);
  await assert.rejects(releaseCall({ destroy: () => Promise.reject(new Error('iframe gone')) }, 100), /iframe gone/);
});

test('an unanswered device permission prompt stops blocking entry', async () => {
  await assert.rejects(waitForPreview(new Promise<MediaStream>(() => {}), new AbortController().signal, 5), PreviewTimeoutError);
});

test('camera granted after the preview timed out is immediately released', async () => {
  let grant!: (stream: { getTracks: () => { stop: () => void }[] }) => void;
  let stopped = 0;
  const operation = new Promise<{ getTracks: () => { stop: () => void }[] }>(resolve => { grant = resolve; });
  await assert.rejects(waitForPreview(operation, new AbortController().signal, 5), PreviewTimeoutError);
  grant({ getTracks: () => [{ stop: () => { stopped++; } }] });
  await operation;
  await Promise.resolve();
  assert.equal(stopped, 1);
});

test('leaving preview before permission is granted releases late devices', async () => {
  const controller = new AbortController();
  let grant!: (stream: { getTracks: () => { stop: () => void }[] }) => void;
  let stopped = false;
  const operation = new Promise<{ getTracks: () => { stop: () => void }[] }>(resolve => { grant = resolve; });
  const preview = waitForPreview(operation, controller.signal);
  controller.abort(new Error('left preview'));
  await assert.rejects(preview, /left preview/);
  grant({ getTracks: () => [{ stop: () => { stopped = true; } }] });
  await operation;
  await Promise.resolve();
  assert.equal(stopped, true);
});

test('normal preview keeps the granted devices and preserves permission errors', async () => {
  let stopped = false;
  const media = { getTracks: () => [{ stop: () => { stopped = true; } }] };
  assert.equal(await waitForPreview(Promise.resolve(media), new AbortController().signal), media);
  assert.equal(stopped, false);
  const denied = new DOMException('denied', 'NotAllowedError');
  await assert.rejects(waitForPreview(Promise.reject(denied), new AbortController().signal), error => error === denied);
});

test('fallback preserves the exact private room and encodes its participant token', () => {
  const link = new URL(directCallUrl({ url: 'https://team.daily.co/interview', token: 'test-only.a+b/c=' }));
  assert.equal(link.origin + link.pathname, 'https://team.daily.co/interview');
  assert.equal(link.searchParams.get('t'), 'test-only.a+b/c=');
});

test('fallback cannot send a participant token to another origin', () => {
  for (const url of ['https://evil.test/room', 'https://daily.co.evil.test/room', 'http://team.daily.co/room', 'https://user@team.daily.co/room']) {
    assert.throws(() => directCallUrl({ url, token: 'test-only' }), CallConnectionError);
  }
});

test('device denial, missing hardware, busy hardware and unanswered prompts give distinct recovery steps', () => {
  assert.match(deviceAccessMessage(new DOMException('', 'NotAllowedError')), /настройках сайта/);
  assert.match(deviceAccessMessage(new DOMException('', 'NotFoundError')), /не найдены/);
  assert.match(deviceAccessMessage(new DOMException('', 'NotReadableError')), /другой звонок/);
  assert.match(deviceAccessMessage(new PreviewTimeoutError()), /не ответил/);
  assert.doesNotMatch(deviceAccessMessage(new Error('secret provider detail')), /secret provider detail/);
});

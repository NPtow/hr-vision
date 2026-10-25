import assert from 'node:assert/strict';
import test from 'node:test';
import { startZoomSessionReconcile } from '../src/components/call/zoom-session-reconcile.ts';

const settle = async () => { await Promise.resolve(); await Promise.resolve(); };
const delays = [2000, 5000, 10000, 20000, 30000];

test('a live meeting keeps polling and only explicit completion stops it', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let requests = 0;
  const callbacks = [];
  startZoomSessionReconcile({
    request: async signal => {
      assert.equal(signal.aborted, false);
      return ++requests < 3 ? { completed: false, status: 'live' } : { completed: true, status: 'ended' };
    },
    onPending: () => callbacks.push('pending'), onCompleted: () => callbacks.push('completed'),
    onExhausted: () => callbacks.push('exhausted'),
  });
  assert.equal(requests, 1);
  await settle();
  assert.deepEqual(callbacks, ['pending']);
  t.mock.timers.tick(1999); await settle();
  assert.equal(requests, 1);
  t.mock.timers.tick(1); await settle();
  assert.equal(requests, 2);
  t.mock.timers.tick(5000); await settle();
  assert.equal(requests, 3);
  assert.deepEqual(callbacks, ['pending', 'completed']);
  t.mock.timers.tick(1000000); await settle();
  assert.equal(requests, 3);
});

test('already completed session stops after the immediate request', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let requests = 0;
  const callbacks = [];
  startZoomSessionReconcile({
    request: async () => { requests++; return { completed: true }; },
    onPending: () => callbacks.push('pending'), onCompleted: () => callbacks.push('completed'),
    onExhausted: () => callbacks.push('exhausted'),
  });
  await settle(); t.mock.timers.tick(1000000); await settle();
  assert.equal(requests, 1);
  assert.deepEqual(callbacks, ['completed']);
});

test('status wording and truthy non-boolean values cannot assert completion', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let requests = 0;
  const callbacks = [];
  startZoomSessionReconcile({
    request: async () => ({ completed: ++requests % 2 ? false : 'true', status: 'completed' }),
    onPending: () => callbacks.push('pending'), onCompleted: () => callbacks.push('completed'),
    onExhausted: () => callbacks.push('exhausted'),
  });
  await settle();
  for (const delay of delays) { t.mock.timers.tick(delay); await settle(); }
  assert.equal(requests, 6);
  assert.deepEqual(callbacks, ['pending', 'exhausted']);
});

test('errors consume six bounded attempts without overlapping requests', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let requests = 0, inFlight = 0;
  const callbacks = [];
  startZoomSessionReconcile({
    request: async () => {
      assert.equal(inFlight, 0);
      requests++; inFlight++;
      await Promise.resolve(); inFlight--;
      throw new Error('temporary provider delay');
    },
    onPending: () => callbacks.push('pending'), onCompleted: () => callbacks.push('completed'),
    onExhausted: () => callbacks.push('exhausted'),
  });
  await settle();
  for (const [index, delay] of delays.entries()) {
    t.mock.timers.tick(delay - 1); await settle();
    assert.equal(requests, index + 1);
    t.mock.timers.tick(1); await settle();
    assert.equal(requests, index + 2);
  }
  assert.deepEqual(callbacks, ['pending', 'exhausted']);
  t.mock.timers.tick(1000000); await settle();
  assert.equal(requests, 6);
});

test('cancellation aborts in-flight request and suppresses late responses', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  for (const outcome of ['completed', 'live', 'error']) {
    let signal, resolve, reject, requests = 0;
    const callbacks = [];
    const cancel = startZoomSessionReconcile({
      request: input => {
        requests++; signal = input;
        return new Promise((done, fail) => { resolve = done; reject = fail; });
      },
      onPending: () => callbacks.push('pending'), onCompleted: () => callbacks.push('completed'),
      onExhausted: () => callbacks.push('exhausted'),
    });
    cancel(); cancel();
    assert.equal(signal.aborted, true);
    if (outcome === 'error') reject(new Error('late error'));
    else resolve({ completed: outcome === 'completed' });
    await settle(); t.mock.timers.tick(1000000); await settle();
    assert.equal(requests, 1);
    assert.deepEqual(callbacks, []);
  }
});

test('cancellation while waiting removes the pending retry', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let requests = 0;
  const callbacks = [];
  const cancel = startZoomSessionReconcile({
    request: async () => { requests++; return { completed: false }; },
    onPending: () => callbacks.push('pending'), onCompleted: () => callbacks.push('completed'),
    onExhausted: () => callbacks.push('exhausted'),
  });
  await settle(); cancel();
  t.mock.timers.tick(1000000); await settle();
  assert.equal(requests, 1);
  assert.deepEqual(callbacks, ['pending']);
});

test('presentation failures cannot block retries or cause an extra request', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let requests = 0;
  startZoomSessionReconcile({
    request: async () => ({ completed: ++requests === 2 }),
    onPending: () => { throw new Error('optional presentation failure'); },
    onCompleted: () => { throw new Error('optional presentation failure'); },
  });
  await settle(); t.mock.timers.tick(2000); await settle();
  t.mock.timers.tick(1000000); await settle();
  assert.equal(requests, 2);
});

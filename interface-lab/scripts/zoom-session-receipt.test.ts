import assert from 'node:assert/strict';
import test from 'node:test';
import { startZoomSessionReceipt } from '../src/components/call/zoom-session-receipt.ts';

const settle = async () => { await Promise.resolve(); await Promise.resolve(); };

test('transient metadata failure retries independently and success stops further work', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let requests = 0;
  const callbacks: string[] = [];
  const cancel = startZoomSessionReceipt({
    request: async signal => {
      assert.equal(signal.aborted, false);
      requests++;
      if (requests === 1) throw Object.assign(new Error('temporary metadata outage'), { status: 503 });
    },
    onPending: () => callbacks.push('pending'), onSuccess: () => callbacks.push('success'),
    onExhausted: () => callbacks.push('exhausted'),
  });
  assert.equal(requests, 1);
  await settle();
  assert.deepEqual(callbacks, ['pending']);
  t.mock.timers.tick(999); await settle();
  assert.equal(requests, 1);
  t.mock.timers.tick(1); await settle();
  assert.equal(requests, 2);
  assert.deepEqual(callbacks, ['pending', 'success']);
  t.mock.timers.tick(1000000); await settle();
  assert.equal(requests, 2);
  cancel();
});

test('six failed requests exhaust the bounded schedule without overlapping requests', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let requests = 0, inFlight = 0;
  const callbacks: string[] = [];
  startZoomSessionReceipt({
    request: async () => {
      assert.equal(inFlight, 0);
      inFlight++; requests++;
      await Promise.resolve(); inFlight--;
      throw Object.assign(new Error('receipt not ready'), { status: requests % 2 ? 409 : 502 });
    },
    onPending: () => callbacks.push('pending'), onSuccess: () => callbacks.push('success'),
    onExhausted: () => callbacks.push('exhausted'),
  });
  await settle();
  for (const [index, delay] of [1000, 3000, 8000, 15000, 30000].entries()) {
    t.mock.timers.tick(delay - 1); await settle();
    assert.equal(requests, index + 1);
    t.mock.timers.tick(1); await settle();
    assert.equal(requests, index + 2);
  }
  assert.deepEqual(callbacks, ['pending', 'exhausted']);
  t.mock.timers.tick(1000000); await settle();
  assert.equal(requests, 6);
});

test('cancel aborts in-flight HTTP and suppresses a late success or failure', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  for (const result of ['success', 'failure']) {
    let signal!: AbortSignal, resolve!: () => void, reject!: (reason: unknown) => void;
    let requests = 0;
    const callbacks: string[] = [];
    const cancel = startZoomSessionReceipt({
      request: input => {
        requests++; signal = input;
        return new Promise<void>((done, fail) => { resolve = done; reject = fail; });
      },
      onPending: () => callbacks.push('pending'), onSuccess: () => callbacks.push('success'),
      onExhausted: () => callbacks.push('exhausted'),
    });
    cancel(); cancel();
    assert.equal(signal.aborted, true);
    if (result === 'success') resolve(); else reject(new Error('late network error'));
    await settle(); t.mock.timers.tick(1000000); await settle();
    assert.equal(requests, 1);
    assert.deepEqual(callbacks, []);
  }
});

test('cancel while waiting removes the pending retry', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let requests = 0;
  const callbacks: string[] = [];
  const cancel = startZoomSessionReceipt({
    request: async () => { requests++; throw new TypeError('network offline'); },
    onPending: () => callbacks.push('pending'), onSuccess: () => callbacks.push('success'),
    onExhausted: () => callbacks.push('exhausted'),
  });
  await settle();
  cancel();
  t.mock.timers.tick(1000000); await settle();
  assert.equal(requests, 1);
  assert.deepEqual(callbacks, ['pending']);
});

test('numeric access and invalid-request errors stop immediately without retrying', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  for (const status of [400, 401, 403, 404]) {
    let requests = 0;
    const callbacks: string[] = [];
    startZoomSessionReceipt({
      request: async () => { requests++; throw Object.assign(new Error('denied'), { status }); },
      onPending: () => callbacks.push('pending'), onSuccess: () => callbacks.push('success'),
      onExhausted: () => callbacks.push('exhausted'),
    });
    await settle(); t.mock.timers.tick(1000000); await settle();
    assert.equal(requests, 1);
    assert.deepEqual(callbacks, ['pending', 'exhausted']);
  }
});

test('failed presentation callback does not cancel a recoverable metadata receipt', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let requests = 0, succeeded = false;
  startZoomSessionReceipt({
    request: async () => { if (++requests === 1) throw new Error('network offline'); },
    onPending: () => { throw new Error('optional presentation failure'); },
    onSuccess: () => { succeeded = true; },
  });
  await settle(); t.mock.timers.tick(1000); await settle();
  assert.equal(requests, 2);
  assert.equal(succeeded, true);
});

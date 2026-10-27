import assert from 'node:assert/strict';
import test from 'node:test';
import { CallConnectionError, releaseCall, waitForCall } from '../src/scenes/candidate-portal/call-connection.ts';

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

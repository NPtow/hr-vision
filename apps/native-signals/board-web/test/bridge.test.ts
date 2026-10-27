import test from 'node:test';
import assert from 'node:assert/strict';
const sent: unknown[] = [];
(globalThis as Record<string, unknown>).window = { webkit: { messageHandlers: { board: { postMessage: (message: unknown) => sent.push(message) } } } };
const { post, subscribe, sessionId } = await import('../src/bridge.ts');

test('commands include session and protocol and retry preserves transaction ID/payload', () => {
  const payload = { transactionId: 'same-request', documentId: 'document', baseRevision: 3, operations: [] };
  assert.equal(post('transaction', payload), true); post('transaction', payload);
  assert.deepEqual(sent[0], sent[1]);
  assert.equal((sent[0] as Record<string, unknown>).sessionId, sessionId);
  assert.equal((sent[0] as Record<string, unknown>).protocolVersion, 1);
});
test('bridge ignores stale renderer messages and validates current messages', () => {
  const received: unknown[] = []; const errors: string[] = [];
  const unsubscribe = subscribe(message => received.push(message), message => errors.push(message));
  window.boardHost.receive({ protocolVersion: 1, sessionId: 'previous', type: 'error', message: 'stale' });
  assert.equal(received.length, 0); assert.equal(errors.length, 0);
  window.boardHost.receive({ protocolVersion: 1, sessionId, type: 'error', message: 'current' });
  assert.equal(received.length, 1);
  window.boardHost.receive({ protocolVersion: 3, sessionId, type: 'state' });
  assert.equal(errors.length, 1);
  unsubscribe();
});

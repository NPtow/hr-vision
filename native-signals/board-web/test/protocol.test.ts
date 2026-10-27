import test from 'node:test';
import assert from 'node:assert/strict';
import { makeNode, validDocument, parseHostMessage, canConnect, deletionOperations, type BoardDocument, type HostState } from '../src/protocol.ts';
import { draftFor, draftIsDirty, reconcileDraft, isAcknowledged } from '../src/session.ts';

// Synthetic identifiers exercise the wire contract only; runtime has no seeded data.
const node = (id: string) => makeNode('note', id, { x: 0, y: 0 });
const document = (): BoardDocument => ({ id: 'test-document', companyId: 'test-company', title: '', revision: 0, nodes: [node('a'), node('b')], edges: [] });
const state = (): HostState => ({ protocolVersion: 1, sessionId: 'current', type: 'state', document: document(), sources: [], contacts: [], canUndo: false, canRedo: false });

test('empty authoritative board is supported without inventing output content', () => {
  const message = state(); message.document.nodes = [];
  assert.deepEqual(parseHostMessage(message, 'current'), message);
  const offer = makeNode('offer', 'offer', { x: 0, y: 0 });
  assert.equal(offer.body, ''); assert.equal(offer.status, 'requiresVerification');
});
test('source and contact creation requires a host reference; body is not copied into graph', () => {
  assert.throws(() => makeNode('source', 'x', { x: 0, y: 0 }));
  assert.throws(() => makeNode('contact', 'x', { x: 0, y: 0 }));
  const result = makeNode('source', 'x', { x: 0, y: 0 }, { id: 'archive-id', title: '<img onerror=evil>', body: 'archival text' });
  assert.equal(result.referenceId, 'archive-id'); assert.equal(result.body, ''); assert.equal(result.title, '<img onerror=evil>');
});
test('reject incompatible protocol, missing session and previous renderer session', () => {
  assert.throws(() => parseHostMessage({ ...state(), protocolVersion: 2 }, 'current'));
  assert.throws(() => parseHostMessage({ ...state(), sessionId: undefined }, 'current'));
  assert.throws(() => parseHostMessage({ ...state(), sessionId: 'old' }, 'current'));
});
test('unsupported model, dangling edges and duplicate stable IDs fail closed', () => {
  for (const mutate of [
    (doc: BoardDocument) => { doc.nodes[0].kind = 'unknown' as never; },
    (doc: BoardDocument) => { doc.nodes[0].position.x = Infinity; },
    (doc: BoardDocument) => { doc.nodes.push(node('a')); },
    (doc: BoardDocument) => { doc.edges.push({ id: 'edge', source: 'a', target: 'missing', kind: 'supports' }); },
    (doc: BoardDocument) => { doc.revision = -1; },
    (doc: BoardDocument) => { doc.nodes[0].kind = 'source'; },
  ]) { const doc = document(); mutate(doc); assert.equal(validDocument(doc), false); }
});
test('connection validation prevents self-links, nonexistent endpoints and duplicate typed edges', () => {
  const doc = document();
  assert.equal(canConnect(doc, 'a', 'a', 'supports'), false);
  assert.equal(canConnect(doc, 'a', 'missing', 'supports'), false);
  assert.equal(canConnect(doc, 'a', 'b', 'supports'), true);
  doc.edges.push({ id: 'edge', source: 'a', target: 'b', kind: 'supports' });
  assert.equal(canConnect(doc, 'a', 'b', 'supports'), false);
  assert.equal(canConnect(doc, 'a', 'b', 'contradicts'), true);
});
test('node deletion delegates incident edge cleanup to native once', () => {
  const doc = document(); doc.nodes.push(node('c'));
  doc.edges = [{ id: 'incident', source: 'a', target: 'b', kind: 'supports' }, { id: 'other', source: 'b', target: 'c', kind: 'supports' }];
  assert.deepEqual(deletionOperations(doc, ['a', 'a', 'missing'], ['incident', 'other']), [{ type: 'removeEdge', edgeId: 'other' }, { type: 'deleteNode', nodeId: 'a' }]);
});
test('unsolicited state is never treated as an ACK and cannot finish an active drag', () => {
  assert.equal(isAcknowledged(undefined, undefined), false);
  assert.equal(isAcknowledged('pending', undefined), false);
  assert.equal(isAcknowledged('pending', 'old-request'), false);
  assert.equal(isAcknowledged('pending', 'pending'), true);
});
test('dirty draft survives incoming conflicting canonical text', () => {
  const original = { title: 'Заметка', body: '', status: 'requiresVerification' as const };
  const draft = { ...draftFor(original), body: 'Ручной ввод' };
  const incoming = { ...original, body: 'Изменение из другого клиента' };
  const result = reconcileDraft(draft, incoming, false);
  assert.equal(result.body, 'Ручной ввод'); assert.equal(result.conflict, true); assert.equal(draftIsDirty(result), true);
  // An inspector can be remounted from this keyed draft without losing content.
  const cache = new Map([['document:node', result]]);
  assert.deepEqual(cache.get('document:node'), result);
});
test('canonical ACK clears dirty state only when fields equal the submitted draft', () => {
  const original = { title: 'Заметка', body: '', status: 'requiresVerification' as const };
  const draft = { ...draftFor(original), body: 'Текст' };
  assert.equal(draftIsDirty(reconcileDraft(draft, { ...original, body: 'Текст' }, true)), false);
  const newer = { ...draft, body: 'Ещё не сохранённая правка' };
  assert.equal(reconcileDraft(newer, { ...original, body: 'Текст' }, true).body, 'Ещё не сохранённая правка');
});
test('clean unfocused draft accepts canonical changes', () => {
  const original = { title: 'Заметка', body: '', status: 'requiresVerification' as const };
  assert.equal(reconcileDraft(draftFor(original), { ...original, body: 'Новое значение' }, false).body, 'Новое значение');
});

test('independent native fields rebase without reverting them on later human save', () => {
  const original = { title: 'Заметка', body: '', status: 'requiresVerification' as const };
  const draft = { ...draftFor(original), body: 'Ручной текст' };
  const result = reconcileDraft(draft, { title: 'Новое название', body: '', status: 'accepted' }, false);
  assert.equal(result.body, 'Ручной текст');
  assert.equal(result.title, 'Новое название');
  assert.equal(result.status, 'accepted');
  assert.equal(result.conflict, false);
  assert.equal(draftIsDirty(result), true);
});

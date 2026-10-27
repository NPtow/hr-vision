export const PROTOCOL_VERSION = 1 as const;
export const NODE_KINDS = ['source', 'observation', 'hypothesis', 'offer', 'contact', 'note'] as const;
export const EDGE_KINDS = ['supports', 'contradicts', 'requiresVerification', 'contactRationale'] as const;
export const NODE_STATUSES = ['proposed', 'accepted', 'rejected', 'requiresVerification'] as const;
export type NodeKind = typeof NODE_KINDS[number];
export type EdgeKind = typeof EDGE_KINDS[number];
export type NodeStatus = typeof NODE_STATUSES[number];
export type Position = { x: number; y: number };
export type BoardNode = { id: string; kind: NodeKind; title: string; body: string; status: NodeStatus; referenceId?: string; position: Position };
export type BoardEdge = { id: string; source: string; target: string; kind: EdgeKind };
export type BoardDocument = { id: string; companyId: string; title: string; revision: number; nodes: BoardNode[]; edges: BoardEdge[] };
export type Reference = { id: string; title: string; body?: string; subtitle?: string; url?: string };
export type NodeFields = Pick<BoardNode, 'title' | 'body' | 'status'>;
export type Operation =
  | { type: 'createNode'; node: BoardNode }
  | { type: 'setNodeFields'; nodeId: string; fields: Partial<NodeFields> }
  | { type: 'moveNodes'; positions: Array<{ nodeId: string; x: number; y: number }> }
  | { type: 'connect'; edge: BoardEdge }
  | { type: 'removeEdge'; edgeId: string }
  | { type: 'deleteNode'; nodeId: string };
export type HostState = { protocolVersion: 1; type: 'state'; sessionId: string; document: BoardDocument; sources: Reference[]; contacts: Reference[]; canUndo: boolean; canRedo: boolean; ackTransactionId?: string };
export type HostMessage = HostState
  | { protocolVersion: 1; type: 'error'; sessionId: string; message: string; transactionId?: string }
  | { protocolVersion: 1; type: 'setSelection'; sessionId: string; documentId?: string; nodeIds: string[]; edgeIds?: string[]; reveal?: boolean };
export type OutgoingMessage = Record<string, unknown> & { protocolVersion: 1; sessionId: string; type: string };

export const kindLabels: Record<NodeKind, string> = { source: 'Источник', observation: 'Наблюдение', hypothesis: 'Гипотеза', offer: 'Оффер', contact: 'Контакт', note: 'Заметка' };
export const statusLabels: Record<NodeStatus, string> = { proposed: 'Предложено', accepted: 'Принято', rejected: 'Отклонено', requiresVerification: 'Требует проверки' };
export const edgeLabels: Record<EdgeKind, string> = { supports: 'Поддерживает', contradicts: 'Противоречит', requiresVerification: 'Требует проверки', contactRationale: 'Основание выбора' };
export const isReferenceNode = (node: BoardNode) => node.kind === 'source' || node.kind === 'contact';

function record(value: unknown): value is Record<string, unknown> { return typeof value === 'object' && value !== null && !Array.isArray(value); }
function nonempty(value: unknown): value is string { return typeof value === 'string' && value.length > 0 && value.length <= 512; }
function string(value: unknown): value is string { return typeof value === 'string'; }
function inList<T extends string>(value: unknown, list: readonly T[]): value is T { return typeof value === 'string' && list.includes(value as T); }
function validPosition(value: unknown): value is Position { return record(value) && typeof value.x === 'number' && Number.isFinite(value.x) && typeof value.y === 'number' && Number.isFinite(value.y); }
function validNode(value: unknown): value is BoardNode {
  return record(value) && nonempty(value.id) && inList(value.kind, NODE_KINDS) && string(value.title) && string(value.body)
    && inList(value.status, NODE_STATUSES) && validPosition(value.position)
    && (value.referenceId === undefined || nonempty(value.referenceId))
    && (!['source', 'contact'].includes(value.kind) || nonempty(value.referenceId));
}
function validEdge(value: unknown): value is BoardEdge { return record(value) && nonempty(value.id) && nonempty(value.source) && nonempty(value.target) && value.source !== value.target && inList(value.kind, EDGE_KINDS); }
function validReference(value: unknown): value is Reference {
  return record(value) && nonempty(value.id) && string(value.title)
    && ['body', 'subtitle', 'url'].every(key => value[key] === undefined || string(value[key]));
}
function uniqueIDs(values: Array<{ id: string }>): boolean { return new Set(values.map(v => v.id)).size === values.length; }
export function validDocument(value: unknown): value is BoardDocument {
  if (!record(value) || !nonempty(value.id) || !nonempty(value.companyId) || !string(value.title) || !Number.isSafeInteger(value.revision) || (value.revision as number) < 0) return false;
  if (!Array.isArray(value.nodes) || !value.nodes.every(validNode) || !uniqueIDs(value.nodes) || !Array.isArray(value.edges) || !value.edges.every(validEdge) || !uniqueIDs(value.edges)) return false;
  const nodeIDs = new Set(value.nodes.map(node => node.id));
  return value.edges.every(edge => nodeIDs.has(edge.source) && nodeIDs.has(edge.target));
}
export function parseHostMessage(input: unknown, currentSession: string): HostMessage {
  if (!record(input) || input.protocolVersion !== PROTOCOL_VERSION) throw new Error('Несовместимая версия холста. Обновите приложение.');
  if (input.sessionId !== currentSession) throw new Error('Ответ относится к предыдущей сессии холста.');
  if (input.type === 'state' && validDocument(input.document)
      && Array.isArray(input.sources) && input.sources.every(validReference) && uniqueIDs(input.sources)
      && Array.isArray(input.contacts) && input.contacts.every(validReference) && uniqueIDs(input.contacts)
      && typeof input.canUndo === 'boolean' && typeof input.canRedo === 'boolean'
      && (input.ackTransactionId === undefined || nonempty(input.ackTransactionId))) return input as HostState;
  if (input.type === 'error' && string(input.message) && (input.transactionId === undefined || nonempty(input.transactionId))) return input as HostMessage;
  if (input.type === 'setSelection' && Array.isArray(input.nodeIds) && input.nodeIds.every(nonempty)
      && (input.edgeIds === undefined || (Array.isArray(input.edgeIds) && input.edgeIds.every(nonempty)))
      && (input.documentId === undefined || nonempty(input.documentId)) && (input.reveal === undefined || typeof input.reveal === 'boolean')) return input as HostMessage;
  throw new Error('Не удалось прочитать документ разбора. Данные не изменены.');
}

export function makeNode(kind: NodeKind, id: string, position: Position, reference?: Reference): BoardNode {
  if ((kind === 'source' || kind === 'contact') && !reference) throw new Error('Выберите сохранённый материал или контакт.');
  return { id, kind, position, title: reference?.title ?? kindLabels[kind], body: '', status: 'requiresVerification', ...(reference ? { referenceId: reference.id } : {}) };
}
export function canConnect(doc: BoardDocument, source: string, target: string, kind: EdgeKind): boolean {
  return source !== target && doc.nodes.some(n => n.id === source) && doc.nodes.some(n => n.id === target)
    && !doc.edges.some(edge => edge.source === source && edge.target === target && edge.kind === kind);
}
export function deletionOperations(doc: BoardDocument, nodeIds: string[], edgeIds: string[]): Operation[] {
  const nodes = new Set(nodeIds.filter(id => doc.nodes.some(n => n.id === id)));
  // Native DeleteNode owns removal of incident edges; avoid a second operation for those edges.
  return [
    ...doc.edges.filter(edge => edgeIds.includes(edge.id) && !nodes.has(edge.source) && !nodes.has(edge.target)).map(edge => ({ type: 'removeEdge' as const, edgeId: edge.id })),
    ...[...nodes].map(nodeId => ({ type: 'deleteNode' as const, nodeId })),
  ];
}
export function editableTarget(target: EventTarget | null): boolean {
  return target instanceof Element && Boolean((target as HTMLElement).isContentEditable || target.closest('input, textarea, select, [contenteditable="true"], [contenteditable=""], [contenteditable="plaintext-only"]'));
}

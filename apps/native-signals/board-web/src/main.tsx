import { StrictMode, createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  ReactFlow, ReactFlowProvider, Background, Handle, Position, MarkerType, SelectionMode,
  applyNodeChanges, applyEdgeChanges, useReactFlow, useOnViewportChange,
  type Node, type Edge, type NodeProps, type NodeChange, type EdgeChange, type Connection,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import './style.css';
import {
  EDGE_KINDS, NODE_KINDS, NODE_STATUSES, kindLabels, statusLabels, edgeLabels,
  isReferenceNode, makeNode, canConnect, deletionOperations, editableTarget,
  type BoardNode, type BoardDocument, type HostState, type HostMessage, type Reference,
  type Operation, type NodeKind, type EdgeKind, type NodeFields, type NodeStatus,
} from './protocol';
import { newId, post, subscribe } from './bridge';
import { isAcknowledged, draftFor, draftIsDirty, reconcileDraft, type Draft, type PendingCommand } from './session';
import { vacantPosition, arrangeCards, CARD_WIDTH, CARD_HEIGHT } from './canvas-layout';

const glyphs = {
  cursor: 'M5 3l14 9-7 1-3 7-4-17z', hand: 'M8 12V6a2 2 0 014 0v5-7a2 2 0 014 0v7-4a2 2 0 014 0v8c0 4-2 6-6 6h-1c-2 0-4-1-5-3l-4-6a2 2 0 013-2l2 2',
  source: 'M6 3h9l4 4v14H6V3zm8 0v5h5M9 12h7M9 16h6', contact: 'M16 7a4 4 0 11-8 0 4 4 0 018 0zM4 21v-2a6 6 0 0112 0v2M18 12a4 4 0 012 7',
  note: 'M4 3h16v12l-6 6H4V3zm10 18v-6h6', plus: 'M12 5v14M5 12h14', minus: 'M5 12h14', close: 'M6 6l12 12M18 6L6 18',
  link: 'M5 19L19 5M9 5h10v10', undo: 'M9 6L4 11l5 5M4 11h10a6 6 0 016 6', redo: 'M15 6l5 5-5 5M20 11H10a6 6 0 00-6 6',
  fit: 'M8 3H3v5M16 3h5v5M3 16v5h5M21 16v5h-5M9 9h6v6H9z', layout: 'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z',
  list: 'M8 6h13M8 12h13M8 18h13M3 6h.1M3 12h.1M3 18h.1', canvas: 'M3 4h18v16H3zM7 8h4v4H7zM14 13h3v3h-3z',
  edit: 'M14 5l5 5M4 20l5-1L21 7l-5-5L4 14v6z', trash: 'M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7',
  check: 'M5 12l4 4L19 6', idea: 'M9 18h6M10 21h4M8 14a7 7 0 118 0l-1 4H9l-1-4z', observation: 'M4 12s3-7 8-7 8 7 8 7-3 7-8 7-8-7-8-7zm11 0a3 3 0 11-6 0 3 3 0 016 0z', offer: 'M3 8h18v13H3zM8 8V4h8v4M3 13h18M10 13v3h4v-3',
};
type IconName = keyof typeof glyphs;
function Icon({ name }: { name: IconName }) { return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={glyphs[name]}/></svg>; }
function referenceHost(reference?: Reference) { try { return new URL(reference?.url ?? '').hostname.replace(/^www\./, ''); } catch { return ''; } }

type CardData = { model: BoardNode; reference?: Reference; unavailable: boolean };
type FlowNode = Node<CardData, 'card'>;
type FlowEdge = Edge<{ kind: EdgeKind }>;
const colors: Record<EdgeKind, string> = { supports: '#8a929b', contradicts: '#c7827a', requiresVerification: '#a69ac1', contactRationale: '#8a929b' };
const CardActions = createContext({ open: (_node: BoardNode) => {}, edit: (_id: string) => {} });
function Card({ data, selected }: NodeProps<FlowNode>) {
  const actions = useContext(CardActions);
  const { model, reference, unavailable } = data;
  const external = isReferenceNode(model);
  const title = external ? reference?.title ?? model.title : model.title;
  const body = external ? reference?.body ?? '' : model.body;
  return <article className={`card card-${model.kind} ${selected ? 'card-selected' : ''}`} aria-label={`${kindLabels[model.kind]}: ${title}`}>
    <Handle id="in" type="target" position={Position.Left} aria-label="Входящая связь" />
    <div className="card-top"><span>{external ? <Icon name={model.kind === 'contact' ? 'contact' : 'source'}/> : <span className="kind-dot"/>}{kindLabels[model.kind]}</span><span className={`status-dot status-${model.status}`} title={statusLabels[model.status]} aria-label={statusLabels[model.status]}/></div>
    <h2>{title || kindLabels[model.kind]}</h2>
    <p className={`card-body ${body ? '' : 'placeholder'}`}>{body || (external ? 'Текст доступен в сохранённом материале.' : 'Выберите карточку и добавьте текст.')}</p>
    {unavailable && <p className="reference-warning">Связанный материал сейчас недоступен</p>}
    <div className="card-bottom"><span>{external ? referenceHost(reference) : statusLabels[model.status]}</span><button className="text-button nodrag nopan" aria-label={`Подробнее: ${title}`} onClick={() => actions.edit(model.id)}><Icon name="link"/></button></div>
    <Handle id="out" type="source" position={Position.Right} aria-label="Исходящая связь" />
  </article>;
}
const nodeTypes = { card: Card };
function referenceFor(node: BoardNode, state: HostState): Reference | undefined {
  return (node.kind === 'source' ? state.sources : node.kind === 'contact' ? state.contacts : []).find(item => item.id === node.referenceId);
}
function projectNodes(state: HostState, previous: FlowNode[], moving: Set<string>): FlowNode[] {
  return state.document.nodes.map(model => {
    const prior = previous.find(node => node.id === model.id);
    const reference = referenceFor(model, state);
    return { id: model.id, type: 'card', position: moving.has(model.id) && prior ? prior.position : model.position,
      width: CARD_WIDTH, height: CARD_HEIGHT,
      handles: [
        { id: 'in', type: 'target', position: Position.Left, x: -4, y: CARD_HEIGHT / 2 - 4, width: 8, height: 8 },
        { id: 'out', type: 'source', position: Position.Right, x: CARD_WIDTH - 4, y: CARD_HEIGHT / 2 - 4, width: 8, height: 8 },
      ],
      selected: prior?.selected ?? false, measured: prior?.measured, data: { model, reference, unavailable: isReferenceNode(model) && !reference },
      ariaLabel: `${kindLabels[model.kind]}: ${reference?.title ?? model.title}`, dragHandle: '.card',
    };
  });
}
function projectEdges(doc: BoardDocument, previous: FlowEdge[]): FlowEdge[] {
  return doc.edges.map(edge => ({ id: edge.id, source: edge.source, target: edge.target, sourceHandle: 'out', targetHandle: 'in', data: { kind: edge.kind },
    type: 'default', label: edgeLabels[edge.kind], selected: previous.find(e => e.id === edge.id)?.selected ?? false,
    style: { stroke: colors[edge.kind], strokeWidth: 1.4, strokeDasharray: edge.kind === 'requiresVerification' ? '5 5' : undefined },
    labelStyle: { fill: colors[edge.kind], fontSize: 10 }, labelBgStyle: { fill: 'var(--canvas)' }, labelBgPadding: [5, 3],
    markerEnd: { type: MarkerType.ArrowClosed, color: colors[edge.kind], width: 16, height: 16 }, ariaLabel: `${edgeLabels[edge.kind]}: ${doc.nodes.find(n => n.id === edge.source)?.title ?? ''} → ${doc.nodes.find(n => n.id === edge.target)?.title ?? ''}`,
  }));
}

function BoardApp() {
  const [host, setHost] = useState<HostState>();
  const hostRef = useRef<HostState | undefined>(undefined);
  const [nodes, setNodes] = useState<FlowNode[]>([]);
  const [edges, setEdges] = useState<FlowEdge[]>([]);
  const nodesRef = useRef(nodes); nodesRef.current = nodes;
  const [error, setError] = useState('');
  const [fatal, setFatal] = useState(false);
  const [pending, setPending] = useState<string>();
  const pendingRef = useRef<string | undefined>(undefined);
  const pendingCommand = useRef<PendingCommand | undefined>(undefined);
  const drafts = useRef(new Map<string, Draft>());
  const [mode, setMode] = useState<'canvas' | 'list'>('canvas');
  const [tool, setTool] = useState<'select' | 'hand'>('select');
  const [inspector, setInspector] = useState<'card' | 'connection'>();
  const [more, setMore] = useState(false);
  const [zoom, setZoom] = useState(1);
  const revealCreated = useRef<string | undefined>(undefined);
  const fitAfterRevision = useRef<number | undefined>(undefined);
  const [picker, setPicker] = useState<'source' | 'contact' | undefined>();
  const [query, setQuery] = useState('');
  const [edgeKind, setEdgeKind] = useState<EdgeKind>('supports');
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [selectedEdgeIds, setSelectedEdgeIds] = useState<string[]>([]);
  const selectionRef = useRef({ nodeIds: [] as string[], edgeIds: [] as string[] });
  const [connectFrom, setConnectFrom] = useState('');
  const [connectTo, setConnectTo] = useState('');
  const moving = useRef(new Set<string>());
  const [waiting, setWaiting] = useState(false);
  const flow = useReactFlow<FlowNode, FlowEdge>();
  useOnViewportChange({ onChange: viewport => setZoom(viewport.zoom) });
  const firstDocument = useRef<string | undefined>(undefined);
  const busy = Boolean(pending) || fatal;

  const resolvePending = useCallback(() => { pendingRef.current = undefined; pendingCommand.current = undefined; setPending(undefined); setWaiting(false); }, []);
  const sendSelection = useCallback((nodeIds: string[], edgeIds: string[], notify = true) => {
    const old = selectionRef.current;
    if (old.nodeIds.join('\0') === nodeIds.join('\0') && old.edgeIds.join('\0') === edgeIds.join('\0')) return;
    selectionRef.current = { nodeIds, edgeIds };
    setSelectedIds(nodeIds); setSelectedEdgeIds(edgeIds);
    if (notify && hostRef.current) post('selectionChanged', { documentId: hostRef.current.document.id, nodeIds, edgeIds });
  }, []);
  const select = useCallback((nodeIds: string[], edgeIds: string[] = [], notify = true) => {
    setNodes(current => current.map(n => ({ ...n, selected: nodeIds.includes(n.id) })));
    setEdges(current => current.map(e => ({ ...e, selected: edgeIds.includes(e.id) })));
    sendSelection(nodeIds, edgeIds, notify);
  }, [sendSelection]);
  const editing = useCallback((nodeIds: string[], fields: string[], active: boolean) => {
    const documentId = hostRef.current?.document.id;
    if (documentId) post('editingChanged', { documentId, nodeIds, fields, editing: active });
  }, []);

  useEffect(() => {
    const receive = (message: HostMessage) => {
      if (message.type === 'error') {
        setError(message.message);
        if (!message.transactionId || message.transactionId === pendingRef.current) resolvePending();
        moving.current.clear();
        const confirmed = hostRef.current;
        if (confirmed) setNodes(current => projectNodes(confirmed, current, moving.current));
        return;
      }
      if (message.type === 'setSelection') {
        const current = hostRef.current;
        if (!current || (message.documentId && current.document.id !== message.documentId)) return;
        const ids = message.nodeIds.filter(id => current.document.nodes.some(n => n.id === id));
        const edgeIds = (message.edgeIds ?? []).filter(id => current.document.edges.some(e => e.id === id));
        select(ids, edgeIds, false);
        if (message.reveal && ids.length) { setInspector('card'); void flow.fitView({ nodes: ids.map(id => ({ id })), padding: 0.3, maxZoom: 1 }); }
        return;
      }
      const previous = hostRef.current;
      if (previous?.document.id === message.document.id && message.document.revision < previous.document.revision) return;
      const changedDocument = previous?.document.id !== message.document.id;
      if (changedDocument) {
        moving.current.clear(); resolvePending(); sendSelection([], [], false); setPicker(undefined); setQuery(''); setInspector(undefined); setMore(false);
      }
      if (isAcknowledged(pendingRef.current, message.ackTransactionId)) { resolvePending(); moving.current.clear(); }
      hostRef.current = message; setHost(message); setFatal(false);
      setNodes(current => projectNodes(message, changedDocument ? [] : current, moving.current));
      setEdges(current => projectEdges(message.document, changedDocument ? [] : current));
      if (changedDocument) firstDocument.current = message.document.id;
    };
    const unsubscribe = subscribe(receive, message => { setError(message); setFatal(true); resolvePending(); });
    const connected = post('ready');
    if (!connected) setError('Разбор открывается внутри приложения TG Signals. Подключение к локальному документу недоступно.');
    const timeout = window.setTimeout(() => { if (!hostRef.current && connected) setError('Приложение не передало документ. Повторите подключение.'); }, 8000);
    return () => { unsubscribe(); window.clearTimeout(timeout); };
  }, [flow, resolvePending, select, sendSelection]);

  useEffect(() => {
    if (firstDocument.current && nodes.length && nodes.every(n => n.measured?.width)) {
      firstDocument.current = undefined;
      void flow.fitView({ padding: 0.22, maxZoom: 0.95 });
    }
  }, [nodes, flow]);
  useEffect(() => {
    const id = revealCreated.current;
    if (id && host?.document.nodes.some(node => node.id === id)) {
      revealCreated.current = undefined; select([id]); setInspector('card');
    }
    if (fitAfterRevision.current !== undefined && host && host.document.revision > fitAfterRevision.current && nodes.every(n => n.measured?.width)) {
      fitAfterRevision.current = undefined; void flow.fitView({ padding: 0.18, maxZoom: 1 });
    }
  }, [host, nodes, flow, select]);
  useEffect(() => {
    if (!pending) return;
    const timeout = window.setTimeout(() => setWaiting(true), 6000);
    return () => window.clearTimeout(timeout);
  }, [pending]);

  const transact = useCallback((operations: Operation[]) => {
    const current = hostRef.current;
    if (!current || pendingRef.current || operations.length === 0 || fatal) return false;
    const transactionId = newId();
    pendingRef.current = transactionId; setPending(transactionId); setError('');
    const payload = { transactionId, documentId: current.document.id, baseRevision: current.document.revision, actor: 'human', operations };
    pendingCommand.current = { type: 'transaction', payload };
    if (!post('transaction', payload)) {
      resolvePending(); setError('Не удалось передать изменение приложению.'); return false;
    }
    return true;
  }, [fatal, resolvePending]);
  const history = useCallback((type: 'undo' | 'redo') => {
    const current = hostRef.current;
    if (!current || pendingRef.current || fatal || !(type === 'undo' ? current.canUndo : current.canRedo)) return;
    const transactionId = newId(); pendingRef.current = transactionId; setPending(transactionId); setError('');
    const payload = { documentId: current.document.id, baseRevision: current.document.revision, transactionId };
    pendingCommand.current = { type, payload };
    if (!post(type, payload)) resolvePending();
  }, [fatal, resolvePending]);
  const removeSelection = useCallback(() => {
    const doc = hostRef.current?.document;
    const selection = selectionRef.current;
    if (doc) transact(deletionOperations(doc, selection.nodeIds, selection.edgeIds));
  }, [transact]);

  useEffect(() => {
    const keydown = (event: KeyboardEvent) => {
      if (editableTarget(event.target) || event.isComposing) return;
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'z') {
        event.preventDefault(); history(event.shiftKey ? 'redo' : 'undo');
      } else if (event.key === 'Backspace' || event.key === 'Delete') {
        event.preventDefault(); removeSelection();
      } else if (event.key === 'Escape') {
        select([]); setPicker(undefined); setInspector(undefined); setMore(false);
      } else if (!event.metaKey && !event.ctrlKey && !event.altKey && event.key.toLowerCase() === 'v') {
        setTool('select');
      } else if (!event.metaKey && !event.ctrlKey && !event.altKey && event.key.toLowerCase() === 'h') {
        setTool('hand');
      }
    };
    window.addEventListener('keydown', keydown);
    return () => window.removeEventListener('keydown', keydown);
  }, [history, removeSelection, select]);

  const add = (kind: NodeKind, reference?: Reference) => {
    if (busy || !host) return;
    setMore(false); setInspector(undefined);
    if (isReferenceKind(kind) && !reference) { setPicker(kind); setQuery(''); return; }
    const frame = document.querySelector('.flow-region')?.getBoundingClientRect();
    const preferred = frame ? flow.screenToFlowPosition({ x: frame.left + frame.width * 0.4, y: frame.top + frame.height * 0.4 }) : { x: 80, y: 100 };
    const position = vacantPosition(preferred, host.document.nodes.map(node => node.position));
    const id = newId();
    if (transact([{ type: 'createNode', node: makeNode(kind, id, position, reference) }])) revealCreated.current = id;
    setPicker(undefined);
  };
  const connect = (connection: Connection) => {
    const doc = hostRef.current?.document;
    if (doc && canConnect(doc, connection.source, connection.target, edgeKind)) transact([{ type: 'connect', edge: { id: newId(), source: connection.source, target: connection.target, kind: edgeKind } }]);
  };
  const onNodesChange = useCallback((changes: NodeChange<FlowNode>[]) => {
    // Deletion is handled semantically, never by React Flow's local history.
    const allowed = changes.filter(change => change.type !== 'remove' && !(pendingRef.current && change.type === 'position'));
    setNodes(current => applyNodeChanges(allowed, current));
    const positions = allowed.flatMap(change => change.type === 'position' && change.position && change.dragging === false && !moving.current.has(change.id)
      ? [{ nodeId: change.id, ...change.position }] : []);
    if (positions.length) transact([{ type: 'moveNodes', positions }]);
  }, [transact]);
  const onEdgesChange = useCallback((changes: EdgeChange<FlowEdge>[]) => {
    setEdges(current => applyEdgeChanges(changes.filter(change => change.type !== 'remove'), current));
  }, []);
  const finishMove = (_event: unknown, node: FlowNode, movedNodes: FlowNode[]) => {
    const moved = movedNodes.length ? movedNodes : [node];
    const doc = hostRef.current?.document;
    const changed = moved.filter(item => { const original = doc?.nodes.find(n => n.id === item.id); return original && (original.position.x !== item.position.x || original.position.y !== item.position.y); });
    if (changed.length) transact([{ type: 'moveNodes', positions: changed.map(item => ({ nodeId: item.id, ...item.position })) }]);
    editing(moved.map(n => n.id), ['position'], false);
    moving.current.clear();
  };
  const open = useCallback((node: BoardNode) => {
    if (node.referenceId && isReferenceKind(node.kind)) post('openReference', { kind: node.kind, referenceId: node.referenceId, documentId: hostRef.current?.document.id });
  }, []);
  const edit = useCallback((id: string) => { select([id]); setInspector('card'); setPicker(undefined); setMore(false); }, [select]);
  const actions = useMemo(() => ({ open, edit }), [open, edit]);
  const selected = host?.document.nodes.find(node => node.id === selectedIds[0]);
  const selectedEdge = host?.document.edges.find(edge => edge.id === selectedEdgeIds[0]);
  const available = picker === 'source' ? host?.sources ?? [] : host?.contacts ?? [];
  const filtered = available.filter(item => `${item.title}\n${item.subtitle ?? ''}`.toLocaleLowerCase('ru').includes(query.toLocaleLowerCase('ru')));
  const canonicalDoc = host?.document;
  const arrange = () => {
    if (host && transact([{ type: 'moveNodes', positions: arrangeCards(host.document) }])) {
      fitAfterRevision.current = host.document.revision; select([]); setInspector(undefined); setMode('canvas');
    }
  };

  return <CardActions.Provider value={actions}><main className={`board-app ${tool === 'hand' ? 'hand-tool' : ''}`}>
    <section className="flow-region" aria-label={mode === 'canvas' ? 'Холст доказательств' : 'Список карточек и связей'}>
      {!host && <div className="loading-state"><span className="loading-dot"/><p>Открываем доску…</p></div>}
      <div className={mode === 'list' ? 'flow-hidden' : 'flow-wrap'} aria-hidden={mode === 'list'}>
        <ReactFlow<FlowNode, FlowEdge> nodes={nodes} edges={edges} nodeTypes={nodeTypes} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
          onConnect={connect} isValidConnection={connection => Boolean(canonicalDoc && canConnect(canonicalDoc, connection.source, connection.target, edgeKind))}
          onSelectionChange={({ nodes: selectedNodes, edges: selectedEdges }) => sendSelection(selectedNodes.map(n => n.id), selectedEdges.map(e => e.id))}
          onPaneClick={() => { select([]); setInspector(undefined); setPicker(undefined); setMore(false); }}
          onNodeDoubleClick={(_event, node) => edit(node.id)}
          onEdgeDoubleClick={(_event, edge) => { select([], [edge.id]); setInspector('card'); }}
          onNodeDragStart={(_event, node, dragged) => { const ids = (dragged.length ? dragged : [node]).map(n => n.id); moving.current = new Set(ids); editing(ids, ['position'], true); }}
          onNodeDragStop={finishMove} nodesDraggable={!busy && tool === 'select'} nodesConnectable={!busy && tool === 'select'} deleteKeyCode={null} minZoom={0.12} maxZoom={2}
          defaultViewport={{ x: 100, y: 100, zoom: 0.85 }} panOnScroll selectionOnDrag={tool === 'select'} selectionMode={SelectionMode.Partial} panOnDrag={tool === 'hand' ? true : [1, 2]} panActivationKeyCode="Space" multiSelectionKeyCode="Meta"
          colorMode="light" elevateEdgesOnSelect disableKeyboardA11y={false}
          ariaLabelConfig={{ 'node.a11yDescription.default': 'Enter или пробел — выбрать. Стрелки — переместить. Delete — удалить. Подробнее — редактирование.' }}
        ><Background color="#d8d9dd" gap={24} size={0.85}/></ReactFlow>
      </div>
      {host && !nodes.length && mode === 'canvas' && <div className="empty-state"><Icon name="note"/><h2>Место для ваших идей</h2><p>Добавьте материал или заметку.</p><button disabled={busy} onClick={() => add('source')}>Добавить материал</button></div>}
      {mode === 'list' && host && <div className="linear-view">
        <h2>Карточки <span>{host.document.nodes.length}</span></h2>
        {!host.document.nodes.length && <p className="empty-copy">Добавьте материал или заметку на панели слева.</p>}
        <ul>{host.document.nodes.map(node => <li key={node.id} className={selectedIds.includes(node.id) ? 'linear-selected' : ''}><button onClick={() => edit(node.id)}><span className={`linear-kind kind-${node.kind}`}>{kindLabels[node.kind]}</span><strong>{referenceFor(node, host)?.title ?? node.title}</strong><span>{isReferenceNode(node) ? referenceFor(node, host)?.body ?? 'Сохранённый материал' : node.body || 'Текст пока не добавлен'}</span><small>{statusLabels[node.status]}</small></button></li>)}</ul>
        <h2>Связи <span>{host.document.edges.length}</span></h2>
        <ul>{host.document.edges.map(edge => <li key={edge.id} className={selectedEdgeIds.includes(edge.id) ? 'linear-selected' : ''}><button onClick={() => { select([], [edge.id]); setInspector('card'); }}><strong>{host.document.nodes.find(n => n.id === edge.source)?.title} → {host.document.nodes.find(n => n.id === edge.target)?.title}</strong><span>{edgeLabels[edge.kind]}</span></button></li>)}</ul>
      </div>}
    </section>

    <header className="board-header floating">
      <h1>Доска</h1><span className="toolbar-divider"/>
      <button className="icon-button" disabled={!host?.canUndo || busy} onClick={() => history('undo')} title="Отменить · ⌘Z" aria-label="Отменить"><Icon name="undo"/></button>
      <button className="icon-button" disabled={!host?.canRedo || busy} onClick={() => history('redo')} title="Повторить · ⇧⌘Z" aria-label="Повторить"><Icon name="redo"/></button>
      <span className="save-state" role="status" title={pending ? 'Сохраняем изменения' : 'Сохранено на этом Mac'}>{pending ? 'Сохраняем…' : host ? <Icon name="check"/> : '…'}</span>
    </header>
    <div className="view-tools floating" role="toolbar" aria-label="Вид доски">
      <button className="icon-button" disabled={!host?.document.nodes.length || busy} onClick={arrange} aria-label="Упорядочить карточки" title="Упорядочить карточки"><Icon name="layout"/></button>
      <span className="toolbar-divider"/>
      <button className="icon-button" aria-pressed={mode === 'canvas'} title="Холст" aria-label="Холст" onClick={() => setMode('canvas')}><Icon name="canvas"/></button>
      <button className="icon-button" aria-pressed={mode === 'list'} title="Список" aria-label="Список" onClick={() => setMode('list')}><Icon name="list"/></button>
    </div>

    <div className="tool-rail floating" role="toolbar" aria-label="Инструменты доски">
      <button className="icon-button" title="Выбрать · V" aria-label="Выбрать" aria-pressed={tool === 'select'} onClick={() => setTool('select')}><Icon name="cursor"/></button>
      <button className="icon-button" title="Перемещать поле · H" aria-label="Перемещать поле" aria-pressed={tool === 'hand'} onClick={() => setTool('hand')}><Icon name="hand"/></button>
      <span className="rail-divider"/>
      <button className="icon-button" disabled={!host || busy} title="Добавить материал" aria-label="Добавить материал" aria-pressed={picker === 'source'} onClick={() => add('source')}><Icon name="source"/></button>
      <button className="icon-button" disabled={!host || busy} title="Добавить контакт" aria-label="Добавить контакт" aria-pressed={picker === 'contact'} onClick={() => add('contact')}><Icon name="contact"/></button>
      <button className="icon-button note-tool" disabled={!host || busy} title="Заметка" aria-label="Добавить заметку" onClick={() => add('note')}><Icon name="note"/></button>
      <button className="icon-button" disabled={!host || busy} title="Связь" aria-label="Связать карточки" aria-pressed={inspector === 'connection'} onClick={() => { setInspector(inspector === 'connection' ? undefined : 'connection'); setPicker(undefined); setMore(false); }}><Icon name="link"/></button>
      <span className="rail-divider"/>
      <button className="icon-button" disabled={!host || busy} title="Ещё карточки" aria-label="Ещё карточки" aria-expanded={more} onClick={() => { setMore(!more); setPicker(undefined); setInspector(undefined); }}><Icon name="plus"/></button>
    </div>
    {more && <div className="add-menu floating" role="menu" aria-label="Тип карточки">{(['observation', 'hypothesis', 'offer'] as NodeKind[]).map(kind => <button role="menuitem" key={kind} onClick={() => add(kind)}><Icon name={kind === 'hypothesis' ? 'idea' : kind === 'offer' ? 'offer' : 'observation'}/>{kindLabels[kind]}</button>)}</div>}

    {(selectedIds.length > 0 || selectedEdgeIds.length > 0) && !inspector && !picker && <div className="selection-toolbar floating" role="toolbar" aria-label="Выбранное">
      <span>{selectedIds.length > 1 ? `${selectedIds.length} карточки` : selected ? kindLabels[selected.kind] : 'Связь'}</span><span className="toolbar-divider"/>
      {(selected || selectedEdge) && <button className="icon-button" aria-label="Редактировать выбранное" title="Редактировать" onClick={() => setInspector('card')}><Icon name="edit"/></button>}
      {selected && <button className="icon-button" aria-label="Связать выбранную карточку" title="Связать" onClick={() => setInspector('connection')}><Icon name="link"/></button>}
      <button className="icon-button" disabled={busy} title="Удалить · Delete" aria-label="Удалить выбранное" onClick={removeSelection}><Icon name="trash"/></button>
    </div>}

    {picker && <aside className="source-picker floating" aria-label={picker === 'source' ? 'Сохранённые материалы' : 'Сохранённые контакты'}>
      <div className="panel-heading"><h2>{picker === 'source' ? 'Материалы' : 'Контакты'}</h2><button className="icon-button" aria-label="Закрыть список" onClick={() => setPicker(undefined)}><Icon name="close"/></button></div>
      <input type="search" aria-label="Поиск в сохранённых объектах" placeholder="Поиск" value={query} onChange={event => setQuery(event.target.value)} autoFocus/>
      <div className="picker-items">{filtered.map(item => <button className="picker-item" key={item.id} disabled={busy} onClick={() => add(picker, item)}><strong>{item.title || 'Без заголовка'}</strong>{item.subtitle && <span>{item.subtitle}</span>}</button>)}{!filtered.length && <p className="empty-copy">{available.length ? 'Совпадений нет.' : 'Сохранённых объектов пока нет.'}</p>}</div>
    </aside>}
    {host && inspector && (inspector === 'connection' || selected || selectedEdge) && <aside className="inspector floating" aria-label={inspector === 'connection' ? 'Создать связь' : 'Свойства выбранной карточки'}>
      <div className="panel-heading"><h2>{inspector === 'connection' ? 'Связь' : selected ? kindLabels[selected.kind] : 'Связь'}</h2><button className="icon-button" aria-label="Закрыть свойства" onClick={() => setInspector(undefined)}><Icon name="close"/></button></div>
      {inspector === 'card' && (selected ? <CardEditor key={`${host.document.id}:${selected.id}`} draftKey={`${host.document.id}:${selected.id}`} drafts={drafts.current} node={selected} reference={referenceFor(selected, host)} busy={busy} commit={fields => transact([{ type: 'setNodeFields', nodeId: selected.id, fields }])} editing={(fields, active) => editing([selected.id], fields, active)} open={() => open(selected)}/> : selectedEdge ? <><p className="edge-kind">{edgeLabels[selectedEdge.kind]}</p><p className="inspector-copy">{host.document.nodes.find(n => n.id === selectedEdge.source)?.title}<br/><span aria-hidden="true">↓</span><br/>{host.document.nodes.find(n => n.id === selectedEdge.target)?.title}</p><button className="danger" disabled={busy} onClick={removeSelection}>Удалить связь</button></> : null)}
      {inspector === 'connection' && <div className="connection-editor"><label>От<select aria-label="Начало связи" value={connectFrom || selected?.id || ''} onChange={event => setConnectFrom(event.target.value)}><option value="">Выберите карточку</option>{host.document.nodes.map(node => <option key={node.id} value={node.id}>{node.title || kindLabels[node.kind]}</option>)}</select></label><label>Связь<select aria-label="Смысл связи" value={edgeKind} onChange={event => setEdgeKind(event.target.value as EdgeKind)}>{EDGE_KINDS.map(kind => <option key={kind} value={kind}>{edgeLabels[kind]}</option>)}</select></label><label>С карточкой<select aria-label="Конец связи" value={connectTo} onChange={event => setConnectTo(event.target.value)}><option value="">Выберите карточку</option>{host.document.nodes.map(node => <option key={node.id} value={node.id}>{node.title || kindLabels[node.kind]}</option>)}</select></label><button className="primary" disabled={busy || !canConnect(host.document, connectFrom || selected?.id || '', connectTo, edgeKind)} onClick={() => connect({ source: connectFrom || selected?.id || '', target: connectTo, sourceHandle: null, targetHandle: null })}>Соединить</button></div>}
    </aside>}
    {mode === 'canvas' && <div className="zoom-tools floating" role="toolbar" aria-label="Масштаб">
      <button className="icon-button" aria-label="Отдалить" title="Отдалить" onClick={() => void flow.zoomOut()}><Icon name="minus"/></button>
      <button className="zoom-value" aria-label="Масштаб 100 процентов" title="Масштаб 100%" onClick={() => void flow.zoomTo(1)}>{Math.round(zoom * 100)}%</button>
      <button className="icon-button" aria-label="Приблизить" title="Приблизить" onClick={() => void flow.zoomIn()}><Icon name="plus"/></button><span className="toolbar-divider"/>
      <button className="icon-button" aria-label="Показать все карточки" title="Показать всё" onClick={() => void flow.fitView({ padding: 0.18, maxZoom: 1 })}><Icon name="fit"/></button>
    </div>}
    <span className="canvas-hint">{mode === 'canvas' ? 'Пробел + движение — переместить поле' : `${host?.document.nodes.length ?? 0} карточек`}</span>
    {(error || waiting) && <div className={`message floating ${fatal ? 'message-error' : ''}`} role="alert"><span>{error || 'Ожидаем подтверждение сохранения.'}</span>{waiting ? <button onClick={() => { const command = pendingCommand.current; if (command) post(command.type, command.payload); }}>Повторить</button> : (!host || fatal) && <button onClick={() => post('ready')}>Подключиться</button>}<button className="icon-button" aria-label="Скрыть сообщение" onClick={() => setError('')}><Icon name="close"/></button></div>}
  </main></CardActions.Provider>;
}
function isReferenceKind(kind: NodeKind): kind is 'source' | 'contact' { return kind === 'source' || kind === 'contact'; }

type EditorProps = { node: BoardNode; reference?: Reference; busy: boolean; draftKey: string; drafts: Map<string, Draft>; commit: (fields: Partial<NodeFields>) => boolean; editing: (fields: string[], active: boolean) => void; open: () => void };
function CardEditor({ node, reference, busy, draftKey, drafts, commit, editing, open }: EditorProps) {
  const [draft, setDraft] = useState(() => drafts.get(draftKey) ?? draftFor(node));
  const focused = useRef(false);
  const external = isReferenceNode(node);
  const dirty = draftIsDirty(draft);
  const update = (fields: Partial<Draft>) => setDraft(current => {
    const next = { ...current, ...fields }; drafts.set(draftKey, next); return next;
  });
  useEffect(() => {
    setDraft(current => { const next = reconcileDraft(current, node, focused.current); drafts.set(draftKey, next); return next; });
  }, [node.title, node.body, node.status, draftKey, drafts]);
  useEffect(() => {
    const interval = window.setInterval(() => { if (focused.current) editing(['title', 'body', 'status'], true); }, 10000);
    return () => { window.clearInterval(interval); editing(['title', 'body', 'status'], false); };
  }, [node.id]);
  const save = () => {
    const fields: Partial<NodeFields> = {};
    if (!external && draft.title !== node.title) fields.title = draft.title;
    if (!external && draft.body !== node.body) fields.body = draft.body;
    if (draft.status !== node.status) fields.status = draft.status;
    if (Object.keys(fields).length && commit(fields)) update({ conflict: false });
  };
  return <div className="card-editor" onFocusCapture={() => { focused.current = true; editing(['title', 'body', 'status'], true); }} onBlurCapture={event => { if (!event.currentTarget.contains(event.relatedTarget)) { focused.current = false; editing(['title', 'body', 'status'], false); } }}>
    {external ? <><h2>{reference?.title ?? node.title}</h2>{reference?.subtitle && <p className="card-meta">{reference.subtitle}</p>}<p className="reference-text">{reference?.body || 'Полный текст — в сохранённом материале.'}</p><button onClick={open}>Открыть {node.kind === 'source' ? 'материал' : 'контакт'}</button></> : <><label>Название<input aria-label="Название карточки" value={draft.title} maxLength={500} onChange={event => update({ title: event.target.value })}/></label><label>Текст<textarea aria-label="Текст карточки" value={draft.body} maxLength={50000} rows={8} placeholder={node.kind === 'offer' ? 'Какую пользу предлагаем, на каком основании и с какими допущениями' : node.kind === 'hypothesis' ? 'Предположение, которое нужно проверить' : node.kind === 'observation' ? 'Проверяемое утверждение и точные основания' : 'Ваши заметки'} onChange={event => update({ body: event.target.value })}/></label></>}
    <label>Статус<select aria-label="Статус карточки" value={draft.status} onChange={event => update({ status: event.target.value as NodeStatus })}>{NODE_STATUSES.map(value => <option value={value} key={value}>{statusLabels[value]}</option>)}</select></label>
    {draft.conflict && <p className="conflict" role="alert">Карточка изменилась в приложении. Ваш текст сохранён в поле; проверьте его перед сохранением.</p>}
    <div className="editor-actions"><button className="primary" disabled={busy || !dirty} onClick={save}>Сохранить</button>{dirty && <button disabled={busy} onClick={() => { const next = draftFor(node); drafts.set(draftKey, next); setDraft(next); }}>Сбросить</button>}</div>
    {dirty && <p className="draft-note">Черновик сохранится при выборе другой карточки. Нажмите «Сохранить», чтобы записать его в документ.</p>}
  </div>;
}

const root = document.getElementById('root');
if (root) createRoot(root).render(<StrictMode><ReactFlowProvider><BoardApp/></ReactFlowProvider></StrictMode>);

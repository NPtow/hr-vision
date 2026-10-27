import { useCallback, useEffect, useRef, useState } from 'react';
import type { JSONContent } from '@tiptap/core';

export type NoteSnapshot = {
  document: JSONContent;
  questionNotes: Record<string, string>;
};
export type NoteVersion = {
  id: string;
  scope: string;
  createdAt: string;
  reason: 'manual' | 'before_restore' | 'restored' | 'before_ai' | 'ai';
  snapshot: NoteSnapshot;
};

const DATABASE = 'hrv-coach-note-history-v1';
const STORE = 'versions';
const SAVE_DELAY = 1800;

// JSON is also the editor's persistence format. A copy is taken at invocation,
// before a queued write can observe later mutations of the caller's objects.
function copy(snapshot: NoteSnapshot): NoteSnapshot {
  return JSON.parse(JSON.stringify(snapshot)) as NoteSnapshot;
}

function fingerprint(snapshot: NoteSnapshot): string {
  return JSON.stringify({
    document: snapshot.document,
    questionNotes: Object.fromEntries(
      Object.entries(snapshot.questionNotes).sort(([a], [b]) => a.localeCompare(b)),
    ),
  });
}

function hasContent(snapshot: NoteSnapshot): boolean {
  const populated = (node: JSONContent): boolean =>
    Boolean(node.text?.trim()) ||
    !['doc', 'paragraph', 'text', 'hardBreak'].includes(node.type || '') ||
    Boolean(node.content?.some(populated));
  return populated(snapshot.document) || Object.values(snapshot.questionNotes).some(value => value.trim());
}

function newestFirst(versions: NoteVersion[]): NoteVersion[] {
  return versions.sort((a, b) => b.createdAt.localeCompare(a.createdAt) || b.id.localeCompare(a.id));
}

function openHistory(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    if (typeof indexedDB === 'undefined') {
      reject(new Error('История недоступна в этом браузере.'));
      return;
    }
    let settled = false;
    const fail = (error: unknown) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      reject(error);
    };
    const timeout = setTimeout(
      () => fail(new Error('Не удалось открыть историю заметок. Попробуйте ещё раз.')),
      10000,
    );
    let request: IDBOpenDBRequest;
    try {
      request = indexedDB.open(DATABASE, 1);
    } catch (error) {
      fail(error);
      return;
    }
    request.onupgradeneeded = () => {
      const db = request.result;
      const store = db.objectStoreNames.contains(STORE)
        ? request.transaction!.objectStore(STORE)
        : db.createObjectStore(STORE, { keyPath: 'id' });
      if (!store.indexNames.contains('scope')) store.createIndex('scope', 'scope', { unique: false });
    };
    request.onsuccess = () => {
      if (settled) {
        request.result.close();
        return;
      }
      settled = true;
      clearTimeout(timeout);
      request.result.onversionchange = () => request.result.close();
      resolve(request.result);
    };
    request.onerror = () => fail(request.error);
    request.onblocked = () => fail(new Error('История открыта в другом окне. Закройте его и повторите.'));
  });
}

/** The read and optional append share one transaction: another tab cannot
 * insert between comparison with the latest snapshot and this append. */
async function transact(
  scope: string,
  pending?: { snapshot: NoteSnapshot; reason: NoteVersion['reason'] },
): Promise<NoteVersion[]> {
  const db = await openHistory();
  try {
    return await new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, pending ? 'readwrite' : 'readonly');
      const store = tx.objectStore(STORE);
      let result: NoteVersion[] = [];
      let failure: unknown;
      const read = store.index('scope').getAll(IDBKeyRange.only(scope));
      read.onsuccess = () => {
        try {
          result = newestFirst(read.result as NoteVersion[]);
          const latest = result[0];
          if (!pending || latest && fingerprint(latest.snapshot) === fingerprint(pending.snapshot)) return;
          // Monotonic within the scope, even if consecutive checkpoints share
          // a clock tick or the user's clock moves backwards.
          const createdAt = new Date(Math.max(Date.now(), latest ? Date.parse(latest.createdAt) + 1 : 0)).toISOString();
          const version: NoteVersion = {
            id: crypto.randomUUID(),
            scope,
            createdAt,
            reason: pending.reason,
            snapshot: pending.snapshot,
          };
          store.add(version);
          result = [version, ...result];
        } catch (error) {
          failure = error;
          tx.abort();
        }
      };
      tx.oncomplete = () => resolve(result);
      tx.onabort = () => reject(failure || tx.error || new Error('Не удалось сохранить историю заметок.'));
      tx.onerror = () => { failure ||= tx.error; };
    });
  } finally {
    db.close();
  }
}

function readable(error: unknown): string {
  if (error instanceof DOMException && error.name === 'QuotaExceededError') {
    return 'Недостаточно места для истории заметок. Предыдущие версии не удалены.';
  }
  if (error instanceof Error && !(error instanceof DOMException)) return error.message;
  return 'Не удалось сохранить историю заметок. Попробуйте ещё раз.';
}

type HistoryState = {
  scope: string;
  versions: NoteVersion[];
  loading: boolean;
  error: string;
};

/** Current text is persisted by the caller independently. This hook keeps
 * durable checkpoints; it never truncates history or replaces the editor. */
export function useNoteHistory(scope: string, snapshot: NoteSnapshot) {
  const [state, setState] = useState<HistoryState>({ scope, versions: [], loading: true, error: '' });
  const latest = useRef({ scope, snapshot });
  latest.current = { scope, snapshot };
  const mounted = useRef(false);
  const queue = useRef<Promise<void>>(Promise.resolve());
  const contentFingerprint = fingerprint(snapshot);
  const hadContent = useRef({ scope, value: hasContent(snapshot) });
  if (hadContent.current.scope !== scope) hadContent.current = { scope, value: hasContent(snapshot) };
  else hadContent.current.value ||= hasContent(snapshot);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const enqueue = useCallback((operation: () => Promise<void>): Promise<void> => {
    const next = queue.current.then(operation);
    // A failed checkpoint must not poison subsequent retries.
    queue.current = next.catch(() => {});
    return next;
  }, []);

  const checkpoint = useCallback((
    value?: NoteSnapshot,
    reason: NoteVersion['reason'] = 'manual',
  ): Promise<void> => {
    let captured: NoteSnapshot;
    try {
      if (!value && latest.current.scope !== scope) {
        return Promise.reject(new Error('Открыта другая заметка. Повторите действие.'));
      }
      captured = copy(value || latest.current.snapshot);
    } catch (error) {
      if (mounted.current && latest.current.scope === scope) {
        setState(previous => ({ ...previous, scope, error: readable(error) }));
      }
      return Promise.reject(error);
    }
    return enqueue(async () => {
      try {
        const versions = await transact(scope, { snapshot: captured, reason });
        if (mounted.current && latest.current.scope === scope) {
          setState({ scope, versions, loading: false, error: '' });
        }
      } catch (error) {
        if (mounted.current && latest.current.scope === scope) {
          setState(previous => ({ ...previous, scope, loading: false, error: readable(error) }));
        }
        throw error;
      }
    });
  }, [scope, enqueue]);

  useEffect(() => {
    let cancelled = false;
    setState({ scope, versions: [], loading: true, error: '' });
    void enqueue(async () => {
      try {
        if (cancelled || latest.current.scope !== scope) return;
        const initial = copy(latest.current.snapshot);
        const versions = await transact(scope, hasContent(initial) ? { snapshot: initial, reason: 'manual' } : undefined);
        if (!cancelled && mounted.current && latest.current.scope === scope) {
          setState({ scope, versions, loading: false, error: '' });
        }
      } catch (error) {
        if (!cancelled && mounted.current && latest.current.scope === scope) {
          setState({ scope, versions: [], loading: false, error: readable(error) });
        }
      }
    });
    return () => { cancelled = true; };
  }, [scope, enqueue]);

  useEffect(() => {
    // Explicit checkpoint(empty) is supported for the pre-edit baseline.
    // Untouched emptiness is skipped, but deleting an edited document is saved.
    if (!hadContent.current.value) return;
    const timer = setTimeout(() => { void checkpoint().catch(() => {}); }, SAVE_DELAY);
    return () => { clearTimeout(timer); };
  }, [scope, contentFingerprint, checkpoint]);

  const current = state.scope === scope ? state : { scope, versions: [], loading: true, error: '' };
  return { versions: current.versions, loading: current.loading, error: current.error, checkpoint };
}

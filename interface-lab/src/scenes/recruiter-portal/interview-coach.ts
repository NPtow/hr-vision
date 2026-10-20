import type { JSONContent } from '@tiptap/core';
import { useCallback, useEffect, useRef, useState } from 'react';
import { recruiterRequest } from './api';

export type ScriptQuestion = { id: string; topic: string; question: string; why: string; followups: string[] };
export type InterviewScript = { revision: number; questions: ScriptQuestion[]; source: string; createdAt: string; approvedAt: string | null; approvedBy: string | null };
export type CoachProgress = { currentId?: string; answers?: Record<string, { status: '' | 'answered' | 'skipped'; note: string }>; general?: string; noteDocument?: JSONContent };
export type CoachState = {
  candidateId: string; interviewId: string; interviewStatus: 'booked' | 'live' | 'completed';
  latest: InterviewScript | null; approved: InterviewScript | null; active: InterviewScript | null;
  pinnedRevision: number | null; revision: number; progress: CoachProgress; backupUrl: string;
};
export type CoachContext = { candidateId: string; interviewId: string; expectedRevision: number; profile: Record<string, unknown>; resume: { filename: string; text: string } | null; instruction: string };
export type LiveInterviewTurn = { id: string; text: string; speaker: string; at: string };
type Identity = { candidateId: string; interviewId: string; token: string };

/** One serial writer prevents delayed autosaves from replacing newer notes.
 * Explicit revision conflicts stop saving and retain the local text for export.
 */
export function useInterviewCoach({ candidateId, interviewId, token }: Identity) {
  const [data, setData] = useState<CoachState | null>(null);
  const [progress, setProgress] = useState<CoachProgress>({});
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const latest = useRef<CoachState | null>(null);
  const draft = useRef<CoachProgress>({});
  const version = useRef(0);
  const saved = useRef(0);
  const alive = useRef(true);
  const inflight = useRef<Promise<boolean> | null>(null);
  const commandLock = useRef(false);
  const readSequence = useRef(0);
  const identity = { candidateId, interviewId };
  const request = <T,>(action: string, body: Record<string, unknown> = {}) =>
    recruiterRequest<T>('interview/coach/' + action, token, { ...identity, ...body });
  const accept = (next: CoachState, replaceProgress: boolean) => {
    latest.current = next;
    if (alive.current) setData(next);
    if (replaceProgress) {
      draft.current = next.progress;
      if (alive.current) setProgress(next.progress);
    }
  };
  const refresh = useCallback(async () => {
    if (!interviewId || commandLock.current || inflight.current || version.current !== saved.current) return;
    const seq = ++readSequence.current;
    try {
      const next = await recruiterRequest<CoachState>('interview/coach/state', token, { candidateId, interviewId });
      if (alive.current && seq === readSequence.current && version.current === saved.current && !commandLock.current && !inflight.current) {
        accept(next, true); setError('');
      }
    } catch (cause) { if (alive.current && seq === readSequence.current) setError((cause as Error).message); }
  }, [candidateId, interviewId, token]);

  function flush(): Promise<boolean> {
    if (inflight.current) return inflight.current;
    if (version.current === saved.current) return Promise.resolve(true);
    readSequence.current++;
    const work = async () => {
      if (alive.current) { setBusy(true); setError(''); }
      try {
        while (version.current !== saved.current) {
          const state = latest.current;
          if (!state) throw new Error('Материалы интервью недоступны. Текст заметок сохранён в этом окне.');
          const sendingVersion = version.current;
          const next = await request<CoachState>('progress', {
            expectedRevision: state.revision, scriptRevision: state.active?.revision ?? null, progress: draft.current,
          });
          saved.current = sendingVersion;
          accept(next, false);
        }
        if (alive.current) setDirty(false);
        return true;
      } catch (cause) { if (alive.current) setError((cause as Error).message); return false; }
      finally { inflight.current = null; if (alive.current) setBusy(false); }
    };
    inflight.current = Promise.resolve().then(work);
    return inflight.current;
  }
  function change(next: CoachProgress) {
    draft.current = next; version.current++; readSequence.current++;
    setProgress(next); setDirty(true);
  }
  async function command(action: 'draft' | 'approve' | 'backup' | 'pin' | 'restore', body: Record<string, unknown> = {}) {
    if (commandLock.current) return false;
    commandLock.current = true; readSequence.current++;
    try {
      if (!await flush()) return false;
      setBusy(true); setError('');
      // Backup and progress share a revision. Read it after flushing notes.
      const input = action === 'restore' ? { ...body, expectedRevision: latest.current?.revision ?? 0, scriptRevision: latest.current?.active?.revision ?? null } : action === 'backup' ? { ...body, expectedRevision: latest.current?.revision ?? 0 } : body;
      const next = await request<CoachState>(action, input);
      accept(next, version.current === saved.current);
      return true;
    } catch (cause) { if (alive.current) setError((cause as Error).message); return false; }
    finally { commandLock.current = false; if (alive.current) setBusy(false); }
  }
  useEffect(() => {
    alive.current = true;
    void refresh();
    const timer = window.setInterval(() => { if (!document.hidden) void refresh(); }, 15000);
    const warn = (event: BeforeUnloadEvent) => {
      if (version.current !== saved.current) { event.preventDefault(); event.returnValue = ''; }
    };
    window.addEventListener('beforeunload', warn);
    return () => { alive.current = false; readSequence.current++; clearInterval(timer); window.removeEventListener('beforeunload', warn); };
  }, [refresh]);
  useEffect(() => {
    if (!dirty || error) return;
    const timer = window.setTimeout(() => { void flush(); }, 750);
    return () => clearTimeout(timer);
  }, [progress, dirty, error]);
  return { data, progress, dirty, busy, error, change, flush, command, refresh,
    context: () => request<CoachContext>('context') };
}

export type InterviewCoachController = ReturnType<typeof useInterviewCoach>;

export function downloadText(filename: string, value: string) {
  const url = URL.createObjectURL(new Blob([value], { type: 'text/plain;charset=utf-8' }));
  const a = document.createElement('a'); a.href = url; a.download = filename; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function contactLinks(email: string, telegram: string) {
  const cleanEmail = /^[^\s@<>?&#]+@[^\s@<>?&#]+\.[^\s@<>?&#]+$/.test(email) ? email : '';
  const handle = telegram.trim().replace(/^https?:\/\/t\.me\//i, '').replace(/^@/, '');
  return { email: cleanEmail, telegram: /^[a-zA-Z0-9_]{5,32}$/.test(handle) ? `https://t.me/${handle}` : '' };
}

import { useCallback, useEffect, useRef, useState } from 'react';
import { journeyRequestTarget, request, type EmployerReviewResponse } from '../connected-journey/api';
import type { Scope } from './EmployerShell';
import type { RecordingChapter } from '../interview-review/InterviewReview';

export type IntakeFile = { id: string; name: string; size: number };
export type PreparedProfile = {
  cvText: string;
  evaluationText: string;
  evaluationUrl?: string;
  recordings: { name: string; publicUrl: string; mime: string; size?: number }[];
  sourceLabel: string;
  transcriptText?: string;
  chapters?: RecordingChapter[];
  photo?: string | null;
  score?: number | null;
  summary?: string;
  profile?: { label: string; value: string }[];
  source?: { kind: 'dsa-panel'; candidateId: string; example: boolean };
};
export type IntakeComment = { id: string; recordingIndex: number; recordingId: string; seconds: number; chapterTitle?: string; text: string; createdAt: string };
export type IntakePerson = { id: string; name: string; role: string; contact: string; link: string; note: string; scope: Scope; files: IntakeFile[]; createdAt: string; archived: boolean; preparedProfile?: PreparedProfile; screeningComments?: IntakeComment[]; reviewRevision?: number; reviewResponse?: EmployerReviewResponse | null; reviewRejection?: { reason: string; missing: string; improve: string; createdAt?: string } | null };
export function useIntake(token: string, scope: Scope, vacancyId?: string) {
  const context = JSON.stringify([token, scope, vacancyId || '']);
  const [result, setResult] = useState<{ context: string; people: IntakePerson[]; error: string; loading: boolean } | null>(null);
  const sequence = useRef(0);
  const currentContext = useRef<string | null>(context);
  currentContext.current = context;
  const refresh = useCallback(async () => {
    if (!token || currentContext.current !== context) return;
    const seq = ++sequence.current;
    setResult(previous => ({ context, people: previous?.context === context ? previous.people : [], error: '', loading: true }));
    try { const data = await request<{ candidates: IntakePerson[] }>(`intake?scope=${scope}${vacancyId ? '&vacancyId=' + encodeURIComponent(vacancyId) : ''}`, token); if (sequence.current === seq && currentContext.current === context) setResult({ context, people: data.candidates, error: '', loading: false }); }
    catch (e) { if (sequence.current === seq && currentContext.current === context) setResult(previous => ({ context, people: previous?.context === context ? previous.people : [], error: (e as Error).message, loading: false })); }
  }, [token, scope, vacancyId, context]);
  useEffect(() => {
    currentContext.current = context;
    void refresh();
    return () => { sequence.current++; if (currentContext.current === context) currentContext.current = null; };
  }, [refresh, context]);
  // The new vacancy renders before effects run; never expose the previous result in that frame.
  const current = result?.context === context ? result : null;
  return { people: current?.people || [], error: current?.error || '', loading: !!token && (!current || current.loading), refresh };
}
export async function loadResumeFile(token: string, file: IntakeFile, signal?: AbortSignal) {
  const target = journeyRequestTarget(`intake/files/${encodeURIComponent(file.id)}`, token);
  const response = await fetch('/api/hr/' + target.path, { headers: { Authorization: `Bearer ${token}` }, cache: 'no-store', signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(30000)]) : AbortSignal.timeout(30000) });
  if (!response.ok) throw new Error('Не удалось загрузить резюме. Попробуйте ещё раз.');
  return response.blob();
}
export async function downloadResume(token: string, file: IntakeFile, mode: 'download' | 'open' = 'download') {
  const targetWindow = mode === 'open' ? window.open('about:blank', '_blank') : null;
  if (targetWindow) targetWindow.opener = null;
  try {
    const url = URL.createObjectURL(await loadResumeFile(token, file));
    if (targetWindow) targetWindow.location.replace(url);
    else {
      const a = document.createElement('a'); a.href = url; a.download = file.name; document.body.appendChild(a); a.click(); a.remove();
    }
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  } catch (cause) { targetWindow?.close(); throw cause; }
}

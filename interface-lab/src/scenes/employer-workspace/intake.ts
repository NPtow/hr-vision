import { useCallback, useEffect, useRef, useState } from 'react';
import { journeyRequestTarget, request } from '../connected-journey/api';
import type { Scope } from './EmployerShell';

export type IntakeFile = { id: string; name: string; size: number };
export type PreparedProfile = {
  cvText: string;
  evaluationText: string;
  evaluationUrl?: string;
  recordings: { name: string; publicUrl: string; mime: string; size: number }[];
  sourceLabel: string;
};
export type IntakePerson = { id: string; name: string; role: string; contact: string; link: string; note: string; scope: Scope; files: IntakeFile[]; createdAt: string; archived: boolean; preparedProfile?: PreparedProfile };
export function useIntake(token: string, scope: Scope, vacancyId?: string) {
  const context = JSON.stringify([token, scope, vacancyId || '']);
  const [result, setResult] = useState<{ context: string; people: IntakePerson[]; error: string; loading: boolean } | null>(null);
  const sequence = useRef(0);
  const refresh = useCallback(async () => {
    const seq = ++sequence.current;
    if (!token) return;
    setResult(previous => ({ context, people: previous?.context === context ? previous.people : [], error: '', loading: true }));
    try { const data = await request<{ candidates: IntakePerson[] }>(`intake?scope=${scope}${vacancyId ? '&vacancyId=' + encodeURIComponent(vacancyId) : ''}`, token); if (sequence.current === seq) setResult({ context, people: data.candidates, error: '', loading: false }); }
    catch (e) { if (sequence.current === seq) setResult(previous => ({ context, people: previous?.context === context ? previous.people : [], error: (e as Error).message, loading: false })); }
  }, [token, scope, vacancyId, context]);
  useEffect(() => { void refresh(); return () => { sequence.current++; }; }, [refresh]);
  // The new vacancy renders before effects run; never expose the previous result in that frame.
  const current = result?.context === context ? result : null;
  return { people: current?.people || [], error: current?.error || '', loading: !!token && (!current || current.loading), refresh };
}
export async function downloadResume(token: string, file: IntakeFile) {
  const target = journeyRequestTarget(`intake/files/${encodeURIComponent(file.id)}`, token);
  const response = await fetch('/api/hr/' + target.path, { headers: { Authorization: `Bearer ${token}` }, cache: 'no-store' });
  if (!response.ok) throw new Error('Не удалось загрузить резюме. Попробуйте ещё раз.');
  const url = URL.createObjectURL(await response.blob());
  const a = document.createElement('a'); a.href = url; a.download = file.name; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}

import { useCallback, useEffect, useRef, useState } from 'react';
import { request } from '../connected-journey/api';
import type { Scope } from './EmployerShell';

export type IntakeFile = { id: string; name: string; size: number };
export type IntakePerson = { id: string; name: string; role: string; contact: string; link: string; note: string; scope: Scope; files: IntakeFile[]; createdAt: string; archived: boolean };
export function useIntake(token: string, scope: Scope) {
  const [people, setPeople] = useState<IntakePerson[]>([]);
  const [error, setError] = useState('');
  const sequence = useRef(0);
  const refresh = useCallback(async () => {
    const seq = ++sequence.current;
    if (!token) return;
    try { const data = await request<{ candidates: IntakePerson[] }>(`intake?scope=${scope}`, token); if (sequence.current === seq) { setPeople(data.candidates); setError(''); } }
    catch (e) { if (sequence.current === seq) setError((e as Error).message); }
  }, [token, scope]);
  useEffect(() => { setPeople([]); void refresh(); return () => { sequence.current++; }; }, [refresh]);
  return { people, error, refresh };
}
export async function downloadResume(token: string, file: IntakeFile) {
  const response = await fetch(`/api/hr/intake/files/${file.id}`, { headers: { Authorization: `Bearer ${token}` }, cache: 'no-store' });
  if (!response.ok) throw new Error('Не удалось загрузить резюме. Попробуйте ещё раз.');
  const url = URL.createObjectURL(await response.blob());
  const a = document.createElement('a'); a.href = url; a.download = file.name; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}

import { useEffect, useRef, useState } from 'react';
import type { InterviewAnswer, InterviewAssistantRequest, InterviewSearch } from './interview-assistant-api';

export type InterviewChatTurn = { id: string; query: string; answer?: InterviewAnswer; error?: string };
export function useInterviewAssistant(request: InterviewAssistantRequest | undefined) {
  const [messages, setMessages] = useState<InterviewChatTurn[]>([]);
  const [search, setSearch] = useState<InterviewSearch | null>(null);
  const [busy, setBusy] = useState<'chat' | 'search' | null>(null);
  const [searchError, setSearchError] = useState('');
  const controller = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; controller.current?.abort(); }; }, []);

  async function submit(mode: 'chat' | 'search', query: string, retryId?: string) {
    if (controller.current || !query.trim()) return false;
    const id = retryId || crypto.randomUUID();
    const abort = new AbortController(); controller.current = abort;
    setBusy(mode);
    if (mode === 'search') { setSearch(null); setSearchError(''); }
    else setMessages(rows => retryId ? rows.map(row => row.id === id ? { id, query } : row) : [...rows, { id, query }]);
    try {
      if (!request) throw new Error('Расшифровка ещё не готова. Попробуйте позже.');
      const result = await request(mode, query.trim(), id, abort.signal);
      if (!mounted.current || abort.signal.aborted) return false;
      if (result.mode === 'search') setSearch(result);
      else setMessages(rows => rows.map(row => row.id === id ? { ...row, answer: result } : row));
      return true;
    } catch (cause) {
      if (!mounted.current || abort.signal.aborted) return false;
      const message = cause instanceof Error ? cause.message : 'Помощник временно недоступен.';
      if (mode === 'search') setSearchError(message);
      else setMessages(rows => rows.map(row => row.id === id ? { ...row, error: message } : row));
      return false;
    } finally { if (mounted.current) setBusy(null); controller.current = null; }
  }
  function clearSearch() { setSearch(null); setSearchError(''); }
  return { messages, search, busy, searchError, submit, clearSearch };
}

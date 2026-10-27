export type InterviewSearchMatch = { id: string; text: string; startMs: number; endMs: number; speakerName: string };
export type InterviewAnswer = {
  mode: 'chat'; text: string; supported: boolean;
  parts?: { text: string; sourceIds: string[] }[];
  evidence: { id: string; quote: string; startMs: number | null; endMs?: number; speakerName: string }[];
  coverage: { selected: number; omitted: number }; model: string; version: string;
};
export type InterviewSearch = { mode: 'search'; matches: InterviewSearchMatch[]; totalMatches: number; coverage: string; model: string; version: string };
export type InterviewSavedTurn = { id: string; query: string; answer: InterviewAnswer };
export type InterviewAssistantRequest = ((action: 'chat' | 'search', query: string, requestId: string, signal: AbortSignal) => Promise<InterviewAnswer | InterviewSearch>) & {
  scopeKey?: string;
  history?: (signal: AbortSignal) => Promise<InterviewSavedTurn[]>;
};

/** Only source identities cross this boundary. Transcript text is resolved server-side. */
export function interviewAssistant(token: string, source: Record<string, unknown>): InterviewAssistantRequest {
  const call = async (action: 'chat' | 'search' | 'history', query: string, requestId: string, signal: AbortSignal) => {
    const response = await fetch('/api/hr/employer/interview-assistant', {
      method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...source, action, query, requestId }), cache: 'no-store',
      signal: AbortSignal.any([signal, AbortSignal.timeout(150000)]),
    });
    const data = await response.json().catch(() => ({ error: 'Помощник временно недоступен.' }));
    if (!response.ok) throw new Error(data.error || 'Не удалось получить ответ. Попробуйте ещё раз.');
    if (data.mode !== action || (action === 'history' ? !Array.isArray(data.messages)
      : action === 'chat' ? typeof data.text !== 'string' || !Array.isArray(data.evidence) : !Array.isArray(data.matches))) {
      throw new Error('Не удалось прочитать ответ. Попробуйте ещё раз.');
    }
    return data;
  };
  const request: InterviewAssistantRequest = (action, query, requestId, signal) => call(action, query, requestId, signal);
  // In-memory identity only, never rendered, logged or stored in browser persistence.
  request.scopeKey = JSON.stringify([token, source]);
  request.history = async signal => {
    const result = await call('history', '', '', signal);
    if (result.messages.some((row: InterviewSavedTurn) => typeof row.id !== 'string' || typeof row.query !== 'string'
      || row.answer?.mode !== 'chat' || typeof row.answer.text !== 'string' || !Array.isArray(row.answer.evidence))) {
      throw new Error('Не удалось прочитать историю разговора.');
    }
    return result.messages;
  };
  return request;
}

export type InterviewSearchMatch = { id: string; text: string; startMs: number; endMs: number; speakerName: string };
export type InterviewAnswer = {
  mode: 'chat'; text: string; supported: boolean;
  evidence: { id: string; quote: string; startMs: number | null; speakerName: string }[];
  coverage: { selected: number; omitted: number }; model: string; version: string;
};
export type InterviewSearch = { mode: 'search'; matches: InterviewSearchMatch[]; totalMatches: number; coverage: string; model: string; version: string };
export type InterviewAssistantRequest = (action: 'chat' | 'search', query: string, requestId: string, signal: AbortSignal) => Promise<InterviewAnswer | InterviewSearch>;

/** Only source identities cross this boundary. Transcript text is resolved server-side. */
export function interviewAssistant(token: string, source: Record<string, unknown>): InterviewAssistantRequest {
  return async (action, query, requestId, signal) => {
    const response = await fetch('/api/hr/employer/interview-assistant', {
      method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...source, action, query, requestId }), cache: 'no-store',
      signal: AbortSignal.any([signal, AbortSignal.timeout(150000)]),
    });
    const data = await response.json().catch(() => ({ error: 'Помощник временно недоступен.' }));
    if (!response.ok) throw new Error(data.error || 'Не удалось получить ответ. Попробуйте ещё раз.');
    if (data.mode !== action || (action === 'chat' ? typeof data.text !== 'string' || !Array.isArray(data.evidence) : !Array.isArray(data.matches))) {
      throw new Error('Не удалось прочитать ответ. Попробуйте ещё раз.');
    }
    return data;
  };
}

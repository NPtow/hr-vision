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


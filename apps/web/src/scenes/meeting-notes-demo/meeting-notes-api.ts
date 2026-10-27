export type LabTranscriptTurn = {
  id: string;
  speakerId: string;
  speakerName: string;
  text: string;
  startMs: number;
  endMs?: number;
  source?: string;
};

export type LabMessage = {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  createdAt?: string;
  evidenceIds?: string[];
  evidence?: { id: string; sourceId: string; sourceType: string; startMs: number | null; speakerName?: string; quote: string }[];
};

export type LabNotes = { text: string; revision: number; updatedAt?: string };
export type LabCapture = { status: string; error?: string; instanceId?: string };
export type LabSession = {
  id: string;
  title: string;
  status: 'ready' | 'live' | 'completed';
  createdAt: string;
  startedAt?: string | null;
  endedAt?: string | null;
  notes: LabNotes;
  transcript: LabTranscriptTurn[];
  transcriptSource?: 'daily-live' | 'daily-stored';
  messages: LabMessage[];
  capture: { recording: LabCapture; transcription: LabCapture };
  materials?: unknown;
};
export type LabSessionSummary = Pick<LabSession, 'id' | 'title' | 'status' | 'createdAt' | 'startedAt' | 'endedAt'>;
export type LabRecording = {
  id: string;
  url: string;
  duration?: number;
  /** Only an explicit backend mapping may make live transcript timestamps seekable. */
  transcriptOffsetMs?: number;
};
export type LabMaterials = {
  session: LabSession;
  recordings: LabRecording[];
  transcripts: { id: string; text: string }[];
  video: { status: string; error?: string; complete?: boolean };
  transcription: { status: string; error?: string; complete?: boolean };
};
export type LabCapabilities = { dailyConfigured: boolean; modelConfigured: boolean };

export class MeetingLabError extends Error {
  constructor(message: string, public status: number, public code?: string) {
    super(message);
    this.name = 'MeetingLabError';
  }
}

export function createMeetingLabApi(base = '/api/hr/meeting-lab', token?: string) {
  const prefix = base.replace(/\/$/, '');
  async function request<T>(path: string, body?: unknown): Promise<T> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), path.endsWith('/chat') ? 150000 : path.endsWith('/materials') ? 60000 : 30000);
    let response: Response;
    let data: Record<string, any> | null;
    try {
      response = await fetch(`${prefix}${path}`, {
      method: body === undefined ? 'GET' : 'POST',
      credentials: 'include',
      signal: controller.signal,
      headers: {
        ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      data = await response.json().catch(() => null);
    } catch (failure) {
      if (controller.signal.aborted) throw new MeetingLabError('Ответ задерживается. Попробуйте ещё раз.', 408);
      throw failure;
    } finally { clearTimeout(timeout); }
    if (!response.ok) {
      const message = typeof data?.error === 'string' ? data.error : data?.error?.message || data?.message;
      throw new MeetingLabError(
        response.status === 401 ? 'Войдите в кабинет работодателя.' : message || `Не удалось сохранить изменения (${response.status}).`,
        response.status,
        data?.code || data?.error?.code,
      );
    }
    if (!data || typeof data !== 'object') throw new MeetingLabError('Сервер вернул пустой ответ.', response.status);
    return data as T;
  }
  const sessionPath = (id: string) => `/sessions/${encodeURIComponent(id)}`;
  return {
    list: () => request<{ sessions: LabSessionSummary[]; capabilities: LabCapabilities }>('/sessions'),
    create: (requestId: string, title?: string) => request<{ session: LabSession }>('/sessions', { requestId, title }),
    get: (id: string) => request<{ session: LabSession }>(sessionPath(id)),
    join: (id: string) => request<{ session: LabSession; room: { url: string; token: string } }>(`${sessionPath(id)}/join`, { consent: true }),
    started: (id: string) => request<{ session: LabSession }>(`${sessionPath(id)}/started`, {}),
    capture: (id: string, body: { kind: 'recording' | 'transcription'; status: 'started' | 'error'; instanceId?: string; error?: string }) => request<{ session?: LabSession }>(`${sessionPath(id)}/capture`, body),
    transcript: (id: string, turns: LabTranscriptTurn[]) => request<{ session?: LabSession }>(`${sessionPath(id)}/transcript`, {
      turns: turns.map(({ id: turnId, speakerId, speakerName, text, startMs, endMs }) => ({ id: turnId, speakerId, speakerName, text, startMs, endMs })),
    }),
    notes: (id: string, revision: number, text: string) => request<{ notes: LabNotes }>(`${sessionPath(id)}/notes`, { revision, text }),
    chat: (id: string, requestId: string, message: string) => request<{ message: LabMessage; session: LabSession }>(`${sessionPath(id)}/chat`, { requestId, message }),
    finish: (id: string) => request<{ session: LabSession }>(`${sessionPath(id)}/finish`, {}),
    materials: (id: string) => request<LabMaterials>(`${sessionPath(id)}/materials`, {}),
  };
}

export type MeetingLabApi = ReturnType<typeof createMeetingLabApi>;
export function readableError(error: unknown) {
  return error instanceof Error ? error.message : 'Не удалось выполнить действие. Попробуйте ещё раз.';
}

export function clock(seconds: number) {
  const value = Math.max(0, Math.floor(Number.isFinite(seconds) ? seconds : 0));
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor(value / 60) % 60;
  const rest = value % 60;
  return `${hours ? `${hours}:` : ''}${String(minutes).padStart(2, '0')}:${String(rest).padStart(2, '0')}`;
}

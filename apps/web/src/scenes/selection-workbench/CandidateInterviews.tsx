import { useEffect, useState } from 'react';
import type { CallMaterials } from '../candidate-portal/api';
import { OutreachError } from '../hh-outreach/api';
import { SourceScreeningReview } from '../interview-review/InterviewReview';
import { orderedRecordings, selectedTranscript, transcriptStatusMessage } from '../interview-review/recording-transcript';
import { parseMeetingTranscript } from '../connected-journey/transcript';

type InterviewPart = { id: string; recordingId: string; kind: string; state: string; partIndex?: number; transcriptStatus: string; transcriptId?: string | null };
type Interview = { id: string; status: string; createdAt?: string | null; completedAt?: string | null; parts: InterviewPart[] };
type Catalogue = { candidateId: string; interviews: Interview[] };
type Materials = CallMaterials & { candidateId: string; interviewId: string; parts: InterviewPart[]; partTranscripts?: CallMaterials['transcripts'] };

// Read-only adapter: opening the viewer never collects or processes source materials.
async function read<T>(path: string, token: string, signal: AbortSignal): Promise<T> {
  const response = await fetch('/api/hr/selection/candidates/' + path, {
    credentials: 'same-origin', cache: 'no-store', headers: { Authorization: `Bearer ${token}`, Accept: 'application/json' },
    signal: AbortSignal.any([signal, AbortSignal.timeout(30_000)]),
  });
  if (!response.ok) throw new OutreachError('Не удалось загрузить интервью.', response.status);
  return await response.json() as T;
}

function useInterviewRead<T>(path: string, token: string, expire: () => void, validate: (value: T) => boolean) {
  const [value, setValue] = useState<T | null>(null);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setValue(null); setError('');
    void read<T>(path, token, controller.signal).then(result => {
      if (controller.signal.aborted) return;
      if (!validate(result)) throw new Error('identity_mismatch');
      setValue(result);
    }).catch(cause => {
      if (controller.signal.aborted) return;
      if (cause instanceof OutreachError && [401, 403].includes(cause.status)) expire();
      else setError(cause instanceof OutreachError && cause.status === 409 ? 'Материалы этого интервью ещё не готовы.'
        : cause instanceof OutreachError && [404, 410].includes(cause.status) ? 'Материалы этого интервью недоступны.'
        : 'Не удалось загрузить интервью. Повторите загрузку.');
    });
    return () => controller.abort();
    // Each caller is keyed by candidate/interview identity; validators capture that identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, token, expire, attempt]);
  return { value, error, retry: () => setAttempt(current => current + 1) };
}

function InterviewMaterials({ candidateId, interview, name, token, expire }: { candidateId: string; interview: Interview; name: string; token: string; expire: () => void }) {
  const { value: materials, error, retry } = useInterviewRead<Materials>(
    `${encodeURIComponent(candidateId)}/interviews/${encodeURIComponent(interview.id)}/materials`, token, expire,
    value => value.candidateId === candidateId && value.interviewId === interview.id && Array.isArray(value.parts) && Array.isArray(value.recordings) && Array.isArray(value.transcripts));
  const [partId, setPartId] = useState('');
  if (error) return <div role="alert"><p>{error}</p><button className="sl-open" onClick={retry}>Повторить загрузку интервью</button></div>;
  if (!materials) return <p role="status">Загружаем запись и расшифровку…</p>;
  const parts = orderedRecordings(materials.parts);
  const part = parts.find(item => item.id === partId) || parts[0];
  const recording = materials.recordings.find(item => item.id === part?.recordingId);
  // Popup API exposes only the exact saved primary selection for a ready part.
  // Never read transcriptVariants or join independent recordings' transcripts.
  const primary = part?.transcriptStatus === 'ready' ? materials.partTranscripts?.find(item =>
    item.recordingId === part.recordingId && item.id === part.transcriptId) : undefined;
  const legacy = part?.transcriptStatus === 'ready' ? selectedTranscript(materials, recording?.id) : undefined;
  const transcript = primary || (legacy && (!part?.transcriptId || legacy.id === part.transcriptId) ? legacy : undefined);
  const text = transcript?.text || '';
  const chapters = parseMeetingTranscript(text);
  const emptyMessage = part?.state === 'error' ? 'Не удалось получить запись этой части интервью.'
    : ['processing', 'pending'].includes(part?.state || '') ? 'Запись этой части интервью ещё обрабатывается.'
    : 'Запись этой части интервью отсутствует.';
  return <>
    {parts.length > 1 && <label>Часть интервью <select aria-label="Часть интервью" value={part?.id || ''} onChange={event => setPartId(event.target.value)}>
      {parts.map((item, index) => <option key={item.id} value={item.id}>Часть {index + 1}{item.kind === 'audio' ? ' · аудио' : ''}{!materials.recordings.some(recording => recording.id === item.recordingId) ? ' · запись недоступна' : ''}</option>)}
    </select></label>}
    {!text && <p role="status">{['failed', 'error', 'uncertain'].includes(part?.transcriptStatus || '') ? 'Не удалось подготовить основную расшифровку этой части.'
      : ['missing', 'revoked'].includes(part?.transcriptStatus || '') ? 'Сохранённая расшифровка этой части недоступна.'
      : transcriptStatusMessage(part?.transcriptStatus || materials.transcription.status)}</p>}
    <SourceScreeningReview key={`${interview.id}:${part?.id || 'missing'}:${recording?.url || ''}`}
      appearance="candidate" sourceId={`${candidateId}:${interview.id}:${part?.id || 'missing'}`}
      name={name} recordingId={recording?.id} chapters={chapters} fullChapters={chapters}
      media={recording ? { src: recording.url } : undefined} transcriptText={text} transcriptSeekable={!!text}
      hideFeedbackQuestions emptyMessage={emptyMessage}/>
    <button className="sl-open" onClick={retry}>Обновить материалы интервью</button>
  </>;
}

export function CandidateInterviews({ candidateId, name, token, expire }: { candidateId: string; name: string; token: string; expire: () => void }) {
  const { value: catalogue, error, retry } = useInterviewRead<Catalogue>(`${encodeURIComponent(candidateId)}/interviews`, token, expire,
    value => value.candidateId === candidateId && Array.isArray(value.interviews));
  const [interviewId, setInterviewId] = useState('');
  const interview = catalogue?.interviews.find(item => item.id === interviewId) || catalogue?.interviews[0];
  return <section className="sl-interview-view" aria-label="HR-интервью">
    {error ? <div role="alert"><p>{error}</p><button className="sl-open" onClick={retry}>Повторить загрузку интервью</button></div>
      : !catalogue ? <p role="status">Загружаем интервью…</p>
      : !interview ? <p role="status">HR-интервью пока нет.</p> : <>
        {catalogue.interviews.length > 1 && <label>Интервью <select aria-label="Интервью кандидата" value={interview.id} onChange={event => setInterviewId(event.target.value)}>
          {catalogue.interviews.map((item, index) => <option key={item.id} value={item.id}>Интервью {index + 1}{item.completedAt || item.createdAt ? ` · ${new Date(item.completedAt || item.createdAt!).toLocaleDateString('ru-RU')}` : ''}</option>)}
        </select></label>}
        <InterviewMaterials key={interview.id} candidateId={candidateId} interview={interview} name={name} token={token} expire={expire}/>
      </>}
  </section>;
}

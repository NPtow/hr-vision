import { useEffect, useRef, useState } from 'react';
import { getMaterials, isAuthError } from '../api';
import type { Interview, Materials } from '../types';
import { formatTimestamp, parseTranscript, selectMaterialsTranscript } from './transcript-model';

export type InterviewPosition = { interviewId: string; recordingId: string; seconds: number };
type Props = { compact?: boolean; onExpand?: () => void; initialPosition?: InterviewPosition; onPositionChange?: (position: InterviewPosition) => void; candidateId: string; interviews: unknown; onError?: (message: string, error?: unknown) => void; onAuthError?: () => void };
export function InterviewPanel(props: Props) {
  return <InterviewContent key={props.candidateId} {...props} />;
}
function InterviewContent({ candidateId, interviews, onError, onAuthError, compact = false, onExpand, initialPosition, onPositionChange }: Props) {
  const raw = Array.isArray(interviews) ? interviews : (interviews as { interviews?: unknown } | null)?.interviews;
  const list = (Array.isArray(raw) ? raw : []).filter((x): x is Interview => !!x && typeof x === 'object' && typeof x.id === 'string');
  const [selected, setSelected] = useState(initialPosition?.interviewId || '');
  const interviewId = list.some(x => x.id === selected) ? selected : list[0]?.id || '';
  const selectedInterview = list.find(item => item.id === interviewId);
  const sourceDate = selectedInterview?.completedAt || selectedInterview?.createdAt;
  const date = typeof sourceDate === 'string' && Number.isFinite(Date.parse(sourceDate)) ? sourceDate : undefined;
  return <section className={`cp-interviews${compact ? ' cp-interviews-compact' : ''}`} aria-label="Интервью">
    <div className="cp-interview-heading"><div><h2>{compact ? 'HR-интервью' : 'Интервью'}</h2>{compact && <p>{date ? <time dateTime={date}>{new Date(date).toLocaleDateString('ru-RU')}</time> : 'Дата не указана'}</p>}</div>{compact && onExpand && <button className="cp-interview-expand" type="button" onClick={onExpand}>Открыть интервью</button>}</div>
    {!list.length ? <p>Интервью пока нет.</p> : <>
      {list.length > 1 && <label>Встреча <select value={interviewId} onChange={e => setSelected(e.target.value)}>{list.map((item, i) => <option key={item.id} value={item.id}>Интервью {i + 1}{(() => { const date = item.completedAt || item.createdAt; return typeof date === 'string' && Number.isFinite(Date.parse(date)) ? ` · ${new Date(date).toLocaleString('ru-RU')}` : ' · дата не указана'; })()}</option>)}</select></label>}
      <InterviewMaterials key={interviewId} candidateId={candidateId} interviewId={interviewId} onError={onError} onAuthError={onAuthError} compact={compact} initialPosition={initialPosition} onPositionChange={onPositionChange}/>
    </>}
  </section>;
}
function InterviewMaterials({ candidateId, interviewId, onError, onAuthError, compact, initialPosition, onPositionChange }: { candidateId: string; interviewId: string; onError?: Props['onError']; onAuthError?: () => void; compact?: boolean; initialPosition?: InterviewPosition; onPositionChange?: Props['onPositionChange'] }) {
  const [data, setData] = useState<Materials | null>(null), [error, setError] = useState(''), [loading, setLoading] = useState(false), [revision, setRevision] = useState(0), [recordingId, setRecordingId] = useState(initialPosition?.interviewId === interviewId ? initialPosition.recordingId : '');
  const media = useRef<HTMLMediaElement | null>(null);
  const pendingSeek = useRef<number | null>(null);
  const initialSeek = useRef(initialPosition?.interviewId === interviewId ? initialPosition : undefined);
  const positionCallback = useRef(onPositionChange); positionCallback.current = onPositionChange;
  const restore = useRef<{ id: string; time: number; playing: boolean } | null>(null);
  const callback = useRef(onError); callback.current = onError;
  const authCallback = useRef(onAuthError); authCallback.current = onAuthError;
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError('');
    getMaterials(candidateId, interviewId, controller.signal).then(value => { if (!controller.signal.aborted) setData(value); }).catch(reason => {
      if (controller.signal.aborted) return;
      const message = reason instanceof Error ? reason.message : 'Не удалось получить материалы.';
      media.current?.pause();
      if (media.current) { media.current.removeAttribute('src'); media.current.load(); }
      restore.current = null; pendingSeek.current = null; setData(null);
      setError(message); callback.current?.(message, reason); if (isAuthError(reason)) authCallback.current?.();
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [candidateId, interviewId, revision]);
  const recordings = data?.recordings || [];
  const recording = recordings.find(r => r.id === recordingId) || recordings[0];
  const refresh = () => {
    if (media.current && recording) restore.current = { id: recording.id, time: media.current.currentTime, playing: !media.current.paused };
    setRevision(n => n + 1);
  };
  const expires = typeof recording?.expires === 'number' ? recording.expires * 1000 : typeof recording?.expiresAt === 'string' ? Date.parse(recording.expiresAt) : NaN;
  useEffect(() => {
    if (!Number.isFinite(expires) || expires <= Date.now()) return;
    const timer = setTimeout(() => { const player = media.current; if (player && recording) restore.current = { id: recording.id, time: player.currentTime, playing: !player.paused }; setRevision(n => n + 1); }, Math.min(2_147_483_647, Math.max(1000, expires - Date.now() - 15000)));
    return () => clearTimeout(timer);
  }, [expires, recording?.id, recording?.url]);
  useEffect(() => {
    const focus = () => { if (!loading && Number.isFinite(expires) && expires <= Date.now()) refresh(); };
    window.addEventListener('focus', focus);
    return () => window.removeEventListener('focus', focus);
  }, [expires, loading, recording?.id]);
  useEffect(() => () => { media.current?.pause(); }, []);
  const part = data?.parts.find(p => p.recordingId === recording?.id || p.id === recording?.id);
  const transcript = recording ? selectMaterialsTranscript(data, recording.id) : undefined;
  const turns = parseTranscript(transcript);
  const state = recording?.state ?? recording?.status ?? part?.state;
  const ready = !!recording?.url && (state === undefined || state === 'ready' || state === 'completed');
  const audio = recording?.kind === 'audio' || String(recording?.mimeType ?? recording?.mime ?? '').startsWith('audio/');
  const playerProps = { onTimeUpdate: () => { const player = media.current; if (recording && player && Number.isFinite(player.currentTime)) positionCallback.current?.({ interviewId, recordingId: recording.id, seconds: player.currentTime }); }, controls: true, preload: 'metadata', src: ready ? recording?.url : undefined, ref: (node: HTMLMediaElement | null) => { media.current = node; }, onLoadedMetadata: () => {
    const saved = restore.current, player = media.current;
    const initial = initialSeek.current;
    if (initial && player && recording?.id === initial.recordingId) { if (Number.isFinite(initial.seconds) && initial.seconds >= 0 && Number.isFinite(player.duration) && initial.seconds < player.duration) player.currentTime = initial.seconds; initialSeek.current = undefined; }
    if (player && pendingSeek.current !== null) { if (Number.isFinite(player.duration) && pendingSeek.current < player.duration) player.currentTime = pendingSeek.current; pendingSeek.current = null; }
    if (saved && player && saved.id === recording?.id) { player.currentTime = Number.isFinite(player.duration) ? Math.min(saved.time, player.duration) : saved.time; if (saved.playing) void player.play().catch(() => {}); restore.current = null; }
  }, onError: () => { setError('Не удалось воспроизвести запись. Обновите ссылку и повторите попытку.'); if (!loading && Number.isFinite(expires) && expires <= Date.now()) refresh(); } };
  return <div className="cp-interview-materials" aria-busy={loading}>
    {(!compact || error) && <button type="button" disabled={loading} onClick={refresh}>{loading ? 'Загрузка…' : 'Обновить материалы и ссылку'}</button>}
    {compact && loading && !data && <p role="status">Загружаем интервью…</p>}
    {error && <p role="alert">{error}</p>}
    {!compact && data?.parts.length ? <ul className="cp-recording-parts">{data.parts.map((item, i) => <li key={item.id}>Часть {typeof item.partIndex === 'number' ? item.partIndex + 1 : i + 1} · {item.kind === 'audio' ? 'аудио' : item.kind === 'video' ? 'видео' : 'тип не указан'} · {item.mediaAvailable === true ? 'запись доступна' : `запись: ${item.state || 'нет данных'}`} · расшифровка: {item.transcriptStatus || 'нет данных'}</li>)}</ul> : null}
    {recordings.length > 1 && <label>Запись <select value={recording?.id || ''} onChange={e => { media.current?.pause(); restore.current = null; pendingSeek.current = null; setRecordingId(e.target.value); }}>{recordings.map((r, i) => <option key={r.id} value={r.id}>Запись {i + 1}{r.kind === 'audio' ? ' · аудио' : ''}</option>)}</select></label>}
    {ready ? audio ? <audio key={recording?.id} {...playerProps}/> : <video key={recording?.id} {...playerProps}/> : !loading && <p>Готовая запись недоступна{typeof state === 'string' ? ` · ${state}` : ''}.</p>}
    {!compact && <div className="cp-transcript"><h3>Расшифровка выбранной записи</h3>{turns.length ? turns.map((turn, i) => <p key={i}>{turn.start !== undefined && ready ? <button type="button" onClick={() => { const player = media.current; if (!player) return; const seek = () => { if (Number.isFinite(player.duration) && turn.start! < player.duration) player.currentTime = turn.start!; }; if (player.readyState >= 1) seek(); else pendingSeek.current = turn.start!; }}>{formatTimestamp(turn.start)}</button> : null} {turn.speaker && <strong>{turn.speaker}: </strong>}{turn.text}</p>) : <p>Для этой записи связанная расшифровка недоступна.</p>}</div>}
  </div>;
}
export default InterviewPanel;

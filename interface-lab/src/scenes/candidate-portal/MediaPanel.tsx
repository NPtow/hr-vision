import { useEffect, useRef, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { Button } from '../../components/ui/button';
import type { CallMaterials, MediaChannel } from './api';
import { orderedRecordings, selectedTranscript } from '../interview-review/recording-transcript';

type Props = { identity: string; load: () => Promise<CallMaterials> };

// A different candidate/meeting always gets a fresh panel. Signed URLs and text
// are deliberately kept only in this component, never in browser storage.
export function MediaPanel({ identity, load }: Props) {
  return <MaterialContents key={identity} load={load}/>;
}

function MaterialContents({ load }: Pick<Props, 'load'>) {
  const loader = useRef(load);
  loader.current = load;
  const alive = useRef(true);
  const pending = useRef(false);
  const [materials, setMaterials] = useState<CallMaterials | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const [recordingId, setRecordingId] = useState('');

  async function refresh(channel: 'all' | 'video' | 'transcription' = 'all') {
    if (pending.current) return;
    pending.current = true;
    setBusy(true); setError('');
    try {
      const next = await loader.current();
      if (alive.current) setMaterials(previous => ({
        ...next,
        recordings: next.recordings.map(recording => {
          const old = previous?.recordings.find(item => item.id === recording.id);
          return channel === 'transcription' && old && (!old.expires || old.expires > Date.now() / 1000 + 30)
            ? { ...recording, url: old.url, expires: old.expires } : recording;
        }),
        // Every response is authoritative for selected text, including [] on
        // pending/error. An earlier selected version never fills that gap.
        transcripts: next.transcription.status === 'ready' ? next.transcripts : [],
      }));
    } catch (cause) { if (alive.current) {
      setError(cause instanceof Error ? cause.message : 'Не удалось получить материалы. Попробуйте ещё раз.');
      setMaterials(previous => previous ? { ...previous, transcripts: [], transcription: { status: 'error' } } : null);
    } }
    finally { pending.current = false; if (alive.current) setBusy(false); }
  }

  useEffect(() => { alive.current = true; void refresh(); return () => { alive.current = false; }; }, []);

  useEffect(() => {
    if (materials?.transcription.status !== 'processing') return;
    const timer = setTimeout(() => { void refresh('transcription'); }, 8_000);
    return () => clearTimeout(timer);
  }, [materials]);
  const recordings = orderedRecordings(materials?.recordings || []);
  const recording = recordings.find(item => item.id === recordingId) || recordings[0];
  const transcript = selectedTranscript(materials, recording?.id);

  return <div style={{ display: 'grid', gap: 28, minWidth: 0 }}>
    {error && <div role="alert"><p className="cp-error rp-error">{error}</p><Button variant="secondary" disabled={busy} onClick={() => refresh()}>Повторить загрузку</Button></div>}
    {!materials && !error && <p role="status">Проверяем материалы встречи…</p>}
    {materials && <>
      <section aria-label="Запись встречи" style={{ display: 'grid', gap: 14, minWidth: 0 }}>
        <h2 style={{ margin: 0, fontSize: 20 }}>Запись встречи</h2>
        {recordings.length > 1 && <label>Часть интервью <select aria-label="Часть интервью" value={recording?.id || ''} onChange={event => setRecordingId(event.target.value)}>
          {recordings.map((item, index) => <option key={item.id} value={item.id}>Запись {index + 1}</option>)}
        </select></label>}
        {recording && <RecordingPlayer key={recording.id} recording={recording} busy={busy} onRefresh={() => refresh('video')}/>}
        <ChannelStatus channel={materials.video} kind="video" hasMaterial={materials.recordings.length > 0} busy={busy} onRefresh={() => refresh('video')}/>
      </section>
      <section aria-label="Расшифровка" style={{ display: 'grid', gap: 14, minWidth: 0 }}>
        <h2 style={{ margin: 0, fontSize: 20 }}>Расшифровка</h2>
        {transcript && <div key={transcript.id} style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', lineHeight: 1.65, padding: 20, borderRadius: 20, background: '#f7f7f8', maxHeight: 440, overflowY: 'auto' }}>{transcript.text}</div>}
        <ChannelStatus channel={materials.transcription} kind="transcription" hasMaterial={!!transcript} busy={busy} onRefresh={() => refresh('transcription')}/>
      </section>
    </>}
  </div>;
}

function ChannelStatus({ channel, kind, hasMaterial, busy, onRefresh }: {
  channel: MediaChannel; kind: 'video' | 'transcription'; hasMaterial: boolean; busy: boolean; onRefresh: () => void;
}) {
  const video = kind === 'video';
  const hasError = channel.status === 'error' || !!channel.error;
  if (channel.status === 'ready' && hasMaterial && !hasError) return null;
  const message = hasError
    ? channel.error || (video ? 'Не удалось получить запись.' : 'Не удалось получить расшифровку.')
    : channel.status === 'not_recorded'
      ? video ? 'Запись этой встречи не создавалась.' : 'Расшифровка этой встречи не создавалась.'
      : channel.status === 'processing'
        ? video ? 'Запись ещё обрабатывается. Можно проверить позже.' : 'Расшифровка ещё обрабатывается. Можно проверить позже.'
        : video ? 'Запись пока недоступна.' : 'Расшифровка пока недоступна.';
  return <div>
    <p role={hasError ? 'alert' : 'status'} style={{ margin: '0 0 10px', color: hasError && !hasMaterial ? '#a33838' : '#727277' }}>{message}</p>
    <Button variant="secondary" disabled={busy} onClick={onRefresh} aria-label={video ? 'Обновить запись' : 'Обновить расшифровку'}><RefreshCw size={15}/>{busy ? 'Проверяем…' : hasError ? 'Повторить' : 'Проверить ещё раз'}</Button>
  </div>;
}

function RecordingPlayer({ recording, busy, onRefresh }: {
  recording: CallMaterials['recordings'][number]; busy: boolean; onRefresh: () => void;
}) {
  const player = useRef<HTMLVideoElement>(null);
  const position = useRef(0);
  const [failed, setFailed] = useState(false);
  const expired = typeof recording.expires === 'number' && recording.expires <= Date.now() / 1000;
  useEffect(() => { setFailed(false); }, [recording.url]);
  return <div style={{ minWidth: 0 }}>
    <video ref={player} controls playsInline preload="metadata" src={recording.url} aria-label="Видеозапись встречи" style={{ display: 'block', width: '100%', maxHeight: 520, borderRadius: 20, background: '#222530' }}
      onTimeUpdate={event => { if (event.currentTarget.readyState >= 2) position.current = event.currentTarget.currentTime; }}
      onLoadedMetadata={event => { if (position.current > 0 && Number.isFinite(event.currentTarget.duration)) event.currentTarget.currentTime = Math.min(position.current, event.currentTarget.duration); }}
      onError={() => setFailed(true)}/>
    {(failed || expired) && <p role="alert" style={{ color: '#727277', marginBottom: 10 }}>Ссылка на видео недоступна или истекла. Обновите её, чтобы продолжить.</p>}
    <Button variant="ghost" size="sm" disabled={busy} onClick={onRefresh} style={{ marginTop: 8 }}><RefreshCw size={14}/>{busy ? 'Обновляем…' : 'Обновить ссылку на видео'}</Button>
  </div>;
}

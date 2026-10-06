import { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from '../../components/ui/button';
import { MeetingRecordingReview } from '../interview-review/InterviewReview';
import { request, type Journey, type Person } from './api';
import { parseMeetingTranscript } from './transcript';

type MaterialData = {
  recordings: { id: string; url: string }[];
  transcripts: { id: string; text: string }[];
};

export function Materials({ person, journey }: { person: Person; journey: Journey }) {
  // Keying isolates recordings, failed requests and transcript selection across meetings.
  return <MeetingMaterials key={`${journey.state!.generation}:${person.meeting?.id}`} person={person} journey={journey}/>;
}

function MeetingMaterials({ person, journey }: { person: Person; journey: Journey }) {
  const [data, setData] = useState<MaterialData | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const pending = useRef(false);
  const mounted = useRef(true);
  const meeting = person.meeting!;
  const available = journey.state!.videoConfigured && !meeting.testSkip;
  const load = useCallback(async () => {
    if (pending.current) return;
    pending.current = true; setLoading(true); setError('');
    try {
      const response = await request<MaterialData>('materials', journey.token, {
        candidate: person.id, meetingId: meeting.id, generation: journey.state!.generation,
      });
      if (mounted.current) setData(response);
    } catch (e) { if (mounted.current) setError((e as Error).message); }
    finally { pending.current = false; if (mounted.current) setLoading(false); }
  }, [journey.token, journey.state!.generation, person.id, meeting.id]);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (!available) return;
    void load();
  }, [available, load]);

  // Provider output is the only evidence source here. Screening examples are never used.
  const transcript = data?.transcripts.map(t => t.text).join('\n\n') || '';
  const chapters = parseMeetingTranscript(transcript);
  const emptyMessage = meeting.testSkip
    ? 'Звонок был пропущен в тесте. Записи и расшифровки у этой встречи нет.'
    : !available ? 'Видеосервис ещё не подключён. Здесь будет запись именно этой встречи.'
    : 'Запись обрабатывается. Проверьте готовность материалов через некоторое время.';
  const recordings = data?.recordings || [];
  // Multiple recording segments may have different clocks. Do not apply one transcript's
  // timestamps to several files until the backend provides a verified correspondence.
  const canSeekTranscript = recordings.length <= 1;
  return <section className="cj-materials">
    {available && <div className="cj-materials-toolbar"><span>{loading ? 'Получаем материалы встречи…' : data?.recordings.length ? 'Материалы этой встречи' : 'Подготовка материалов'}</span><Button variant="ghost" disabled={loading} onClick={load}>{loading ? 'Проверяем…' : 'Обновить материалы'}</Button></div>}
    {error && <p className="cj-materials-error" role="alert">{error}</p>}
    {(recordings.length ? recordings : [null]).map((recording, index) => <MeetingRecordingReview
      key={recording?.id || 'pending'} sourceId={`${meeting.id}:${recording?.id || 'pending'}`}
      name={person.name} chapters={canSeekTranscript ? chapters : []} emptyMessage={emptyMessage}
      media={recording ? { src: recording.url, label: recordings.length > 1 ? `Запись встречи · часть ${index + 1}` : 'Запись этой встречи' } : undefined}
      transcriptText={index === 0 ? transcript : undefined}/>) }
    {!canSeekTranscript && <p className="cj-info">Встреча записана несколькими файлами. Расшифровка доступна целиком; привязка таймкодов к частям ещё не определена.</p>}
  </section>;
}

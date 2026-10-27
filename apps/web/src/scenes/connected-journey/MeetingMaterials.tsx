import { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from '../../components/ui/button';
import { MeetingRecordingReview } from '../interview-review/InterviewReview';
import { EmployerTimecodeComment } from '../employer-workspace/EmployerCandidateProfile';
import { request, type Journey, type Person } from './api';
import { parseMeetingTranscript } from './transcript';
import { interviewAssistant } from '../interview-review/interview-assistant-api';

type MaterialData = {
  recordings: { id: string; url: string; expires?: number }[];
  transcripts: { id: string; text: string }[];
  video?: { status: 'ready' | 'processing' | 'absent' | 'not_recorded' | 'error' | 'unavailable'; error?: string };
  transcription?: { status: 'ready' | 'processing' | 'absent' | 'not_recorded' | 'error' | 'unavailable'; error?: string };
};

type MaterialsProps = { person: Person; journey: Journey; appearance?: 'panel' | 'candidate'; enableComments?: boolean };

export function Materials(props: MaterialsProps) {
  const { person, journey } = props;
  // Keying isolates recordings, failed requests and transcript selection across meetings.
  return <MeetingMaterials key={`${journey.state!.generation}:${person.vacancyId || journey.state!.vacancy.id}:${person.id}:${person.meeting?.id}`} {...props}/>;
}

function MeetingMaterials({ person, journey, appearance, enableComments = false }: MaterialsProps) {
  const [data, setData] = useState<MaterialData | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const pending = useRef(false);
  const mounted = useRef(true);
  const meeting = person.meeting!;
  // Archived recordings have their own provider/source. The provider used for new
  // calls being disabled must not hide a completed meeting's existing materials.
  const available = !meeting.testSkip;
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
    : data?.video?.status === 'absent' || data?.video?.status === 'not_recorded' ? 'Видеосервис не вернул запись этой встречи.'
    : data?.video?.status === 'error' || data?.video?.status === 'unavailable' ? 'Сейчас не удалось получить запись. Можно проверить ещё раз.'
    : 'Запись обрабатывается. Проверьте готовность материалов через некоторое время.';
  const recordings = data?.recordings || [];
  // Multiple recording segments may have different clocks. Do not apply one transcript's
  // timestamps to several files until the backend provides a verified correspondence.
  const canSeekTranscript = recordings.length <= 1;
  const showComments = enableComments && journey.state!.actor === 'manager';
  const canComment = showComments && meeting.status === 'completed' && !meeting.testSkip
    && !!data && !journey.state!.closedBy && person.decision !== 'declined';
  const meetingComments = showComments ? (person.meetingComments || []).filter(item => item.meetingId === meeting.id) : [];
  const commentsFor = (recordingId: string | null) => meetingComments.filter(item => item.recordingId === recordingId);
  function commentTimestamps(recordingId: string | null) {
    const counts = new Map<number, number>();
    for (const item of commentsFor(recordingId)) counts.set(item.seconds, (counts.get(item.seconds) || 0) + 1);
    return [...counts].sort(([a], [b]) => a - b).map(([seconds, count]) => ({ seconds, count }));
  }
  function comment(seconds: number, recordingId: string | null, chapterTitle = '', onClose?: () => void, openRequest?: number) {
    if (!showComments) return null;
    return <EmployerTimecodeComment key={`${meeting.id}:${recordingId}:${seconds}`} seconds={seconds}
      appearance={appearance === 'candidate' ? 'player' : undefined} openRequest={openRequest}
      comments={commentsFor(recordingId).filter(item => Math.abs(item.seconds - seconds) < 0.001)}
      workflowRevision={person.workflowRevision || 0} busy={journey.busy} initiallyOpen={!!onClose} onClose={onClose}
      onSave={canComment ? (text, expectedRevision, requestId) => journey.act('meeting.comment.add', person.id, {
        meetingId: meeting.id, recordingId, seconds, chapterTitle, text, expectedRevision, requestId,
      }) : undefined}/>;
  }
  const materialsReady = recordings.length > 0 && !!transcript.trim()
    && (!data?.video || data.video.status === 'ready')
    && (!data?.transcription || data.transcription.status === 'ready');
  const showToolbar = available && (appearance !== 'candidate' || !materialsReady || loading || !!error);
  return <section className="cj-materials">
    {showToolbar && <div className="cj-materials-toolbar"><span>{loading ? 'Получаем материалы встречи…' : data?.video?.status === 'processing' ? 'Подготовка материалов' : 'Материалы этой встречи'}</span><Button variant="ghost" disabled={loading} onClick={load}>{loading ? 'Проверяем…' : 'Обновить материалы'}</Button></div>}
    {error && <p className="cj-materials-error" role="alert">{error}</p>}
    {(data?.transcription?.status === 'error' || data?.transcription?.status === 'unavailable') && <p className="cj-info" role="status">Расшифровка сейчас недоступна.{recordings.length > 0 ? ' Готовую запись можно посмотреть.' : ' Попробуйте обновить материалы позже.'}</p>}
    {(recordings.length ? recordings : [null]).map((recording, index) => <MeetingRecordingReview
      key={recording?.id || 'pending'} sourceId={`${meeting.id}:${recording?.id || 'pending'}`} appearance={appearance}
      name={person.name} chapters={canSeekTranscript ? chapters : []} emptyMessage={emptyMessage}
      assistant={recording && recordings.length === 1 && transcript ? interviewAssistant(journey.token, {
        source: 'meeting', candidate: person.id, applicationId: person.applicationId || person.id,
        vacancyId: person.vacancyId || journey.state!.vacancy.id, generation: journey.state!.generation,
        meetingId: meeting.id, recordingId: recording.id,
      }) : undefined}
      media={recording ? { src: recording.url, label: recordings.length > 1 ? `Запись встречи · часть ${index + 1}` : 'Запись этой встречи' } : undefined}
      renderChapterComment={showComments ? chapter => chapter.seconds === null ? null : comment(chapter.seconds, recording?.id || null, chapter.title) : undefined}
      renderTimeComment={showComments ? (seconds, onClose, openRequest) => comment(seconds, recording?.id || null, '', onClose, openRequest) : undefined}
      commentTimestamps={showComments ? commentTimestamps(recording?.id || null) : undefined}
      canComment={canComment} comments={commentsFor(recording?.id || null)}
      transcriptText={index === 0 ? transcript : undefined} transcriptSeekable={canSeekTranscript}/>) }
    {!canSeekTranscript && <p className="cj-info">Встреча записана несколькими файлами. Расшифровка доступна целиком; привязка таймкодов к частям ещё не определена.</p>}
    {recordings.length > 0 && commentsFor(null).length > 0 && <section>
      <h3 className="ir-eyebrow">Комментарии до появления записи</h3>
      {commentsFor(null).map(item => <p key={item.id}>{item.text}</p>)}
    </section>}
  </section>;
}

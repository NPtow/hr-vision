import { useEffect, useState } from 'react';
import type { CallMaterials } from '../candidate-portal/api';
import { SourceScreeningReview } from '../interview-review/InterviewReview';
import { EmployerTimecodeComment } from '../employer-workspace/EmployerCandidateProfile';
import { parseMeetingTranscript } from './transcript';
import { request, type Journey, type Person, type SourceProfile } from './api';

/** Bind the existing interview view to this application's authorised source media. */
type ScreeningProps = { journey: Journey; person: Person; source: SourceProfile; appearance?: 'panel' | 'candidate'; enableComments?: boolean };
export function EmployerScreening(props: ScreeningProps) {
  return <Screening key={`${props.person.vacancyId}:${props.person.id}:${props.source.interviewId || props.source.id}`} {...props}/>;
}

function Screening({ journey, person, source, appearance = 'panel', enableComments = false }: ScreeningProps) {
  const [materials, setMaterials] = useState<CallMaterials | null>(null);
  const [error, setError] = useState('');
  const vacancyId = person.vacancyId || journey.state!.vacancy.id;
  const generation = journey.state!.generation;
  const available = journey.state!.videoConfigured;
  useEffect(() => {
    if (!available) return;
    let mounted = true;
    void request<CallMaterials>('employer/screening/materials', journey.token, {
      candidate: person.id, applicationId: person.applicationId || person.id, vacancyId, generation,
    }).then(result => { if (mounted) setMaterials(result); }).catch(cause => { if (mounted) setError((cause as Error).message); });
    return () => { mounted = false; };
  }, [available, journey.token, person.id, person.applicationId, vacancyId, generation, source.interviewId]);

  const transcript = materials?.transcripts.map(item => item.text).join('\n\n') || '';
  const recordings = materials?.recordings || (source.video ? [{ id: source.id, url: source.video }] : []);
  const transcriptChapters = recordings.length <= 1 ? parseMeetingTranscript(transcript) : [];
  const chapters = source.shortChapters.length ? source.shortChapters : transcriptChapters;
  const fullChapters = source.fullChapters.length ? source.fullChapters : transcriptChapters;
  const emptyMessage = error || (!available ? 'Видеосервис ещё не подключён.'
    : materials?.video.status === 'not_recorded' ? 'Запись первичного интервью отсутствует.'
    : materials?.video.status === 'error' ? 'Сейчас не удалось получить запись первичного интервью.'
    : 'Запись первичного интервью обрабатывается.');
  const showComments = enableComments && !!source.interviewId;
  const canComment = showComments && !journey.state?.closedBy && person.decision !== 'declined';
  function commentTimestamps(recordingId: string | null) {
    const counts = new Map<number, number>();
    for (const item of person.screeningComments || []) {
      if (item.interviewId === source.interviewId && (item.recordingId === recordingId || (recordings.length <= 1 && item.recordingId === null))) {
        counts.set(item.seconds, (counts.get(item.seconds) || 0) + 1);
      }
    }
    return [...counts].sort(([a], [b]) => a - b).map(([seconds, count]) => ({ seconds, count }));
  }
  function comment(seconds: number, recordingId: string | null, chapterTitle = '', onClose?: () => void) {
    if (!showComments) return null;
    return <EmployerTimecodeComment key={`${source.interviewId}:${recordingId}:${seconds}`} seconds={seconds}
      comments={(person.screeningComments || []).filter(item => item.interviewId === source.interviewId
        && (item.recordingId === recordingId || (recordings.length <= 1 && item.recordingId === null))
        && Math.abs(item.seconds - seconds) < 0.001)}
      workflowRevision={person.workflowRevision || 0} busy={journey.busy} initiallyOpen={!!onClose} onClose={onClose}
      onSave={canComment ? (text, expectedRevision, requestId) => journey.act('screening.comment.add', person.id, {
        interviewId: source.interviewId, recordingId, seconds, chapterTitle, text, expectedRevision, requestId,
      }) : undefined}/>;
  }
  return <>{(recordings.length ? recordings : [null]).map((recording, index) => <SourceScreeningReview
    key={recording?.id || 'pending'} appearance={appearance} sourceId={`${source.id}:${recording?.id || 'pending'}`}
    name={person.name} chapters={recordings.length <= 1 ? chapters : []} fullChapters={recordings.length <= 1 ? fullChapters : []}
    shortPlaylist={source.shortChapters.length > 0} media={recording ? { src: recording.url } : undefined}
    transcriptText={index === 0 ? transcript : undefined} summary={index === 0 ? source.shortSummary : undefined}
    renderChapterComment={showComments ? chapter => chapter.seconds === null ? null : comment(chapter.seconds, recording?.id || null, chapter.title) : undefined}
    renderTimeComment={showComments ? (seconds, onClose) => comment(seconds, recording?.id || null, '', onClose) : undefined}
    commentTimestamps={enableComments ? commentTimestamps(recording?.id || null) : undefined}
    emptyMessage={emptyMessage} recruiterContent={index === 0 && (source.conclusion.length || source.questions.length) ? <>
      <span className="ir-eyebrow">Мнение рекрутера{source.score !== null ? ` · ${source.score} / 5` : ''}</span>
      {source.conclusion.map((text, item) => <p key={item}>{text}</p>)}
      {source.questions.map((question, item) => <p key={item}>{question}</p>)}
    </> : undefined}/>) }
    {recordings.length > 1 && (person.screeningComments || []).some(item => item.interviewId === source.interviewId && item.recordingId === null) && <section className="ew-detail-card">
      <h3>Комментарии к интервью</h3>
      {(person.screeningComments || []).filter(item => item.interviewId === source.interviewId && item.recordingId === null).map(item => <p key={item.id}>
        <strong>{Math.floor(item.seconds / 60).toString().padStart(2, '0')}:{Math.floor(item.seconds % 60).toString().padStart(2, '0')}</strong> · {item.text}
      </p>)}
    </section>}
  </>;
}

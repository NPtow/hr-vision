import { useEffect, useState, type ReactNode } from 'react';
import type { CallMaterials } from '../candidate-portal/api';
import type { RecordingReviewProps } from '../interview-review/InterviewReview';
import { evidenceRecording, orderedRecordings, selectedTranscript, transcriptStatusMessage } from '../interview-review/recording-transcript';
import { SourceScreeningReview } from '../interview-review/InterviewReview';
import { EmployerTimecodeComment } from '../employer-workspace/EmployerCandidateProfile';
import { parseMeetingTranscript } from './transcript';
import { request, type Journey, type Person, type SourceProfile } from './api';
import { interviewAssistant } from '../interview-review/interview-assistant-api';
import { useInterviewAssessment } from '../employer-workspace/useInterviewAssessment';

/** Bind the existing interview view to this application's authorised source media. */
type ScreeningProps = { journey: Journey; person: Person; source: SourceProfile; appearance?: 'panel' | 'candidate'; header?: ReactNode; enableComments?: boolean };
export function EmployerScreening(props: ScreeningProps) {
  return <Screening key={`${props.person.vacancyId}:${props.person.id}:${props.source.interviewId || props.source.id}`} {...props}/>;
}

function Screening({ journey, person, source, appearance = 'panel', header, enableComments = false }: ScreeningProps) {
  const [materials, setMaterials] = useState<CallMaterials | null>(null);
  const [error, setError] = useState('');
  const [selectedId, setSelectedId] = useState('');
  const [evidenceRequest, setEvidenceRequest] = useState<RecordingReviewProps['evidenceRequest']>();
  const vacancyId = person.vacancyId || journey.state!.vacancy.id;
  const generation = journey.state!.generation;
  const available = journey.state!.videoConfigured;
  useEffect(() => {
    let mounted = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = () => void request<CallMaterials>('employer/screening/materials', journey.token, {
      candidate: person.id, applicationId: person.applicationId || person.id, vacancyId, generation,
    }).then(result => {
      if (!mounted) return;
      setMaterials(result); setError('');
      timer = setTimeout(load, result.transcription.status === 'processing' ? 8_000 : 60_000);
    }).catch(cause => { if (mounted) { setMaterials(null); setError((cause as Error).message); timer = setTimeout(load, 15_000); } });
    load();
    return () => { mounted = false; clearTimeout(timer); };
  }, [journey.token, person.id, person.applicationId, vacancyId, generation, source.interviewId]);

  const recordings = orderedRecordings(materials?.recordings || []);
  const recording = recordings.find(item => item.id === selectedId) || recordings[0];
  const transcript = selectedTranscript(materials, recording?.id)?.text || '';
  const assessment = useInterviewAssessment(journey.token, vacancyId && recordings[0] && materials?.transcription.status === 'ready' ? {
    source: 'screening', candidate: person.id, applicationId: person.applicationId || person.id,
    vacancyId, generation, recordingId: recordings[0].id,
  } : null, JSON.stringify([materials?.transcription.status, materials?.transcripts.map(item =>
    [item.id, item.recordingId, item.selectionRevision, item.text])]));
  const transcriptChapters = parseMeetingTranscript(transcript);
  const chapters = recordings.length === 1 && source.shortChapters.length ? source.shortChapters : transcriptChapters;
  const fullChapters = recordings.length === 1 && source.fullChapters.length ? source.fullChapters : transcriptChapters;
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
  function comment(seconds: number, recordingId: string | null, chapterTitle = '', onClose?: () => void, openRequest?: number) {
    if (!showComments) return null;
    return <EmployerTimecodeComment key={`${source.interviewId}:${recordingId}:${seconds}`} seconds={seconds}
      appearance={appearance === 'candidate' ? 'player' : undefined} openRequest={openRequest}
      comments={(person.screeningComments || []).filter(item => item.interviewId === source.interviewId
        && (item.recordingId === recordingId || (recordings.length <= 1 && item.recordingId === null))
        && Math.abs(item.seconds - seconds) < 0.001)}
      workflowRevision={person.workflowRevision || 0} busy={journey.busy} initiallyOpen={!!onClose} onClose={onClose}
      onSave={canComment ? (text, expectedRevision, requestId) => journey.act('screening.comment.add', person.id, {
        interviewId: source.interviewId, recordingId, seconds, chapterTitle, text, expectedRevision, requestId,
      }) : undefined}/>;
  }
  return <>
    {recordings.length > 1 && <label>Часть интервью <select aria-label="Часть интервью" value={recording?.id || ''} onChange={event => { setSelectedId(event.target.value); setEvidenceRequest(undefined); }}>
      {recordings.map((item, part) => <option key={item.id} value={item.id}>Запись {part + 1}</option>)}
    </select></label>}
    {!transcript && materials && <p role="status">{transcriptStatusMessage(materials.transcription.status)}</p>}
    <SourceScreeningReview
    appearance={appearance} sourceId={`${source.id}:${recording?.id || 'pending'}`}
    recordingId={recording?.id} evidenceRequest={evidenceRequest}
    onEvidenceNavigate={(evidence, criterionId) => {
      const target = evidenceRecording(materials, evidence);
      if (!target || evidence.seconds === null) return false;
      setSelectedId(target); setEvidenceRequest({ recordingId: target, seconds: evidence.seconds,
        requestId: crypto.randomUUID(), evidence, criterionId }); return true;
    }}
    name={person.name} header={header} chapters={chapters} fullChapters={fullChapters}
    shortPlaylist={recordings.length === 1 && source.shortChapters.length > 0} media={recording ? { src: recording.url } : undefined}
    assessment={assessment.assessment}
    hideFeedbackQuestions transcriptText={transcript} transcriptSeekable={!!transcript} summary={source.shortSummary}
    assistant={recording && transcript ? interviewAssistant(journey.token, {
      source: 'screening', candidate: person.id, applicationId: person.applicationId || person.id,
      vacancyId, generation, recordingId: recording.id,
    }) : undefined}
    renderChapterComment={showComments ? chapter => chapter.seconds === null ? null : comment(chapter.seconds, recording?.id || null, chapter.title) : undefined}
    renderTimeComment={showComments ? (seconds, onClose, openRequest) => comment(seconds, recording?.id || null, '', onClose, openRequest) : undefined}
    commentTimestamps={enableComments ? commentTimestamps(recording?.id || null) : undefined}
    canComment={canComment && !!recording} comments={showComments ? (person.screeningComments || []).filter(item => item.interviewId === source.interviewId
      && (item.recordingId === (recording?.id || null) || (recordings.length <= 1 && item.recordingId === null))) : []}
    emptyMessage={emptyMessage} recruiterContent={(source.conclusion.length || source.questions.length) ? <>
      <span className="ir-eyebrow">Мнение рекрутера{source.score !== null ? ` · ${source.score} / 5` : ''}</span>
      {source.conclusion.map((text, item) => <p key={item}>{text}</p>)}
      {source.questions.map((question, item) => <p key={item}>{question}</p>)}
    </> : undefined}/>
    {recordings.length > 1 && (person.screeningComments || []).some(item => item.interviewId === source.interviewId && item.recordingId === null) && <section className="ew-detail-card">
      <h3>Комментарии к интервью</h3>
      {(person.screeningComments || []).filter(item => item.interviewId === source.interviewId && item.recordingId === null).map(item => <p key={item.id}>
        <strong>{Math.floor(item.seconds / 60).toString().padStart(2, '0')}:{Math.floor(item.seconds % 60).toString().padStart(2, '0')}</strong> · {item.text}
      </p>)}
    </section>}
  </>;
}

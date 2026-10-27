import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { Button } from '../../components/ui/button';
import { request } from '../connected-journey/api';
import { parseMeetingTranscript } from '../connected-journey/transcript';
import type { CallMaterials } from '../candidate-portal/api';
import type { RecordingReviewProps } from '../interview-review/InterviewReview';
import { evidenceRecording, orderedRecordings, selectedTranscript, transcriptStatusMessage } from '../interview-review/recording-transcript';
import { SourceScreeningReview } from '../interview-review/InterviewReview';
import { interviewAssistant } from '../interview-review/interview-assistant-api';
import { downloadResume, loadResumeFile, type IntakePerson, type PreparedProfile } from './intake';
import { EmployerCandidateProfile, EmployerTimecodeComment } from './EmployerCandidateProfile';
import { candidateDecision, respondToCandidate } from './candidate-response';
import type { EmployerCandidateResponseValues } from './employer-candidate-profile-types';
import { useInterviewAssessment } from './useInterviewAssessment';

function webUrl(value: string) {
  if (!value.trim()) return '';
  try {
    const url = new URL(value, window.location.origin);
    return ['https:', 'http:'].includes(url.protocol) ? url.href : '';
  } catch { return ''; }
}

type CommentValues = { recordingIndex: number; seconds: number; chapterTitle: string; text: string; expectedRevision: number; requestId: string };
function PreparedInterview({ person, profile, index, token, vacancyId, busy, onComment, header }: {
  person: IntakePerson; profile: PreparedProfile; index: number; token: string; vacancyId?: string; busy: boolean;
  onComment: (values: CommentValues) => Promise<boolean>; header?: ReactNode;
}) {
  const [materials, setMaterials] = useState<CallMaterials | null>(null);
  const [selectedId, setSelectedId] = useState('');
  const [evidenceRequest, setEvidenceRequest] = useState<RecordingReviewProps['evidenceRequest']>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const context = JSON.stringify([person.id, token, vacancyId, profile.recordings]);
  const activeContext = useRef(context);
  activeContext.current = context;
  const sequence = useRef(0);
  const refresh = useCallback(async () => {
    if (!vacancyId) { setLoading(false); return; }
    const serial = ++sequence.current;
    setLoading(true); setError('');
    try {
      const result = await request<CallMaterials>('employer/intake/materials', token, { id: person.id, vacancyId });
      if (activeContext.current === context && serial === sequence.current) setMaterials(result);
    } catch (cause) {
      if (activeContext.current === context && serial === sequence.current) {
        setMaterials(null); setError(cause instanceof Error ? cause.message : 'Не удалось загрузить материалы.');
      }
    } finally { if (activeContext.current === context && serial === sequence.current) setLoading(false); }
  }, [context, person.id, token, vacancyId]);
  useEffect(() => {
    setMaterials(null); void refresh();
    return () => { sequence.current++; };
  }, [refresh]);
  useEffect(() => {
    if (!materials) return;
    const timer = setTimeout(() => { void refresh(); }, materials.transcription.status === 'processing' ? 8_000 : 60_000);
    return () => clearTimeout(timer);
  }, [materials, refresh]);
  const recordings = orderedRecordings(materials?.recordings || []);
  const source = recordings.find(item => item.id === selectedId) || recordings[index] || recordings[0];
  const selectedIndex = source ? recordings.findIndex(item => item.id === source.id) : 0;
  const transcript = selectedTranscript(materials, source?.id)?.text || '';
  const materialIdentity = JSON.stringify([materials?.transcription.status,
    materials?.transcripts.map(item => [item.recordingId, item.id, item.selectionRevision, item.text])]);
  const assessment = useInterviewAssessment(token, vacancyId ?
    { source: 'intake', id: person.id, index: 0, vacancyId } : null, materialIdentity);
  const chapters = recordings.length === 1 && materials?.chapters?.length ? materials.chapters : parseMeetingTranscript(transcript);
  const canComment = !!vacancyId && !!source && !person.archived;
  const commentCounts = new Map<number, number>();
  for (const item of person.screeningComments || []) {
    if (item.recordingId === source?.id || !item.recordingId && item.recordingIndex === selectedIndex) commentCounts.set(item.seconds, (commentCounts.get(item.seconds) || 0) + 1);
  }
  function comment(seconds: number, chapterTitle = '', onClose?: () => void, openRequest?: number) {
    return <EmployerTimecodeComment key={`${person.id}:${source?.id}:${seconds}`} seconds={seconds}
      appearance="player" openRequest={openRequest}
      comments={(person.screeningComments || []).filter(item => (item.recordingId === source?.id || !item.recordingId && item.recordingIndex === selectedIndex) && Math.abs(item.seconds - seconds) < 0.001)}
      workflowRevision={person.reviewRevision || 0} busy={busy} initiallyOpen={!!onClose} onClose={onClose}
      onSave={canComment ? (text, expectedRevision, requestId) => onComment({ recordingIndex: selectedIndex, seconds, chapterTitle, text, expectedRevision, requestId }) : undefined}/>;
  }
  return <>
    {assessment.error && <div className="ew-inline-error" role="alert"><p>{assessment.error}</p><Button type="button" variant="ghost" onClick={assessment.retry}>Повторить загрузку оценки</Button></div>}
    {error && <div className="ew-inline-error" role="alert"><p>{error}</p><Button type="button" variant="ghost" onClick={() => { void refresh(); }}>Повторить загрузку</Button></div>}
    {recordings.length > 1 && <label>Часть интервью <select aria-label="Часть интервью" value={source?.id || ''} onChange={event => { setSelectedId(event.target.value); setEvidenceRequest(undefined); }}>
      {recordings.map((recording, part) => <option key={recording.id} value={recording.id}>Запись {part + 1}</option>)}
    </select></label>}
    {!transcript && materials && <p role="status">{transcriptStatusMessage(materials.transcription.status)}</p>}
    <SourceScreeningReview appearance="candidate" sourceId={`${person.id}:prepared:${source?.id || 'none'}`}
      recordingId={source?.id} evidenceRequest={evidenceRequest}
      onEvidenceNavigate={(evidence, criterionId) => {
        const target = evidenceRecording(materials, evidence);
        if (!target || evidence.seconds === null) return false;
        setSelectedId(target); setEvidenceRequest({ recordingId: target, seconds: evidence.seconds,
          requestId: crypto.randomUUID(), evidence, criterionId }); return true;
      }}
      name={person.name} header={header} chapters={chapters} fullChapters={chapters} transcriptText={transcript}
      assessment={assessment.assessment}
      assessmentStatus={assessment.error || assessment.status === 'failed' ? 'unavailable' : 'loading'}
      assistant={vacancyId && transcript ? interviewAssistant(token, { source: 'intake', id: person.id, index: selectedIndex, vacancyId }) : undefined}
      media={source && webUrl(source.url) ? { src: webUrl(source.url), label: source.name || 'Интервью' } : undefined}
      renderChapterComment={chapter => chapter.seconds === null ? null : comment(chapter.seconds, chapter.title)}
      renderTimeComment={(seconds, onClose, openRequest) => comment(seconds, '', onClose, openRequest)}
      commentTimestamps={[...commentCounts].sort(([a], [b]) => a - b).map(([seconds, count]) => ({ seconds, count }))}
      canComment={canComment} comments={(person.screeningComments || []).filter(item => item.recordingId === source?.id || !item.recordingId && item.recordingIndex === selectedIndex)}
      emptyMessage={loading ? 'Загружаем запись…' : error || 'Запись интервью не добавлена.'}/>
  </>;
}

export function PreparedIntakeProfile({ person, token, vacancyId, onUpdated, onResponseStart, onResponded }: {
  person: IntakePerson; token: string; vacancyId?: string; onUpdated?: () => void;
  onResponseStart?: () => void; onResponded?: () => void;
}) {
  const [current, setCurrent] = useState(person);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const saving = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => setCurrent(person), [person]);
  const profile: PreparedProfile = current.preparedProfile || { cvText: '', evaluationText: '', recordings: [], sourceLabel: '' };
  const resumeUrl = webUrl(current.link);
  const firstFile = current.files[0];
  async function mutate(path: string, values: Record<string, unknown>) {
    if (!vacancyId || saving.current) return false;
    saving.current = true; setBusy(true); setError('');
    try {
      const result = await request<{ candidates: IntakePerson[] }>(`employer/intake/${path}`, token, { id: current.id, vacancyId, ...values });
      const saved = result.candidates.find(candidate => candidate.id === current.id);
      if (mounted.current) {
        if (saved) setCurrent(saved);
      }
      onUpdated?.();
      return true;
    } catch (cause) {
      if (mounted.current) setError(cause instanceof Error ? cause.message : 'Не удалось сохранить.'); return false;
    } finally { saving.current = false; if (mounted.current) setBusy(false); }
  }
  async function respond(values: EmployerCandidateResponseValues) {
    if (!vacancyId || saving.current || !mounted.current) return false;
    saving.current = true; setBusy(true); setError('');
    onResponseStart?.();
    try {
      const result = await respondToCandidate(token, { source: 'intake', id: current.id, vacancyId }, values);
      if (mounted.current) {
        const saved = result.candidates?.find(candidate => candidate.id === current.id);
        if (saved) setCurrent(saved);
        onResponded?.();
      }
      // Refresh the exact list even when this card was closed during the request.
      // useIntake ignores stale refresh callbacks after a workspace/vacancy switch.
      onUpdated?.();
      return true;
    } finally { saving.current = false; if (mounted.current) setBusy(false); }
  }
  const download = (file: typeof firstFile) => {
    setError(''); void downloadResume(token, file).catch(cause => setError(cause instanceof Error ? cause.message : 'Не удалось загрузить резюме.'));
  };
  return <EmployerCandidateProfile person={{ ...current, photo: profile.photo }}
    source={{ interviewRole: current.role, role: profile.summary, contact: current.contact, cv: profile.cvText, score: profile.score,
      profile: profile.profile || (current.note ? [{ label: 'Заметка', value: current.note }] : []) }}
    resume={firstFile ? { id: firstFile.id, name: firstFile.name, sizeLabel: `${Math.max(1, Math.ceil(firstFile.size / 1024))} КБ`, load: signal => loadResumeFile(token, firstFile, signal), onDownload: () => download(firstFile) }
      : resumeUrl ? { id: resumeUrl, name: 'Резюме', load: async signal => {
        const response = await fetch(resumeUrl, { credentials: 'omit', signal });
        if (!response.ok) throw new Error('Не удалось загрузить резюме.');
        return response.blob();
      }, onDownload: () => { window.open(resumeUrl, '_blank', 'noopener,noreferrer'); } } : undefined}
    interview={header => <>
      {current.files.slice(1).map(file => <button key={file.id} type="button" className="ew-file-row" onClick={() => download(file)}>{file.name}</button>)}
      <PreparedInterview key={`${current.id}:${vacancyId}:${token}`} person={current} profile={profile} index={0} token={token} vacancyId={vacancyId} busy={busy} header={header} onComment={values => mutate('comment', values)}/>
    </>}
    onRespond={vacancyId && !current.archived ? respond : undefined}
    meetingOptions={{ mode: 'proposal' }}
    decision={candidateDecision(current.reviewResponse, current.reviewRejection)}
    responseIdentity={`${token}:intake:${vacancyId}:${current.id}`}
    workflowRevision={current.reviewRevision || 0} busy={busy} error={error}/>;
}

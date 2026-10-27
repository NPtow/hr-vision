import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Button } from '../../components/ui/button';
import { request } from '../connected-journey/api';
import { parseMeetingTranscript } from '../connected-journey/transcript';
import { SourceScreeningReview } from '../interview-review/InterviewReview';
import { downloadResume, loadResumeFile, type IntakePerson, type PreparedProfile } from './intake';
import { EmployerCandidateProfile, EmployerTimecodeComment } from './EmployerCandidateProfile';
import { candidateDecision, respondToCandidate } from './candidate-response';
import type { EmployerCandidateResponseValues } from './employer-candidate-profile-types';

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
  const source = profile.recordings[index];
  const [media, setMedia] = useState<{ url: string; mime: string } | null>(null);
  const [loading, setLoading] = useState(Boolean(source));
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let current = true;
    setMedia(null); setError(''); setLoading(Boolean(source));
    if (!source) return;
    void request<{ url: string; mime: string }>('employer/intake/recording', token, { id: person.id, index, vacancyId })
      .then(result => {
        if (!current) return;
        const url = webUrl(result.url);
        if (!url) throw new Error('Не удалось получить ссылку на запись.');
        setMedia({ ...result, url });
      })
      .catch(cause => { if (current) setError(cause instanceof Error ? cause.message : 'Не удалось загрузить запись.'); })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [person.id, index, token, vacancyId, source?.publicUrl, attempt]);
  const transcript = profile.recordings.length <= 1 ? profile.transcriptText || '' : '';
  const chapters = profile.recordings.length <= 1 ? profile.chapters || parseMeetingTranscript(transcript) : [];
  const canComment = !!vacancyId && !!source && !person.archived;
  const commentCounts = new Map<number, number>();
  for (const item of person.screeningComments || []) {
    if (item.recordingIndex === index) commentCounts.set(item.seconds, (commentCounts.get(item.seconds) || 0) + 1);
  }
  function comment(seconds: number, chapterTitle = '', onClose?: () => void) {
    return <EmployerTimecodeComment key={`${person.id}:${index}:${seconds}`} seconds={seconds}
      appearance="player"
      comments={(person.screeningComments || []).filter(item => item.recordingIndex === index && Math.abs(item.seconds - seconds) < 0.001)}
      workflowRevision={person.reviewRevision || 0} busy={busy} initiallyOpen={!!onClose} onClose={onClose}
      onSave={canComment ? (text, expectedRevision, requestId) => onComment({ recordingIndex: index, seconds, chapterTitle, text, expectedRevision, requestId }) : undefined}/>;
  }
  return <>
    {error && <div className="ew-inline-error" role="alert"><p>{error}</p><Button type="button" variant="ghost" onClick={() => setAttempt(value => value + 1)}>Повторить загрузку</Button></div>}
    <SourceScreeningReview appearance="candidate" sourceId={`${person.id}:prepared:${index}:${source?.publicUrl || 'none'}`}
      name={person.name} header={header} chapters={chapters} fullChapters={chapters} transcriptText={transcript}
      media={media ? { src: media.url, label: source?.name } : undefined}
      renderChapterComment={chapter => chapter.seconds === null ? null : comment(chapter.seconds, chapter.title)}
      renderTimeComment={(seconds, onClose) => comment(seconds, '', onClose)}
      commentTimestamps={[...commentCounts].sort(([a], [b]) => a - b).map(([seconds, count]) => ({ seconds, count }))}
      canComment={canComment} comments={(person.screeningComments || []).filter(item => item.recordingIndex === index)}
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
  const profile = current.preparedProfile;
  if (!profile) return null;
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
      <PreparedInterview key={`${current.id}:${token}`} person={current} profile={profile} index={0} token={token} vacancyId={vacancyId} busy={busy} header={header} onComment={values => mutate('comment', values)}/>
    </>}
    onRespond={vacancyId && !current.archived ? respond : undefined}
    meetingOptions={{ mode: 'proposal' }}
    decision={candidateDecision(current.reviewResponse, current.reviewRejection)}
    responseIdentity={`${token}:intake:${vacancyId}:${current.id}`}
    workflowRevision={current.reviewRevision || 0} busy={busy} error={error}/>;
}

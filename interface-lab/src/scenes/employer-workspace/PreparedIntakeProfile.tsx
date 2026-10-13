import { useEffect, useRef, useState } from 'react';
import { Link2, Loader2 } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { request } from '../connected-journey/api';
import { parseMeetingTranscript } from '../connected-journey/transcript';
import { SourceScreeningReview } from '../interview-review/InterviewReview';
import { downloadResume, type IntakePerson, type PreparedProfile } from './intake';
import { EmployerCandidateProfile, EmployerTimecodeComment } from './EmployerCandidateProfile';

function webUrl(value: string) {
  if (!value.trim()) return '';
  try {
    const url = new URL(value, window.location.origin);
    return ['https:', 'http:'].includes(url.protocol) ? url.href : '';
  } catch { return ''; }
}

type CommentValues = { recordingIndex: number; seconds: number; chapterTitle: string; text: string; expectedRevision: number; requestId: string };
function PreparedInterview({ person, profile, index, token, vacancyId, busy, onComment }: {
  person: IntakePerson; profile: PreparedProfile; index: number; token: string; vacancyId?: string; busy: boolean;
  onComment: (values: CommentValues) => Promise<boolean>;
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
  const originalUrl = webUrl(source?.publicUrl || '');
  const evaluationUrl = webUrl(profile.evaluationUrl || '');
  const transcript = profile.recordings.length <= 1 ? profile.transcriptText || '' : '';
  const chapters = profile.recordings.length <= 1 ? profile.chapters || parseMeetingTranscript(transcript) : [];
  const canComment = !!vacancyId && !!source && !person.archived;
  const commentCounts = new Map<number, number>();
  for (const item of person.screeningComments || []) {
    if (item.recordingIndex === index) commentCounts.set(item.seconds, (commentCounts.get(item.seconds) || 0) + 1);
  }
  function comment(seconds: number, chapterTitle = '', onClose?: () => void) {
    return <EmployerTimecodeComment key={`${person.id}:${index}:${seconds}`} seconds={seconds}
      comments={(person.screeningComments || []).filter(item => item.recordingIndex === index && Math.abs(item.seconds - seconds) < 0.001)}
      workflowRevision={person.reviewRevision || 0} busy={busy} initiallyOpen={!!onClose} onClose={onClose}
      onSave={canComment ? (text, expectedRevision, requestId) => onComment({ recordingIndex: index, seconds, chapterTitle, text, expectedRevision, requestId }) : undefined}/>;
  }
  return <>
    {loading && <p className="ir-muted" role="status"><Loader2 className="cj-spin" size={16} aria-hidden="true"/> Загружаем запись…</p>}
    {error && <div className="ew-inline-error" role="alert"><p>{error}</p><Button type="button" variant="ghost" onClick={() => setAttempt(value => value + 1)}>Повторить загрузку</Button></div>}
    <SourceScreeningReview appearance="candidate" sourceId={`${person.id}:prepared:${index}:${source?.publicUrl || 'none'}`}
      name={person.name} chapters={chapters} fullChapters={chapters} transcriptText={transcript}
      media={media ? { src: media.url, label: source?.name } : undefined}
      renderChapterComment={chapter => chapter.seconds === null ? null : comment(chapter.seconds, chapter.title)}
      renderTimeComment={(seconds, onClose) => comment(seconds, '', onClose)}
      allowNewTimeComment={canComment}
      commentTimestamps={[...commentCounts].sort(([a], [b]) => a - b).map(([seconds, count]) => ({ seconds, count }))}
      emptyMessage={loading ? 'Загружаем запись…' : error || 'Запись интервью не добавлена.'}
      recruiterContent={profile.evaluationText || evaluationUrl ? <>{profile.evaluationText ? <p className="ew-prewrap">{profile.evaluationText}</p> : <p><a href={evaluationUrl} target="_blank" rel="noreferrer">Открыть оценку рекрутера ↗</a></p>}</> : undefined}/>
    {originalUrl && <a className="ew-file-row" href={originalUrl} target="_blank" rel="noreferrer"><Link2 size={18} aria-hidden="true"/><span>Исходная запись ↗</span></a>}
  </>;
}

export function PreparedIntakeProfile({ person, token, vacancyId, onUpdated }: {
  person: IntakePerson; token: string; vacancyId?: string; onUpdated?: () => void;
}) {
  const [current, setCurrent] = useState(person);
  const [index, setIndex] = useState(0);
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
        onUpdated?.();
      }
      return true;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Не удалось сохранить.'); return false;
    } finally { saving.current = false; setBusy(false); }
  }
  const download = (file: typeof firstFile, mode: 'download' | 'open' = 'download') => {
    setError(''); void downloadResume(token, file, mode).catch(cause => setError(cause instanceof Error ? cause.message : 'Не удалось загрузить резюме.'));
  };
  return <EmployerCandidateProfile person={current}
    source={{ interviewRole: current.role, contact: current.contact, cv: profile.cvText, profile: current.note ? [{ label: 'Заметка', value: current.note }] : [] }}
    resume={firstFile ? { name: firstFile.name, sizeLabel: `${Math.max(1, Math.ceil(firstFile.size / 1024))} КБ`, onOpen: () => download(firstFile, 'open'), onDownload: () => download(firstFile) }
      : resumeUrl ? { name: 'Резюме', onOpen: () => { window.open(resumeUrl, '_blank', 'noopener,noreferrer'); } } : undefined}
    interview={<>
      {current.files.slice(1).map(file => <button key={file.id} type="button" className="ew-file-row" onClick={() => download(file)}>{file.name}</button>)}
      {profile.recordings.length > 1 && <div className="ew-methods" role="group" aria-label="Части интервью">{profile.recordings.map((recording, recordingIndex) => <button type="button" key={`${recordingIndex}:${recording.name}`} aria-pressed={index === recordingIndex} onClick={() => setIndex(recordingIndex)}>Часть {recordingIndex + 1}</button>)}</div>}
      <PreparedInterview key={`${current.id}:${index}:${token}`} person={current} profile={profile} index={index} token={token} vacancyId={vacancyId} busy={busy} onComment={values => mutate('comment', values)}/>
    </>}
    workflowRevision={current.reviewRevision || 0} busy={busy} error={error}
    meetingUnavailableReason={current.archived ? 'Кандидат в архиве.' : 'Для встречи нужно согласие кандидата и его свободное время.'}
    onReject={vacancyId && !current.archived ? ({ expectedRevision, requestId, ...rejectionAnswers }) => mutate('review', { expectedRevision, requestId, rejectionAnswers }) : undefined}
    decisionFeedback={current.reviewRejection || undefined}/>;
}

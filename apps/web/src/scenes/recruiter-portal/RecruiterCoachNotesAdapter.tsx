import { useCallback, useEffect, useRef, useState } from 'react';
import { CoachNotesPanel } from '../recruiter-coach/CoachNotesPanel';
import { OriginalResumeViewer } from '../recruiter-coach/OriginalResumeViewer';
import { documentFromText } from '../recruiter-coach/note-document';
import type { NoteVersion } from '../recruiter-coach/note-history';
import type { CoachTranscriptTurn } from '../recruiter-coach/coach-types';
import type { InterviewCoachController, ScriptQuestion } from './interview-coach';
import { recruiterRequest } from './api';

type Identity = { token: string; candidateId: string; interviewId: string };
type HistoryPage = { versions: NoteVersion[]; hasMore: boolean; nextCursor: string | null };

export function RecruiterCoachNotesAdapter({ coach, token, candidateId, interviewId, questions, writable, onSelectQuestion, transcript }: Identity & {
  coach: InterviewCoachController; questions: ScriptQuestion[]; writable: boolean;
  onSelectQuestion: (index: number) => void; transcript?: CoachTranscriptTurn[];
}) {
  const [versions, setVersions] = useState<NoteVersion[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [restoring, setRestoring] = useState(false);
  const sequence = useRef(0);
  const history = useCallback(async (cursor?: string) => {
    const seq = ++sequence.current;
    setLoading(true); setError('');
    try {
      const page = await recruiterRequest<HistoryPage>('interview/coach/history', token, { candidateId, interviewId, ...(cursor ? { beforeVersionId: cursor } : {}) });
      if (seq !== sequence.current) return;
      setVersions(previous => cursor ? [...previous, ...page.versions.filter(v => !previous.some(p => p.id === v.id))] : page.versions);
      setNextCursor(page.hasMore ? page.nextCursor : null);
    } catch (cause) {
      if (seq === sequence.current) setError((cause as Error).message || 'Не удалось открыть историю.');
      throw cause;
    } finally { if (seq === sequence.current) setLoading(false); }
  }, [token, candidateId, interviewId]);
  useEffect(() => { void history().catch(() => {}); return () => { sequence.current++; }; }, [history]);
  const questionNotes = questions.map((question, index) => ({ id: question.id, index, title: question.topic || question.question, text: coach.progress.answers?.[question.id]?.note || '' }));
  return <CoachNotesPanel
    document={coach.progress.noteDocument || documentFromText(coach.progress.general || '')}
    questionNotes={questionNotes}
    onChange={(document, text) => coach.change({ ...coach.progress, noteDocument: document, general: text })}
    onSelectQuestion={onSelectQuestion}
    versions={versions} historyLoading={loading} historyError={error || coach.error}
    checkpoint={async () => { if (!await coach.flush()) throw new Error('Не удалось сохранить заметки.'); await history(); }}
    onRestore={() => {}}
    restoreVersion={async versionId => {
      setRestoring(true);
      try { if (!await coach.command('restore', { versionId })) throw new Error('Не удалось восстановить заметки.'); await history(); }
      finally { setRestoring(false); }
    }}
    hasMoreHistory={Boolean(nextCursor)} onMoreHistory={() => { if (nextCursor) void history(nextCursor).catch(() => {}); }}
    writable={writable && !restoring} transcript={transcript}
  />;
}

export function RecruiterOriginalResume({ token, candidateId, interviewId }: Identity) {
  const [source, setSource] = useState<{ name: string; blob: Blob }>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let alive = true;
    setLoading(true); setError(''); setSource(undefined);
    recruiterRequest<{ resume: { filename: string; mimeType: string; contentBase64: string } | null }>('interview/coach/resume', token, { candidateId, interviewId })
      .then(({ resume }) => {
        if (!alive || !resume) return;
        const binary = atob(resume.contentBase64);
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
        setSource({ name: resume.filename, blob: new Blob([bytes], { type: resume.mimeType }) });
      }).catch(cause => { if (alive) setError((cause as Error).message || 'Не удалось открыть резюме.'); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [token, candidateId, interviewId, retry]);
  if (loading) return <p role="status">Открываем резюме…</p>;
  if (error) return <div role="alert"><p>{error}</p><button onClick={() => setRetry(value => value + 1)}>Повторить</button></div>;
  return <OriginalResumeViewer scope={interviewId} source={source} allowLocalImport={false}/>;
}

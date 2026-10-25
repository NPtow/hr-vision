import { useEffect, useRef, useState } from 'react';
import { ZoomCallFrame } from '../../components/call/ZoomCallFrame';
import type { ZoomCredentials } from '../../components/call/call-provider';
import { startZoomSessionReceipt } from '../../components/call/zoom-session-receipt';
import { startZoomSessionReconcile } from '../../components/call/zoom-session-reconcile';
import { RecruiterInterviewShell } from '../recruiter-coach/RecruiterInterviewShell';
import { RecruiterInterviewPanel } from '../recruiter-coach/RecruiterInterviewPanel';
import type { CoachDraft, CoachPerson } from '../recruiter-coach/coach-types';
import { recruiterRequest, type Room } from './api';
import type { ActiveCall } from './RecruiterCall';
import { downloadText, useInterviewCoach } from './interview-coach';
import { RecruiterCoachNotesAdapter, RecruiterOriginalResume } from './RecruiterCoachNotesAdapter';

type NotesAccess = { flush: () => Promise<boolean>; dirty: boolean; export: () => void };
const EMPTY_DRAFT: CoachDraft = { current: 0, status: {}, general: '', notes: {} };

/** One provider frame, independent of every hook and render in the optional panel. */
export function RecruiterZoomCall({ active, token, onClose, onJoined, onCompleted }: {
  active: ActiveCall; token: string; onClose: () => void; onJoined: () => void; onCompleted: () => void;
}) {
  const notes = useRef<NotesAccess | null>(null);
  const cancelReceipt = useRef<(() => void) | null>(null);
  const cancelReconcile = useRef<(() => void) | null>(null);
  const callbacks = useRef({ onClose, onJoined, onCompleted });
  callbacks.current = { onClose, onJoined, onCompleted };
  const [credentials, setCredentials] = useState<ZoomCredentials | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [joined, setJoined] = useState(false);
  const [left, setLeft] = useState(false);
  const [completed, setCompleted] = useState(false);
  const [reconciling, setReconciling] = useState(false);
  const [error, setError] = useState('');
  const [receiptError, setReceiptError] = useState('');
  const [consent, setConsent] = useState(active.recordingConsent === true);
  const [entryApproved, setEntryApproved] = useState(active.recordingConsent === true);
  const joining = useRef(false);
  const identity = active.kind === 'interview' ? { candidateId: active.candidateId, interviewId: active.interviewId } : { candidateId: active.candidateId, applicationId: active.applicationId, meetingId: active.meetingId };
  const person: CoachPerson = { id: active.candidateId, name: active.title, sourceSummary: '', promptSource: '', questions: [] };
  useEffect(() => {
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { cancelReceipt.current?.(); cancelReconcile.current?.(); document.body.style.overflow = overflow; };
  }, []);
  useEffect(() => {
    if (!entryApproved) return;
    let cancelled = false;
    cancelReceipt.current?.(); cancelReconcile.current?.(); setReconciling(false); setReceiptError('');
    joining.current = true; setError(''); setLeft(false); setJoined(false); setCredentials(null);
    void recruiterRequest<Room>(`${active.kind}/join`, token, { ...identity, consent: true })
      .then(room => {
        if (cancelled) return;
        if (room.provider !== 'zoom') throw new Error('Видеосервис встречи изменился. Вернитесь к интервью и откройте его снова.');
        setCredentials(room);
      }).catch(cause => { if (!cancelled) setError((cause as Error).message || 'Не удалось получить доступ к Zoom.'); })
      .finally(() => { if (!cancelled) joining.current = false; });
    return () => { cancelled = true; cancelReceipt.current?.(); };
  }, [attempt, entryApproved]);
  function flushNotes() { void notes.current?.flush().catch(() => false); }
  function back() {
    cancelReconcile.current?.(); flushNotes();
    if (completed) callbacks.current.onCompleted(); else callbacks.current.onClose();
  }
  function nativeLeft() {
    cancelReceipt.current?.(); cancelReconcile.current?.(); flushNotes(); setJoined(false); setLeft(true);
    // SDK "left" also means an ordinary local leave. Only the server can prove
    // that this exact Zoom session ended; this endpoint never sends Zoom end.
    if (credentials?.role !== 1) return;
    setReconciling(true);
    cancelReconcile.current = startZoomSessionReconcile({
      request: signal => recruiterRequest(`${active.kind}/reconcile`, token, identity, signal),
      onCompleted: () => { setReconciling(false); setCompleted(true); callbacks.current.onJoined(); },
      onExhausted: () => setReconciling(false),
    });
  }
  function connected() {
    setJoined(true); setError(''); callbacks.current.onJoined();
    // This pins the actual Zoom session, but its failure cannot tear down media.
    if (credentials?.role !== 1) return;
    cancelReceipt.current?.();
    cancelReceipt.current = startZoomSessionReceipt({
      request: signal => recruiterRequest(`${active.kind}/connected`, token, identity, signal),
      onPending: () => setReceiptError('Обновляем статус встречи. Звонок продолжается.'),
      onSuccess: () => setReceiptError(''),
      onExhausted: () => setReceiptError('Не удалось обновить статус встречи. Звонок продолжается.'),
    });
  }
  const mediaSlot = !entryApproved ? <div className="md-zoom-after-call">
    <label className="ric-zoom-prejoin-consent"><input type="checkbox" checked={consent} onChange={event => setConsent(event.target.checked)}/>Согласен на запись встречи и сохранение видео в сервисе.</label>
    <button disabled={!consent} onClick={() => setEntryApproved(true)}>Подключиться к звонку</button>
    <button onClick={back}>Вернуться к интервью</button>
  </div> : credentials && !left && !completed
    ? <ZoomCallFrame key={attempt} credentials={credentials} onJoined={connected} onError={setError}
      onLeft={nativeLeft}/>
    : <div className="md-zoom-after-call"><p>{completed ? 'Встреча завершена' : left ? 'Вы вышли из Zoom' : error || 'Получаем доступ к встрече…'}</p>
      {!completed && (left || error) && <button disabled={joining.current} onClick={() => setAttempt(value => value + 1)}>Подключиться снова</button>}
      {(left || error || completed) && <button onClick={back}>Вернуться к интервью</button>}
      {notes.current?.dirty && <p>Есть несохранённые заметки. Скачайте их перед закрытием.<button onClick={() => notes.current?.export()}>Скачать заметки</button></p>}
    </div>;
  return <div className="ric-approved-shell" role="dialog" aria-modal="true" aria-label={`Интервью · ${active.title}`}>
    <RecruiterInterviewShell providerOwnsControls person={person} draft={EMPTY_DRAFT} onChange={() => {}}
      phase={completed ? 'completed' : 'live'} mediaSlot={mediaSlot}
      onFinish={back} onResume={back}
      notice={error || receiptError || reconciling ? <span role="status">{error || receiptError || 'Проверяем статус встречи…'}{error && !left && credentials && <button onClick={nativeLeft}>Вернуться к подключению</button>}</span> : undefined}
      panelContent={active.kind === 'interview' && active.interviewId ? <ZoomInterviewNotes active={active} token={token} notes={notes} connected={joined} completed={completed}/>
        : <div className="rcc-script-empty">Материалы встречи доступны в кабинете.</div>}
      resumeContent={active.interviewId ? <RecruiterOriginalResume token={token} candidateId={active.candidateId} interviewId={active.interviewId}/> : undefined}
    />
  </div>;
}

/** A failed coach render/request never mounts, hides or destroys the Zoom frame. */
function ZoomInterviewNotes({ active, token, notes, connected, completed }: {
  active: ActiveCall; token: string; notes: React.MutableRefObject<NotesAccess | null>; connected: boolean; completed: boolean;
}) {
  const coach = useInterviewCoach({ candidateId: active.candidateId, interviewId: active.interviewId!, token });
  const [currentId, setCurrentId] = useState('');
  const questions = coach.data?.active?.questions || [];
  const current = Math.max(0, questions.findIndex(question => question.id === (currentId || coach.progress.currentId)));
  const person: CoachPerson = { id: active.candidateId, name: active.title, sourceSummary: '', promptSource: '', questions };
  const draft: CoachDraft = { current, status: {}, general: coach.progress.general || '', noteDocument: coach.progress.noteDocument,
    notes: Object.fromEntries(Object.entries(coach.progress.answers || {}).map(([id, answer]) => [id, answer.note])) };
  const writable = completed || connected || coach.data?.interviewStatus === 'live';
  const exportDraft = () => downloadText(`interview-${active.interviewId}-notes.json`, JSON.stringify(coach.progress, null, 2));
  notes.current = { flush: coach.flush, dirty: coach.dirty, export: exportDraft };
  function change(patch: Partial<CoachDraft>) {
    if (patch.current !== undefined && questions[patch.current]) setCurrentId(questions[patch.current].id);
    if (!writable) return;
    const next = { ...coach.progress, ...(questions[current] ? { currentId: questions[current].id } : {}) };
    if (patch.current !== undefined && questions[patch.current]) next.currentId = questions[patch.current].id;
    if (patch.general !== undefined) next.general = patch.general;
    if (patch.noteDocument !== undefined) next.noteDocument = patch.noteDocument;
    if (patch.notes) next.answers = Object.fromEntries(Object.entries(patch.notes).map(([id, note]) => [id, { ...coach.progress.answers?.[id], status: coach.progress.answers?.[id]?.status || '' as const, note }]));
    coach.change(next);
  }
  return <RecruiterInterviewPanel person={person} draft={draft} onChange={change} writable={writable} completed={completed}
    saveError={coach.error} onExport={exportDraft}
    notesContent={navigate => <RecruiterCoachNotesAdapter coach={coach} token={token} candidateId={active.candidateId} interviewId={active.interviewId!}
      questions={questions} writable={writable} onSelectQuestion={navigate}/>}/>;
}

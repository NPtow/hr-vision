import { useEffect, useRef, useState } from 'react';
import type { DailyCall } from '@daily-co/daily-js';
import { Maximize2, Minimize2, PanelRightOpen, PhoneOff, X } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { recruiterRequest, type RecruiterCandidate, type Room } from './api';
import type { InterviewRecording } from '../candidate-portal/api';
import { useInterviewCoach, type LiveInterviewTurn } from './interview-coach';
import { InterviewCoachPanel } from './InterviewCoachPanel';
import { InterviewContacts, ResumeAction } from './InterviewPreparation';
import { RecruiterInterviewShell } from '../recruiter-coach/RecruiterInterviewShell';
import type { CoachDraft, CoachPerson, CoachTranscriptTurn } from '../recruiter-coach/coach-types';
import { RecruiterCoachNotesAdapter, RecruiterOriginalResume } from './RecruiterCoachNotesAdapter';
import './interview-coach.css';
import './recruiter-coach-live.css';

export type ActiveCall = { kind: 'interview' | 'meeting'; preview?: boolean; candidateId: string; interviewId?: string; candidate?: RecruiterCandidate; applicationId?: string; meetingId?: string; title: string; accountActive?: boolean; recording?: InterviewRecording };
type CaptureStatus = 'unknown' | 'active' | 'stopped' | 'error';

export function RecruiterCall({ active, token, onClose, onJoined, onCompleted }: {
  active: ActiveCall; token: string; onClose: () => void; onJoined: () => void; onCompleted: () => void;
}) {
  const mount = useRef<HTMLDivElement>(null);
  const call = useRef<DailyCall | null>(null);
  const ending = useRef(false);
  const [joined, setJoined] = useState(false);
  const [present, setPresent] = useState(false);
  const [recording, setRecording] = useState<CaptureStatus>('unknown');
  const [transcribing, setTranscribing] = useState<CaptureStatus>('unknown');
  const [finishing, setFinishing] = useState(false);
  const [oralConsent, setOralConsent] = useState(active.recording?.consentReceived || false);
  const [starting, setStarting] = useState(false);
  const [startRequested, setStartRequested] = useState(active.recording?.startRequested || false);
  const [retryRecording, setRetryRecording] = useState(false);
  const [retryTranscription, setRetryTranscription] = useState(false);
  const [startFailed, setStartFailed] = useState(false);
  const [error, setError] = useState('');
  const [panel, setPanel] = useState(true);
  const [fullscreen, setFullscreen] = useState(false);
  const [turns, setTurns] = useState<LiveInterviewTurn[]>([]);
  const [currentId, setCurrentId] = useState('');
  const root = useRef<HTMLDivElement>(null);
  const coach = useInterviewCoach({ candidateId: active.candidateId, interviewId: active.interviewId || '', token });
  const coachRef = useRef(coach);
  coachRef.current = coach;
  const hasCoach = active.kind === 'interview' && !!active.interviewId;
  const callbacks = useRef({ onClose, onJoined, onCompleted });
  callbacks.current = { onClose, onJoined, onCompleted };
  const identity = active.kind === 'interview' ? { candidateId: active.candidateId } : { candidateId: active.candidateId, applicationId: active.applicationId, meetingId: active.meetingId };

  useEffect(() => {
    const update = () => setFullscreen(document.fullscreenElement === root.current);
    document.addEventListener('fullscreenchange', update);
    return () => document.removeEventListener('fullscreenchange', update);
  }, []);
  async function close() {
    if (!await coachRef.current.flush()) { setPanel(true); return; }
    callbacks.current.onClose();
  }

  useEffect(() => {
    let alive = true;
    ending.current = false;
    setRecording('unknown'); setTranscribing('unknown');
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    async function open() {
      try {
        const room = await recruiterRequest<Room>(`${active.kind}/join`, token, { ...identity, ...(active.kind === 'meeting' ? { consent: true } : {}) });
        const { default: DailyIframe } = await import('@daily-co/daily-js');
        if (!alive || !mount.current) return;
        const frame = DailyIframe.createFrame(mount.current, {
          iframeStyle: { width: '100%', height: '100%', border: '0' },
          showLeaveButton: false, showFullscreenButton: !hasCoach, showUserNameChangeUI: false,
        });
        call.current = frame;
        frame.iframe()?.setAttribute('title', `Интервью · ${active.title}`);
        const presence = () => { if (alive) setPresent(Object.values(frame.participants()).some(person => !person.local)); };
        frame.on('participant-joined', presence);
        frame.on('participant-left', presence);
        frame.on('joined-meeting', () => {
          if (!alive) return;
          setJoined(true); presence(); callbacks.current.onJoined();
          void coachRef.current.refresh();
        });
        const seenTurns = new Set<string>();
        frame.on('transcription-message', event => {
          if (!alive || !event?.text?.trim() || event.rawResponse?.is_final !== true) return;
          const timestamp = new Date(event.timestamp).getTime();
          if (!Number.isFinite(timestamp)) return;
          const id = `${event.instanceId || 'live'}:${event.participantId}:${timestamp}`;
          if (seenTurns.has(id)) return;
          seenTurns.add(id);
          const speaker = Object.values(frame.participants()).find(person => person.session_id === event.participantId)?.user_name || 'Участник';
          setTurns(previous => [...previous, { id, text: event.text.trim(), speaker, at: new Date(timestamp).toISOString() }]);
        });
        frame.on('recording-started', () => { if (alive) { setRecording('active'); setStartRequested(true); setRetryRecording(false); } });
        frame.on('recording-stopped', () => { if (alive) { setRecording('stopped'); setRetryRecording(true); } });
        frame.on('transcription-started', () => { if (alive) { setTranscribing('active'); setStartRequested(true); setRetryTranscription(false); } });
        frame.on('transcription-stopped', () => { if (alive) { setTranscribing('stopped'); setRetryTranscription(true); } });
        frame.on('recording-error', () => { if (alive) { setRecording('error'); setRetryRecording(true); setError('Ошибка записи. Можно повторить запуск.'); } });
        frame.on('transcription-error', () => { if (alive) { setTranscribing('error'); setRetryTranscription(true); setError('Ошибка расшифровки. Можно повторить запуск.'); } });
        const resetCapture = () => { if (alive) { setRecording('unknown'); setTranscribing('unknown'); setRetryRecording(false); setRetryTranscription(false); } };
        frame.on('network-connection', event => { if (event?.event === 'interrupted') resetCapture(); });
        frame.on('error', () => { if (alive) { resetCapture(); setError('Не удалось подключиться. Проверьте сеть и разрешения камеры и микрофона.'); } });
        frame.on('left-meeting', () => { if (alive && !ending.current) { setJoined(false); void close(); } });
        await frame.join({ url: room.url, token: room.token });
      } catch (e) { if (alive) setError((e as Error).message); }
    }
    void open();
    return () => {
      alive = false; ending.current = true;
      document.body.style.overflow = previousOverflow;
      const frame = call.current; call.current = null;
      void frame?.destroy();
    };
  }, [active.kind, active.candidateId, active.interviewId, active.applicationId, active.meetingId, token]);

  async function startInterview() {
    if (starting || !joined || !oralConsent) return;
    setStarting(true); setError(''); setStartFailed(false);
    setRetryRecording(false); setRetryTranscription(false);
    try {
      if (active.kind === 'interview') await recruiterRequest('interview/start', token, { candidateId: active.candidateId, oralConsent: true, ...(retryRecording ? { retryRecording: true } : {}), ...(retryTranscription ? { retryTranscription: true } : {}) });
      else {
        if (!call.current) throw new Error('Сначала подключитесь ко встрече.');
        await call.current.startRecording({ type: 'cloud' });
        await call.current.startTranscription({ language: 'ru', model: 'nova-2' });
      }
      // Requests are not evidence of actual media capture. Provider events above
      // independently control the "Запись идёт" / transcription indicators.
      setStartRequested(true); callbacks.current.onJoined();
      void coachRef.current.refresh();
    } catch (cause) {
      setStartFailed(true); setRetryRecording(current => current || retryRecording); setRetryTranscription(current => current || retryTranscription);
      setError((cause as Error).message);
    }
    finally { setStarting(false); }
  }

  async function finish() {
    if (finishing || starting) return;
    setFinishing(true); setError(''); ending.current = true;
    try {
      if (!await coachRef.current.flush()) { ending.current = false; setFinishing(false); setPanel(true); return; }
      await recruiterRequest(`${active.kind}/complete`, token, identity);
      // Completion is already persisted. A frame that was ejected by the
      // provider must not turn successful completion into a client error.
      try {
        // The interview backend owns provider stop and recording persistence.
        if (active.kind !== 'interview') {
          if (recording === 'active') call.current?.stopRecording();
          if (transcribing === 'active') call.current?.stopTranscription();
        }
        await call.current?.destroy();
      } catch { /* The provider closes the room as part of completion. */ }
      call.current = null;
      callbacks.current.onCompleted();
    } catch (e) { ending.current = false; setError((e as Error).message); setFinishing(false); }
  }

  const questions = coach.data?.active?.questions || [];
  const current = Math.max(0, questions.findIndex(question => question.id === (currentId || coach.progress.currentId)));
  const canWrite = joined && !finishing && coach.data?.interviewStatus === 'live';
  const person: CoachPerson = { id: active.candidateId, name: active.title, sourceSummary: '', promptSource: '', questions };
  const draft: CoachDraft = {
    current, status: {}, general: coach.progress.general || '', noteDocument: coach.progress.noteDocument,
    notes: Object.fromEntries(Object.entries(coach.progress.answers || {}).map(([id, answer]) => [id, answer.note])),
  };
  const transcript: CoachTranscriptTurn[] = turns.map(turn => ({
    id: turn.id, text: turn.text, speaker: turn.speaker,
    time: new Date(turn.at).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' }),
  }));
  function changeDraft(patch: Partial<CoachDraft>) {
    if (patch.current !== undefined && questions[patch.current]) setCurrentId(questions[patch.current].id);
    if (!canWrite) return;
    const next = { ...coach.progress, ...(questions[current] ? { currentId: questions[current].id } : {}) };
    if (patch.current !== undefined && questions[patch.current]) next.currentId = questions[patch.current].id;
    if (patch.general !== undefined) next.general = patch.general;
    if (patch.noteDocument !== undefined) next.noteDocument = patch.noteDocument;
    if (patch.notes) next.answers = Object.fromEntries(Object.entries(patch.notes).map(([id, note]) => [id, { ...coach.progress.answers?.[id], status: coach.progress.answers?.[id]?.status || '' as const, note }]));
    coach.change(next);
  }
  const captureStatus = recording === 'active' ? transcribing === 'active' ? 'Запись и расшифровка идут' : 'Запись идёт · ждём расшифровку' : recording === 'stopped' ? 'Запись остановлена' : recording === 'error' ? 'Ошибка записи' : startRequested ? 'Проверяем статус записи' : 'Запись не начата';

  if (hasCoach) return <div className="ric-approved-shell" role="dialog" aria-modal="true" aria-label={`Интервью · ${active.title}`}>
    <RecruiterInterviewShell
      person={person} draft={draft} onChange={changeDraft}
      phase={finishing ? 'finishing' : 'live'} writable={canWrite}
      mediaSlot={<div className="ric-approved-daily" ref={mount}/>}
      onClose={() => { if (!finishing) void close(); }} onFinish={() => { if (joined && !starting) void finish(); }}
      finishDisabled={!joined || starting || finishing} closeDisabled={finishing}
      recording={recording === 'active'} transcript={transcript}
      durationLabel={!joined ? 'Подключаемся…' : present ? 'Собеседник подключён' : 'Ждём собеседника'}
      notice={error || coach.error ? <span role="alert">{error || coach.error}{coach.error && <button onClick={() => void (coach.dirty ? coach.flush() : coach.refresh())}>Повторить сохранение</button>}</span> : undefined}
      saveError={coach.error}
      resumeContent={<RecruiterOriginalResume token={token} candidateId={active.candidateId} interviewId={active.interviewId!}/>}
      stageToolbar={joined ? <div className="ric-approved-capture">
        {(recording !== 'active' || transcribing !== 'active') && <><label><input type="checkbox" checked={oralConsent} disabled={starting || finishing} onChange={event => setOralConsent(event.target.checked)}/><span>Согласие на запись получено</span></label><button disabled={!oralConsent || starting || finishing || (startRequested && !retryRecording && !retryTranscription && !startFailed)} onClick={() => void startInterview()}>{starting ? 'Запускаем…' : retryRecording || retryTranscription || startFailed ? 'Повторить запуск записи' : startRequested ? 'Ожидаем подтверждения…' : 'Начать запись'}</button></>}
        <span className="ric-approved-status" role="status">{captureStatus}</span>
      </div> : undefined}
      notesContent={onSelectQuestion => <RecruiterCoachNotesAdapter coach={coach} token={token} candidateId={active.candidateId} interviewId={active.interviewId!} questions={questions} writable={canWrite} onSelectQuestion={onSelectQuestion} transcript={transcript}/>}
    />
  </div>;

  return <div ref={root} className={`rp-call-layer ${hasCoach ? `ric-call ${panel ? '' : 'ric-call-wide'}` : ''}`} role="dialog" aria-modal="true" aria-labelledby="rp-call-title">
    <div className="ric-call-stage">
    <header className="rp-call-header">
      <div><strong id="rp-call-title">{active.title}</strong><p role="status">{!joined ? 'Подключение…' : present ? 'Собеседник подключён' : 'Ждём собеседника'}{joined && ` · ${recording === 'active' ? 'Запись идёт' : recording === 'stopped' ? 'Запись остановлена' : recording === 'error' ? 'Ошибка записи' : 'Статус записи уточняется'}`}{joined && ` · ${transcribing === 'active' ? 'Расшифровка включена' : transcribing === 'stopped' ? 'Расшифровка остановлена' : transcribing === 'error' ? 'Ошибка расшифровки' : 'Статус расшифровки уточняется'}`}</p></div>
      <div className="rp-actions">
        {hasCoach && <><ResumeAction coach={coach} name={active.title} container={root.current}/><InterviewContacts candidate={active.candidate || { email: null, telegram: '' }}/>{!panel && <button className="ric-icon" aria-label="Показать помощника" onClick={() => setPanel(true)}><PanelRightOpen size={19}/></button>}
          <button className="ric-icon" aria-label={fullscreen ? 'Выйти из полноэкранного режима' : 'На весь экран'} onClick={() => { if (fullscreen) void document.exitFullscreen(); else void root.current?.requestFullscreen().catch(() => setError('Полноэкранный режим недоступен в этом браузере.')); }}>{fullscreen ? <Minimize2 size={18}/> : <Maximize2 size={18}/>}</button></>}
        <Button variant="secondary" autoFocus disabled={finishing} onClick={() => void close()}><X size={16}/>Выйти</Button>{joined && <Button disabled={finishing || starting} onClick={finish}><PhoneOff size={16}/>{finishing ? 'Завершаем…' : active.kind === 'interview' ? 'Завершить интервью' : 'Завершить встречу'}</Button>}</div>
    </header>
    {joined && <div className="rp-call-controls">
      <div className="rp-recording-controls">
        {(recording !== 'active' || transcribing !== 'active') && <><label className="rp-check"><input type="checkbox" checked={oralConsent} disabled={starting} onChange={event => setOralConsent(event.target.checked)}/><span>Согласие получено</span></label><Button disabled={!oralConsent || starting || finishing || (startRequested && !retryRecording && !retryTranscription && !startFailed)} onClick={startInterview}>{starting ? 'Запускаем…' : retryRecording || retryTranscription || startFailed ? 'Повторить запуск записи' : startRequested ? 'Ожидаем подтверждения…' : 'Начать интервью и запись'}</Button></>}
        <span className="rp-muted rp-help" role="status">{recording === 'active' ? transcribing === 'active' ? 'Запись и расшифровка идут' : 'Запись идёт · статус расшифровки уточняется' : recording === 'stopped' ? 'Запись остановлена' : recording === 'error' ? 'Ошибка записи' : 'Статус записи уточняется'}</span>
      </div>
    </div>}
    {error && <div className="rp-error" role="alert">{error}</div>}
    <div className="rp-call-frame" ref={mount}/>
    </div>
    {hasCoach && panel && <InterviewCoachPanel coach={coach} name={active.title} joined={joined} turns={turns} onCollapse={() => setPanel(false)}/>}

  </div>;
}

import { useEffect, useRef, useState } from 'react';
import type { DailyCall } from '@daily-co/daily-js';
import { Maximize2, Minimize2, PanelRightOpen, PhoneOff, X } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { recruiterRequest, RecruiterRequestError, type RecruiterCandidate, type Room } from './api';
import { CallDeviceAccess, useCallDeviceAccess } from '../../components/call-device-access';
import { CallConnectionError, directCallUrl, releaseCall, waitForCall } from '../candidate-portal/call-connection';
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
  const [connectionPhase, setConnectionPhase] = useState<'devices' | 'loading' | 'ready' | 'joined' | 'failed'>('devices');
  const [connectionHint, setConnectionHint] = useState('');
  const [directUrl, setDirectUrl] = useState('');
  const [reloadRequired, setReloadRequired] = useState(false);
  const [joinAttempt, setJoinAttempt] = useState(0);
  const devices = useCallDeviceAccess();
  const selectedDevices = useRef({ camera: true, microphone: true });
  const connection = useRef<AbortController | null>(null);
  const cleanup = useRef<Promise<void> | null>(null);
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
    devices.stop(); connection.current?.abort(); ending.current = true;
    if (call.current) await retire(call.current);
    callbacks.current.onClose();
  }

  async function retire(frame: DailyCall) {
    if (call.current !== frame) return cleanup.current;
    call.current = null;
    const pending = releaseCall(frame).catch(() => setReloadRequired(true));
    cleanup.current = pending;
    await pending;
    if (cleanup.current === pending) cleanup.current = null;
  }
  async function enableDevices(camera = true) {
    if (await devices.request({ audio: true, video: camera })) {
      devices.stop();
      selectedDevices.current = { camera, microphone: true };
      setJoinAttempt(value => value + 1);
    }
  }
  function joinWithoutDevices() {
    devices.stop();
    selectedDevices.current = { camera: false, microphone: false };
    setJoinAttempt(value => value + 1);
  }
  async function retry() {
    connection.current?.abort(); ending.current = true;
    if (call.current) await retire(call.current);
    if (cleanup.current) await cleanup.current;
    setJoinAttempt(value => value + 1);
  }

  useEffect(() => {
    let alive = true;
    let ownedFrame: DailyCall | null = null;
    let timeout: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    connection.current = controller;
    const deadline = (message: string) => {
      clearTimeout(timeout);
      timeout = setTimeout(() => controller.abort(new CallConnectionError(message)), 30000);
    };
    ending.current = false;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    async function open() {
      setRecording('unknown'); setTranscribing('unknown');
      setConnectionPhase('loading'); setConnectionHint('Получаем доступ к встрече…');
      setError(''); setDirectUrl(''); setJoined(false); setPresent(false);
      deadline('Не удалось получить доступ к встрече. Попробуйте подключиться ещё раз.');
      try {
        if (cleanup.current) await cleanup.current;
        if (!alive || controller.signal.aborted) return;
        const access = recruiterRequest<Room>(`${active.kind}/join`, token, { ...identity, ...(active.kind === 'meeting' ? { consent: true } : {}) }).then(room => {
          if (alive && !controller.signal.aborted) {
            setDirectUrl(directCallUrl(room));
            setConnectionHint('Загружаем видеокомнату…');
            deadline('Видеокомната не загрузилась. Проверьте соединение или откройте её отдельно.');
          }
          return room;
        });
        // Retain authorized direct access even when the SDK chunk fails.
        const sdk = import('@daily-co/daily-js').catch(() => null);
        const [room, module] = await waitForCall(Promise.all([access, sdk]), controller.signal);
        if (!module) throw new CallConnectionError('Не удалось загрузить видеосервис. Откройте комнату отдельно.');
        const { default: DailyIframe } = module;
        if (!alive || controller.signal.aborted || !mount.current) return;
        if (DailyIframe.getCallInstance() && !DailyIframe.getCallInstance()!.isDestroyed()) {
          setReloadRequired(true);
          throw new CallConnectionError('Предыдущее подключение не закрылось. Обновите страницу.');
        }
        const frame = DailyIframe.createFrame(mount.current, {
          iframeStyle: { width: '100%', height: '100%', border: '0' },
          showLeaveButton: false, showFullscreenButton: !hasCoach, showUserNameChangeUI: false,
        });
        ownedFrame = frame;
        call.current = frame;
        frame.iframe()?.setAttribute('title', `Интервью · ${active.title}`);
        const current = () => alive && !controller.signal.aborted && call.current === frame;
        const presence = () => { if (current()) setPresent(Object.values(frame.participants()).some(person => !person.local)); };
        frame.on('loaded', () => {
          if (!current()) return;
          // The prejoin screen may wait for a human indefinitely. It is not a
          // network timeout and must stay usable while a permission prompt is open.
          clearTimeout(timeout);
          setConnectionPhase('ready');
          setConnectionHint('В окне видеосвязи разрешите устройства и нажмите «Присоединиться». Если запроса нет, проверьте настройки сайта рядом с адресом.');
        });
        frame.on('load-attempt-failed', () => {
          if (current()) setConnectionHint('Видеосервис загружается дольше обычного. Можно повторить подключение или открыть комнату отдельно.');
        });
        frame.on('camera-error', event => {
          if (!current()) return;
          setConnectionHint(event?.error?.type === 'permissions'
            ? 'Видеосервису запрещён доступ к устройствам. Разрешите камеру и микрофон в настройках сайта или войдите без них.'
            : 'Видеосервис не смог включить камеру или микрофон. Закройте другой звонок или войдите без устройств.');
        });
        frame.on('participant-joined', presence);
        frame.on('participant-left', presence);
        frame.on('joined-meeting', () => {
          if (!current()) return;
          clearTimeout(timeout); setConnectionPhase('joined'); setConnectionHint('');
          setJoined(true); presence(); callbacks.current.onJoined();
          void coachRef.current.refresh();
        });
        const seenTurns = new Set<string>();
        frame.on('transcription-message', event => {
          if (!current() || !event?.text?.trim() || event.rawResponse?.is_final !== true) return;
          const timestamp = new Date(event.timestamp).getTime();
          if (!Number.isFinite(timestamp)) return;
          const id = `${event.instanceId || 'live'}:${event.participantId}:${timestamp}`;
          if (seenTurns.has(id)) return;
          seenTurns.add(id);
          const speaker = Object.values(frame.participants()).find(person => person.session_id === event.participantId)?.user_name || 'Участник';
          setTurns(previous => [...previous, { id, text: event.text.trim(), speaker, at: new Date(timestamp).toISOString() }]);
        });
        frame.on('recording-started', () => { if (current()) { setRecording('active'); setStartRequested(true); setRetryRecording(false); } });
        frame.on('recording-stopped', () => { if (current()) { setRecording('stopped'); setRetryRecording(true); } });
        frame.on('transcription-started', () => { if (current()) { setTranscribing('active'); setStartRequested(true); setRetryTranscription(false); } });
        frame.on('transcription-stopped', () => { if (current()) { setTranscribing('stopped'); setRetryTranscription(true); } });
        frame.on('recording-error', () => { if (current()) { setRecording('error'); setRetryRecording(true); setError('Ошибка записи. Можно повторить запуск.'); } });
        frame.on('transcription-error', () => { if (current()) { setTranscribing('error'); setRetryTranscription(true); setError('Ошибка расшифровки. Можно повторить запуск.'); } });
        const resetCapture = () => { if (current()) { setRecording('unknown'); setTranscribing('unknown'); setRetryRecording(false); setRetryTranscription(false); } };
        frame.on('network-connection', event => { if (event?.event === 'interrupted') resetCapture(); });
        frame.on('error', event => { if (current()) {
          resetCapture();
          const failure = new CallConnectionError(event?.error?.type === 'connection-error' && event.error.details?.on === 'load'
            ? 'Видеокомната не загрузилась. Проверьте соединение или откройте её отдельно.'
            : 'Соединение с видеосервисом прервалось. Повторите подключение или откройте комнату отдельно.');
          controller.abort(failure);
          setJoined(false); setConnectionPhase('failed'); setError(failure.message);
          void retire(frame);
        } });
        frame.on('left-meeting', () => { if (current() && !ending.current) {
          setJoined(false); setConnectionPhase('failed'); setError('Вы вышли из видеокомнаты. Можно подключиться снова.');
          controller.abort(); void retire(frame);
        } });
        await waitForCall(frame.join({ url: room.url, token: room.token,
          startVideoOff: !selectedDevices.current.camera, startAudioOff: !selectedDevices.current.microphone }), controller.signal);
      } catch (e) {
        if (alive) {
          setJoined(false); setConnectionPhase('failed');
          setError(e instanceof CallConnectionError || e instanceof RecruiterRequestError ? e.message : 'Не удалось загрузить видеокомнату. Попробуйте ещё раз.');
          if (ownedFrame) await retire(ownedFrame);
        }
      } finally { clearTimeout(timeout); }
    }
    if (joinAttempt) void open();
    return () => {
      alive = false; ending.current = true;
      controller.abort(); clearTimeout(timeout);
      document.body.style.overflow = previousOverflow;
      if (ownedFrame) void retire(ownedFrame);
    };
  }, [active.kind, active.candidateId, active.interviewId, active.applicationId, active.meetingId, token, joinAttempt]);

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
  const connectionStatus = connectionPhase === 'devices' ? 'Подготовка к звонку' : connectionPhase === 'failed' ? 'Не подключены'
    : connectionPhase === 'ready' ? 'Завершите вход в видеокомнату' : connectionPhase === 'loading' ? 'Загружаем видеокомнату…'
      : present ? 'Собеседник подключён' : 'Ждём собеседника';
  const accessCard = connectionPhase === 'devices'
    ? <CallDeviceAccess checking={devices.checking} error={devices.error} onRequest={() => void enableDevices()} onAudioOnly={() => void enableDevices(false)} onSkip={joinWithoutDevices}/>
    : connectionPhase === 'failed' ? <div className="ric-approved-prejoin" role="alert">
      <h2>Не удалось подключиться</h2><p>{error}</p>
      {reloadRequired ? <button className="ric-approved-join" onClick={() => window.location.reload()}>Обновить страницу</button>
        : <button className="ric-approved-join" onClick={() => void retry()}>Повторить подключение</button>}
      {directUrl && <a href={directUrl} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">Открыть комнату отдельно</a>}
    </div> : null;
  const connectionTools = !joined && connectionPhase !== 'devices' && connectionPhase !== 'failed' ? <div className="ric-connection-tools" role="status">
    <span>{connectionHint}</span>
    <button onClick={() => void retry()}>Повторить подключение</button>
    {connectionPhase === 'ready' && <button onClick={() => { selectedDevices.current = { camera: false, microphone: false }; void retry(); }}>Войти без устройств</button>}
    {directUrl && <a href={directUrl} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">Открыть комнату отдельно</a>}
  </div> : joined && connectionHint ? <div className="ric-connection-tools" role="status">{connectionHint}</div> : null;

  if (hasCoach) return <div className="ric-approved-shell" role="dialog" aria-modal="true" aria-label={`Интервью · ${active.title}`}>
    <RecruiterInterviewShell
      person={person} draft={draft} onChange={changeDraft}
      phase={finishing ? 'finishing' : 'live'} writable={canWrite}
      mediaSlot={<div className="ric-approved-connection"><div className="ric-approved-daily" ref={mount} inert={!!accessCard}/>{accessCard && <div className="ric-connection-card">{accessCard}</div>}</div>}
      onClose={() => { if (!finishing) void close(); }} onFinish={() => { if (joined && !starting) void finish(); }}
      finishDisabled={!joined || starting || finishing} closeDisabled={finishing}
      recording={recording === 'active'} transcript={transcript}
      durationLabel={connectionStatus}
      notice={(error && connectionPhase !== 'failed') || coach.error ? <span role="alert">{connectionPhase !== 'failed' && error || coach.error}{coach.error && <button onClick={() => void (coach.dirty ? coach.flush() : coach.refresh())}>Повторить сохранение</button>}</span> : undefined}
      saveError={coach.error}
      resumeContent={<RecruiterOriginalResume token={token} candidateId={active.candidateId} interviewId={active.interviewId!}/>}
      stageToolbar={<>{connectionTools}{joined && <div className="ric-approved-capture">
        {(recording !== 'active' || transcribing !== 'active') && <><label><input type="checkbox" checked={oralConsent} disabled={starting || finishing} onChange={event => setOralConsent(event.target.checked)}/><span>Согласие на запись получено</span></label><button disabled={!oralConsent || starting || finishing || (startRequested && !retryRecording && !retryTranscription && !startFailed)} onClick={() => void startInterview()}>{starting ? 'Запускаем…' : retryRecording || retryTranscription || startFailed ? 'Повторить запуск записи' : startRequested ? 'Ожидаем подтверждения…' : 'Начать запись'}</button></>}
        <span className="ric-approved-status" role="status">{captureStatus}</span>
      </div>}</>}
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
    {error && !accessCard && <div className="rp-error" role="alert">{error}</div>}
    {connectionTools}
    <div className="rp-call-frame ric-approved-connection"><div className="ric-approved-daily" ref={mount} inert={!!accessCard}/>{accessCard && <div className="ric-connection-card">{accessCard}</div>}</div>
    </div>
    {hasCoach && panel && <InterviewCoachPanel coach={coach} name={active.title} joined={joined} turns={turns} onCollapse={() => setPanel(false)}/>}

  </div>;
}

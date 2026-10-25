import { useEffect, useRef, useState } from 'react';
import { RecruiterZoomCall } from './RecruiterZoomCall';
import type { CallProvider } from '../../components/call/call-provider';
import type { DailyCall, DailyParticipant } from '@daily-co/daily-js';
import { Camera, CameraOff, Mic, MicOff, MonitorUp, PhoneOff, X } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { recruiterRequest, RecruiterRequestError, type RecruiterCandidate, type Room } from './api';
import { CallConnectionError, dailyCallConfig, directCallUrl, releaseCall, waitForCall } from '../candidate-portal/call-connection';
import { CallDeviceAccess, useCallDeviceAccess } from '../../components/call-device-access';
import { callInputTracks, canSendTrack } from '../candidate-portal/call-media';
import { CallMediaStage } from '../candidate-portal/CallMediaStage';
import type { InterviewRecording } from '../candidate-portal/api';
import { useInterviewCoach, type LiveInterviewTurn } from './interview-coach';
import { RecruiterInterviewShell } from '../recruiter-coach/RecruiterInterviewShell';
import type { CoachDraft, CoachPerson, CoachTranscriptTurn } from '../recruiter-coach/coach-types';
import { RecruiterCoachNotesAdapter, RecruiterOriginalResume } from './RecruiterCoachNotesAdapter';
import './interview-coach.css';
import './recruiter-coach-live.css';

export type ActiveCall = { callProvider?: CallProvider; kind: 'interview' | 'meeting'; preview?: boolean; recordingConsent?: boolean; candidateId: string; interviewId?: string; candidate?: RecruiterCandidate; applicationId?: string; meetingId?: string; title: string; accountActive?: boolean; recording?: InterviewRecording };
type CaptureStatus = 'unknown' | 'active' | 'stopped' | 'error';

export type RecruiterCallProps = {
  active: ActiveCall; token: string; onClose: () => void; onJoined: () => void; onCompleted: () => void; onZoomHostLeft?: () => void;
};
export function RecruiterCall(props: RecruiterCallProps) {
  return props.active.callProvider === 'zoom' ? <RecruiterZoomCall {...props}/> : <DailyRecruiterCall {...props}/>;
}
function DailyRecruiterCall({ active, token, onClose, onJoined, onCompleted }: RecruiterCallProps) {
  const preview = useRef<HTMLVideoElement>(null);
  const devices = useCallDeviceAccess();
  const prepared = useRef<(ReturnType<typeof callInputTracks> & { camera: boolean; microphone: boolean }) | null>(null);
  const inputTracks = useRef<MediaStreamTrack[]>([]);
  const [participants, setParticipants] = useState<DailyParticipant[]>([]);
  const [camera, setCamera] = useState(true);
  const [microphone, setMicrophone] = useState(true);
  const call = useRef<DailyCall | null>(null);
  const ending = useRef(false);
  const [joined, setJoined] = useState(false);
  const [present, setPresent] = useState(false);
  const [screenAllowed, setScreenAllowed] = useState(false);
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
  const [turns, setTurns] = useState<LiveInterviewTurn[]>([]);
  const [currentId, setCurrentId] = useState('');
  const [connectionPhase, setConnectionPhase] = useState<'loading' | 'ready' | 'joined' | 'failed'>('ready');
  const [connectionHint, setConnectionHint] = useState('');
  const [directUrl, setDirectUrl] = useState('');
  const [reloadRequired, setReloadRequired] = useState(false);
  const mustReload = useRef(false);
  function requireReload() { mustReload.current = true; setReloadRequired(true); }
  const [joinAttempt, setJoinAttempt] = useState(0);
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
    const element = preview.current;
    if (!element) return;
    element.srcObject = devices.stream;
    if (devices.stream) void element.play().catch(() => undefined);
    return () => { element.srcObject = null; };
  }, [devices.stream]);
  useEffect(() => {
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = overflow; };
  }, []);
  function releaseInputs() {
    inputTracks.current.forEach(track => track.stop());
    inputTracks.current = [];
  }
  function prepare(audio = true, video = true) {
    setMicrophone(audio); setCamera(video);
    void devices.request({ audio, video });
  }
  function togglePreview(kind: 'audio' | 'video') {
    const enabled = kind === 'audio' ? !microphone : !camera;
    const source = devices.stream?.getTracks().find(track => track.kind === kind && track.readyState === 'live');
    if (source) {
      source.enabled = enabled;
      if (kind === 'audio') setMicrophone(enabled); else setCamera(enabled);
    } else prepare(kind === 'audio' ? enabled : microphone, kind === 'video' ? enabled : camera);
  }
  function enter(selected = { camera, microphone }) {
    if (connectionPhase !== 'ready' || (devices.checking && (selected.camera || selected.microphone)) || mustReload.current || cleanup.current) return;
    try {
      const inputs = callInputTracks(devices.stream, selected);
      prepared.current = { ...inputs, ...selected };
      inputTracks.current = inputs.owned;
      devices.stop();
      setConnectionPhase('loading');
      setJoinAttempt(value => value + 1);
    } catch (cause) { setError((cause as Error).message); }
  }
  function control(kind: 'microphone' | 'camera' | 'screen') {
    const media = call.current;
    if (!media || !joined || finishing || media.isDestroyed()) return;
    const local = media.participants().local;
    if (kind === 'screen' && !screenAllowed) return;
    if (!canSendTrack(local, kind === 'microphone' ? 'audio' : kind === 'camera' ? 'video' : 'screenVideo')) return;
    setError('');
    try {
      const enabling = kind === 'microphone' && !media.localAudio() || kind === 'camera' && !media.localVideo();
      if (enabling && document.fullscreenElement) void document.exitFullscreen().catch(() => undefined);
      if (kind === 'microphone') media.setLocalAudio(!media.localAudio());
      else if (kind === 'camera') media.setLocalVideo(!media.localVideo());
      else if (local?.screen) media.stopScreenShare();
      else media.startScreenShare();
    } catch { setError('Не удалось включить устройство. Проверьте доступ в браузере.'); }
  }
  async function close() {
    if (!await coachRef.current.flush()) { return; }
    connection.current?.abort(); ending.current = true;
    devices.stop(); releaseInputs();
    if (call.current) await retire(call.current);
    callbacks.current.onClose();
  }

  async function retire(frame: DailyCall) {
    if (call.current !== frame) return cleanup.current;
    call.current = null;
    releaseInputs();
    const pending = releaseCall(frame).catch(() => requireReload());
    cleanup.current = pending;
    await pending;
    if (cleanup.current === pending) cleanup.current = null;
  }
  async function retry() {
    connection.current?.abort(); ending.current = true;
    if (call.current) await retire(call.current);
    if (cleanup.current) await cleanup.current;
    releaseInputs(); devices.stop(); prepared.current = null;
    if (mustReload.current) { setConnectionPhase('failed'); setError('Предыдущее подключение не закрылось. Обновите страницу.'); return; }
    setError(''); setConnectionHint(''); setParticipants([]); setJoined(false);
    setConnectionPhase('ready'); setJoinAttempt(0);
  }

  useEffect(() => {
    if (!joinAttempt || !prepared.current) return;
    const inputs = prepared.current;
    let alive = true;
    let ownedFrame: DailyCall | null = null;
    let timeout: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    connection.current = controller;
    const deadline = (message: string, milliseconds = 30000) => {
      clearTimeout(timeout);
      timeout = setTimeout(() => controller.abort(new CallConnectionError(message)), milliseconds);
    };
    ending.current = false;
    async function open() {
      setRecording('unknown'); setTranscribing('unknown');
      setConnectionPhase('loading'); setConnectionHint('Получаем доступ к встрече…');
      setError(''); setDirectUrl(''); setJoined(false); setPresent(false); setScreenAllowed(false);
      deadline('Не удалось получить доступ к встрече. Попробуйте подключиться ещё раз.');
      try {
        if (cleanup.current) await cleanup.current;
        if (!alive || controller.signal.aborted) return;
        const access = recruiterRequest<Room>(`${active.kind}/join`, token, { ...identity, ...(active.kind === 'meeting' ? { consent: true } : {}) }).then(room => {
          if (room.provider === 'zoom') throw new CallConnectionError('Видеосервис встречи изменился. Вернитесь к интервью и откройте его заново.');
          if (alive && !controller.signal.aborted) {
            setDirectUrl(directCallUrl(room));
            setConnectionHint('Загружаем видеокомнату…');
            // Allow the SDK's own CDN fallback attempts to complete.
            deadline('Не удалось загрузить видеосервис. Проверьте соединение или откройте комнату отдельно.', 90000);
          }
          return room;
        });
        // Retain authorized direct access even when the SDK chunk fails.
        const sdk = import('@daily-co/daily-js').catch(() => null);
        const [room, module] = await waitForCall(Promise.all([access, sdk]), controller.signal);
        if (!module) throw new CallConnectionError('Не удалось загрузить видеосервис. Откройте комнату отдельно.');
        const { default: DailyIframe } = module;
        if (!alive || controller.signal.aborted) return;
        if (DailyIframe.getCallInstance() && !DailyIframe.getCallInstance()!.isDestroyed()) {
          requireReload();
          throw new CallConnectionError('Предыдущее подключение не закрылось. Обновите страницу.');
        }
        const frame = DailyIframe.createCallObject({
          dailyConfig: dailyCallConfig(),
          ...(inputs.audioSource ? { audioSource: inputs.audioSource } : {}),
          ...(inputs.videoSource ? { videoSource: inputs.videoSource } : {}),
          startVideoOff: !inputs.camera, startAudioOff: !inputs.microphone,
        });
        ownedFrame = frame;
        call.current = frame;
        const current = () => alive && !controller.signal.aborted && call.current === frame;
        const presence = () => {
          if (!current()) return;
          const people = Object.values(frame.participants());
          setParticipants(people); setPresent(people.some(person => !person.local));
        };
        frame.on('loaded', () => {
          if (!current()) return;
          setConnectionHint('Подключаемся к встрече…');
          deadline('Не удалось соединиться с видеосервисом. Проверьте сеть или откройте комнату отдельно.');
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
        frame.on('participant-updated', presence);
        frame.on('track-started', presence);
        frame.on('track-stopped', presence);
        frame.on('joined-meeting', () => {
          if (!current()) return;
          clearTimeout(timeout); setConnectionPhase('joined'); setConnectionHint('');
          setJoined(true); presence(); callbacks.current.onJoined();
          // Room-level restrictions also apply when participant canSend is true.
          void frame.room({ includeRoomConfigDefaults: true }).then(info => {
            if (current()) setScreenAllowed(!!info && 'config' in info && info.config.enable_screenshare === true);
          }).catch(() => undefined);
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
        frame.on('network-connection', event => {
          if (!current()) return;
          if (event?.event === 'interrupted') { resetCapture(); setConnectionHint('Восстанавливаем соединение…'); }
          if (event?.event === 'connected') { setConnectionHint(''); presence(); }
        });
        frame.on('nonfatal-error', event => {
          if (current() && event?.type === 'screen-share-error') setError('Не удалось показать экран. Повторите выбор окна.');
        });
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
        // Same captured source as the candidate: no embedded prejoin, no second
        // getUserMedia request, and no dependency on Daily's call-ui bundles.
        await waitForCall(frame.join({ url: room.url, token: room.token,
          startVideoOff: !inputs.camera, startAudioOff: !inputs.microphone }), controller.signal);
      } catch (e) {
        if (alive) {
          setJoined(false); setConnectionPhase('failed');
          setError(e instanceof CallConnectionError || e instanceof RecruiterRequestError ? e.message : 'Не удалось загрузить видеокомнату. Попробуйте ещё раз.');
          if (ownedFrame) await retire(ownedFrame);
          else releaseInputs();
        }
      } finally { clearTimeout(timeout); }
    }
    void open();
    return () => {
      alive = false; ending.current = true;
      controller.abort(); clearTimeout(timeout);
      inputs.owned.forEach(track => track.stop());
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
      if (!await coachRef.current.flush()) { ending.current = false; setFinishing(false); return; }
      await recruiterRequest(`${active.kind}/complete`, token, identity);
      // Completion is already persisted. A frame that was ejected by the
      // provider must not turn successful completion into a client error.
      try {
        // The interview backend owns provider stop and recording persistence.
        if (active.kind !== 'interview') {
          if (recording === 'active') call.current?.stopRecording();
          if (transcribing === 'active') call.current?.stopTranscription();
        }
        connection.current?.abort();
        if (call.current) await retire(call.current);
      } catch { /* The provider closes the room as part of completion. */ }
      connection.current?.abort(); releaseInputs(); devices.stop();
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
  const connectionStatus = connectionPhase === 'failed' ? 'Не подключены'
    : connectionPhase === 'ready' ? 'Проверка камеры и микрофона' : connectionPhase === 'loading' ? 'Загружаем видеокомнату…'
      : present ? 'Собеседник подключён' : 'Ждём собеседника';
  const accessCard = connectionPhase === 'failed' ? <div className="ric-approved-prejoin" role="alert">
      <h2>Не удалось подключиться</h2><p>{error}</p>
      {reloadRequired ? <button className="ric-approved-join" onClick={() => window.location.reload()}>Обновить страницу</button>
        : <button className="ric-approved-join" onClick={() => void retry()}>Повторить подключение</button>}
      {directUrl && <a href={directUrl} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">Открыть комнату отдельно</a>}
    </div> : null;
  const connectionTools = connectionHint && connectionPhase !== 'failed' ? <div className="ric-connection-tools" role="status"><span>{connectionHint}</span></div> : null;
  const local = participants.find(person => person.local);
  const mediaControls = joined ? {
    micOn: !!local?.audio, cameraOn: !!local?.video, screenOn: !!local?.screen,
    busy: finishing, canMic: canSendTrack(local, 'audio'), canCamera: canSendTrack(local, 'video'),
    canShare: screenAllowed && typeof navigator.mediaDevices?.getDisplayMedia === 'function' && canSendTrack(local, 'screenVideo'),
    toggleMic: () => control('microphone'), toggleCamera: () => control('camera'), toggleScreen: () => control('screen'),
  } : undefined;
  const mediaSlot = <div className="ric-approved-connection">
    {connectionPhase === 'ready' ? <div className="ric-device-preparation">
      {devices.stream ? <div className="ric-device-preview">
        <video ref={preview} autoPlay muted playsInline className="rcc-mirror" aria-label="Предпросмотр камеры" hidden={!camera}/>
        {!camera && <CameraOff size={32}/>}
        <p>Проверьте камеру и микрофон. Собеседник пока вас не видит и не слышит.</p>
        <div className="rcc-device-controls">
          <button className="rcc-tool" aria-label={microphone ? 'Выключить микрофон' : 'Включить микрофон'} onClick={() => togglePreview('audio')}>{microphone ? <Mic/> : <MicOff/>}</button>
          <button className="rcc-tool" aria-label={camera ? 'Выключить камеру' : 'Включить камеру'} onClick={() => togglePreview('video')}>{camera ? <Camera/> : <CameraOff/>}</button>
        </div>
        <button className="ric-approved-join" onClick={() => enter()}>{active.kind === 'interview' ? 'Подключиться к интервью' : 'Подключиться к встрече'}</button>
      </div> : <CallDeviceAccess checking={devices.checking} error={devices.error}
        onRequest={() => prepare()} onAudioOnly={() => prepare(true, false)}
        onSkip={() => { devices.stop(); enter({ camera: false, microphone: false }); }}/>}
    </div> : accessCard ? <div className="ric-connection-card">{accessCard}</div>
      : <CallMediaStage participants={participants} joined={joined} status={connectionHint || connectionStatus}/>}
  </div>;

  if (hasCoach) return <div className="ric-approved-shell" role="dialog" aria-modal="true" aria-label={`Интервью · ${active.title}`}>
    <RecruiterInterviewShell
      person={person} draft={draft} onChange={changeDraft}
      phase={finishing ? 'finishing' : 'live'} writable={canWrite}
      mediaSlot={mediaSlot} mediaControls={mediaControls}
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

  return <div ref={root} className="rp-call-layer" role="dialog" aria-modal="true" aria-labelledby="rp-call-title">
    <div className="ric-call-stage">
    <header className="rp-call-header">
      <div><strong id="rp-call-title">{active.title}</strong><p role="status">{!joined ? 'Подключение…' : present ? 'Собеседник подключён' : 'Ждём собеседника'}{joined && ` · ${recording === 'active' ? 'Запись идёт' : recording === 'stopped' ? 'Запись остановлена' : recording === 'error' ? 'Ошибка записи' : 'Статус записи уточняется'}`}{joined && ` · ${transcribing === 'active' ? 'Расшифровка включена' : transcribing === 'stopped' ? 'Расшифровка остановлена' : transcribing === 'error' ? 'Ошибка расшифровки' : 'Статус расшифровки уточняется'}`}</p></div>
      <div className="rp-actions">
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
    <div className="rp-call-frame ric-approved-connection">{mediaSlot}</div>
    {mediaControls && <div className="rcc-device-controls ric-meeting-devices">
      <button className="rcc-tool" disabled={finishing || !mediaControls.canMic} aria-label={mediaControls.micOn ? 'Выключить микрофон' : 'Включить микрофон'} onClick={mediaControls.toggleMic}>{mediaControls.micOn ? <Mic/> : <MicOff/>}</button>
      <button className="rcc-tool" disabled={finishing || !mediaControls.canCamera} aria-label={mediaControls.cameraOn ? 'Выключить камеру' : 'Включить камеру'} onClick={mediaControls.toggleCamera}>{mediaControls.cameraOn ? <Camera/> : <CameraOff/>}</button>
      {mediaControls.canShare && <button className="rcc-tool" disabled={finishing} aria-label={mediaControls.screenOn ? 'Остановить показ экрана' : 'Показать экран'} onClick={mediaControls.toggleScreen}><MonitorUp/></button>}
    </div>}
    </div>

  </div>;
}

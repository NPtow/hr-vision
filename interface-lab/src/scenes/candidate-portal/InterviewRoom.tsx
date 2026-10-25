import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { ArrowLeft, Camera, CameraOff, LoaderCircle, Mic, MicOff } from 'lucide-react';
import type { DailyCall, DailyParticipant } from '@daily-co/daily-js';
import { Button } from '../../components/ui/button';
import { CandidateApiError, candidateRequest, dateTimeLabel, type CallMaterials, type RoomCredentials } from './api';
import { CallConnectionError, dailyCallConfig, directCallUrl, releaseCall, waitForCall } from './call-connection';
import { CallDeviceAccess, useCallDeviceAccess } from '../../components/call-device-access';
import { MediaPanel } from './MediaPanel';
import { CandidateCallSurface, type CandidateRecordingState } from './CandidateCallSurface';
import { callInputTracks } from './call-media';
import { ZoomCallFrame } from '../../components/call/ZoomCallFrame';
import type { CallProvider, ZoomCredentials } from '../../components/call/call-provider';

type InterviewRoomProps = {
  title: string; start: string; completed: boolean; configured: boolean; callProvider?: CallProvider; applicationId?: string; meetingId?: string; guestToken?: string; onRefresh: () => Promise<void>; onBack: () => void; onExit?: () => void;
};

/** Choose the provider before mounting Daily's device or connection hooks. */
export function InterviewRoom(props: InterviewRoomProps) {
  return props.callProvider === 'zoom' ? <ZoomInterviewRoom {...props}/> : <DailyInterviewRoom {...props}/>;
}

function ZoomInterviewRoom({ title, start, completed, configured, applicationId, meetingId, guestToken, onRefresh, onBack, onExit = onBack }: InterviewRoomProps) {
  const host = useRef<HTMLElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const alive = useRef(true);
  const attempt = useRef(0);
  const [credentials, setCredentials] = useState<ZoomCredentials | null>(null);
  const [opened, setOpened] = useState(false);
  const [joining, setJoining] = useState(false);
  const [consent, setConsent] = useState(false);
  const [error, setError] = useState('');
  const close = () => {
    attempt.current++;
    setCredentials(null); setOpened(false); setJoining(false);
  };
  useEffect(() => { alive.current = true; return () => { alive.current = false; attempt.current++; }; }, []);
  useEffect(() => {
    if (completed) {
      close();
      // Only a call already open in this tab exits automatically. Opening a
      // completed interview from the cabinet must still show its materials.
      if (opened) onExit();
    }
  }, [completed]);
  useEffect(() => {
    if (!opened) return;
    const shell = host.current?.closest('.cp-root');
    const previous = document.body.style.overflow;
    shell?.setAttribute('inert', '');
    document.body.style.overflow = 'hidden';
    return () => { shell?.removeAttribute('inert'); document.body.style.overflow = previous; if (returnFocus.current?.isConnected) returnFocus.current.focus(); };
  }, [opened]);
  async function join() {
    if (opened || joining || !configured || !consent) return;
    const id = ++attempt.current;
    returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setError(''); setJoining(true); setOpened(true);
    try {
      const room = guestToken
        ? await candidateRequest<RoomCredentials>('guest/join', { token: guestToken, consent })
        : await candidateRequest<RoomCredentials>(applicationId ? 'meeting/join' : 'interview/join', { ...(applicationId ? { applicationId, meetingId } : {}), consent });
      if (!alive.current || attempt.current !== id) return;
      if (room.provider !== 'zoom') {
        void onRefresh().catch(() => undefined);
        throw new CallConnectionError('Сервис встречи изменился. Подключитесь ещё раз.');
      }
      setCredentials(room);
    } catch (cause) {
      if (alive.current && attempt.current === id) {
        setError(cause instanceof CandidateApiError || cause instanceof CallConnectionError ? cause.message : 'Не удалось подключиться. Попробуйте ещё раз.');
        close();
      }
    }
  }
  if (completed) return <section className="cp-content-page">
    {!guestToken && <Button className="cp-back" variant="ghost" onClick={onBack}><ArrowLeft size={16}/>В кабинет</Button>}
    <div className="cp-page-heading"><h1>Встреча завершена</h1><p>{title} · {dateTimeLabel(start)}</p></div>
    {guestToken ? <p>Спасибо за разговор. Рекрутер пришлёт дальнейшие шаги.</p>
      : applicationId && !meetingId ? <p className="cp-error" role="alert">Не удалось определить встречу. Откройте её заново из вакансии.</p>
        : <MediaPanel identity={applicationId ? `meeting:${applicationId}:${meetingId}` : `interview:${start}`} load={() => candidateRequest<CallMaterials>(applicationId ? 'meeting/materials' : 'interview/materials', applicationId ? { applicationId, meetingId } : {})}/>}
  </section>;
  return <section className="cp-interview-page" ref={host}>
    <div className="cp-call-settings">
      <div className="cp-page-heading"><h1>{title}</h1><p>{dateTimeLabel(start)}</p></div>
      <label className="cp-check"><input type="checkbox" checked={consent} onChange={event => setConsent(event.target.checked)}/><span>Согласен на запись встречи и сохранение видео в сервисе.</span></label>
      {error && <p className="cp-error" role="alert">{error}</p>}
      {!configured && <p className="cp-notice">Видеовстреча пока недоступна. Свяжитесь с рекрутером; выбранное время сохранено.</p>}
      <Button className="cp-join-button" disabled={!consent || !configured || joining || opened} onClick={() => void join()}>{joining ? 'Подключаем…' : error ? 'Попробовать ещё раз' : 'Присоединиться'}</Button>
    </div>
    {opened && createPortal(<div className="agency-product agency-product-theme cp-call-overlay" role="dialog" aria-modal="true" aria-label={title}>
      <div className="cp-call-stage">{credentials ? <ZoomCallFrame credentials={credentials}
        onJoined={() => { setJoining(false); void onRefresh().catch(() => undefined); }}
        onLeft={() => { close(); void onRefresh().catch(() => undefined); onExit(); }}
        onError={message => { setJoining(false); setError(message); }}/>
        : <div className="md-zoom-after-call"><p role="status">Подключаемся…</p><Button variant="secondary" onClick={close}>Отменить</Button></div>}</div>
      {error && <div className="md-zoom-recovery"><Button variant="secondary" onClick={() => { close(); void onRefresh().catch(() => undefined); }}>Вернуться к подключению</Button></div>}
    </div>, document.body)}
  </section>;
}

function DailyInterviewRoom({ title, start, completed, configured, applicationId, meetingId, guestToken, onRefresh, onBack }: InterviewRoomProps) {
  const host = useRef<HTMLElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const preview = useRef<HTMLVideoElement>(null);
  const devices = useCallDeviceAccess();
  const inputTracks = useRef<MediaStreamTrack[]>([]);
  const frame = useRef<DailyCall | null>(null);
  const alive = useRef(true);
  const attempt = useRef(0);
  const connection = useRef<AbortController | null>(null);
  const cleanup = useRef<Promise<void> | null>(null);
  const [directUrl, setDirectUrl] = useState('');
  const [camera, setCamera] = useState(true);
  const [microphone, setMicrophone] = useState(true);
  const checking = devices.checking;
  const previewReady = !!devices.stream;
  const [opened, setOpened] = useState(false);
  const [joining, setJoining] = useState(false);
  const [joined, setJoined] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [closing, setClosing] = useState(false);
  const [reloadRequired, setReloadRequired] = useState(false);
  const [otherPresent, setOtherPresent] = useState(false);
  const [participants, setParticipants] = useState<DailyParticipant[]>([]);
  const [reconnecting, setReconnecting] = useState(false);
  const [recording, setRecording] = useState<CandidateRecordingState>('unknown');
  const [consent, setConsent] = useState(false);
  const [error, setError] = useState('');
  const stopPreview = () => { devices.stop(); if (preview.current) preview.current.srcObject = null; };
  const close = async () => {
    attempt.current++;
    connection.current?.abort(); connection.current = null;
    inputTracks.current.forEach(track => track.stop()); inputTracks.current = [];
    if (cleanup.current) return cleanup.current;
    const previous = frame.current; frame.current = null;
    if (alive.current) { setClosing(Boolean(previous)); setJoining(false); setJoined(false); }
    // Require a reload if Daily cannot release its call object. Never create a
    // second connection while the first one may still hold the devices.
    const pending = (previous ? releaseCall(previous) : Promise.resolve())
      .catch(() => { if (alive.current) { setReloadRequired(true); setError(''); } })
      .finally(() => {
        cleanup.current = null;
        if (alive.current) { setOpened(false); setClosing(false); setLoaded(false); setParticipants([]); }
      });
    cleanup.current = pending;
    return pending;
  };
  useEffect(() => { alive.current = true; return () => { alive.current = false; stopPreview(); void close(); }; }, []);
  useEffect(() => { if (completed) { stopPreview(); void close(); } }, [completed]);
  useEffect(() => {
    if (!opened) return;
    const shell = host.current?.closest('.cp-root');
    const previous = document.body.style.overflow;
    shell?.setAttribute('inert', '');
    document.body.style.overflow = 'hidden';
    return () => { shell?.removeAttribute('inert'); document.body.style.overflow = previous; if (returnFocus.current?.isConnected) returnFocus.current.focus(); };
  }, [opened]);
  useEffect(() => {
    if (!preview.current) return;
    preview.current.srcObject = devices.stream;
    if (devices.stream) void preview.current.play().catch(() => undefined);
  }, [devices.stream]);
  async function join(selected = { camera, microphone }) {
    if (joining || opened || closing || reloadRequired || cleanup.current || !configured || (!guestToken && !consent)) return;
    const currentAttempt = ++attempt.current;
    const controller = new AbortController(); connection.current = controller;
    let timeout: ReturnType<typeof setTimeout> | undefined;
    let callJoined = false;
    const deadline = (message: string) => {
      clearTimeout(timeout);
      timeout = setTimeout(() => controller.abort(new CallConnectionError(message)), 30000);
    };
    deadline('Сервер не выдал доступ к встрече. Попробуйте ещё раз.');
    setError(''); setDirectUrl(''); setJoining(true); setJoined(false); setLoaded(false); setOtherPresent(false); setParticipants([]); setReconnecting(false); setRecording('unknown');
    returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setOpened(true);
    try {
      // The browser prompt belongs to our origin. Keep the granted capture
      // source instead of stopping it and asking an embedded service again.
      const inputs = callInputTracks(devices.stream, selected);
      inputTracks.current = inputs.owned;
      stopPreview();
      const credentialsRequest = guestToken
        ? candidateRequest<RoomCredentials>('guest/join', { token: guestToken })
        : candidateRequest<RoomCredentials>(applicationId ? 'meeting/join' : 'interview/join', { ...(applicationId ? { applicationId, meetingId } : {}), consent });
      // Preserve a fallback as soon as credentials arrive, even if the SDK chunk fails.
      const access = credentialsRequest.then(credentials => {
        if (credentials.provider === 'zoom') {
          void onRefresh().catch(() => undefined);
          throw new CallConnectionError('Сервис встречи изменился. Подключитесь ещё раз.');
        }
        if (alive.current && attempt.current === currentAttempt && !controller.signal.aborted) {
          setDirectUrl(directCallUrl(credentials));
          deadline('Не удалось загрузить видеосервис. Попробуйте открыть комнату отдельно.');
        }
        return credentials;
      });
      // Do not cancel the access request when a cached/chunk load error arrives
      // first: its successful response is needed for the separate-room fallback.
      const sdk = import('@daily-co/daily-js').catch(() => null);
      const [credentials, sdkModule] = await waitForCall(Promise.all([access, sdk]), controller.signal);
      if (!sdkModule) throw new CallConnectionError('Не удалось загрузить видеосервис. Попробуйте открыть комнату отдельно.');
      const { default: DailyIframe } = sdkModule;
      if (!alive.current || attempt.current !== currentAttempt || controller.signal.aborted) return;
      const previousCall = DailyIframe.getCallInstance();
      if (previousCall && !previousCall.isDestroyed()) {
        setReloadRequired(true);
        throw new CallConnectionError('');
      }
      // Native tracks power the same UI as the recruiter call. Candidate tokens
      // and all access checks stay unchanged; no owner/capture commands run here.
      const call = DailyIframe.createCallObject({
          dailyConfig: dailyCallConfig(),
        ...(inputs.audioSource ? { audioSource: inputs.audioSource } : {}),
        ...(inputs.videoSource ? { videoSource: inputs.videoSource } : {}),
        startVideoOff: !selected.camera, startAudioOff: !selected.microphone,
      });
      frame.current = call;
      deadline('Видеокомната не загрузилась. Попробуйте открыть её отдельно.');
      const active = () => alive.current && attempt.current === currentAttempt && !controller.signal.aborted && frame.current === call;
      const presence = () => {
        if (!active()) return;
        const people = Object.values(call.participants());
        setParticipants(people);
        setOtherPresent(people.some(person => !person.local));
      };
      call.on('loaded', () => { if (active()) { setLoaded(true); if (!callJoined) deadline('Не удалось соединиться с видеосервисом. Проверьте сеть или откройте комнату отдельно.'); } });
      call.on('joined-meeting', () => { if (active()) { callJoined = true; clearTimeout(timeout); setLoaded(true); setJoined(true); setJoining(false); presence(); void onRefresh().catch(() => undefined); } });
      call.on('participant-joined', presence);
      call.on('participant-left', presence);
      call.on('participant-updated', presence);
      call.on('track-started', presence);
      call.on('track-stopped', presence);
      call.on('recording-started', () => { if (active()) setRecording('recording'); });
      call.on('recording-stopped', () => { if (active()) setRecording('stopped'); });
      call.on('recording-error', () => { if (active()) setRecording('error'); });
      call.on('network-connection', event => {
        if (!active()) return;
        if (event?.event === 'interrupted') { setReconnecting(true); setRecording('unknown'); }
        if (event?.event === 'connected') { setReconnecting(false); presence(); }
      });
      call.on('camera-error', () => { if (active()) setError('Не удалось включить устройство. Разрешите камеру и микрофон в настройках сайта и повторите включение.'); });
      call.on('nonfatal-error', event => {
        if (active() && event?.type === 'screen-share-error') setError('Не удалось показать экран. Повторите выбор окна.');
      });
      call.on('error', () => { if (active()) {
        setRecording('unknown');
        const failure = new CallConnectionError('Соединение с видеосервисом прервалось. Попробуйте ещё раз или откройте комнату отдельно.');
        setError(failure.message);
        controller.abort(failure);
        // Also clean up terminal failures after join() has already resolved.
        if (callJoined) void close();
      } });
      call.on('left-meeting', () => { if (active()) {
        // Explicit exit aborts this attempt before destroying the call. An
        // active callback here is an unexpected disconnect, not a user exit.
        setError('Подключение к встрече завершилось. Попробуйте подключиться ещё раз.');
        void close(); void onRefresh().catch(() => undefined);
      } });
      await waitForCall(call.join({ url: credentials.url, token: credentials.token, startVideoOff: !selected.camera, startAudioOff: !selected.microphone }), controller.signal);
    } catch (cause) {
      if (alive.current && attempt.current === currentAttempt) {
        stopPreview();
        setError(cause instanceof CandidateApiError || cause instanceof CallConnectionError ? cause.message : 'Не удалось подключиться. Попробуйте ещё раз.');
        await close();
      }
    } finally { clearTimeout(timeout); if (alive.current && attempt.current === currentAttempt) setJoining(false); }
  }
  function control(kind: 'microphone' | 'camera' | 'screen') {
    const call = frame.current;
    if (!call || call.isDestroyed() || !joined || closing) return;
    setError('');
    try {
      // A person can enter without devices and enable them later. Keep the
      // browser permission prompt visible instead of hiding it in fullscreen.
      const enablingDevice = kind === 'microphone' && !call.localAudio() || kind === 'camera' && !call.localVideo();
      if (enablingDevice && document.fullscreenElement) void document.exitFullscreen().catch(() => undefined);
      if (kind === 'microphone') call.setLocalAudio(!call.localAudio());
      else if (kind === 'camera') call.setLocalVideo(!call.localVideo());
      else if (call.participants().local?.screen) call.stopScreenShare();
      else call.startScreenShare();
    } catch {
      setError(kind === 'screen' ? 'Не удалось показать экран. Повторите выбор окна.' : 'Не удалось переключить устройство. Проверьте доступ в браузере.');
    }
  }
  if (completed) return <section className="cp-content-page">
    {!guestToken && <Button className="cp-back" variant="ghost" onClick={onBack}><ArrowLeft size={16}/>В кабинет</Button>}
    <div className="cp-page-heading"><h1>Встреча завершена</h1><p>{title} · {dateTimeLabel(start)}</p></div>
    {guestToken ? <p>Спасибо за разговор. Рекрутер пришлёт дальнейшие шаги.</p>
      : applicationId && !meetingId ? <p className="cp-error" role="alert">Не удалось определить встречу. Откройте её заново из вакансии.</p>
        : <MediaPanel identity={applicationId ? `meeting:${applicationId}:${meetingId}` : `interview:${start}`} load={() => candidateRequest<CallMaterials>(applicationId ? 'meeting/materials' : 'interview/materials', applicationId ? { applicationId, meetingId } : {})}/>}
  </section>;
  return <section className="cp-interview-page" ref={host}>
    <div className="cp-precall">
      <div className="cp-preview">
        <video ref={preview} autoPlay muted playsInline aria-label="Предпросмотр камеры" className={previewReady && camera ? 'is-visible' : ''}/>
        {(!previewReady || !camera) && <div className="cp-preview-placeholder" role="status" aria-label={checking ? 'Подключаем устройства' : 'Камера выключена'}>{checking ? <LoaderCircle size={30} className="cp-device-loading"/> : <CameraOff size={34}/>}</div>}
        <div className="cp-preview-controls">
          <button type="button" className={`cp-device-toggle${microphone ? '' : ' is-muted'}`} aria-label={microphone ? 'Выключить микрофон' : 'Включить микрофон'} title={microphone ? 'Выключить микрофон' : 'Включить микрофон'} aria-pressed={microphone} disabled={joining} onClick={() => { setMicrophone(!microphone); void devices.request({ audio: !microphone, video: camera }); }}>{microphone ? <Mic size={21}/> : <MicOff size={21}/>}</button>
          <button type="button" className={`cp-device-toggle${camera ? '' : ' is-muted'}`} aria-label={camera ? 'Выключить камеру' : 'Включить камеру'} title={camera ? 'Выключить камеру' : 'Включить камеру'} aria-pressed={camera} disabled={joining} onClick={() => { setCamera(!camera); void devices.request({ audio: microphone, video: !camera }); }}>{camera ? <Camera size={21}/> : <CameraOff size={21}/>}</button>
        </div>
      </div>
      <div className="cp-call-settings">
        <div className="cp-page-heading"><h1>{title}</h1><p>{dateTimeLabel(start)}</p></div>
        {guestToken ? <p className="cp-muted">Запись начнётся после вашего согласия рекрутеру.</p> : <label className="cp-check"><input type="checkbox" checked={consent} onChange={event => setConsent(event.target.checked)}/><span>Согласен на запись и расшифровку встречи.</span></label>}
        {!previewReady && (camera || microphone) && configured && !joining && !closing && !reloadRequired && <CallDeviceAccess
          checking={checking} error={devices.error} onRequest={() => void devices.request({ audio: microphone, video: camera })}
          onAudioOnly={() => { setCamera(false); setMicrophone(true); void devices.request({ audio: true, video: false }); }}
          skipDisabled={!guestToken && !consent}
          onSkip={() => { setCamera(false); setMicrophone(false); void join({ camera: false, microphone: false }); }}/>}
        {error && <p className="cp-error" role="alert">{error}</p>}
        {!configured && <p className="cp-notice">Видеовстреча пока недоступна. Свяжитесь с рекрутером; выбранное время сохранено.</p>}
        {reloadRequired ? <><p className="cp-error" role="alert">Видеокомната не отвечает. Обновите страницу, чтобы подключиться заново.</p><Button onClick={() => window.location.reload()}>Обновить страницу</Button></>
          : (previewReady || (!camera && !microphone) || joining || closing || !configured) && <Button className="cp-join-button" disabled={(!guestToken && !consent) || !configured || joining || checking || closing} onClick={() => void join()}>{joining ? 'Подключаем…' : closing ? 'Закрываем…' : error ? 'Попробовать ещё раз' : 'Присоединиться'}</Button>}
        {(error || reloadRequired) && directUrl && !joining && !closing && <p><a href={directUrl} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">Открыть комнату отдельно</a></p>}
      </div>
    </div>
    {opened && createPortal(<CandidateCallSurface title={title} participants={participants} joined={joined} closing={closing}
      status={closing ? 'Выходим…' : !loaded ? 'Подключаемся…' : !joined ? 'Соединяем…' : reconnecting ? 'Восстанавливаем соединение…' : otherPresent ? 'Вы на встрече' : 'Вы подключены'}
      recording={recording} error={error} onMic={() => control('microphone')} onCamera={() => control('camera')} onScreen={() => control('screen')}
      onLeave={() => { void close().then(() => onRefresh()).catch(() => undefined); }}/>, document.body)}
  </section>;
}

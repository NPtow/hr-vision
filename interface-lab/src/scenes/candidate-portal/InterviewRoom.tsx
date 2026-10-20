import { useEffect, useRef, useState } from 'react';
import { createPortal, flushSync } from 'react-dom';
import { ArrowLeft, Camera, CameraOff, LoaderCircle, Mic, MicOff, PhoneOff } from 'lucide-react';
import type { DailyCall } from '@daily-co/daily-js';
import { Button } from '../../components/ui/button';
import { CandidateApiError, candidateRequest, dateTimeLabel, type CallMaterials, type RoomCredentials } from './api';
import { CallConnectionError, directCallUrl, releaseCall, waitForCall } from './call-connection';
import { CallDeviceAccess, useCallDeviceAccess } from '../../components/call-device-access';
import { MediaPanel } from './MediaPanel';

export function InterviewRoom({ title, start, completed, configured, applicationId, meetingId, guestToken, onRefresh, onBack }: {
  title: string; start: string; completed: boolean; configured: boolean; applicationId?: string; meetingId?: string; guestToken?: string; onRefresh: () => Promise<void>; onBack: () => void;
}) {
  const host = useRef<HTMLElement>(null);
  const mount = useRef<HTMLDivElement>(null);
  const preview = useRef<HTMLVideoElement>(null);
  const devices = useCallDeviceAccess();
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
  const [recording, setRecording] = useState<'unknown' | 'recording' | 'stopped' | 'error'>('unknown');
  const [consent, setConsent] = useState(false);
  const [error, setError] = useState('');
  const stopPreview = () => { devices.stop(); if (preview.current) preview.current.srcObject = null; };
  const close = async () => {
    attempt.current++;
    connection.current?.abort(); connection.current = null;
    if (cleanup.current) return cleanup.current;
    const previous = frame.current; frame.current = null;
    if (alive.current) { setClosing(Boolean(previous)); setJoining(false); setJoined(false); }
    // Keep the iframe mounted while Daily acknowledges leave. If it cannot,
    // return to the page and require a reload instead of creating a second call.
    const pending = (previous ? releaseCall(previous) : Promise.resolve())
      .catch(() => { if (alive.current) { setReloadRequired(true); setError(''); } })
      .finally(() => {
        cleanup.current = null;
        if (alive.current) { setOpened(false); setClosing(false); setLoaded(false); }
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
    return () => { shell?.removeAttribute('inert'); document.body.style.overflow = previous; };
  }, [opened]);
  useEffect(() => {
    if (!preview.current) return;
    preview.current.srcObject = devices.stream;
    if (devices.stream) void preview.current.play().catch(() => undefined);
  }, [devices.stream]);
  async function join(devices = { camera, microphone }) {
    if (joining || closing || reloadRequired || cleanup.current) return;
    const currentAttempt = ++attempt.current;
    const controller = new AbortController(); connection.current = controller;
    let timeout: ReturnType<typeof setTimeout> | undefined;
    let callJoined = false;
    const deadline = (message: string) => {
      clearTimeout(timeout);
      timeout = setTimeout(() => controller.abort(new CallConnectionError(message)), 30000);
    };
    deadline('Сервер не выдал доступ к встрече. Попробуйте ещё раз.');
    setError(''); setDirectUrl(''); setJoining(true); setJoined(false); setLoaded(false); setOtherPresent(false); setRecording('unknown'); stopPreview();
    try {
      const credentialsRequest = guestToken
        ? candidateRequest<RoomCredentials>('guest/join', { token: guestToken })
        : candidateRequest<RoomCredentials>(applicationId ? 'meeting/join' : 'interview/join', { ...(applicationId ? { applicationId, meetingId } : {}), consent });
      // Preserve a fallback as soon as credentials arrive, even if the SDK chunk fails.
      const access = credentialsRequest.then(credentials => {
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
      // Daily requires a committed DOM node. A single animation frame does not
      // guarantee that React has mounted a conditional portal (notably in Safari).
      flushSync(() => setOpened(true));
      if (!mount.current) throw new CallConnectionError('Не удалось открыть видеокомнату. Попробуйте ещё раз.');
      const call = DailyIframe.createFrame(mount.current, { iframeStyle: { width: '100%', height: '100%', border: '0' }, showLeaveButton: false, showFullscreenButton: false, showUserNameChangeUI: false });
      frame.current = call;
      deadline('Видеокомната не загрузилась. Попробуйте открыть её отдельно.');
      call.iframe()?.setAttribute('title', title);
      const active = () => alive.current && attempt.current === currentAttempt && !controller.signal.aborted && frame.current === call;
      const presence = () => { if (active()) setOtherPresent(Object.values(call.participants()).some(person => !person.local)); };
      // Loaded Daily may be waiting at prejoin for a human/permission prompt.
      // That wait is not a network failure; keep its controls and an exit visible.
      call.on('loaded', () => { if (active()) { setLoaded(true); clearTimeout(timeout); } });
      call.on('joined-meeting', () => { if (active()) { callJoined = true; clearTimeout(timeout); setLoaded(true); setJoined(true); setJoining(false); presence(); void onRefresh(); } });
      call.on('participant-joined', presence);
      call.on('participant-left', presence);
      call.on('recording-started', () => { if (active()) setRecording('recording'); });
      call.on('recording-stopped', () => { if (active()) setRecording('stopped'); });
      call.on('recording-error', () => { if (active()) setRecording('error'); });
      call.on('network-connection', event => { if (active() && event?.event === 'interrupted') setRecording('unknown'); });
      call.on('camera-error', () => { if (active()) setError('Не удалось включить камеру или микрофон. Проверьте доступ к устройствам.'); });
      call.on('error', () => { if (active()) {
        setRecording('unknown');
        const failure = new CallConnectionError('Соединение с видеосервисом прервалось. Попробуйте ещё раз или откройте комнату отдельно.');
        setError(failure.message);
        controller.abort(failure);
        // Also clean up terminal failures after join() has already resolved.
        if (callJoined) void close();
      } });
      call.on('left-meeting', () => { if (active()) { void close(); void onRefresh(); } });
      await waitForCall(call.join({ url: credentials.url, token: credentials.token, startVideoOff: !devices.camera, startAudioOff: !devices.microphone }), controller.signal);
    } catch (cause) {
      if (alive.current && attempt.current === currentAttempt) {
        setError(cause instanceof CandidateApiError || cause instanceof CallConnectionError ? cause.message : 'Не удалось подключиться. Попробуйте ещё раз.');
        await close();
      }
    } finally { clearTimeout(timeout); if (alive.current && attempt.current === currentAttempt) setJoining(false); }
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
          skipDisabled={!guestToken && !consent}
          onSkip={() => { setCamera(false); setMicrophone(false); void join({ camera: false, microphone: false }); }}/>}
        {error && <p className="cp-error" role="alert">{error}</p>}
        {!configured && <p className="cp-notice">Видеовстреча пока недоступна. Свяжитесь с рекрутером; выбранное время сохранено.</p>}
        {reloadRequired ? <><p className="cp-error" role="alert">Видеокомната не отвечает. Обновите страницу, чтобы подключиться заново.</p><Button onClick={() => window.location.reload()}>Обновить страницу</Button></>
          : (previewReady || (!camera && !microphone) || joining || closing || !configured) && <Button className="cp-join-button" disabled={(!guestToken && !consent) || !configured || joining || checking || closing} onClick={() => void join()}>{joining ? 'Подключаем…' : closing ? 'Закрываем…' : error ? 'Попробовать ещё раз' : 'Присоединиться'}</Button>}
        {(error || reloadRequired) && directUrl && !joining && !closing && <p><a href={directUrl} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">Открыть комнату отдельно</a></p>}
      </div>
    </div>
    {opened && createPortal(<div className="agency-product-theme cp-root cp-call-overlay" role="dialog" aria-modal="true" aria-label={title}>
      <div className="cp-call-bar"><div><strong>{title}</strong><span role="status">{closing ? 'Закрываем…' : !loaded ? 'Загружаем комнату…' : !joined ? 'Подключение…' : otherPresent ? 'Вы в эфире' : 'Ждём собеседника'}{joined && (recording === 'recording' ? ' · Запись идёт' : recording === 'stopped' ? ' · Запись остановлена' : recording === 'error' ? ' · Запись недоступна' : ' · Статус записи уточняется')}</span></div><Button autoFocus variant="secondary" disabled={closing} onClick={async () => { await close(); await onRefresh(); }}><PhoneOff size={17}/>Выйти</Button></div>
      {error && <p role="alert" className="cp-call-error">{error}</p>}
      {loaded && !joined && !closing && <p role="status" className="cp-call-error">В окне видеосвязи разрешите устройства и нажмите «Присоединиться». Если запроса нет, проверьте доступ к камере и микрофону в настройках сайта. {directUrl && <a href={directUrl} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">Открыть комнату отдельно</a>}</p>}
      <div className="cp-call-stage"><div ref={mount} className="cp-daily-mount"/>{!loaded && <div className="cp-call-loading" aria-hidden="true"><LoaderCircle size={28} className="cp-device-loading"/></div>}</div>
    </div>, document.body)}
  </section>;
}

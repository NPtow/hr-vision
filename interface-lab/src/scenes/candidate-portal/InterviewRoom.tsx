import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { ArrowLeft, Camera, CameraOff, LoaderCircle, Mic, MicOff, PhoneOff } from 'lucide-react';
import type { DailyCall } from '@daily-co/daily-js';
import { Button } from '../../components/ui/button';
import { candidateRequest, dateTimeLabel, type CallMaterials, type RoomCredentials } from './api';
import { MediaPanel } from './MediaPanel';

export function InterviewRoom({ title, start, completed, configured, applicationId, meetingId, guestToken, onRefresh, onBack }: {
  title: string; start: string; completed: boolean; configured: boolean; applicationId?: string; meetingId?: string; guestToken?: string; onRefresh: () => Promise<void>; onBack: () => void;
}) {
  const host = useRef<HTMLElement>(null);
  const mount = useRef<HTMLDivElement>(null);
  const preview = useRef<HTMLVideoElement>(null);
  const stream = useRef<MediaStream | null>(null);
  const frame = useRef<DailyCall | null>(null);
  const alive = useRef(true);
  const attempt = useRef(0);
  const [camera, setCamera] = useState(true);
  const [microphone, setMicrophone] = useState(true);
  const [deviceAttempt, setDeviceAttempt] = useState(0);
  const [deviceError, setDeviceError] = useState('');
  const [checking, setChecking] = useState(false);
  const [previewReady, setPreviewReady] = useState(false);
  const [opened, setOpened] = useState(false);
  const [joining, setJoining] = useState(false);
  const [joined, setJoined] = useState(false);
  const [otherPresent, setOtherPresent] = useState(false);
  const [recording, setRecording] = useState<'unknown' | 'recording' | 'stopped' | 'error'>('unknown');
  const [consent, setConsent] = useState(false);
  const [error, setError] = useState('');
  const stopPreview = () => { stream.current?.getTracks().forEach(track => track.stop()); stream.current = null; if (preview.current) preview.current.srcObject = null; };
  const close = async () => {
    attempt.current++;
    const previous = frame.current; frame.current = null;
    await previous?.destroy();
    if (alive.current) { setOpened(false); setJoining(false); setJoined(false); }
  };
  useEffect(() => { alive.current = true; return () => { alive.current = false; attempt.current++; stopPreview(); const previous = frame.current; frame.current = null; void previous?.destroy(); }; }, []);
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
    let cancelled = false;
    stopPreview(); setPreviewReady(false); setDeviceError('');
    if (completed || !configured || opened || joining || (!camera && !microphone)) {
      setChecking(false);
      return;
    }
    setChecking(true);
    void (async () => {
      try {
        if (!navigator.mediaDevices?.getUserMedia) throw new Error('Устройства недоступны.');
        const media = await navigator.mediaDevices.getUserMedia({ audio: microphone, video: camera });
        if (cancelled || !alive.current) { media.getTracks().forEach(track => track.stop()); return; }
        stream.current = media;
        if (preview.current) { preview.current.srcObject = media; await preview.current.play().catch(() => undefined); }
        if (!cancelled && alive.current) setPreviewReady(true);
      } catch (cause) {
        if (!cancelled && alive.current) setDeviceError(cause instanceof Error && cause.name === 'NotAllowedError'
          ? 'Разрешите доступ к камере и микрофону в браузере.'
          : cause instanceof Error && cause.name === 'NotFoundError'
            ? 'Устройство не найдено. Проверьте подключение или выключите его ниже.'
            : 'Не удалось включить устройства. Попробуйте ещё раз.');
      } finally { if (!cancelled && alive.current) setChecking(false); }
    })();
    return () => { cancelled = true; stopPreview(); };
  }, [camera, microphone, deviceAttempt, completed, configured, opened, joining]);
  async function join() {
    const currentAttempt = ++attempt.current;
    setError(''); setJoining(true); setRecording('unknown'); stopPreview(); setPreviewReady(false);
    try {
      const credentials = guestToken
        ? await candidateRequest<RoomCredentials>('guest/join', { token: guestToken })
        : await candidateRequest<RoomCredentials>(applicationId ? 'meeting/join' : 'interview/join', { ...(applicationId ? { applicationId, meetingId } : {}), consent });
      const { default: DailyIframe } = await import('@daily-co/daily-js');
      if (!alive.current || attempt.current !== currentAttempt) return;
      setOpened(true);
      await new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
      if (!mount.current || !alive.current || attempt.current !== currentAttempt) return;
      const call = DailyIframe.createFrame(mount.current, { iframeStyle: { width: '100%', height: '100%', border: '0' }, showLeaveButton: false, showFullscreenButton: false, showUserNameChangeUI: false });
      frame.current = call;
      call.iframe()?.setAttribute('title', title);
      const active = () => alive.current && frame.current === call;
      const presence = () => { if (active()) setOtherPresent(Object.values(call.participants()).some(person => !person.local)); };
      call.on('joined-meeting', () => { if (active()) { setJoined(true); setJoining(false); presence(); void onRefresh(); } });
      call.on('participant-joined', presence);
      call.on('participant-left', presence);
      call.on('recording-started', () => { if (active()) setRecording('recording'); });
      call.on('recording-stopped', () => { if (active()) setRecording('stopped'); });
      call.on('recording-error', () => { if (active()) setRecording('error'); });
      call.on('network-connection', event => { if (active() && event?.event === 'interrupted') setRecording('unknown'); });
      call.on('camera-error', () => { if (active()) setError('Не удалось включить камеру или микрофон. Проверьте доступ к устройствам.'); });
      call.on('error', () => { if (active()) { setRecording('unknown'); setError('Соединение прервалось. Проверьте сеть; после выхода можно подключиться снова.'); } });
      call.on('left-meeting', () => { if (active()) { void close(); void onRefresh(); } });
      await call.join({ url: credentials.url, token: credentials.token, startVideoOff: !camera, startAudioOff: !microphone });
    } catch (cause) {
      if (alive.current && attempt.current === currentAttempt) { setError((cause as Error).message); await close(); }
    } finally { if (alive.current && attempt.current === currentAttempt) setJoining(false); }
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
        {(!previewReady || !camera) && <div className="cp-preview-placeholder" role="status" aria-label={checking ? 'Подключаем устройства' : camera ? 'Камера недоступна' : 'Камера выключена'}>{checking ? <LoaderCircle size={30} className="cp-device-loading"/> : <CameraOff size={34}/>}</div>}
        <div className="cp-preview-controls">
          <button type="button" className={`cp-device-toggle${microphone ? '' : ' is-muted'}`} aria-label={microphone ? 'Выключить микрофон' : 'Включить микрофон'} title={microphone ? 'Выключить микрофон' : 'Включить микрофон'} aria-pressed={microphone} disabled={joining} onClick={() => setMicrophone(value => !value)}>{microphone ? <Mic size={21}/> : <MicOff size={21}/>}</button>
          <button type="button" className={`cp-device-toggle${camera ? '' : ' is-muted'}`} aria-label={camera ? 'Выключить камеру' : 'Включить камеру'} title={camera ? 'Выключить камеру' : 'Включить камеру'} aria-pressed={camera} disabled={joining} onClick={() => setCamera(value => !value)}>{camera ? <Camera size={21}/> : <CameraOff size={21}/>}</button>
        </div>
      </div>
      <div className="cp-call-settings">
        <div className="cp-page-heading"><h1>{title}</h1><p>{dateTimeLabel(start)}</p></div>
        {guestToken ? <p className="cp-muted">Запись начнётся после вашего согласия рекрутеру.</p> : <label className="cp-check"><input type="checkbox" checked={consent} onChange={event => setConsent(event.target.checked)}/><span>Согласен на запись и расшифровку встречи.</span></label>}
        {deviceError && <div className="cp-device-error"><p className="cp-error" role="alert">{deviceError}</p><Button variant="ghost" disabled={checking || joining} onClick={() => setDeviceAttempt(value => value + 1)}>Повторить</Button></div>}
        {error && <p className="cp-error" role="alert">{error}</p>}
        {!configured && <p className="cp-notice">Видеовстреча пока недоступна. Свяжитесь с рекрутером; выбранное время сохранено.</p>}
        <Button className="cp-join-button" disabled={(!guestToken && !consent) || !configured || joining || checking} onClick={join}>{joining ? 'Подключаем…' : 'Присоединиться'}</Button>
      </div>
    </div>
    {opened && createPortal(<div className="agency-product-theme cp-root cp-call-overlay" role="dialog" aria-modal="true" aria-label={title}>
      <div className="cp-call-bar"><div><strong>{title}</strong><span role="status">{!joined ? 'Подключение…' : otherPresent ? 'Вы в эфире' : 'Ждём собеседника'}{recording === 'recording' ? ' · Запись идёт' : recording === 'stopped' ? ' · Запись остановлена' : recording === 'error' ? ' · Запись недоступна' : ' · Статус записи уточняется'}</span></div><Button autoFocus variant="secondary" onClick={async () => { await close(); await onRefresh(); }}><PhoneOff size={17}/>Выйти</Button></div>
      {error && <p role="alert" className="cp-call-error">{error}</p>}
      <div ref={mount} className="cp-daily-mount"/>
    </div>, document.body)}
  </section>;
}

import { useEffect, useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import { ArrowLeft, Camera, LoaderCircle, PhoneOff } from 'lucide-react';
import type { DailyCall } from '@daily-co/daily-js';
import { Button } from '../../components/ui/button';
import { CandidateApiError, candidateRequest, dateTimeLabel, type CallMaterials, type RoomCredentials } from './api';
import { CallConnectionError, directCallUrl, releaseCall, waitForCall } from './call-connection';
import { MediaPanel } from './MediaPanel';
import './interview-room.css';

export function InterviewRoom({ title, start, completed, configured, applicationId, meetingId, guestToken, onRefresh, onBack }: {
  title: string; start: string; completed: boolean; configured: boolean; applicationId?: string; meetingId?: string; guestToken?: string; onRefresh: () => Promise<void>; onBack: () => void;
}) {
  const stage = useRef<HTMLDivElement>(null);
  const mount = useRef<HTMLDivElement>(null);
  const frame = useRef<DailyCall | null>(null);
  const alive = useRef(true);
  const attempt = useRef(0);
  const connection = useRef<AbortController | null>(null);
  const cleanup = useRef<Promise<void> | null>(null);
  const [directUrl, setDirectUrl] = useState('');
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
  const close = async () => {
    attempt.current++;
    connection.current?.abort(); connection.current = null;
    if (cleanup.current) return cleanup.current;
    const previous = frame.current; frame.current = null;
    if (alive.current) { setClosing(Boolean(previous)); setJoining(false); }
    // Keep the iframe mounted while Daily acknowledges leave. If it cannot,
    // return to the page and require a reload instead of creating a second call.
    const pending = (previous ? releaseCall(previous) : Promise.resolve())
      .catch(() => { if (alive.current) { setReloadRequired(true); setError(''); } })
      .finally(() => {
        cleanup.current = null;
        if (alive.current) { setOpened(false); setJoined(false); setClosing(false); setLoaded(false); }
      });
    cleanup.current = pending;
    return pending;
  };
  useEffect(() => { alive.current = true; return () => { alive.current = false; void close(); }; }, []);
  useEffect(() => { if (completed) void close(); }, [completed]);
  useEffect(() => {
    if (!joined) return;
    // The native prejoin and live call share the same DOM node. Inert only its
    // siblings: making the surrounding cp-root inert would also disable Daily.
    const siblings: HTMLElement[] = [];
    let branch: HTMLElement | null = stage.current;
    while (branch?.parentElement && branch !== document.body) {
      for (const sibling of branch.parentElement.children) {
        if (sibling !== branch && sibling instanceof HTMLElement && !sibling.hasAttribute('inert')) {
          sibling.setAttribute('inert', '');
          siblings.push(sibling);
        }
      }
      branch = branch.parentElement;
    }
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { siblings.forEach(sibling => sibling.removeAttribute('inert')); document.body.style.overflow = previous; };
  }, [joined]);
  async function join() {
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
    setError(''); setDirectUrl(''); setJoining(true); setJoined(false); setLoaded(false); setOtherPresent(false); setRecording('unknown');
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
      // Commit the visible preparation frame before asking Daily to load it.
      // Its DOM parent stays unchanged when the joined state becomes fullscreen.
      flushSync(() => setOpened(true));
      if (!mount.current) throw new CallConnectionError('Не удалось открыть видеокомнату. Попробуйте ещё раз.');
      const call = DailyIframe.createFrame(mount.current, { iframeStyle: { width: '100%', height: '100%', border: '0' }, lang: 'ru', showLeaveButton: false, showFullscreenButton: false, showUserNameChangeUI: false });
      frame.current = call;
      deadline('Видеокомната не загрузилась. Попробуйте открыть её отдельно.');
      call.iframe()?.setAttribute('title', title);
      const active = () => alive.current && attempt.current === currentAttempt && !controller.signal.aborted && frame.current === call;
      const presence = () => { if (active()) setOtherPresent(Object.values(call.participants()).some(person => !person.local)); };
      // A loaded native prejoin can wait indefinitely for device permission and
      // the person's Join click. It is not a failed network connection.
      // Daily 0.93 emits joining-meeting before native prejoin is confirmed, so
      // that event cannot distinguish a slow connection from human preparation.
      call.on('loaded', () => { if (active()) { clearTimeout(timeout); setLoaded(true); } });
      call.on('joined-meeting', () => { if (active()) { callJoined = true; clearTimeout(timeout); setError(''); setLoaded(true); setJoined(true); setJoining(false); presence(); void onRefresh(); } });
      call.on('participant-joined', presence);
      call.on('participant-left', presence);
      call.on('recording-started', () => { if (active()) setRecording('recording'); });
      call.on('recording-stopped', () => { if (active()) setRecording('stopped'); });
      call.on('recording-error', () => { if (active()) setRecording('error'); });
      call.on('network-connection', event => { if (active() && event?.event === 'interrupted') setRecording('unknown'); });
      call.on('camera-error', () => { if (active()) setError('Проверьте доступ к камере и микрофону в браузере или выберите вход без них в окне встречи.'); });
      call.on('error', () => { if (active()) {
        setRecording('unknown');
        const failure = new CallConnectionError('Соединение с видеосервисом прервалось. Попробуйте ещё раз или откройте комнату отдельно.');
        setError(failure.message);
        controller.abort(failure);
        // Also clean up terminal failures after join() has already resolved.
        if (callJoined) void close();
      } });
      call.on('left-meeting', () => { if (active()) {
        // Explicit exit aborts this attempt before destroying the frame. An
        // active callback here is an unexpected disconnect, not a user exit.
        setError('Подключение к встрече завершилось. Попробуйте подключиться ещё раз.');
        void close(); void onRefresh();
      } });
      await waitForCall(call.join({ url: credentials.url, token: credentials.token }), controller.signal);
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
  return <section className="cp-interview-page">
    <div className="cp-precall">
      <div ref={stage} className={`cp-native-prejoin${joined ? ' is-joined' : ''}${opened ? ' is-open' : ''}`} role={joined ? 'dialog' : undefined} aria-modal={joined ? true : undefined} aria-label={joined ? title : 'Проверка камеры и микрофона'}>
        {!opened && <div className="cp-native-prejoin-placeholder"><Camera size={34} aria-hidden="true"/></div>}
        {opened && <div className="cp-call-bar"><div>{joined && <strong>{title}</strong>}<span role="status">{closing ? 'Закрываем…' : !loaded ? 'Загружаем комнату…' : !joined ? 'Проверка камеры и микрофона' : otherPresent ? 'Вы в эфире' : 'Ждём собеседника'}{joined && (recording === 'recording' ? ' · Запись идёт' : recording === 'stopped' ? ' · Запись остановлена' : recording === 'error' ? ' · Запись недоступна' : ' · Статус записи уточняется')}</span></div><Button variant="secondary" disabled={closing} onClick={async () => { await close(); await onRefresh(); }}><PhoneOff size={17}/>{joined ? 'Выйти' : 'Закрыть'}</Button></div>}
        {opened && error && <p role="alert" className="cp-call-error">{error}</p>}
        <div className="cp-call-stage" hidden={!opened}><div ref={mount} className="cp-daily-mount"/>{opened && !loaded && <div className="cp-call-loading" aria-hidden="true"><LoaderCircle size={28} className="cp-device-loading"/></div>}</div>
      </div>
      <div className="cp-call-settings">
        <div className="cp-page-heading"><h1>{title}</h1><p>{dateTimeLabel(start)}</p></div>
        {guestToken ? <p className="cp-muted">Запись начнётся после вашего согласия рекрутеру.</p> : <label className="cp-check"><input type="checkbox" checked={consent} disabled={opened || joining || closing} onChange={event => setConsent(event.target.checked)}/><span>Согласен на запись и расшифровку встречи.</span></label>}
        {!opened && error && <p className="cp-error" role="alert">{error}</p>}
        {!configured && <p className="cp-notice">Видеовстреча пока недоступна. Свяжитесь с рекрутером; выбранное время сохранено.</p>}
        {reloadRequired ? <><p className="cp-error" role="alert">Видеокомната не отвечает. Обновите страницу, чтобы подключиться заново.</p><Button onClick={() => window.location.reload()}>Обновить страницу</Button></>
          : !opened && <Button className="cp-join-button" disabled={(!guestToken && !consent) || !configured || joining || closing} onClick={() => void join()}>{joining ? 'Загружаем…' : closing ? 'Закрываем…' : error ? 'Попробовать ещё раз' : 'Проверить камеру и микрофон'}</Button>}
        {(error || reloadRequired) && directUrl && !closing && <p><a href={directUrl} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">Открыть комнату отдельно</a></p>}
      </div>
    </div>
  </section>;
}

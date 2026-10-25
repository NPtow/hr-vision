import { useEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import type { DailyCall } from '@daily-co/daily-js';
import { Dialog } from 'radix-ui';
import { Button } from '../../components/ui/button';
import { Check, PhoneOff, Video } from 'lucide-react';
import { dateTime, request, type Journey, type Person, type SharedState } from './api';
import { ZoomMeeting } from '../../components/call/ZoomMeeting';
import { startZoomSessionReconcile } from '../../components/call/zoom-session-reconcile';
import { CallProviderSelector } from '../../components/call/CallProviderSelector';
import type { CallCredentials } from '../../components/call/call-provider';
import './meeting-panel-compact.css';

type Turn = { id: string; text: string; seconds: number };
type MeetingPanelProps = { person: Person; journey: Journey; onComplete?: () => void; onCallChange: (open: boolean) => void; onReschedule?: () => void; compact?: boolean };
type ProviderPanelProps = MeetingPanelProps & { prejoinOpen: boolean; onPrejoinOpenChange: (open: boolean) => void; providerSelector?: ReactNode };

export function MeetingPanel(props: MeetingPanelProps) {
  const { person, journey } = props;
  const meeting = person.meeting!;
  const [prejoinOpen, setPrejoinOpen] = useState(false);
  const compact = !!props.compact && journey.state?.actor === 'manager' && ['pending', 'confirmed', 'live'].includes(meeting.status);
  useEffect(() => { setPrejoinOpen(false); }, [meeting.id]);
  useEffect(() => { if (!compact || !['confirmed', 'live'].includes(meeting.status)) setPrejoinOpen(false); }, [compact, meeting.status]);
  const providerSelector = journey.state?.mode === 'employer' && journey.state.actor === 'manager' && ['pending', 'confirmed', 'live'].includes(meeting.status) ? <CallProviderSelector
    value={meeting.callProvider || 'daily'} locked={meeting.callProviderLocked || meeting.status === 'live'} options={meeting.callProviderOptions}
    disabled={journey.busy} onChange={provider => { void journey.setCallProvider(person, provider); }}/> : undefined;
  const providerProps = { ...props, compact, prejoinOpen, onPrejoinOpenChange: setPrejoinOpen, providerSelector };
  return <>
    {!compact && providerSelector}
    {meeting.callProvider === 'zoom' ? <ZoomMeetingPanel key={`${meeting.id}:zoom`} {...providerProps}/> : <DailyMeetingPanel key={`${meeting.id}:daily`} {...providerProps}/>}
  </>;
}

function ZoomMeetingPanel({ person, journey, onComplete, onCallChange, onReschedule, compact, prejoinOpen, onPrejoinOpenChange, providerSelector }: ProviderPanelProps) {
  const { state, token, act, busy } = journey;
  const reconcile = useRef<(() => void) | null>(null);
  useEffect(() => () => reconcile.current?.(), []);
  const m = person.meeting!;
  const manager = state!.actor === 'manager';
  const configured = m.videoConfigured ?? state!.videoConfigured;
  const identity = { candidate: person.id, meetingId: m.id, generation: state!.generation };
  const hostIdentity = { ...identity, vacancyId: person.vacancyId || state!.vacancy.id, applicationId: person.applicationId };
  return <section className={`cj-meeting${compact ? ' cj-meeting-compact' : ''}`}>
    {compact || ['confirmed', 'live'].includes(m.status) ? <>
      <ZoomMeeting title={manager ? person.name : `${state!.vacancy.company} · ${state!.actorName}`} subtitle={dateTime(m.start)}
        unavailable={m.status === 'pending' ? 'Ждём подтверждения кандидата.' : !configured ? 'Видеовстреча пока недоступна.' : busy ? 'Сохраняем изменения встречи…' : undefined}
        renderEntry={compact ? ({ consent, onConsentChange, join, opened }) => <CompactMeetingEntry meeting={m}
          consent={consent} onConsentChange={onConsentChange} consentText="Согласен на запись встречи и сохранение видео в сервисе."
          opening={opened} busy={busy} configured={configured} onJoin={join} open={prejoinOpen} onOpenChange={onPrejoinOpenChange}>{providerSelector}</CompactMeetingEntry> : undefined}
        join={signal => request<CallCredentials>('room', token, { ...identity, consent: true }, signal)}
        onOpenChange={open => { if (open) reconcile.current?.(); onCallChange(open); }}
        onJoined={async () => { await request('action', token, { ...identity, action: 'meeting.joined' }); await journey.refresh(); }}
        onLeft={() => { void journey.refresh(); }}
        host={manager && state!.mode === 'employer' ? {
          connected: signal => request('employer/call-connected', token, hostIdentity, signal),
          left: () => {
            void journey.refresh(); reconcile.current?.();
            reconcile.current = startZoomSessionReconcile({
              request: signal => request('employer/call-reconcile', token, hostIdentity, signal),
              onCompleted: () => { void journey.refresh(); onComplete?.(); },
            });
          },
        } : undefined}/>
      {!compact && m.status === 'confirmed' && <Button variant="ghost" disabled={busy} onClick={async () => { if (manager) onReschedule?.(); else if (await act('meeting.cancel', person.id, { meetingId: m.id })) onReschedule?.(); }}>Нужно другое время</Button>}
    </> : <>
      <div className="cj-section-heading"><div><span className="cj-kicker">{manager ? person.name : state!.vacancy.company}</span><h1>{dateTime(m.start)}</h1></div></div>
      {m.status === 'pending' && <div className="cj-actions">{manager && <Button variant="outline" disabled={busy} onClick={onReschedule}>Перенести встречу</Button>}{!manager && <Button disabled={busy} onClick={() => act('meeting.confirm', person.id, { meetingId: m.id })}>Подтвердить встречу<Check size={15}/></Button>}<Button variant={manager ? 'outline' : 'ghost'} disabled={busy} onClick={async () => { if (await act('meeting.cancel', person.id, { meetingId: m.id })) onReschedule?.(); }}>{manager ? 'Отменить приглашение' : 'Нужно другое время'}</Button></div>}
      {m.status === 'cancelled' && <><p>Предыдущее время освобождено.</p><Button onClick={onReschedule}>{manager ? 'Выбрать новое время' : 'Изменить расписание'}</Button></>}
      {m.status === 'completed' && <><p>Встреча завершена</p>{manager && person.feedback && <Button onClick={onComplete}>Открыть разбор</Button>}</>}
    </>}
  </section>;
}

function DailyMeetingPanel({ person, journey, onComplete, onCallChange, onReschedule, compact, prejoinOpen, onPrejoinOpenChange, providerSelector }: ProviderPanelProps) {
  const { state, token, act, busy, setError } = journey;
  const m = person.meeting!;
  const manager = state!.actor === 'manager';
  const mount = useRef<HTMLDivElement>(null);
  const panel = useRef<HTMLElement>(null);
  const call = useRef<DailyCall | null>(null);
  const alive = useRef(true);
  const ending = useRef(false);
  const attempt = useRef(0);
  const turns = useRef<Turn[]>([]);
  const uploading = useRef<Promise<void> | null>(null);
  const [opening, setOpening] = useState(false);
  const [opened, setOpened] = useState(false);
  const [joined, setJoined] = useState(false);
  const [finishing, setFinishing] = useState(false);
  const [consent, setConsent] = useState(false);
  const [mediaStatus, setMediaStatus] = useState('');
  const [callError, setCallError] = useState('');
  const [remotePresent, setRemotePresent] = useState(false);
  const identity = { candidate: person.id, meetingId: m.id, generation: state!.generation };
  useEffect(() => { alive.current = true; return () => { alive.current = false; attempt.current++; void call.current?.destroy(); call.current = null; }; }, [m.id]);
  useEffect(() => {
    onCallChange(opened);
    if (!opened) return;
    const app = panel.current?.closest('.cj-app');
    const previous = document.body.style.overflow;
    app?.setAttribute('inert', ''); app?.setAttribute('aria-hidden', 'true');
    document.body.style.overflow = 'hidden';
    return () => { app?.removeAttribute('inert'); app?.removeAttribute('aria-hidden'); document.body.style.overflow = previous; onCallChange(false); };
  }, [opened]);
  useEffect(() => {
    if (m.status === 'completed') {
      ending.current = true; attempt.current++;
      void call.current?.destroy(); call.current = null;
      setOpened(false); setJoined(false);
    }
  }, [m.status]);
  async function flush() {
    if (!manager) return;
    if (uploading.current) await uploading.current;
    if (!turns.current.length) return;
    const batch = turns.current.slice(0, 25);
    const upload = request('action', token, { ...identity, action: 'meeting.transcript', turns: batch }).then(() => { turns.current.splice(0, batch.length); });
    uploading.current = upload;
    try { await upload; } finally { uploading.current = null; }
    if (turns.current.length) await flush();
  }
  useEffect(() => {
    if (!opened || !manager) return;
    const timer = setInterval(() => { void flush().catch(() => setCallError('Не удалось сохранить часть расшифровки. Повторяем подключение к серверу.')); }, 4000);
    return () => clearInterval(timer);
  }, [opened, m.id]);
  async function closeFrame() {
    const frame = call.current; call.current = null;
    await frame?.destroy();
    if (alive.current) { setOpened(false); setOpening(false); setJoined(false); }
  }
  async function open() {
    const id = ++attempt.current;
    setOpening(true); setOpened(true); setCallError(''); setMediaStatus(''); setError(''); ending.current = false;
    try {
      const room = await request<CallCredentials>('room', token, { ...identity, consent });
      if (room.provider === 'zoom') {
        void journey.refresh();
        throw new Error('Сервис встречи изменился. Подключитесь ещё раз.');
      }
      const { default: DailyIframe } = await import('@daily-co/daily-js');
      if (!alive.current || attempt.current !== id) return;
      if (!mount.current) await new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
      if (!mount.current || attempt.current !== id) return;
      const frame = DailyIframe.createFrame(mount.current, { iframeStyle: { width: '100%', height: '100%', border: '0', borderRadius: '0' }, showLeaveButton: false, showFullscreenButton: false, showUserNameChangeUI: false });
      call.current = frame;
      frame.iframe()?.setAttribute('title', 'Видеовстреча HR Vision');
      const presence = () => setRemotePresent(Object.values(frame.participants()).some(p => !p.local));
      frame.on('participant-joined', presence); frame.on('participant-left', presence);
      let joinedAt = m.startedAt ? new Date(m.startedAt).getTime() : Date.now();
      frame.on('joined-meeting', () => {
        setJoined(true); setOpening(false); presence();
        joinedAt = m.startedAt ? new Date(m.startedAt).getTime() : Date.now();
        void request('action', token, { ...identity, action: 'meeting.joined' }).then(() => journey.refresh()).catch(e => setCallError(e.message));
        if (manager) {
          frame.startRecording({ type: 'cloud' });
          frame.startTranscription({ language: 'ru', model: 'nova-2' });
        }
      });
      frame.on('transcription-message', e => {
        if (!manager || !e?.text || e.rawResponse?.is_final === false) return;
        const speaker = Object.values(frame.participants()).find(p => p.session_id === e.participantId);
        if (speaker?.user_id !== 'manager') return;
        const at = new Date(e.timestamp).getTime();
        if (!Number.isFinite(at)) return;
        turns.current.push({ id: `${e.instanceId || ''}:${e.participantId}:${at}`, text: e.text.slice(0, 1500), seconds: Math.max(0, (at - joinedAt) / 1000) });
      });
      frame.on('recording-started', () => setMediaStatus('Запись идёт'));
      frame.on('recording-error', () => setCallError('Запись не запустилась. Разговор пока не сохраняется.'));
      frame.on('transcription-error', () => setCallError('Расшифровка не запустилась. После встречи можно будет указать заданные вопросы вручную.'));
      frame.on('error', () => setCallError('Не удалось подключиться. Проверьте камеру, микрофон и сеть.'));
      frame.on('left-meeting', () => {
        if (ending.current) return;
        void closeFrame();
        void request<SharedState>('state', token).then(latest => {
          if (latest.candidates[person.id]?.meeting?.status !== 'completed') setError('Соединение со встречей закрыто. Войдите снова, чтобы продолжить.');
          void journey.refresh();
        }).catch(() => setError('Соединение закрыто. Проверьте сеть и войдите во встречу снова.'));
      });
      await frame.join({ url: room.url, token: room.token });
    } catch (e) {
      if (attempt.current === id && alive.current) { setError((e as Error).message); await closeFrame(); }
    } finally { if (attempt.current === id && alive.current) setOpening(false); }
  }
  async function finish(skip = false) {
    if (finishing || ending.current) return;
    setFinishing(true); setCallError('');
    try {
      // Preserve received speech before the room is closed. Provider VTT is also
      // available after processing if this browser did not receive live captions.
      await flush();
      if (await act(skip ? 'meeting.skip' : 'meeting.finish', person.id, { meetingId: m.id })) {
        ending.current = true; await closeFrame(); onComplete?.();
      } else setCallError('Не удалось завершить встречу. Попробуйте ещё раз.');
    } catch { setCallError('Не удалось сохранить разговор. Проверьте соединение и повторите завершение.'); }
    finally { if (alive.current) setFinishing(false); }
  }
  async function leave() { attempt.current++; ending.current = true; await closeFrame(); ending.current = false; }
  const live = m.status === 'live';
  return <section ref={panel} className={`cj-meeting${compact ? ' cj-meeting-compact' : ''}`}>
    {compact ? <CompactMeetingEntry meeting={m} consent={consent} onConsentChange={setConsent} consentText="Согласен на запись и расшифровку встречи. Материалы доступны участникам."
      opening={opening} busy={busy} configured={m.videoConfigured ?? state!.videoConfigured} onJoin={open} open={prejoinOpen} onOpenChange={onPrejoinOpenChange}>{providerSelector}</CompactMeetingEntry> : <>
    <div className="cj-section-heading"><div><span className="cj-kicker">{manager ? person.name : `${journey.state!.vacancy.company} · Иван Петров`}</span><h1>{dateTime(m.start)}</h1><p className="cj-meeting-meta">30 минут · {({ pending: manager ? 'Ждём подтверждения кандидата' : 'Подтвердите время', confirmed: 'Встреча подтверждена', live: 'Комната открыта', cancelled: 'Нужно новое время', completed: 'Встреча завершена' })[m.status]}</p></div></div>
    {m.status === 'pending' && <div className="cj-actions">{manager && <Button variant="outline" disabled={busy} onClick={onReschedule}>Перенести встречу</Button>}{!manager && <Button disabled={busy} onClick={() => act('meeting.confirm', person.id, { meetingId: m.id })}>Подтвердить встречу<Check size={15}/></Button>}<Button variant={manager ? 'outline' : 'ghost'} disabled={busy} onClick={async () => { if (await act('meeting.cancel', person.id, { meetingId: m.id })) onReschedule?.(); }}>{manager ? 'Отменить приглашение' : 'Нужно другое время'}</Button></div>}
    {['confirmed', 'live'].includes(m.status) && <>
      <label className="cj-check"><input type="checkbox" checked={consent} onChange={e => setConsent(e.target.checked)}/><span>Согласен на запись и расшифровку встречи. Материалы доступны участникам.</span></label>
      <div className="cj-actions"><Button disabled={!consent || opening || busy} onClick={open}><Video size={16}/>{opening ? 'Подключаем…' : live ? 'Вернуться во встречу' : 'Войти в видеовстречу'}</Button>{m.status === 'confirmed' && <Button variant="ghost" disabled={busy} onClick={async () => { if (manager) onReschedule?.(); else if (await act('meeting.cancel', person.id, { meetingId: m.id })) onReschedule?.(); }}>Нужно другое время</Button>}</div>
      {!(m.videoConfigured ?? state!.videoConfigured) && <p className="cj-info">Видеосервис ещё не подключён.</p>}
      {manager && !(m.videoConfigured ?? state!.videoConfigured) && m.status === 'confirmed' && <details className="cj-test-controls"><summary>Проверка следующих экранов</summary><p>В тесте можно пропустить звонок. Записи и расшифровки у такой встречи не будет.</p><Button variant="outline" disabled={busy} onClick={() => finish(true)}>Пропустить звонок в тесте</Button></details>}
    </>}
    {m.status === 'cancelled' && <><p>Предыдущее время освобождено.</p><Button onClick={onReschedule}>{manager ? 'Выбрать новое время' : 'Изменить расписание'}</Button></>}
    {m.status === 'completed' && manager && person.feedback && <Button onClick={onComplete}>Открыть разбор</Button>}
    </>}
    {opened && createPortal(<div className="agency-product agency-product-theme cj-call-overlay" role="dialog" aria-modal="true" aria-label={`Видеовстреча с ${manager ? person.name : 'Иваном Петровым'}`}>
      <div className="cj-call-bar"><div><strong>{manager ? person.name : 'Иван Петров'}</strong><span role="status">{!joined ? 'Подключение' : remotePresent ? 'Встреча' : 'Ждём собеседника'}{mediaStatus ? ` · ${mediaStatus}` : ''}</span></div><Button autoFocus className={joined && manager ? 'cj-call-end' : ''} variant={joined && manager ? 'destructive' : 'secondary'} disabled={finishing || busy} onClick={joined && manager ? () => finish() : leave}><PhoneOff size={16}/>{finishing ? 'Завершаем…' : joined && manager ? 'Завершить встречу' : joined ? 'Выйти' : 'Отменить'}</Button></div>
      {callError && <div role="alert" className="cj-call-error">{callError}</div>}
      <div ref={mount} className="cj-room"/>
    </div>, document.body)}
  </section>;
}


/** The entry stays compact; consent is explicit and keeps each provider's existing wording. */
function CompactMeetingEntry({ meeting, consent, onConsentChange, consentText, opening, busy, configured, onJoin, open, onOpenChange, children }: {
  meeting: NonNullable<Person['meeting']>; consent: boolean; onConsentChange: (value: boolean) => void;
  consentText: string; opening: boolean; busy: boolean; configured: boolean; onJoin: () => Promise<void>;
  open: boolean; onOpenChange: (open: boolean) => void; children?: ReactNode;
}) {
  const joinAfterClose = useRef(false);
  const alive = useRef(true);
  const joinFrame = useRef<number | undefined>(undefined);
  const canJoin = ['confirmed', 'live'].includes(meeting.status) && consent && configured && !opening && !busy;
  const latest = useRef({ canJoin, onJoin });
  latest.current = { canJoin, onJoin };
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; if (joinFrame.current !== undefined) cancelAnimationFrame(joinFrame.current); };
  }, []);
  const live = meeting.status === 'live';
  return <Dialog.Root open={open} onOpenChange={onOpenChange}>
    <div className="cj-meeting-compact-copy"><h3>{live ? 'Встреча идёт' : 'Предстоящая встреча'}</h3><p><time dateTime={meeting.start}>{dateTime(meeting.start)}</time></p></div>
    <Dialog.Trigger asChild><Button type="button" disabled={meeting.status === 'pending' || opening || busy} title={meeting.status === 'pending' ? 'Ждём подтверждения кандидата' : undefined}><Video size={16}/>{opening ? 'Подключаем…' : live ? 'Вернуться во встречу' : 'Подключиться'}</Button></Dialog.Trigger>
    <Dialog.Portal>
      <Dialog.Overlay className="cj-meeting-prejoin-overlay"/>
      <Dialog.Content className="agency-product-theme cj-meeting-prejoin" onCloseAutoFocus={event => {
        if (!joinAfterClose.current) return;
        event.preventDefault(); joinAfterClose.current = false;
        // Release the modal's focus trap, pointer lock and aria-hidden before opening the call overlay.
        joinFrame.current = requestAnimationFrame(() => { if (alive.current && latest.current.canJoin) void latest.current.onJoin(); });
      }}>
        <Dialog.Title>Подключение к встрече</Dialog.Title>
        <Dialog.Description>{dateTime(meeting.start)}</Dialog.Description>
        {children}
        <label className="cj-meeting-prejoin-consent"><input type="checkbox" checked={consent} onChange={event => onConsentChange(event.target.checked)}/><span>{consentText}</span></label>
        {!configured && <p className="cj-meeting-prejoin-info">Видеосервис ещё не подключён.</p>}
        <div className="cj-meeting-prejoin-actions"><Dialog.Close asChild><Button type="button" variant="ghost">Отмена</Button></Dialog.Close><Button type="button" disabled={!canJoin} onClick={() => {
          if (!canJoin) return;
          joinAfterClose.current = true; onOpenChange(false);
        }}>Продолжить</Button></div>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}

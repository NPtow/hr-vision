import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { DailyCall } from '@daily-co/daily-js';
import { Button } from '../../components/ui/button';
import { Check, PhoneOff, Video } from 'lucide-react';
import { dateTime, request, type Journey, type Person, type SharedState } from './api';

type Turn = { id: string; text: string; seconds: number };
export function MeetingPanel({ person, journey, onComplete, onCallChange, onReschedule }: { person: Person; journey: Journey; onComplete?: () => void; onCallChange: (open: boolean) => void; onReschedule?: () => void }) {
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
      const room = await request<{ url: string; token: string }>('room', token, { ...identity, consent });
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
  return <section ref={panel} className="cj-meeting">
    <div className="cj-section-heading"><div><span className="cj-kicker">{manager ? person.name : 'Сфера · Иван Петров'}</span><h1>{dateTime(m.start)}</h1><p className="cj-meeting-meta">30 минут · {({ pending: manager ? 'Ждём подтверждения кандидата' : 'Подтвердите время', confirmed: 'Встреча подтверждена', live: 'Комната открыта', cancelled: 'Нужно новое время', completed: 'Встреча завершена' })[m.status]}</p></div></div>
    {m.status === 'pending' && <div className="cj-actions">{manager && <Button variant="outline" disabled={busy} onClick={onReschedule}>Перенести встречу</Button>}{!manager && <Button disabled={busy} onClick={() => act('meeting.confirm', person.id, { meetingId: m.id })}>Подтвердить встречу<Check size={15}/></Button>}<Button variant={manager ? 'outline' : 'ghost'} disabled={busy} onClick={async () => { if (await act('meeting.cancel', person.id, { meetingId: m.id })) onReschedule?.(); }}>{manager ? 'Отменить приглашение' : 'Нужно другое время'}</Button></div>}
    {['confirmed', 'live'].includes(m.status) && <>
      <label className="cj-check"><input type="checkbox" checked={consent} onChange={e => setConsent(e.target.checked)}/><span>Согласен на запись и расшифровку встречи. Материалы доступны участникам.</span></label>
      <div className="cj-actions"><Button disabled={!consent || opening || busy} onClick={open}><Video size={16}/>{opening ? 'Подключаем…' : live ? 'Вернуться во встречу' : 'Войти в видеовстречу'}</Button>{m.status === 'confirmed' && <Button variant="ghost" disabled={busy} onClick={async () => { if (manager) onReschedule?.(); else if (await act('meeting.cancel', person.id, { meetingId: m.id })) onReschedule?.(); }}>Нужно другое время</Button>}</div>
      {!state!.videoConfigured && <p className="cj-info">Видеосервис ещё не подключён.</p>}
      {manager && !state!.videoConfigured && m.status === 'confirmed' && <details className="cj-test-controls"><summary>Проверка следующих экранов</summary><p>В тесте можно пропустить звонок. Записи и расшифровки у такой встречи не будет.</p><Button variant="outline" disabled={busy} onClick={() => finish(true)}>Пропустить звонок в тесте</Button></details>}
    </>}
    {m.status === 'cancelled' && <><p>Предыдущее время освобождено.</p><Button onClick={onReschedule}>{manager ? 'Выбрать новое время' : 'Изменить расписание'}</Button></>}
    {m.status === 'completed' && manager && person.feedback && <Button onClick={onComplete}>Открыть разбор</Button>}
    {opened && createPortal(<div className="agency-product agency-product-theme cj-call-overlay" role="dialog" aria-modal="true" aria-label={`Видеовстреча с ${manager ? person.name : 'Иваном Петровым'}`}>
      <div className="cj-call-bar"><div><strong>{manager ? person.name : 'Иван Петров'}</strong><span role="status">{!joined ? 'Подключение' : remotePresent ? 'Встреча' : 'Ждём собеседника'}{mediaStatus ? ` · ${mediaStatus}` : ''}</span></div><Button autoFocus className={joined && manager ? 'cj-call-end' : ''} variant={joined && manager ? 'destructive' : 'secondary'} disabled={finishing || busy} onClick={joined && manager ? () => finish() : leave}><PhoneOff size={16}/>{finishing ? 'Завершаем…' : joined && manager ? 'Завершить встречу' : joined ? 'Выйти' : 'Отменить'}</Button></div>
      {callError && <div role="alert" className="cj-call-error">{callError}</div>}
      <div ref={mount} className="cj-room"/>
    </div>, document.body)}
  </section>;
}

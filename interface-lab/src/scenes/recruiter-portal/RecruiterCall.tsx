import { useEffect, useRef, useState } from 'react';
import type { DailyCall } from '@daily-co/daily-js';
import { PhoneOff, X } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { recruiterRequest, type Room } from './api';
import type { InterviewRecording } from '../candidate-portal/api';
import { CandidateAccessControls } from './CandidateAccessControls';

export type ActiveCall = { kind: 'interview' | 'meeting'; candidateId: string; applicationId?: string; meetingId?: string; title: string; accountActive?: boolean; recording?: InterviewRecording };
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
  const [showClaim, setShowClaim] = useState(false);
  const [error, setError] = useState('');
  const callbacks = useRef({ onClose, onJoined, onCompleted });
  callbacks.current = { onClose, onJoined, onCompleted };
  const identity = active.kind === 'interview' ? { candidateId: active.candidateId } : { candidateId: active.candidateId, applicationId: active.applicationId, meetingId: active.meetingId };

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
          showLeaveButton: false, showFullscreenButton: true, showUserNameChangeUI: false,
        });
        call.current = frame;
        frame.iframe()?.setAttribute('title', `Интервью · ${active.title}`);
        const presence = () => { if (alive) setPresent(Object.values(frame.participants()).some(person => !person.local)); };
        frame.on('participant-joined', presence);
        frame.on('participant-left', presence);
        frame.on('joined-meeting', () => {
          if (!alive) return;
          setJoined(true); presence(); callbacks.current.onJoined();
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
        frame.on('left-meeting', () => { if (alive && !ending.current) callbacks.current.onClose(); });
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
  }, [active.kind, active.candidateId, active.applicationId, active.meetingId, token]);

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
      await recruiterRequest(`${active.kind}/complete`, token, identity);
      // Completion is already persisted. A frame that was ejected by the
      // provider must not turn successful completion into a client error.
      try {
        if (recording === 'active') call.current?.stopRecording();
        if (transcribing === 'active') call.current?.stopTranscription();
        await call.current?.destroy();
      } catch { /* The provider closes the room as part of completion. */ }
      call.current = null;
      callbacks.current.onCompleted();
    } catch (e) { ending.current = false; setError((e as Error).message); setFinishing(false); }
  }

  return <div className="rp-call-layer" role="dialog" aria-modal="true" aria-labelledby="rp-call-title">
    <header className="rp-call-header">
      <div><strong id="rp-call-title">{active.title}</strong><p role="status">{!joined ? 'Подключение…' : present ? 'Собеседник подключён' : 'Ждём собеседника'}{joined && ` · ${recording === 'active' ? 'Запись идёт' : recording === 'stopped' ? 'Запись остановлена' : recording === 'error' ? 'Ошибка записи' : 'Статус записи уточняется'}`}{joined && ` · ${transcribing === 'active' ? 'Расшифровка включена' : transcribing === 'stopped' ? 'Расшифровка остановлена' : transcribing === 'error' ? 'Ошибка расшифровки' : 'Статус расшифровки уточняется'}`}</p></div>
      <div className="rp-actions"><Button variant="secondary" autoFocus disabled={finishing} onClick={onClose}><X size={16}/>Выйти</Button>{joined && <Button disabled={finishing || starting} onClick={finish}><PhoneOff size={16}/>{finishing ? 'Завершаем…' : active.kind === 'interview' ? 'Завершить интервью' : 'Завершить встречу'}</Button>}</div>
    </header>
    {joined && <div className="rp-call-controls">
      <div className="rp-recording-controls">
        {(recording !== 'active' || transcribing !== 'active') && <><label className="rp-check"><input type="checkbox" checked={oralConsent} disabled={starting} onChange={event => setOralConsent(event.target.checked)}/><span>Согласие получено</span></label><Button disabled={!oralConsent || starting || finishing || (startRequested && !retryRecording && !retryTranscription && !startFailed)} onClick={startInterview}>{starting ? 'Запускаем…' : retryRecording || retryTranscription || startFailed ? 'Повторить запуск записи' : startRequested ? 'Ожидаем подтверждения…' : 'Начать интервью и запись'}</Button></>}
        <span className="rp-muted rp-help" role="status">{recording === 'active' ? transcribing === 'active' ? 'Запись и расшифровка идут' : 'Запись идёт · статус расшифровки уточняется' : recording === 'stopped' ? 'Запись остановлена' : recording === 'error' ? 'Ошибка записи' : 'Статус записи уточняется'}</span>
      </div>
      {active.kind === 'interview' && <div className="rp-call-claim"><Button variant="secondary" onClick={() => setShowClaim(current => !current)} aria-expanded={showClaim}>Доступ кандидату</Button>{showClaim && <CandidateAccessControls candidateId={active.candidateId} token={token} allowClaimFallback={active.accountActive === false && startRequested}/>}</div>}
    </div>}
    {error && <div className="rp-error" role="alert">{error}</div>}
    <div className="rp-call-frame" ref={mount}/>
  </div>;
}

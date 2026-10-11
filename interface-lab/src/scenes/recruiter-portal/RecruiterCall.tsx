import { useEffect, useRef, useState } from 'react';
import type { DailyCall } from '@daily-co/daily-js';
import { PhoneOff, X } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { recruiterRequest, type Room } from './api';

export type ActiveCall = { kind: 'interview' | 'meeting'; candidateId: string; applicationId?: string; title: string };

export function RecruiterCall({ active, token, onClose, onJoined, onCompleted }: {
  active: ActiveCall; token: string; onClose: () => void; onJoined: () => void; onCompleted: () => void;
}) {
  const mount = useRef<HTMLDivElement>(null);
  const call = useRef<DailyCall | null>(null);
  const ending = useRef(false);
  const [joined, setJoined] = useState(false);
  const [present, setPresent] = useState(false);
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [finishing, setFinishing] = useState(false);
  const [error, setError] = useState('');
  const callbacks = useRef({ onClose, onJoined, onCompleted });
  callbacks.current = { onClose, onJoined, onCompleted };
  const identity = active.kind === 'interview' ? { candidateId: active.candidateId } : { applicationId: active.applicationId };

  useEffect(() => {
    let alive = true;
    ending.current = false;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    async function open() {
      try {
        const room = await recruiterRequest<Room>(`${active.kind}/join`, token, { ...identity, consent: true });
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
          frame.startRecording({ type: 'cloud' });
          frame.startTranscription({ language: 'ru', model: 'nova-2' });
        });
        frame.on('recording-started', () => { if (alive) setRecording(true); });
        frame.on('recording-stopped', () => { if (alive) setRecording(false); });
        frame.on('transcription-started', () => { if (alive) setTranscribing(true); });
        frame.on('transcription-stopped', () => { if (alive) setTranscribing(false); });
        frame.on('recording-error', () => { if (alive) { setRecording(false); setError('Запись не запустилась. Разговор пока не сохраняется. Проверьте запись в меню звонка.'); } });
        frame.on('transcription-error', () => { if (alive) { setTranscribing(false); setError('Расшифровка не запустилась. Проверьте её в меню звонка.'); } });
        frame.on('error', () => { if (alive) setError('Не удалось подключиться. Проверьте сеть и разрешения камеры и микрофона.'); });
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
  }, [active.kind, active.candidateId, active.applicationId, token]);

  async function finish() {
    if (finishing) return;
    setFinishing(true); setError(''); ending.current = true;
    try {
      await recruiterRequest(`${active.kind}/complete`, token, identity);
      // Completion is already persisted. A frame that was ejected by the
      // provider must not turn successful completion into a client error.
      try {
        if (recording) call.current?.stopRecording();
        if (transcribing) call.current?.stopTranscription();
        await call.current?.destroy();
      } catch { /* The provider closes the room as part of completion. */ }
      call.current = null;
      callbacks.current.onCompleted();
    } catch (e) { ending.current = false; setError((e as Error).message); setFinishing(false); }
  }

  return <div className="rp-call-layer" role="dialog" aria-modal="true" aria-labelledby="rp-call-title">
    <header className="rp-call-header">
      <div><strong id="rp-call-title">{active.title}</strong><p role="status">{!joined ? 'Подключение…' : present ? 'Собеседник подключён' : 'Ждём собеседника'}{joined && ` · ${recording ? 'Запись идёт' : 'Запись не идёт'}`}{transcribing ? ' · Расшифровка включена' : ''}</p></div>
      <div className="rp-actions"><Button variant="secondary" autoFocus disabled={finishing} onClick={onClose}><X size={16}/>Выйти</Button>{joined && <Button disabled={finishing} onClick={finish}><PhoneOff size={16}/>{finishing ? 'Завершаем…' : active.kind === 'interview' ? 'Завершить интервью' : 'Завершить встречу'}</Button>}</div>
    </header>
    {error && <div className="rp-error" role="alert">{error}</div>}
    <div className="rp-call-frame" ref={mount}/>
  </div>;
}

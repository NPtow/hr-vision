import { useEffect, useRef, useState } from 'react';
import type { DailyParticipant } from '@daily-co/daily-js';
import { Camera, CameraOff, LoaderCircle, Maximize2, Mic, MicOff, Minimize2, MonitorUp, PhoneOff } from 'lucide-react';
import { callLayout, canSendTrack } from './call-media';
import { CallMediaStage } from './CallMediaStage';
// Reuse the approved recruiter's call surfaces and controls, without mounting
// its notes, CV, transcript, interview script or their data hooks.
import '../recruiter-coach/recruiter-coach.css';
import './candidate-call.css';

export type CandidateRecordingState = 'unknown' | 'recording' | 'stopped' | 'error';
type Props = {
  title: string;
  participants: DailyParticipant[];
  joined: boolean;
  closing: boolean;
  status: string;
  recording: CandidateRecordingState;
  error: string;
  onLeave: () => void;
  onMic: () => void;
  onCamera: () => void;
  onScreen: () => void;
};

/** Candidate-only call surface. Leaving never finishes the meeting for others. */
export function CandidateCallSurface({ title, participants, joined, closing, status, recording, error, onLeave, onMic, onCamera, onScreen }: Props) {
  const root = useRef<HTMLDivElement>(null);
  const [full, setFull] = useState(false);
  const [viewError, setViewError] = useState('');
  const { local } = callLayout(participants);
  const busy = !joined || closing;
  const canShare = typeof navigator.mediaDevices?.getDisplayMedia === 'function' && canSendTrack(local, 'screenVideo');
  useEffect(() => {
    const update = () => setFull(document.fullscreenElement === root.current);
    document.addEventListener('fullscreenchange', update);
    return () => document.removeEventListener('fullscreenchange', update);
  }, []);
  async function toggleFull() {
    setViewError('');
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await root.current?.requestFullscreen();
    } catch { setViewError('Не удалось открыть полный экран.'); }
  }
  return <div ref={root} className="rcc-root rcc-wide cc-root" role="dialog" aria-modal="true" aria-label={title}
    onKeyDown={event => {
      if (event.key !== 'Tab') return;
      const controls = Array.from(root.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') || []);
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }}>
    <main className="rcc-stage">
      <header className="rcc-header">
        <div className="cc-heading"><h1>{title}</h1><span role="status">{status}</span></div>
        {joined && <span className={`rcc-recording cc-recording cc-recording-${recording}`} role="status">
          {recording === 'recording' && <i/>}
          {recording === 'recording' ? 'Запись' : recording === 'stopped' ? 'Запись остановлена' : recording === 'error' ? 'Запись недоступна' : 'Проверяем запись'}
        </span>}
      </header>
      <CallMediaStage participants={participants} joined={joined} status={status}/>
      {(error || viewError) && <div className="rcc-call-notice cc-notice">
        <p role="alert">{error || viewError}</p>
      </div>}
      <footer className="rcc-call-controls" aria-label="Управление встречей">
        <div className="rcc-device-controls">
          <button type="button" className={`rcc-tool${local?.audio ? '' : ' cc-muted'}`} disabled={busy || !canSendTrack(local, 'audio')}
            aria-label={local?.audio ? 'Выключить микрофон' : 'Включить микрофон'} title={local?.audio ? 'Выключить микрофон' : 'Включить микрофон'} aria-pressed={!!local?.audio} onClick={onMic}>{local?.audio ? <Mic/> : <MicOff/>}</button>
          <button type="button" className={`rcc-tool${local?.video ? '' : ' cc-muted'}`} disabled={busy || !canSendTrack(local, 'video')}
            aria-label={local?.video ? 'Выключить камеру' : 'Включить камеру'} title={local?.video ? 'Выключить камеру' : 'Включить камеру'} aria-pressed={!!local?.video} onClick={onCamera}>{local?.video ? <Camera/> : <CameraOff/>}</button>
          {canShare && <button type="button" className={`rcc-tool${local?.screen ? ' rcc-tool-active' : ''}`} disabled={busy}
            aria-label={local?.screen ? 'Остановить показ экрана' : 'Показать экран'} title={local?.screen ? 'Остановить показ экрана' : 'Показать экран'} aria-pressed={!!local?.screen} onClick={onScreen}><MonitorUp/></button>}
        </div>
        <button type="button" autoFocus className="rcc-hangup" aria-label={closing ? 'Выходим из встречи' : 'Выйти из встречи'} title="Выйти из встречи" disabled={closing} onClick={onLeave}>{closing ? <LoaderCircle size={21} className="cc-spinner"/> : <PhoneOff size={21}/>}</button>
        <div className="rcc-view-controls">
          {document.fullscreenEnabled && <button type="button" className="rcc-tool rcc-tool-quiet" aria-label={full ? 'Выйти из полноэкранного режима' : 'На весь экран'} title={full ? 'Выйти из полноэкранного режима' : 'На весь экран'} onClick={() => void toggleFull()}>{full ? <Minimize2/> : <Maximize2/>}</button>}
        </div>
      </footer>
    </main>
  </div>;
}

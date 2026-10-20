import { useCallback, useEffect, useRef, useState } from 'react';
import type { DailyParticipant, DailyTrackState } from '@daily-co/daily-js';
import { Camera, CameraOff, LoaderCircle, Maximize2, Mic, MicOff, Minimize2, MonitorUp, PhoneOff, Volume2 } from 'lucide-react';
import { callLayout, canSendTrack, mediaTrack } from './call-media';
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

function VideoTrack({ track, mirror = false }: { track: DailyTrackState; mirror?: boolean }) {
  const ref = useRef<HTMLVideoElement>(null);
  const source = mediaTrack(track);
  useEffect(() => {
    const element = ref.current;
    if (!element || !source) return;
    element.srcObject = new MediaStream([source]);
    void element.play().catch(() => undefined);
    return () => { element.srcObject = null; };
  }, [source]);
  return <video ref={ref} autoPlay muted playsInline aria-hidden="true"
    className={mirror ? 'rcc-mirror' : ''} hidden={track.state !== 'playable'}/>;
}

function ParticipantTile({ person, screen = false }: { person: DailyParticipant; screen?: boolean }) {
  const track = screen ? person.tracks.screenVideo : person.tracks.video;
  const name = person.local ? 'Вы' : person.user_name || 'Собеседник';
  return <div className={`cc-participant${screen ? ' cc-screen' : ''}`} role="group" aria-label={screen ? `Экран: ${name}` : name}>
    <VideoTrack track={track} mirror={person.local && !screen}/>
    {track.state !== 'playable' && <div className="cc-camera-off">
      <span className="rcc-avatar">{name.split(' ').filter(Boolean).slice(0, 2).map(word => word[0]).join('')}</span>
      <CameraOff size={18} aria-label="Камера выключена"/>
    </div>}
    <span className="cc-participant-name">{!screen && !person.audio && <MicOff size={13} aria-label="Микрофон выключен"/>}{name}{screen ? ' · Экран' : ''}</span>
  </div>;
}

function AudioTrack({ id, track, register, blocked }: {
  id: string; track: DailyTrackState;
  register: (id: string, element: HTMLAudioElement | null) => void;
  blocked: (id: string, value: boolean) => void;
}) {
  const ref = useRef<HTMLAudioElement>(null);
  const source = mediaTrack(track);
  const playable = track.state === 'playable';
  useEffect(() => {
    const element = ref.current;
    if (!element || !source) return;
    let active = true;
    register(id, element);
    element.srcObject = new MediaStream([source]);
    if (playable) void element.play().then(() => { if (active) blocked(id, false); })
      .catch(cause => { if (active && cause?.name === 'NotAllowedError') blocked(id, true); });
    return () => { active = false; element.srcObject = null; register(id, null); blocked(id, false); };
  }, [id, source, playable, register, blocked]);
  return <audio ref={ref} autoPlay muted={!playable} aria-hidden="true"/>;
}

/** Candidate-only call surface. Leaving never finishes the meeting for others. */
export function CandidateCallSurface({ title, participants, joined, closing, status, recording, error, onLeave, onMic, onCamera, onScreen }: Props) {
  const root = useRef<HTMLDivElement>(null);
  const audio = useRef(new Map<string, HTMLAudioElement>());
  const [blockedAudio, setBlockedAudio] = useState(new Set<string>());
  const [full, setFull] = useState(false);
  const [viewError, setViewError] = useState('');
  const { local, remote, sharing } = callLayout(participants);
  const busy = !joined || closing;
  const canShare = typeof navigator.mediaDevices?.getDisplayMedia === 'function' && canSendTrack(local, 'screenVideo');
  const registerAudio = useCallback((id: string, element: HTMLAudioElement | null) => {
    if (element) audio.current.set(id, element); else audio.current.delete(id);
  }, []);
  const reportBlocked = useCallback((id: string, value: boolean) => setBlockedAudio(previous => {
    if (previous.has(id) === value) return previous;
    const next = new Set(previous); if (value) next.add(id); else next.delete(id); return next;
  }), []);
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
  function enableAudio() {
    // play() stays in this user gesture for Safari and in-app browsers.
    for (const [id, element] of audio.current) {
      if (!element.muted) void element.play().then(() => reportBlocked(id, false)).catch(() => reportBlocked(id, true));
    }
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
      <div className="rcc-video-stage cc-video-stage">
        {joined ? <>
          {sharing ? <div className="cc-presentation">
            <ParticipantTile person={sharing} screen/>
            <div className="cc-filmstrip">{participants.map(person => <ParticipantTile key={person.session_id} person={person}/>)}</div>
          </div> : <>
            {remote.length ? <div className="cc-remote-grid" data-multiple={remote.length > 1}>
              {remote.map(person => <ParticipantTile key={person.session_id} person={person}/>)}
            </div> : <div className="cc-waiting"><span className="rcc-avatar"><Camera size={32}/></span><p>Ждём собеседника</p></div>}
            {local && <div className="rcc-self-preview"><ParticipantTile person={local}/></div>}
          </>}
          {remote.map(person => <div key={person.session_id} className="cc-audio">
            <AudioTrack id={`${person.session_id}:audio`} track={person.tracks.audio} register={registerAudio} blocked={reportBlocked}/>
            <AudioTrack id={`${person.session_id}:screen`} track={person.tracks.screenAudio} register={registerAudio} blocked={reportBlocked}/>
          </div>)}
        </> : <div className="cc-waiting"><LoaderCircle size={30} className="cc-spinner" aria-hidden="true"/><p>{status}</p></div>}
      </div>
      {(error || viewError || blockedAudio.size > 0) && <div className="rcc-call-notice cc-notice">
        {(error || viewError) && <p role="alert">{error || viewError}</p>}
        {blockedAudio.size > 0 && <button type="button" onClick={enableAudio}><Volume2 size={17}/>Включить звук</button>}
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

import { useCallback, useEffect, useRef, useState } from 'react';
import type { DailyParticipant, DailyTrackState } from '@daily-co/daily-js';
import { Camera, CameraOff, LoaderCircle, MicOff, Volume2 } from 'lucide-react';
import { callLayout, mediaTrack } from './call-media';
import './candidate-call.css';

export type CallMediaStageProps = {
  participants: DailyParticipant[];
  joined: boolean;
  status: string;
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

/**
 * Shared participant media. The call owner controls devices and connection.
 * Keep this component mounted while opening or folding surrounding panels.
 * Detaching a media element must never stop a provider-owned track.
 */
export function CallMediaStage({ participants, joined, status }: CallMediaStageProps) {
  const audio = useRef(new Map<string, HTMLAudioElement>());
  const [blockedAudio, setBlockedAudio] = useState(new Set<string>());
  const { local, remote, sharing } = callLayout(participants);
  const registerAudio = useCallback((id: string, element: HTMLAudioElement | null) => {
    if (element) audio.current.set(id, element); else audio.current.delete(id);
  }, []);
  const reportBlocked = useCallback((id: string, value: boolean) => setBlockedAudio(previous => {
    if (previous.has(id) === value) return previous;
    const next = new Set(previous); if (value) next.add(id); else next.delete(id); return next;
  }), []);
  function enableAudio() {
    // play() stays in this user gesture for Safari and in-app browsers.
    for (const [id, element] of audio.current) {
      if (!element.muted) void element.play().then(() => reportBlocked(id, false)).catch(() => reportBlocked(id, true));
    }
  }
  return <div className="cc-call-media-stage">
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
    {blockedAudio.size > 0 && <div className="rcc-call-notice cc-notice cc-audio-notice">
      <button type="button" onClick={enableAudio}><Volume2 size={17}/>Включить звук</button>
    </div>}
  </div>;
}

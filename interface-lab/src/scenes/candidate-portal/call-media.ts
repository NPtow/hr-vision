import type { DailyParticipant, DailyParticipantPermissionsCanSendValues, DailyTrackState } from '@daily-co/daily-js';
import { CallConnectionError } from './call-connection.ts';

/** Transfer a clone of the granted preview tracks to the call object. Cloning
 * does not request permission again; retiring preview must not kill the call.
 * The caller owns and releases these clones even if SDK loading/join fails. */
export function callInputTracks(stream: MediaStream | null, devices: { camera: boolean; microphone: boolean }) {
  const owned: MediaStreamTrack[] = [];
  function take(kind: 'video' | 'audio', enabled: boolean): MediaStreamTrack | false {
    if (!enabled) return false;
    const source = stream?.getTracks().find(track => track.kind === kind && track.readyState === 'live');
    if (!source) throw new CallConnectionError('Доступ к устройствам потерян. Разрешите камеру и микрофон ещё раз.');
    const track = source.clone();
    owned.push(track);
    return track;
  }
  try {
    return { videoSource: take('video', devices.camera), audioSource: take('audio', devices.microphone), owned };
  } catch (cause) {
    owned.forEach(track => track.stop());
    throw cause;
  }
}

/** Keep provider-owned tracks alive across renders; only Daily may stop them. */
export function mediaTrack(track?: DailyTrackState): MediaStreamTrack | undefined {
  return track?.persistentTrack || track?.track;
}

export function canSendTrack(participant: DailyParticipant | undefined, kind: DailyParticipantPermissionsCanSendValues): boolean {
  if (!participant) return false;
  const permission = participant.permissions?.canSend;
  return permission === true || permission instanceof Set && permission.has(kind);
}

export function callLayout(participants: DailyParticipant[]) {
  const local = participants.find(person => person.local);
  const remote = participants.filter(person => !person.local);
  // A remote presentation takes priority over our own preview.
  const sharing = [...remote, ...(local ? [local] : [])].find(person => person.tracks.screenVideo.state === 'playable');
  return { local, remote, sharing };
}

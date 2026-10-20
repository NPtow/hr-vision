import { useCallback, useEffect, useRef, useState } from 'react';

type Channel = 'microphone' | 'camera' | 'screen';
type OwnedStream = { stream: MediaStream; release: () => void };
type Streams = Record<Channel, OwnedStream | null>;

function stopTracks(stream: MediaStream) {
  stream.getTracks().forEach(track => track.stop());
}

function mediaError(cause: unknown, channel: Channel) {
  const name = cause instanceof Error ? cause.name : '';
  if (name === 'NotAllowedError' || name === 'PermissionDeniedError') {
    return channel === 'screen'
      ? 'Показ экрана отменён или доступ не разрешён.'
      : `Разрешите доступ к ${channel === 'camera' ? 'камере' : 'микрофону'} в настройках браузера.`;
  }
  if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
    return channel === 'camera' ? 'Камера не найдена.' : 'Микрофон не найден.';
  }
  if (name === 'NotReadableError' || name === 'TrackStartError') {
    return 'Устройство недоступно. Проверьте, не использует ли его другое приложение.';
  }
  if (name === 'AbortError') return 'Подключение отменено. Можно попробовать ещё раз.';
  if (name === 'SecurityError') return 'Браузер запретил доступ к устройству для этой страницы.';
  if (cause instanceof Error && name === 'Error') return cause.message;
  return channel === 'screen' ? 'Не удалось показать экран.' : 'Не удалось подключить устройство.';
}

/**
 * Browser-only preview, not an interview transport.
 * Camera belongs to the current user; render it as a muted local self-preview.
 * Microphone is held separately and is never rendered, recorded, or transmitted.
 * No device permission is requested until a toggle is explicitly invoked.
 */
export function useLocalCoachMedia() {
  const [cameraStream, setCameraStream] = useState<MediaStream | null>(null);
  const [screenStream, setScreenStream] = useState<MediaStream | null>(null);
  const [micOn, setMicOn] = useState(false);
  const [cameraOn, setCameraOn] = useState(false);
  const [screenOn, setScreenOn] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const owned = useRef<Streams>({ microphone: null, camera: null, screen: null });
  const mounted = useRef(true);
  const generation = useRef(0);
  const pending = useRef<symbol | null>(null);

  const publish = useCallback((channel: Channel, stream: MediaStream | null) => {
    if (!mounted.current) return;
    if (channel === 'microphone') setMicOn(Boolean(stream));
    if (channel === 'camera') { setCameraStream(stream); setCameraOn(Boolean(stream)); }
    if (channel === 'screen') { setScreenStream(stream); setScreenOn(Boolean(stream)); }
  }, []);

  const release = useCallback((channel: Channel) => {
    const current = owned.current[channel];
    owned.current[channel] = null;
    current?.release();
    publish(channel, null);
  }, [publish]);

  const stop = useCallback(() => {
    // Any pending browser prompt may still resolve. Its result will be discarded.
    generation.current += 1;
    pending.current = null;
    release('microphone');
    release('camera');
    release('screen');
    if (mounted.current) { setBusy(false); setError(''); }
  }, [release]);

  const toggle = useCallback(async (channel: Channel) => {
    if (!mounted.current || pending.current) return;
    setError('');
    if (owned.current[channel]) { release(channel); return; }

    const request = Symbol(channel);
    const currentGeneration = generation.current;
    pending.current = request;
    setBusy(true);
    let acquired: MediaStream | null = null;
    try {
      const devices = navigator.mediaDevices;
      if (!devices || (channel === 'screen' ? !devices.getDisplayMedia : !devices.getUserMedia)) {
        throw new Error(channel === 'screen'
          ? 'Этот браузер не поддерживает показ экрана.'
          : 'Доступ к камере и микрофону недоступен. Откройте страницу через HTTPS или localhost.');
      }
      acquired = channel === 'screen'
        ? await devices.getDisplayMedia({ video: true, audio: false })
        : await devices.getUserMedia({ video: channel === 'camera', audio: channel === 'microphone' });

      if (!mounted.current || generation.current !== currentGeneration || pending.current !== request) {
        stopTracks(acquired);
        return;
      }
      const tracks = channel === 'microphone' ? acquired.getAudioTracks() : acquired.getVideoTracks();
      if (!tracks.some(track => track.readyState === 'live')) {
        throw new Error(channel === 'screen' ? 'Экран не был выбран.' : 'Устройство не передало поток.');
      }
      // Enforce separation even if a browser returns an unexpected extra track.
      acquired.getTracks().filter(track => !tracks.includes(track)).forEach(track => {
        track.stop();
        acquired?.removeTrack(track);
      });
      const stream = acquired;
      const onEnded = () => {
        if (owned.current[channel]?.stream === stream) release(channel);
      };
      tracks.forEach(track => track.addEventListener('ended', onEnded));
      owned.current[channel] = {
        stream,
        release: () => {
          tracks.forEach(track => track.removeEventListener('ended', onEnded));
          stopTracks(stream);
        },
      };
      publish(channel, stream);
    } catch (cause) {
      if (acquired) stopTracks(acquired);
      if (mounted.current && generation.current === currentGeneration && pending.current === request) {
        publish(channel, null);
        setError(mediaError(cause, channel));
      }
    } finally {
      // An earlier, cancelled request must not clear a newer request's busy state.
      if (pending.current === request) {
        pending.current = null;
        if (mounted.current) setBusy(false);
      }
    }
  }, [publish, release]);

  const toggleMic = useCallback(() => toggle('microphone'), [toggle]);
  const toggleCamera = useCallback(() => toggle('camera'), [toggle]);
  const toggleScreen = useCallback(() => toggle('screen'), [toggle]);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      stop();
    };
  }, [stop]);

  return {
    cameraStream, screenStream, micOn, cameraOn, screenOn, busy, error,
    toggleMic, toggleCamera, toggleScreen, stop,
  };
}

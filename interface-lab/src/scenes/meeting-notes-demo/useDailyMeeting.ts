import { useCallback, useEffect, useRef, useState } from 'react';
import type { DailyCall, DailyParticipant } from '@daily-co/daily-js';
import { readableError, type LabCapture, type LabSession, type LabTranscriptTurn, type MeetingLabApi } from './meeting-notes-api';

type Phase = 'idle' | 'joining' | 'joined' | 'finishing';
type CaptureState = { recording: LabCapture; transcription: LabCapture };

export function useDailyMeeting(api: MeetingLabApi, onSession: (session: LabSession) => void) {
  const [phase, setPhase] = useState<Phase>('idle');
  const [participants, setParticipants] = useState<DailyParticipant[]>([]);
  const [turns, setTurns] = useState<LabTranscriptTurn[]>([]);
  const [capture, setCapture] = useState<CaptureState>({ recording: { status: 'idle' }, transcription: { status: 'idle' } });
  const [error, setError] = useState('');
  const [saveError, setSaveError] = useState('');
  const callRef = useRef<DailyCall | null>(null);
  const idRef = useRef<string | null>(null);
  const originRef = useRef(0);
  const alive = useRef(true);
  const generation = useRef(0);
  const queue = useRef<LabTranscriptTurn[]>([]);
  const seen = useRef(new Set<string>());
  const flushing = useRef<Promise<void> | null>(null);
  const finishing = useRef(false);
  const callback = useRef(onSession);
  callback.current = onSession;
  const captureRef = useRef(capture);
  captureRef.current = capture;
  const captureTimers = useRef<Partial<Record<keyof CaptureState, ReturnType<typeof setTimeout>>>>({});

  const flush = useCallback(async () => {
    if (flushing.current) await flushing.current;
    const id = idRef.current;
    if (!id || !queue.current.length) return;
    const operation = (async () => {
      while (queue.current.length) {
        const batch = queue.current.slice(0, 40);
        await api.transcript(id, batch);
        const ids = new Set(batch.map(turn => turn.id));
        queue.current = queue.current.filter(turn => !ids.has(turn.id));
      }
      if (alive.current) setSaveError('');
    })();
    flushing.current = operation;
    try { await operation; }
    catch (failure) {
      if (alive.current) setSaveError(`Речь ещё не сохранена. ${readableError(failure)}`);
      throw failure;
    } finally { if (flushing.current === operation) flushing.current = null; }
  }, [api]);

  const reportCapture = useCallback((kind: keyof CaptureState, status: 'started' | 'error', instanceId?: string, message?: string) => {
    if (!idRef.current) return;
    clearTimeout(captureTimers.current[kind]);
    captureRef.current = { ...captureRef.current, [kind]: { status, instanceId, error: message } };
    setCapture(previous => ({ ...previous, [kind]: { status, instanceId, error: message } }));
    void api.capture(idRef.current, { kind, status, instanceId, ...(message ? { error: message } : {}) })
      .catch(failure => { if (alive.current) setSaveError(readableError(failure)); });
  }, [api]);

  const startCapture = useCallback((kind?: keyof CaptureState) => {
    const call = callRef.current;
    if (!call || call.meetingState() !== 'joined-meeting') return;
    for (const captureKind of (kind ? [kind] : ['recording', 'transcription']) as (keyof CaptureState)[]) {
      if (captureRef.current[captureKind].status === 'started') continue;
      captureRef.current = { ...captureRef.current, [captureKind]: { status: 'starting' } };
      setCapture(captureRef.current);
      clearTimeout(captureTimers.current[captureKind]);
      captureTimers.current[captureKind] = setTimeout(() => {
        if (captureRef.current[captureKind].status === 'starting') reportCapture(captureKind, 'error', undefined, 'Провайдер не подтвердил запуск.');
      }, 20000);
      try {
        const result = captureKind === 'recording' ? call.startRecording({ type: 'cloud' })
          : call.startTranscription({ language: 'ru', model: 'nova-2', includeRawResponse: true, punctuate: true });
        // Daily's current declarations say void; some SDK builds return a promise.
        void Promise.resolve(result).catch(failure => reportCapture(captureKind, 'error', undefined, readableError(failure)));
      } catch (failure) { reportCapture(captureKind, 'error', undefined, readableError(failure)); }
    }
  }, [reportCapture]);

  const join = useCallback(async (session: LabSession, displayName: string) => {
    if (callRef.current || phase !== 'idle') return;
    const run = ++generation.current;
    setPhase('joining'); setError(''); setSaveError(''); setTurns([]);
    captureRef.current = { recording: { status: 'idle' }, transcription: { status: 'idle' } };
    setCapture(captureRef.current);
    queue.current = []; seen.current = new Set(session.transcript.map(turn => turn.id));
    idRef.current = session.id;
    try {
      // This demo shares the product entry bundle. Loading its SDK eagerly
      // made even the candidate invitation depend on the video chunk.
      const { default: Daily } = await import('@daily-co/daily-js');
      if (!alive.current || generation.current !== run) return;
      const result = await api.join(session.id);
      if (!alive.current || generation.current !== run) return;
      callback.current(result.session);
      originRef.current = result.session.startedAt ? Date.parse(result.session.startedAt) : Date.now();
      const call = Daily.createCallObject({ startVideoOff: false, startAudioOff: false });
      callRef.current = call;
      const syncParticipants = () => {
        if (alive.current && generation.current === run && !call.isDestroyed()) setParticipants(Object.values(call.participants()));
      };
      call.on('participant-joined', syncParticipants).on('participant-updated', syncParticipants)
        .on('participant-left', syncParticipants).on('track-started', syncParticipants).on('track-stopped', syncParticipants);
      call.on('joined-meeting', () => { if (alive.current) { setPhase('joined'); syncParticipants(); } });
      call.on('left-meeting', () => { if (alive.current) { setParticipants([]); if (!finishing.current) setPhase('idle'); } });
      call.on('error', event => {
        if (alive.current && !(finishing.current && event?.error?.type === 'ejected')) setError(event?.errorMsg || 'Соединение прервалось.');
      });
      call.on('camera-error', () => { if (alive.current) setError('Не удалось включить камеру или микрофон. Проверьте разрешения браузера.'); });
      call.on('recording-started', event => reportCapture('recording', 'started', event?.instanceId));
      call.on('recording-error', event => reportCapture('recording', 'error', event?.instanceId, event?.errorMsg || 'Запись не началась.'));
      call.on('transcription-started', event => reportCapture('transcription', 'started', event?.instanceId));
      call.on('transcription-error', event => reportCapture('transcription', 'error', event?.instanceId, event?.errorMsg || 'Расшифровка не началась.'));
      call.on('transcription-message', event => {
        if (!event?.text?.trim() || event.rawResponse?.is_final !== true) return;
        const timestamp = new Date(event.timestamp).getTime();
        if (!Number.isFinite(timestamp)) return;
        const id = `${event.instanceId || 'live'}:${event.participantId}:${timestamp}`;
        if (seen.current.has(id)) return;
        seen.current.add(id);
        const participant = Object.values(call.participants()).find(value => value.session_id === event.participantId);
        const turn: LabTranscriptTurn = {
          id, speakerId: event.participantId,
          speakerName: participant?.user_name || 'Участник', text: event.text.trim(),
          startMs: Math.max(0, timestamp - originRef.current), source: 'daily-live',
        };
        queue.current.push(turn);
        if (alive.current) setTurns(previous => [...previous, turn]);
      });
      await call.join({ url: result.room.url, token: result.room.token, userName: displayName });
      if (!alive.current || generation.current !== run) return;
      const started = await api.started(session.id);
      callback.current(started.session);
      if (started.session.startedAt) originRef.current = Date.parse(started.session.startedAt);
      // Only events from this Daily connection confirm capture. Persisted browser
      // events can be stale after everyone left the room and then rejoined.
      startCapture();
    } catch (failure) {
      if (alive.current && generation.current === run) { setError(readableError(failure)); setPhase('idle'); }
      const call = callRef.current; callRef.current = null;
      if (call && !call.isDestroyed()) await call.destroy().catch(() => undefined);
    }
  }, [api, phase, reportCapture, startCapture]);

  const finish = useCallback(async () => {
    const id = idRef.current;
    if (!id || finishing.current) return;
    finishing.current = true;
    setPhase('finishing'); setError('');
    const call = callRef.current;
    let cancelDrain = () => {};
    let waitForDrain = () => Promise.resolve();
    if (call && !call.isDestroyed()) {
      let recordingStopped = false;
      let transcriptionStopped = false;
      let resolveStopped: () => void;
      const stopped = new Promise<void>(resolve => { resolveStopped = resolve; });
      const onRecordingStopped = () => { recordingStopped = true; if (transcriptionStopped) resolveStopped(); };
      const onTranscriptionStopped = () => { transcriptionStopped = true; if (recordingStopped) resolveStopped(); };
      const onLeft = () => resolveStopped();
      let timer: ReturnType<typeof setTimeout> | undefined;
      // Subscribe before the request: only the backend owns provider stop commands.
      call.on('recording-stopped', onRecordingStopped);
      call.on('transcription-stopped', onTranscriptionStopped);
      call.on('left-meeting', onLeft);
      waitForDrain = async () => {
        // Start the fallback after the response, not before a potentially slow request.
        await Promise.race([stopped, new Promise<void>(resolve => { timer = setTimeout(resolve, 4000); })]);
        clearTimeout(timer);
      };
      cancelDrain = () => {
        clearTimeout(timer);
        call.off('recording-stopped', onRecordingStopped);
        call.off('transcription-stopped', onTranscriptionStopped);
        call.off('left-meeting', onLeft);
      };
    }
    try {
      await flush();
      const result = await api.finish(id);
      await waitForDrain();
      // The API accepts late final utterances for a bounded grace period.
      // A late upload failure remains queued; the successful server response is
      // still authoritative about completion, and saveError stays visible.
      await flush().catch(() => undefined);
      callback.current(result.session);
      callRef.current = null;
      if (call && !call.isDestroyed()) { await call.leave().catch(() => undefined); await call.destroy().catch(() => undefined); }
      await flush().catch(() => undefined);
      if (alive.current) { setPhase('idle'); setParticipants([]); }
    } catch (failure) {
      await waitForDrain();
      await flush().catch(() => undefined);
      const stillJoined = Boolean(call && !call.isDestroyed() && call.meetingState() === 'joined-meeting');
      if (!stillJoined) {
        callRef.current = null;
        if (call && !call.isDestroyed()) await call.destroy().catch(() => undefined);
      }
      if (alive.current) { setError(readableError(failure)); setPhase(stillJoined ? 'joined' : 'idle'); if (!stillJoined) setParticipants([]); }
    } finally { cancelDrain(); finishing.current = false; }
  }, [api, flush]);

  const leave = useCallback(async () => {
    await flush();
    const call = callRef.current; callRef.current = null;
    if (call && !call.isDestroyed()) { await call.leave(); await call.destroy(); }
    setPhase('idle'); setParticipants([]);
  }, [flush]);

  const toggleMic = () => { const call = callRef.current; if (call) call.setLocalAudio(!call.localAudio()); };
  const toggleCamera = () => { const call = callRef.current; if (call) call.setLocalVideo(!call.localVideo()); };
  const toggleScreen = () => {
    const call = callRef.current;
    if (!call) return;
    try { if (call.participants().local?.screen) call.stopScreenShare(); else call.startScreenShare(); }
    catch (failure) { setError(readableError(failure)); }
  };

  useEffect(() => {
    alive.current = true;
    const timer = window.setInterval(() => { void flush().catch(() => undefined); }, 2500);
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (callRef.current || queue.current.length) { event.preventDefault(); event.returnValue = ''; }
    };
    window.addEventListener('beforeunload', beforeUnload);
    return () => {
      alive.current = false; ++generation.current;
      window.clearInterval(timer); window.removeEventListener('beforeunload', beforeUnload);
      Object.values(captureTimers.current).forEach(clearTimeout);
      void flush().catch(() => undefined);
      const call = callRef.current; callRef.current = null;
      if (call && !call.isDestroyed()) void call.destroy().catch(() => undefined);
    };
  }, [flush]);

  return { phase, participants, turns, capture, error, saveError, join, finish, leave, toggleMic, toggleCamera, toggleScreen, startCapture, flush };
}

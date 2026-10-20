import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import type { DailyParticipant, DailyTrackState } from '@daily-co/daily-js';
import {
  AlignLeft, ArrowDown, ArrowLeft, ArrowUp, Check, ChevronDown, Copy, History,
  LoaderCircle, Maximize2, MessageSquare, Mic, MicOff, Minimize2, MonitorUp,
  PanelRightClose, PanelRightOpen, Pause, Phone, PhoneOff, Play, Plus,
  Redo2, RefreshCw, SquarePen, Undo2, Video, VideoOff, Volume2, VolumeX, X,
} from 'lucide-react';
import { createMeetingLabApi, clock, MeetingLabError, readableError, type LabCapabilities, type LabMaterials,
  type LabNotes, type LabSession, type LabSessionSummary, type LabTranscriptTurn } from './meeting-notes-api';
import { useDailyMeeting } from './useDailyMeeting';
import './meeting-notes-demo.css';

export type MeetingNotesDemoProps = {
  token?: string;
  onExit?: () => void;
  displayName?: string;
  initialSessionId?: string;
  apiBase?: string;
};
type Tab = 'transcript' | 'note' | 'chat';

function IconButton({ label, children, onClick, className = '', pressed, disabled = false }: {
  label: string; children: ReactNode; onClick: () => void; className?: string; pressed?: boolean; disabled?: boolean;
}) {
  return <button type="button" className={`mnd-icon ${className}`} aria-label={label} title={label}
    aria-pressed={pressed} disabled={disabled} onClick={onClick}>{children}</button>;
}

function Track({ track, audio = false, muted = false, mirror = false }: {
  track?: DailyTrackState; audio?: boolean; muted?: boolean; mirror?: boolean;
}) {
  const elementRef = useRef<HTMLVideoElement | null>(null);
  const persistent = track?.persistentTrack;
  useEffect(() => {
    const element = elementRef.current;
    if (!element || !persistent) return;
    const stream = new MediaStream([persistent]);
    element.srcObject = stream;
    void element.play().catch(() => undefined);
    return () => { element.srcObject = null; };
  }, [persistent]);
  return <video ref={elementRef} autoPlay playsInline muted={muted} aria-hidden="true"
    className={`${audio ? 'mnd-audio-track' : 'mnd-video-track'} ${mirror ? 'mnd-mirror' : ''}`} />;
}

function Participant({ participant, screen = false }: { participant: DailyParticipant; screen?: boolean }) {
  const track = screen ? participant.tracks.screenVideo : participant.tracks.video;
  return <div className={`mnd-participant ${screen ? 'mnd-screen-tile' : ''}`}>
    {track.state === 'playable' ? <Track track={track} muted mirror={participant.local && !screen} />
      : <div className="mnd-avatar" aria-label={`${participant.user_name || 'Участник'}: камера выключена`}>
        {(participant.user_name || 'У').split(' ').slice(0, 2).map(word => word[0]).join('')}
      </div>}
    <span className="mnd-participant-label">{participant.user_name || 'Участник'}{participant.local && ' · вы'}
      {participant.tracks.audio.state !== 'playable' && <MicOff size={13} />}</span>
  </div>;
}

function sessionLabel(session: LabSessionSummary) {
  const date = new Date(session.startedAt || session.createdAt);
  return date.toLocaleString('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

export function MeetingNotesDemo({ token, onExit, displayName = 'Никита', initialSessionId, apiBase }: MeetingNotesDemoProps) {
  const api = useMemo(() => createMeetingLabApi(apiBase, token), [apiBase, token]);
  const [session, setSession] = useState<LabSession | null>(null);
  const [sessions, setSessions] = useState<LabSessionSummary[]>([]);
  const [capabilities, setCapabilities] = useState<LabCapabilities | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [materials, setMaterials] = useState<LabMaterials | null>(null);
  const [materialsBusy, setMaterialsBusy] = useState(false);
  const [recordingIndex, setRecordingIndex] = useState(0);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [consent, setConsent] = useState(false);
  const [title, setTitle] = useState('Новая встреча');
  const [tab, setTab] = useState<Tab>('transcript');
  const [panelOpen, setPanelOpen] = useState(true);
  const [controls, setControls] = useState(true);
  const [full, setFull] = useState(false);
  const [now, setNow] = useState(Date.now());
  const [toast, setToast] = useState('');
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [videoError, setVideoError] = useState('');
  const [follow, setFollow] = useState(true);
  const [noteDraft, setNoteDraft] = useState('');
  const [noteState, setNoteState] = useState<'saved' | 'dirty' | 'saving' | 'conflict' | 'error'>('saved');
  const [noteError, setNoteError] = useState('');
  const [chatDraft, setChatDraft] = useState('');
  const [chatBusy, setChatBusy] = useState(false);
  const [chatError, setChatError] = useState('');
  const rootRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const transcriptRef = useRef<HTMLDivElement>(null);
  const chatRef = useRef<HTMLDivElement>(null);
  const noteRef = useRef<HTMLTextAreaElement>(null);
  const controlsTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const sessionRef = useRef<LabSession | null>(null);
  const draftRef = useRef('');
  const savedNotesRef = useRef<LabNotes>({ text: '', revision: 0 });
  const noteSaving = useRef(false);
  const noteConflict = useRef(false);
  const materialsRef = useRef<LabMaterials | null>(null);
  const createRequestId = useRef(crypto.randomUUID());
  const chatRequest = useRef<{ id: string; text: string } | null>(null);
  const loadGeneration = useRef(0);
  sessionRef.current = session;
  draftRef.current = noteDraft;

  const acceptSession = useCallback((next: LabSession) => {
    const previous = sessionRef.current;
    // A poll started before a successful note save may arrive afterwards.
    // Its older revision must never replace the newly saved shared text.
    if (previous?.id === next.id && next.notes.revision < savedNotesRef.current.revision) next = { ...next, notes: savedNotesRef.current };
    if (!previous || previous.id !== next.id) {
      noteConflict.current = false;
      savedNotesRef.current = next.notes;
      draftRef.current = next.notes.text;
      setNoteDraft(next.notes.text); setNoteState('saved'); setNoteError('');
    } else if (next.notes.revision !== savedNotesRef.current.revision && !noteSaving.current) {
      if (draftRef.current === savedNotesRef.current.text) {
        noteConflict.current = false;
        savedNotesRef.current = next.notes; draftRef.current = next.notes.text;
        setNoteDraft(next.notes.text); setNoteState('saved');
      } else {
        savedNotesRef.current = next.notes; noteConflict.current = true; setNoteState('conflict');
      }
    }
    sessionRef.current = next; setSession(next);
    setSessions(previousSessions => [next, ...previousSessions.filter(value => value.id !== next.id)]);
  }, []);
  const daily = useDailyMeeting(api, acceptSession);
  const connected = daily.phase === 'joined' || daily.phase === 'finishing';
  const completed = session?.status === 'completed';
  const recording = materials?.recordings[recordingIndex];
  const activeMedia = connected || Boolean(recording);
  const local = daily.participants.find(participant => participant.local);
  const sharing = daily.participants.find(participant => participant.tracks.screenVideo.state === 'playable');

  const notify = (message: string) => {
    setToast(message);
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(''), 2400);
  };
  const showControls = () => {
    setControls(true);
    if (controlsTimer.current) clearTimeout(controlsTimer.current);
    controlsTimer.current = setTimeout(() => setControls(false), 2400);
  };
  const updateUrl = (id?: string) => {
    const url = new URL(window.location.href);
    if (id) url.searchParams.set('meetingSession', id); else url.searchParams.delete('meetingSession');
    window.history.replaceState(null, '', url);
  };
  const refreshMaterials = useCallback(async (id: string, renewUrl = false) => {
    setMaterialsBusy(true);
    try {
      const result = await api.materials(id);
      if (sessionRef.current?.id !== id) return;
      const previous = materialsRef.current;
      const stable = { ...result, recordings: result.recordings.map(item => {
        const existing = previous?.recordings.find(value => value.id === item.id);
        return existing && !renewUrl ? { ...item, url: existing.url } : item;
      }) };
      materialsRef.current = stable; setMaterials(stable); acceptSession(result.session);
    } catch (failure) { if (sessionRef.current?.id === id) setError(readableError(failure)); }
    finally { setMaterialsBusy(false); }
  }, [api, acceptSession]);
  const openSession = useCallback(async (id: string) => {
    const run = ++loadGeneration.current;
    setLoading(true); setError(''); setMaterials(null); setRecordingIndex(0); setVideoError('');
    materialsRef.current = null;
    try {
      const result = await api.get(id);
      if (loadGeneration.current !== run) return;
      acceptSession(result.session); setTitle(result.session.title); updateUrl(id);
      setHistoryOpen(false); setConsent(false); setFollow(true); setChatDraft(''); setChatError('');
    } catch (failure) { if (loadGeneration.current === run) setError(readableError(failure)); }
    finally { if (loadGeneration.current === run) setLoading(false); }
  }, [api, acceptSession]);

  useEffect(() => {
    let active = true;
    void api.list().then(result => {
      if (!active) return;
      setSessions(result.sessions); setCapabilities(result.capabilities); setError('');
      const id = initialSessionId || new URLSearchParams(window.location.search).get('meetingSession');
      if (id) void openSession(id); else setLoading(false);
    }).catch(failure => { if (active) { setError(readableError(failure)); setLoading(false); } });
    return () => { active = false; ++loadGeneration.current; };
  }, [api, initialSessionId, openSession]);

  useEffect(() => {
    if (!session?.id) return;
    const id = session.id;
    let running = false;
    const poll = async () => {
      if (running) return;
      running = true;
      try {
        const result = await api.get(id);
        if (sessionRef.current?.id === id) acceptSession(result.session);
      } catch { /* Explicit actions keep their own error state; a transient poll does not erase data. */ }
      finally { running = false; }
    };
    const timer = setInterval(() => { void poll(); }, 6000);
    return () => clearInterval(timer);
  }, [api, session?.id, acceptSession]);

  useEffect(() => {
    if (!session?.id || session.status !== 'completed') return;
    const id = session.id;
    void refreshMaterials(id);
    const timer = setInterval(() => {
      const current = materialsRef.current;
      const pending = !current || [current.video, current.transcription].some(channel => channel.complete === false || ['processing', 'pending', 'unavailable', 'error'].includes(channel.status));
      if (pending) void refreshMaterials(id);
    }, 10000);
    return () => clearInterval(timer);
  }, [session?.id, session?.status, refreshMaterials]);

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    const fullscreen = () => setFull(document.fullscreenElement === rootRef.current);
    document.addEventListener('fullscreenchange', fullscreen);
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (draftRef.current !== savedNotesRef.current.text) { event.preventDefault(); event.returnValue = ''; }
    };
    window.addEventListener('beforeunload', beforeUnload);
    return () => {
      clearInterval(timer); document.removeEventListener('fullscreenchange', fullscreen);
      window.removeEventListener('beforeunload', beforeUnload);
      document.body.style.overflow = previous;
      if (controlsTimer.current) clearTimeout(controlsTimer.current);
      if (toastTimer.current) clearTimeout(toastTimer.current);
    };
  }, []);

  const saveNote = useCallback(async (replace = false) => {
    const current = sessionRef.current;
    if (!current || draftRef.current === savedNotesRef.current.text) return true;
    if (noteSaving.current || (noteConflict.current && !replace)) return false;
    const text = draftRef.current;
    noteSaving.current = true; setNoteState('saving'); setNoteError('');
    try {
      const result = await api.notes(current.id, savedNotesRef.current.revision, text);
      if (sessionRef.current?.id !== current.id) return false;
      savedNotesRef.current = result.notes;
      noteConflict.current = false;
      setSession(previous => previous ? { ...previous, notes: result.notes } : previous);
      setNoteState(draftRef.current === text ? 'saved' : 'dirty');
      return draftRef.current === text;
    } catch (failure) {
      if (failure instanceof MeetingLabError && failure.status === 409) {
        try { const fresh = await api.get(current.id); savedNotesRef.current = fresh.session.notes; } catch { /* Keep draft and retry explicitly. */ }
        noteConflict.current = true;
        setNoteState('conflict');
      } else { setNoteState('error'); setNoteError(readableError(failure)); }
      return false;
    } finally { noteSaving.current = false; }
  }, [api]);

  useEffect(() => {
    if (noteState !== 'dirty') return;
    const timer = setTimeout(() => { void saveNote(); }, 900);
    return () => clearTimeout(timer);
  }, [noteDraft, noteState, saveNote]);

  const allTurns = useMemo(() => {
    const combined = new Map((session?.transcript || []).map(turn => [turn.id, turn]));
    // Live additions are scoped to the current active call, never to a different saved session.
    if (connected) daily.turns.forEach(turn => combined.set(turn.id, turn));
    return [...combined.values()].sort((a, b) => a.startMs - b.startMs);
  }, [session?.transcript, daily.turns, connected]);
  const offset = recording?.transcriptOffsetMs;
  const seekableTranscript = Number.isFinite(offset);
  const activeTurn = completed && seekableTranscript
    ? allTurns.reduce((index, turn, i) => turn.startMs / 1000 - (offset || 0) / 1000 <= time ? i : index, -1)
    : connected ? allTurns.length - 1 : -1;

  useEffect(() => {
    if (!follow || tab !== 'transcript') return;
    const panel = transcriptRef.current;
    const row = panel?.querySelector<HTMLElement>(`[data-turn-index="${activeTurn}"]`);
    if (!panel || !row) return;
    panel.scrollTo({ top: row.offsetTop - panel.clientHeight * .32, behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
  }, [activeTurn, follow, tab, panelOpen]);
  useEffect(() => { if (tab === 'chat') chatRef.current?.scrollTo({ top: chatRef.current.scrollHeight, behavior: 'smooth' }); }, [session?.messages.length, chatBusy, tab]);
  useEffect(() => { setTime(0); setDuration(0); setPlaying(false); setVideoError(''); }, [recording?.id]);

  const begin = async () => {
    if (busy || !consent) return;
    setBusy(true); setError('');
    try {
      const target = session || (await api.create(createRequestId.current, title.trim() || 'Новая встреча')).session;
      acceptSession(target); updateUrl(target.id);
      await daily.join(target, displayName);
    } catch (failure) { setError(readableError(failure)); }
    finally { setBusy(false); }
  };
  const finish = async () => {
    if (!await saveNote()) { setTab('note'); setPanelOpen(true); return; }
    await daily.finish();
  };
  const newMeeting = async () => {
    if (connected || daily.phase === 'joining') return;
    if (!await saveNote()) { setTab('note'); setPanelOpen(true); return; }
    ++loadGeneration.current; setSession(null); sessionRef.current = null;
    setMaterials(null); materialsRef.current = null; setRecordingIndex(0); setNoteDraft(''); savedNotesRef.current = { text: '', revision: 0 }; noteConflict.current = false;
    setNoteState('saved'); setTitle('Новая встреча'); setConsent(false); setError(''); setHistoryOpen(false);
    createRequestId.current = crypto.randomUUID(); updateUrl();
  };
  const selectHistory = async (id: string) => {
    if (!await saveNote()) { setTab('note'); setPanelOpen(true); return; }
    await openSession(id);
  };
  const copyInvite = async () => {
    if (!session) return;
    const url = new URL(window.location.href); url.searchParams.set('meetingSession', session.id);
    try { await navigator.clipboard.writeText(url.href); notify('Ссылка скопирована'); }
    catch { setError('Браузер не разрешил скопировать ссылку. Скопируйте адрес страницы.'); }
  };
  const toggleFull = async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await rootRef.current?.requestFullscreen();
    } catch { notify('Браузер не поддерживает полноэкранный режим'); }
  };
  const togglePlay = () => {
    const element = videoRef.current;
    if (!element) return;
    if (element.paused) void element.play().catch(() => setVideoError('Не удалось воспроизвести запись.')); else element.pause();
  };
  const seek = (seconds: number) => { const element = videoRef.current; if (element) { element.currentTime = Math.max(0, Math.min(element.duration || 0, seconds)); setTime(element.currentTime); } };
  const nextSpeed = () => { const next = speed === 1 ? 1.25 : speed === 1.25 ? 1.5 : speed === 1.5 ? 2 : 1; setSpeed(next); if (videoRef.current) videoRef.current.playbackRate = next; };
  const sendChat = async (question?: string) => {
    const text = (question ?? chatDraft).trim();
    if (!session || !text || chatBusy) return;
    const id = session.id;
    if (question) setChatDraft(question);
    if (chatRequest.current?.text !== text) chatRequest.current = { id: crypto.randomUUID(), text };
    setChatBusy(true); setChatError('');
    try {
      await daily.flush();
      const result = await api.chat(id, chatRequest.current.id, text);
      if (sessionRef.current?.id !== id) return;
      acceptSession(result.session); setChatDraft(''); chatRequest.current = null;
    } catch (failure) { setChatError(readableError(failure)); }
    finally { setChatBusy(false); }
  };
  const viewEvidence = (id: string) => {
    setTab('transcript'); setPanelOpen(true); setFollow(false);
    requestAnimationFrame(() => {
      const target = [...(transcriptRef.current?.querySelectorAll<HTMLElement>('[data-turn-id]') || [])].find(row => row.dataset.turnId === id);
      target?.scrollIntoView({ block: 'center', behavior: 'smooth' });
      target?.focus();
    });
  };
  const elapsed = session?.startedAt ? Math.max(0, ((session.endedAt ? Date.parse(session.endedAt) : now) - Date.parse(session.startedAt)) / 1000) : 0;
  const leaveWorkspace = async () => {
    if (!await saveNote()) { setTab('note'); setPanelOpen(true); return; }
    try { await daily.leave(); onExit?.(); } catch (failure) { setError(readableError(failure)); }
  };

  return <div ref={rootRef} className={`mnd-root ${panelOpen ? '' : 'mnd-panel-hidden'} ${controls || !activeMedia ? 'mnd-controls-visible' : ''}`}>
    <section className="mnd-stage" aria-label="Встреча" onPointerMove={showControls} onPointerDown={showControls}>
      <header className="mnd-topbar">
        {onExit && <IconButton label={connected ? 'Выйти из встречи' : 'Вернуться в кабинет'} onClick={() => void leaveWorkspace()}><ArrowLeft /></IconButton>}
        <div className="mnd-history-anchor">
          <button className="mnd-history-trigger" onClick={() => setHistoryOpen(!historyOpen)} disabled={connected || daily.phase === 'joining'} aria-expanded={historyOpen}>
            <History size={17} /><span>{session ? sessionLabel(session) : 'Встречи'}</span><ChevronDown size={13} />
          </button>
          {historyOpen && <div className="mnd-history-menu">
            <button onClick={() => void newMeeting()}><Plus size={16} />Новая встреча</button>
            {sessions.map(item => <button key={item.id} onClick={() => void selectHistory(item.id)} className={item.id === session?.id ? 'mnd-current' : ''}>
              <span>{item.title}<small>{sessionLabel(item)}{item.status === 'live' ? ' · идёт' : ''}</small></span>
              {item.id === session?.id && <Check size={14} />}
            </button>)}
            {!sessions.length && <p>Здесь будут ваши встречи.</p>}
          </div>}
        </div>
        <div className="mnd-top-spacer" />
        {connected && <span className={`mnd-capture-state ${daily.capture.recording.status === 'started' ? 'mnd-recording' : ''}`}>
          <i />{daily.capture.recording.status === 'started' ? 'Запись' : daily.capture.recording.status === 'error' ? 'Без записи' : 'Подключаем запись'}
        </span>}
        {!panelOpen && <IconButton label="Показать материалы" onClick={() => setPanelOpen(true)} className="mnd-panel-restore"><PanelRightOpen /></IconButton>}
      </header>

      {connected ? <div className={`mnd-call-grid ${sharing ? 'mnd-sharing' : ''}`} data-count={Math.min(daily.participants.length, 4)}>
        {sharing && <Participant participant={sharing} screen />}
        <div className="mnd-people-grid">{daily.participants.map(participant => <Participant key={participant.session_id} participant={participant} />)}</div>
        {daily.participants.filter(participant => !participant.local).map(participant => <div key={`audio-${participant.session_id}`}>
          <Track track={participant.tracks.audio} audio /><Track track={participant.tracks.screenAudio} audio />
        </div>)}
      </div> : completed && recording ? <video ref={videoRef} className="mnd-recorded-video" src={recording.url} playsInline preload="metadata"
        muted={muted} onClick={togglePlay} onTimeUpdate={event => setTime(event.currentTarget.currentTime)}
        onLoadedMetadata={event => setDuration(event.currentTarget.duration)} onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)}
        onEnded={() => setPlaying(false)} onError={() => setVideoError('Запись не открылась. Обновите материалы.')} />
        : <div className="mnd-entry">
          {loading ? <LoaderCircle className="mnd-spinner" size={28} aria-label="Загрузка" /> : completed ? <>
            <div className="mnd-entry-symbol"><Video size={26} /></div>
            <h1>{session.title}</h1>
            <p>{materials?.video.status === 'error' ? 'Не удалось получить запись.' : ['unavailable', 'not_started', 'absent'].includes(materials?.video.status || '') ? 'У этой встречи нет записи.' : 'Daily готовит запись.'}</p>
            <button className="mnd-subtle-action" onClick={() => session && void refreshMaterials(session.id)} disabled={materialsBusy}>
              <RefreshCw size={15} className={materialsBusy ? 'mnd-spinner' : ''} />Обновить
            </button>
          </> : <>
            <div className="mnd-entry-symbol"><Video size={27} /></div>
            {session ? <h1>{session.title}</h1> : <input className="mnd-title-input" aria-label="Название встречи" value={title} onChange={event => setTitle(event.target.value)} maxLength={160} />}
            <p>Можно войти одному и начать говорить.</p>
            <label className="mnd-consent"><input type="checkbox" checked={consent} onChange={event => setConsent(event.target.checked)} />Согласен на запись и расшифровку</label>
            <button className="mnd-primary-action" disabled={!consent || busy || loading || capabilities?.dailyConfigured === false || !capabilities}
              onClick={() => void begin()}>{busy || daily.phase === 'joining' ? <LoaderCircle className="mnd-spinner" size={18} /> : <Phone size={18} />}
              {busy || daily.phase === 'joining' ? 'Подключаемся' : session?.status === 'live' ? 'Вернуться во встречу' : 'Войти'}</button>
            {capabilities?.dailyConfigured === false && <p className="mnd-entry-error">Видеосвязь пока не подключена.</p>}
          </>}
        </div>}

      {activeMedia && <div className="mnd-title-overlay" aria-hidden="true"><span>{session?.title}</span></div>}
      {(error || daily.error || daily.saveError || videoError) && <div className="mnd-error" role="alert">
        <span>{error || daily.error || daily.saveError || videoError}</span>
        {daily.saveError && <button onClick={() => void daily.flush().catch(() => undefined)}>Повторить</button>}
        {videoError && session && <button onClick={() => { setVideoError(''); void refreshMaterials(session.id, true); }}>Обновить</button>}
        {error && <IconButton label="Закрыть сообщение" onClick={() => setError('')}><X size={14} /></IconButton>}
      </div>}
      {connected && (['recording', 'transcription'] as const).filter(kind => daily.capture[kind].status === 'error').map(kind => <div key={kind} className={`mnd-capture-error mnd-capture-error-${kind}`} role="status">
        <span>{kind === 'recording' ? 'Запись' : 'Расшифровка'}: {daily.capture[kind].error || 'не удалось запустить'}</span>
        <button onClick={() => daily.startCapture(kind)}>Повторить</button>
      </div>)}

      {activeMedia && <div className="mnd-controls">
        {recording && !connected && <div className="mnd-timeline"><div style={{ width: `${duration ? time / duration * 100 : 0}%` }} />
          <input type="range" min={0} max={duration || 1} step={.1} value={time} aria-label="Позиция записи" onChange={event => seek(Number(event.target.value))} /></div>}
        <div className="mnd-control-row">
          {connected ? <>
            <IconButton label={local?.audio ? 'Выключить микрофон' : 'Включить микрофон'} onClick={daily.toggleMic} pressed={!local?.audio} className={!local?.audio ? 'mnd-off' : ''}>{local?.audio ? <Mic /> : <MicOff />}</IconButton>
            <IconButton label={local?.video ? 'Выключить камеру' : 'Включить камеру'} onClick={daily.toggleCamera} pressed={!local?.video}>{local?.video ? <Video /> : <VideoOff />}</IconButton>
            <IconButton label={local?.screen ? 'Остановить показ экрана' : 'Показать экран'} onClick={daily.toggleScreen} pressed={local?.screen}><MonitorUp /></IconButton>
            <span className="mnd-clock">{clock(elapsed)}</span>
            <div className="mnd-control-spacer" />
            <IconButton label="Скопировать ссылку для коллеги" onClick={() => void copyInvite()}><Copy /></IconButton>
            <button className="mnd-finish" onClick={() => void finish()} disabled={daily.phase === 'finishing'}>
              {daily.phase === 'finishing' ? <LoaderCircle className="mnd-spinner" size={17} /> : <PhoneOff size={17} />}<span>{daily.phase === 'finishing' ? 'Сохраняем' : 'Завершить'}</span>
            </button>
          </> : <>
            <IconButton label={playing ? 'Пауза' : 'Смотреть запись'} onClick={togglePlay}>{playing ? <Pause /> : <Play />}</IconButton>
            <IconButton label="Назад на 5 секунд" onClick={() => seek(time - 5)} className="mnd-skip"><Undo2 /><small>5</small></IconButton>
            <IconButton label="Вперёд на 5 секунд" onClick={() => seek(time + 5)} className="mnd-skip"><Redo2 /><small>5</small></IconButton>
            <IconButton label={muted ? 'Включить звук' : 'Выключить звук'} onClick={() => setMuted(!muted)}>{muted ? <VolumeX /> : <Volume2 />}</IconButton>
            <span className="mnd-clock">{clock(time)}<i>/ {clock(duration)}</i></span>
            <div className="mnd-control-spacer" />
            {(materials?.recordings.length || 0) > 1 && <select className="mnd-recording-select" aria-label="Запись встречи" value={recordingIndex} onChange={event => setRecordingIndex(Number(event.target.value))}>
              {materials?.recordings.map((item, index) => <option value={index} key={item.id}>Запись {index + 1}</option>)}
            </select>}
            <button className="mnd-icon mnd-speed" onClick={nextSpeed} aria-label={`Скорость ${speed}. Изменить`}>{speed}×</button>
          </>}
          <IconButton label={panelOpen ? 'Скрыть материалы' : 'Показать материалы'} onClick={() => setPanelOpen(!panelOpen)}>{panelOpen ? <PanelRightClose /> : <PanelRightOpen />}</IconButton>
          <IconButton label={full ? 'Выйти из полного экрана' : 'На весь экран'} onClick={() => void toggleFull()}>{full ? <Minimize2 /> : <Maximize2 />}</IconButton>
        </div>
      </div>}
      {toast && <div className="mnd-toast" role="status"><Check size={14} />{toast}</div>}
    </section>

    {panelOpen && <aside className="mnd-sidebar" aria-label="Материалы встречи">
      <div className="mnd-side-heading">
        <div className="mnd-tabs" role="tablist" aria-label="Материалы встречи">
          {([{ id: 'transcript', label: 'Расшифровка', icon: <AlignLeft /> }, { id: 'note', label: 'Заметка', icon: <SquarePen /> }, { id: 'chat', label: 'Чат', icon: <MessageSquare /> }] as const).map(item =>
            <button role="tab" id={`mnd-tab-${item.id}`} aria-controls={`mnd-panel-${item.id}`} aria-selected={tab === item.id} title={item.label} aria-label={item.label}
              key={item.id} onClick={() => setTab(item.id)}>{item.icon}</button>)}
        </div>
        <IconButton label="Скрыть материалы" onClick={() => setPanelOpen(false)} className="mnd-pane-hide"><PanelRightClose /></IconButton>
      </div>
      {tab === 'transcript' && <div className="mnd-panel" id="mnd-panel-transcript" role="tabpanel" aria-labelledby="mnd-tab-transcript">
        <div className="mnd-transcript" ref={transcriptRef} onWheel={() => setFollow(false)} onTouchMove={() => setFollow(false)}>
          {allTurns.map((turn, index) => <div key={turn.id} className="mnd-turn" data-active={index === activeTurn} data-turn-index={index} data-turn-id={turn.id} tabIndex={-1}>
            <button className="mnd-turn-content" disabled={!completed || !seekableTranscript} onClick={() => seek((turn.startMs - (offset || 0)) / 1000)}>
              <time>{clock(turn.startMs / 1000)}</time><span><small>{turn.speakerName}</small>{turn.text}</span>
            </button>
            <div className="mnd-turn-tools"><IconButton label="Добавить в заметку" onClick={() => {
              const next = `${draftRef.current}${draftRef.current ? '\n\n' : ''}${clock(turn.startMs / 1000)} · ${turn.speakerName}\n${turn.text}`;
              draftRef.current = next; setNoteDraft(next); if (noteState !== 'conflict') setNoteState('dirty'); setTab('note');
              requestAnimationFrame(() => noteRef.current?.focus());
            }}><SquarePen size={14} /></IconButton></div>
          </div>)}
          {!allTurns.length && <p className="mnd-panel-empty">{connected ? daily.capture.transcription.status === 'started' ? 'Говорите — здесь появится расшифровка.' : daily.capture.transcription.status === 'error' ? 'Расшифровка недоступна.' : 'Подключаем расшифровку…' : completed ? 'Расшифровка пока недоступна.' : 'Речь появится здесь во время встречи.'}</p>}
        </div>
        {!follow && allTurns.length > 0 && <button className="mnd-follow" onClick={() => setFollow(true)}><ArrowDown size={13} />{connected ? 'К текущей реплике' : 'К записи'}</button>}
      </div>}
      {tab === 'note' && <div className="mnd-panel mnd-note-panel" id="mnd-panel-note" role="tabpanel" aria-labelledby="mnd-tab-note">
        <textarea ref={noteRef} aria-label="Общая заметка" placeholder="Что важно запомнить…" value={noteDraft} disabled={!session}
          onChange={event => { draftRef.current = event.target.value; setNoteDraft(event.target.value); if (noteState !== 'conflict') setNoteState('dirty'); }}
          onBlur={() => { if (noteState === 'dirty') void saveNote(); }} />
        <div className="mnd-note-status" role="status">
          {noteState === 'conflict' ? <><span>Коллега обновил заметку. Ваш текст сохранён в поле.</span><button onClick={() => void saveNote(true)}>Сохранить мою версию</button><button onClick={() => { setNoteDraft(savedNotesRef.current.text); draftRef.current = savedNotesRef.current.text; noteConflict.current = false; setNoteState('saved'); }}>Открыть общую</button></>
            : noteState === 'error' ? <><span>{noteError}</span><button onClick={() => void saveNote()}>Повторить</button></>
              : <span>{!session ? 'Доступна после входа' : noteState === 'saving' ? 'Сохраняем…' : noteState === 'dirty' ? 'Не сохранено' : 'Сохранено для всех'}</span>}
        </div>
      </div>}
      {tab === 'chat' && <div className="mnd-panel mnd-chat-panel" id="mnd-panel-chat" role="tabpanel" aria-labelledby="mnd-tab-chat">
        <div className="mnd-messages" ref={chatRef}>
          {session?.messages.map(message => <div key={message.id} className={`mnd-message mnd-message-${message.role}`}>
            <p>{message.text}</p>
            {!!message.evidence?.length && <div className="mnd-evidence-links">{message.evidence.map(evidence => {
              const turn = allTurns.find(item => item.id === evidence.sourceId);
              return turn ? <button key={evidence.id} title={evidence.quote} onClick={() => viewEvidence(turn.id)}>{clock(turn.startMs / 1000)}</button>
                : <details key={evidence.id}><summary>{evidence.sourceType === 'human-note' ? 'Заметка' : Number.isFinite(evidence.startMs) ? clock((evidence.startMs || 0) / 1000) : 'Источник'}</summary><blockquote>{evidence.quote}</blockquote></details>;
            })}</div>}
          </div>)}
          {!session?.messages.length && <p className="mnd-panel-empty">{capabilities?.modelConfigured === false ? 'Чат пока не подключён.' : 'Спросите о том, что обсудили.'}</p>}
          {completed && !session?.messages.length && allTurns.length > 0 && capabilities?.modelConfigured !== false && <button className="mnd-summary-action" disabled={chatBusy} onClick={() => void sendChat('Кратко подведи итог встречи: что обсудили, к чему пришли и какие следующие шаги явно назвали. Опирайся только на материалы встречи.')}>Собрать итог</button>}
          {chatBusy && <div className="mnd-chat-pending" role="status"><LoaderCircle className="mnd-spinner" size={16} />Смотрю расшифровку…</div>}
        </div>
        {chatError && <p className="mnd-chat-error" role="alert">{chatError}</p>}
        <form className="mnd-chat-composer" onSubmit={event => { event.preventDefault(); void sendChat(); }}>
          <textarea aria-label="Вопрос по встрече" placeholder="Спросить о встрече…" value={chatDraft}
            disabled={!session || capabilities?.modelConfigured === false || chatBusy} onChange={event => setChatDraft(event.target.value)}
            onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void sendChat(); } }} rows={1} />
          <button type="submit" aria-label="Отправить вопрос" disabled={!session || !chatDraft.trim() || chatBusy || capabilities?.modelConfigured === false}><ArrowUp size={19} /></button>
        </form>
      </div>}
    </aside>}
  </div>;
}

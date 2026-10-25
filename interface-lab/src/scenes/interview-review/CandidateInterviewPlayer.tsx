import { useCallback, useEffect, useId, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { AlignLeft, ArrowUp, Captions, CaptionsOff, ChartNoAxesCombined, Check, ChevronDown, ChevronLeft, ChevronUp, Link as LinkIcon, Maximize2, MessageSquare, Minimize2, PanelRightClose, PanelRightOpen, Pause, Play, Redo2, Search, SmilePlus, Sparkles, Tv, Undo2, Volume2, VolumeX } from 'lucide-react';
import { Tooltip } from 'radix-ui';
import { useInterviewAssistant } from './useInterviewAssistant';
import { InterviewChatAnswer } from './InterviewChatAnswer';
import { UnifiedInterviewTranscript } from './UnifiedInterviewTranscript';
import { parseMeetingTranscript } from '../connected-journey/transcript';
import type { RecordingReviewProps } from './InterviewReview';
import { interviewTranscriptSections, interviewTime as time, interviewTimeline } from './candidate-player-model';
import { OrbitGraph } from '../interview-assessment/OrbitGraph';
import { FragmentQuestion } from '../interview-assessment/FragmentQuestion';
import { useInterviewAssessment } from '../interview-assessment/useInterviewAssessment';
import './candidate-interview-player.css';

type Tab = 'transcript' | 'assistant' | 'comments';
type Menu = 'speed' | 'quality' | 'emoji' | null;
const emoji = ['❤️', '👍', '🔥', '👏', '🙌', '👀'];
const moreEmoji = ['💡', '✅', '💬', '🎯', '🤔', '⭐', '💪', '🚀'];

function IconButton({ label, children, onClick, className = '', pressed, expanded, disabled }: {
  label: string; children: ReactNode; onClick: () => void; className?: string; pressed?: boolean; expanded?: boolean; disabled?: boolean;
}) {
  return <button type="button" className={`iv-icon ${className}`} aria-label={label} data-tip={label}
    aria-pressed={pressed} aria-expanded={expanded} disabled={disabled} onClick={onClick}>{children}</button>;
}

/** Candidate materials are supplied by the authorised caller. No fixtures or local comment store. */
export function CandidateInterviewPlayer({ sourceId, name, header, media, chapters, fullChapters, transcriptText = '', emptyMessage,
  initialEvidenceIndex, renderTimeComment, comments = [], canComment = false, transcriptSeekable = true, assistant, assessment, readOnly = false }: RecordingReviewProps) {
  const id = useId();
  const root = useRef<HTMLDivElement>(null);
  const video = useRef<HTMLVideoElement>(null);
  const side = useRef<HTMLElement>(null);
  const transcriptList = useRef<HTMLDivElement>(null);
  const lastFocus = useRef<HTMLElement | null>(null);
  const pendingSeek = useRef<number | null>(null);
  const hideTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const reactionTimers = useRef<ReturnType<typeof setTimeout>[]>([]);
  const nativeFull = useRef(false);
  const [full, setFull] = useState(false);
  const [theatre, setTheatre] = useState(false);
  const [tab, setTab] = useState<Tab>(assessment ? 'comments' : 'transcript');
  const explicitTabSelection = useRef(false);
  const [commentHistory, setCommentHistory] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [current, setCurrent] = useState(0);
  const [duration, setDuration] = useState(0);
  const assessmentFlow = useInterviewAssessment(assessment, current, playing);
  useEffect(() => { if (assessment && !explicitTabSelection.current) setTab('comments'); }, [assessment?.revisionId]);
  const [volume, setVolume] = useState(0.8);
  const [muted, setMuted] = useState(false);
  const [captions, setCaptions] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [resolution, setResolution] = useState('');
  const [menu, setMenu] = useState<Menu>(null);
  const [hoverTime, setHoverTime] = useState<number | null>(null);
  const [controls, setControls] = useState(true);
  const [follow, setFollow] = useState(true);
  const [commentTime, setCommentTime] = useState<number | null>(null);
  const [commentRequest, setCommentRequest] = useState(0);
  const [query, setQuery] = useState('');
  const [chatQuery, setChatQuery] = useState('');
  const [portal, setPortal] = useState<HTMLElement | null>(null);
  const chatList = useRef<HTMLDivElement>(null);
  const assist = useInterviewAssistant(assistant);
  const [hit, setHit] = useState(0);
  useEffect(() => setPortal(root.current), []);
  useEffect(() => { chatList.current?.scrollTo({ top: chatList.current.scrollHeight, behavior: 'instant' }); }, [assist.messages, assist.busy, tab]);
  const [toast, setToast] = useState('');
  const [error, setError] = useState('');
  const [floating, setFloating] = useState<{ id: string; emoji: string }[]>([]);
  const topics = useMemo(() => (fullChapters?.length ? fullChapters : chapters), [chapters, fullChapters]);
  const { paragraphs, sections } = useMemo(() => interviewTranscriptSections(parseMeetingTranscript(transcriptText), topics), [transcriptText, topics]);
  const segments = useMemo(() => interviewTimeline(topics, duration), [topics, duration]);
  const activeIndex = paragraphs.reduce((found, row, index) => row.start <= current ? index : found, 0);
  const active = transcriptSeekable ? paragraphs[activeIndex] : undefined;
  const activeChapter = topics.reduce((found, row, index) => row.seconds !== null && row.seconds <= current ? index : found, -1);
  const matchedRows = useMemo(() => paragraphs.flatMap((row, index) => assist.search?.matches.some(match =>
    (row.end === row.start ? row.start * 1000 >= match.startMs : row.end * 1000 > match.startMs)
    && (match.endMs === match.startMs ? row.start * 1000 <= match.endMs : row.start * 1000 < match.endMs)) ? [index] : []), [paragraphs, assist.search]);
  useEffect(() => { setHit(0); }, [assist.search]);
  useEffect(() => {
    if (!assist.search || tab !== 'transcript') return;
    const list = transcriptList.current;
    const row = list?.querySelector<HTMLElement>(`[data-line-index="${matchedRows[hit]}"]`);
    if (list && row) list.scrollTo({ top: Math.max(0, row.getBoundingClientRect().top - list.getBoundingClientRect().top + list.scrollTop - list.clientHeight * .32), behavior: 'instant' });
  }, [assist.search, hit, tab, matchedRows]);
  async function submitSearch() { setFollow(false); await assist.submit('search', query); }
  async function submitChat(value = chatQuery) {
    if (assist.busy || !value.trim()) return;
    setChatQuery(''); await assist.submit('chat', value);
  }

  const notify = useCallback((message: string) => {
    setToast(message); clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(''), 2500);
  }, []);
  const followMoment = useCallback((smooth = false) => {
    const list = tab === 'transcript' ? transcriptList.current : null;
    const row = list?.querySelector<HTMLElement>('[data-active="true"]');
    if (!row || !list) return;
    const top = row.getBoundingClientRect().top - list.getBoundingClientRect().top + list.scrollTop;
    list.scrollTo({ top: Math.max(0, top - list.clientHeight * .32), behavior: smooth && !matchMedia('(prefers-reduced-motion: reduce)').matches ? 'smooth' : 'instant' });
  }, [tab]);
  useEffect(() => {
    if (!follow || theatre || (tab === 'transcript' && !transcriptSeekable)) return;
    const frame = requestAnimationFrame(() => followMoment(playing));
    return () => cancelAnimationFrame(frame);
  }, [follow, activeIndex, activeChapter, tab, full, theatre, paragraphs, playing, followMoment, transcriptSeekable]);
  useEffect(() => {
    if (!side.current || !follow) return;
    const observer = new ResizeObserver(() => followMoment());
    observer.observe(side.current); return () => observer.disconnect();
  }, [follow, followMoment]);
  useEffect(() => {
    const at = initialEvidenceIndex === undefined ? null : topics[initialEvidenceIndex]?.seconds;
    if (pendingSeek.current === null) pendingSeek.current = at ?? null;
  }, [sourceId]);
  useEffect(() => {
    if (!video.current) return;
    video.current.volume = volume; video.current.muted = muted; video.current.playbackRate = speed;
  }, [volume, muted, speed, media?.src]);
  useEffect(() => {
    const pauseOther = (event: Event) => { if ((event as CustomEvent).detail !== video.current) video.current?.pause(); };
    window.addEventListener('hr-vision-interview-play', pauseOther);
    return () => { video.current?.pause(); window.removeEventListener('hr-vision-interview-play', pauseOther); };
  }, []);
  useEffect(() => () => {
    clearTimeout(hideTimer.current); clearTimeout(toastTimer.current);
    reactionTimers.current.forEach(clearTimeout);
  }, []);
  function revealControls() {
    setControls(true); clearTimeout(hideTimer.current);
    hideTimer.current = setTimeout(() => setControls(false), 2200);
  }
  const seek = useCallback((value: number) => {
    if (!Number.isFinite(value) || value < 0) return false;
    const player = video.current;
    if (!player || player.readyState < 1) { pendingSeek.current = value; assessmentFlow.seek(value); return true; }
    if (!Number.isFinite(player.duration) || value >= player.duration) { notify('Таймкод вне этой записи'); return false; }
    player.currentTime = value; setCurrent(value); setFollow(true); pendingSeek.current = null; assessmentFlow.seek(value);
    return true;
  }, [notify, assessmentFlow.seek]);
  const togglePlay = useCallback(() => {
    const player = video.current;
    if (!player) return;
    if (!player.paused) player.pause();
    else { setError(''); void player.play().catch(cause => { if (cause.name !== 'AbortError') setError('Не удалось воспроизвести запись'); }); }
  }, []);
  const exitFull = useCallback(() => {
    setFull(false); setMenu(null);
    if (document.fullscreenElement === root.current) void document.exitFullscreen().catch(() => {});
    requestAnimationFrame(() => lastFocus.current?.focus());
  }, []);
  const toggleFull = useCallback(() => {
    if (full) { exitFull(); return; }
    lastFocus.current = document.activeElement as HTMLElement;
    setFull(true); setMenu(null);
    if (root.current?.requestFullscreen) void root.current.requestFullscreen().catch(() => {});
  }, [full, exitFull]);
  useEffect(() => {
    const changed = () => {
      if (document.fullscreenElement === root.current) nativeFull.current = true;
      else if (nativeFull.current) { nativeFull.current = false; setFull(false); }
    };
    document.addEventListener('fullscreenchange', changed);
    return () => document.removeEventListener('fullscreenchange', changed);
  }, []);
  useEffect(() => {
    if (!full) return;
    const old = document.body.style.overflow; document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = old; };
  }, [full]);
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      if (event.defaultPrevented || target.closest('[role="dialog"]')) return;
      if (!root.current?.contains(target) && !full) return;
      if (event.key === 'Escape') {
        if (menu) setMenu(null); else if (full) exitFull();
        return;
      }
      if (target.closest('input, textarea, select, button, [contenteditable], [role="tab"]') || event.ctrlKey || event.metaKey || event.altKey) return;
      if (event.code === 'Space') { event.preventDefault(); togglePlay(); }
      if (event.key === 'ArrowRight') { event.preventDefault(); seek(Math.min(duration - .1, current + 5)); }
      if (event.key === 'ArrowLeft') { event.preventDefault(); seek(Math.max(0, current - 5)); }
      if (event.key.toLowerCase() === 'f') toggleFull();
      if (event.key.toLowerCase() === 'm') setMuted(value => !value);
    };
    window.addEventListener('keydown', key); return () => window.removeEventListener('keydown', key);
  }, [current, duration, full, menu, togglePlay, seek, toggleFull, exitFull]);
  useEffect(() => {
    if (!menu) return;
    const close = (event: PointerEvent) => { if (!(event.target as HTMLElement).closest('[data-iv-menu]')) setMenu(null); };
    window.addEventListener('pointerdown', close); return () => window.removeEventListener('pointerdown', close);
  }, [menu]);
  function openComment(at = current) {
    if (!canComment || !renderTimeComment) return;
    explicitTabSelection.current = true;
    video.current?.pause(); setTheatre(false); setTab('comments'); setCommentHistory(true); setCommentTime(at); setCommentRequest(value => value + 1); setMenu(null);
  }
  function openCommentHistory() { explicitTabSelection.current = true; setTheatre(false); setTab('comments'); setCommentHistory(true); setMenu(null); }
  function react(value: string) {
    const item = { id: crypto.randomUUID(), emoji: value };
    setFloating(items => [...items, item]); setMenu(null);
    // Reactions are transient feedback, not persisted evaluations of a candidate.
    const timer = setTimeout(() => { setFloating(items => items.filter(row => row.id !== item.id)); reactionTimers.current = reactionTimers.current.filter(t => t !== timer); }, 1800);
    reactionTimers.current.push(timer);
  }
  async function copyMoment(at: number) {
    const url = new URL(location.href); url.searchParams.set('interview', sourceId); url.searchParams.set('t', String(Math.floor(at)));
    try { await navigator.clipboard.writeText(url.toString()); notify(`Ссылка на ${time(at)} скопирована`); }
    catch { notify('Не удалось скопировать ссылку'); }
  }
  function chooseTab(next: Tab) { explicitTabSelection.current = true; setTab(next); setCommentHistory(false); setMenu(null); }
  const reactions = (inPlayer = false) => <div className={`iv-reactions${inPlayer ? ' iv-reactions-inline' : ''}`}>
    {emoji.map((value, i) => <button type="button" className={`iv-emoji iv-emoji-${i}`} key={value} aria-label={`Реакция ${value}`} onClick={() => react(value)}>{value}</button>)}
    <div className="iv-menu-anchor" data-iv-menu><IconButton label="Другие реакции" expanded={menu === 'emoji'} onClick={() => setMenu(menu === 'emoji' ? null : 'emoji')}><SmilePlus/></IconButton>
      {menu === 'emoji' && <div className="iv-popover iv-emoji-menu" role="menu" aria-label="Реакции">{moreEmoji.map(value => <button type="button" role="menuitem" key={value} onClick={() => react(value)}>{value}</button>)}</div>}
    </div>
    {canComment && <><span className="iv-reaction-divider"/><button type="button" className="iv-write" aria-label="Написать комментарий" onClick={() => openComment()}><MessageSquare/>{inPlayer && <span>Написать</span>}</button></>}
    {!canComment && assessment && comments.length > 0 && <button type="button" className="iv-write" aria-label="Комментарии к видео" onClick={openCommentHistory}><MessageSquare/></button>}
  </div>;

  return <Tooltip.Provider delayDuration={650} skipDelayDuration={250}><div ref={root} className={`iv-root${full ? ' iv-full' : ''}${theatre ? ' iv-theatre' : ''}${assessment ? ' iv-has-assessment' : ''}${assessmentFlow.visible ? ' iv-has-question' : ''}`} data-source-id={sourceId}>
    <div className="iv-workspace">
      <section className={`iv-player${!playing || controls || menu ? ' iv-controls-visible' : ''}`} tabIndex={0} aria-label={`Интервью — ${name}`}
        onPointerMove={revealControls} onPointerLeave={() => { if (playing) setControls(false); }}>
        {media ? <video ref={video} src={media.src} preload="metadata" playsInline aria-label={`Видеозапись: ${name}`}
          onClick={togglePlay} onDoubleClick={toggleFull} onLoadedMetadata={event => {
            const player = event.currentTarget;
            setDuration(Number.isFinite(player.duration) ? player.duration : 0); setResolution(`${player.videoWidth} × ${player.videoHeight}`);
            player.volume = volume; player.muted = muted; player.playbackRate = speed;
            const params = new URLSearchParams(location.search);
            const linkedTime = params.get('interview') === sourceId ? Number(params.get('t')) : null;
            const requested = pendingSeek.current ?? linkedTime ?? current;
            if (requested !== null && requested >= 0 && requested < player.duration) seek(requested);
          }} onTimeUpdate={event => setCurrent(event.currentTarget.currentTime)}
          onPlay={() => { setPlaying(true); revealControls(); window.dispatchEvent(new CustomEvent('hr-vision-interview-play', { detail: video.current })); }}
          onPause={() => setPlaying(false)} onEnded={event => { setPlaying(false); assessmentFlow.end(event.currentTarget.currentTime); }} onError={() => setError('Не удалось открыть запись')}/>
          : <div className="iv-media-empty">{emptyMessage || 'Записи пока нет'}</div>}
        {header ? <div className="iv-identity-header">{header}</div>
          : <div className={`iv-video-title${captions ? ' iv-title-hidden' : ''}`} aria-hidden="true"><span className="iv-video-name">{name}</span></div>}
        {error && <button type="button" className="iv-media-error" onClick={() => { setError(''); video.current?.load(); }}>{error}<span>Попробовать ещё раз</span></button>}
        {media && !playing && !error && <button type="button" className="iv-big-play" aria-label="Воспроизвести интервью" onClick={togglePlay}><Play fill="currentColor" strokeWidth={0}/></button>}
        {captions && active && current <= active.end && <div className="iv-captions"><span>{active.text}</span></div>}
        <div className="iv-floating" aria-hidden="true">{floating.map((item, i) => <span key={item.id} style={{ '--drift': `${(i % 3 - 1) * 35}px` } as CSSProperties}>{item.emoji}</span>)}</div>
        {theatre && <IconButton className="iv-panel-restore" label="Показать боковую панель" onClick={() => setTheatre(false)}><PanelRightOpen/></IconButton>}

        {media && <div className="iv-player-controls" onFocusCapture={() => setControls(true)}>
          <div className="iv-timeline" onPointerMove={event => { const rect = event.currentTarget.getBoundingClientRect(); setHoverTime(Math.max(0, Math.min(duration, (event.clientX - rect.left) / rect.width * duration))); }} onPointerLeave={() => setHoverTime(null)}>
            <div className="iv-track" aria-hidden="true">{segments.map(segment => <span key={segment.start} style={{ flexGrow: segment.end - segment.start }}><i style={{ width: `${Math.min(100, Math.max(0, (current - segment.start) / (segment.end - segment.start) * 100))}%` }}/></span>)}</div>
            {duration > 0 && comments.filter(c => c.seconds < duration).map(c => <i key={c.id} className="iv-timeline-comment" style={{ left: `${c.seconds / duration * 100}%` }} title={`${time(c.seconds)} · ${c.text}`}/>)}
            <input type="range" aria-label="Позиция видео" min="0" max={duration || 1} step="0.1" disabled={!duration} value={current} aria-valuetext={`${time(current)} из ${time(duration)}`} onChange={event => seek(Math.min(duration - .05, Number(event.target.value)))}/>
            <span className="iv-playhead" aria-hidden="true" style={{ left: `${duration ? current / duration * 100 : 0}%` }}/>
            {hoverTime !== null && duration > 0 && <div className="iv-time-preview" style={{ left: `${Math.max(8, Math.min(92, hoverTime / duration * 100))}%` }}><span>{topics.filter(c => c.seconds !== null && c.seconds <= hoverTime).at(-1)?.title}</span><b>{time(hoverTime)}</b></div>}
          </div>
          <div className="iv-control-row">
            <IconButton label={playing ? 'Пауза' : 'Воспроизвести'} onClick={togglePlay}>{playing ? <Pause fill="currentColor" strokeWidth={0}/> : <Play fill="currentColor" strokeWidth={0}/>}</IconButton>
            <IconButton label="Назад на 5 секунд" className="iv-skip" onClick={() => seek(Math.max(0, current - 5))}><Undo2/><small>5</small></IconButton>
            <IconButton label="Вперёд на 5 секунд" className="iv-skip" onClick={() => seek(Math.min(duration - .05, current + 5))}><Redo2/><small>5</small></IconButton>
            <div className="iv-volume"><IconButton label={muted ? 'Включить звук' : 'Выключить звук'} onClick={() => setMuted(value => !value)}>{muted || !volume ? <VolumeX/> : <Volume2/>}</IconButton><div className="iv-volume-slider"><input type="range" aria-label="Громкость" min="0" max="1" step="0.05" value={muted ? 0 : volume} onChange={event => { setVolume(Number(event.target.value)); setMuted(false); }}/></div></div>
            <div className="iv-clock"><span>{time(current)}</span><span className="iv-duration"> / {time(duration)}</span></div>
            {full && !readOnly && reactions(true)}<span className="iv-control-spacer"/>
            <div className="iv-menu-anchor" data-iv-menu><button type="button" className="iv-speed iv-icon" aria-label="Скорость воспроизведения" aria-haspopup="menu" aria-expanded={menu === 'speed'} onClick={() => setMenu(menu === 'speed' ? null : 'speed')}>{speed}<span>×</span></button>
              {menu === 'speed' && <div className="iv-popover iv-option-menu" role="menu" aria-label="Скорость воспроизведения">{[.8, 1, 1.2, 1.5, 1.7, 2].map(value => <button type="button" role="menuitemradio" aria-checked={speed === value} key={value} onClick={() => { setSpeed(value); setMenu(null); }}>{value}×{speed === value && <Check size={14}/>}</button>)}</div>}
            </div>
            <div className="iv-menu-anchor" data-iv-menu><IconButton label="Качество записи" expanded={menu === 'quality'} onClick={() => setMenu(menu === 'quality' ? null : 'quality')}><span className="iv-quality-badge">HD</span></IconButton>{menu === 'quality' && <div className="iv-popover iv-source-quality">Оригинал{resolution && <span>{resolution}</span>}</div>}</div>
            <IconButton label={captions ? 'Выключить субтитры' : 'Включить субтитры'} pressed={captions} disabled={!paragraphs.length || !transcriptSeekable} onClick={() => setCaptions(value => !value)}>{captions ? <Captions/> : <CaptionsOff/>}</IconButton>
            <IconButton label={theatre ? 'Показать расшифровку' : 'Режим кинотеатра'} pressed={theatre} onClick={() => setTheatre(value => !value)}><Tv/></IconButton>
            <IconButton label={full ? 'Выйти из полноэкранного режима' : 'На весь экран'} pressed={full} onClick={toggleFull}>{full ? <Minimize2/> : <Maximize2/>}</IconButton>
          </div>
        </div>}
      </section>

      <aside ref={side} className="iv-sidebar" aria-label="Материалы интервью" hidden={theatre}>
        <div className="iv-side-heading"><div role="tablist" aria-label="Материалы интервью" className="iv-tabs">
          {([{id:'transcript', label:'Расшифровка', icon:AlignLeft}, {id:'assistant', label:'Спросить ИИ', icon:Sparkles}, {id:'comments', label:assessment ? 'Оценка навыков' : 'Комментарии', icon:assessment ? ChartNoAxesCombined : MessageSquare}] as const).filter(item => !readOnly || item.id === 'transcript' || (item.id === 'comments' && assessment)).map((item, index, items) => <Tooltip.Root key={item.id}>
            <Tooltip.Trigger asChild><button type="button" id={`${id}-${item.id}-tab`} role="tab" aria-controls={`${id}-${item.id}`} aria-selected={tab === item.id} tabIndex={tab === item.id ? 0 : -1} aria-label={item.label} onClick={() => chooseTab(item.id)} onKeyDown={event => {
              if (event.key === 'ArrowRight' || event.key === 'ArrowLeft' || event.key === 'Home' || event.key === 'End') {
                event.preventDefault(); const next = items[event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : items.length - 1)) % items.length];
                chooseTab(next.id); document.getElementById(`${id}-${next.id}-tab`)?.focus();
              }
            }}><item.icon/>{item.id === 'comments' && comments.length > 0 && <i className="iv-comment-dot"/>}</button></Tooltip.Trigger>
            <Tooltip.Portal container={portal}><Tooltip.Content className="iv-tooltip" side="bottom" sideOffset={9}>{item.label}</Tooltip.Content></Tooltip.Portal>
          </Tooltip.Root>)}
        </div><IconButton className="iv-panel-hide" label="Скрыть боковую панель" onClick={() => setTheatre(true)}><PanelRightClose/></IconButton></div>

        {tab === 'transcript' && <div className="iv-panel iv-transcript-panel" id={`${id}-transcript`} role="tabpanel" aria-labelledby={`${id}-transcript-tab`}>
          <UnifiedInterviewTranscript list={transcriptList} sections={sections} seekable={transcriptSeekable} seek={seek} pauseFollow={() => setFollow(false)}
            renderRow={(row, index) => <div key={`${row.start}:${index}`} className="iv-line" data-line-index={index} data-match={matchedRows.includes(index)} data-search-current={matchedRows[hit] === index} data-active={transcriptSeekable && index === activeIndex}>
              <button type="button" className="iv-line-content" disabled={!transcriptSeekable} onClick={() => seek(row.start)} aria-label={`${time(row.start)} ${row.text}`} aria-current={transcriptSeekable && index === activeIndex ? 'true' : undefined}><time>{time(row.start)}</time><span>{row.text}</span></button>
              {transcriptSeekable && <div className="iv-line-tools"><IconButton label={`Ссылка на ${time(row.start)}`} onClick={() => { void copyMoment(row.start); }}><LinkIcon/></IconButton>{canComment && <IconButton label={`Комментарий к ${time(row.start)}`} onClick={() => openComment(row.start)}><MessageSquare/></IconButton>}</div>}
            </div>}
            empty={<p className="iv-plain-transcript">{transcriptText.trim() && !/^\s*WEBVTT\b/.test(transcriptText) ? transcriptText : 'Расшифровки пока нет'}</p>}/>
        </div>}
        {tab === 'transcript' && !assist.search && !follow && transcriptSeekable && <button type="button" className="iv-follow" onClick={() => { setFollow(true); followMoment(true); }}><AlignLeft size={14}/>К текущему моменту<ChevronDown size={13}/></button>}
        {tab === 'assistant' && <div className="iv-panel iv-chat" id={`${id}-assistant`} role="tabpanel" aria-labelledby={`${id}-assistant-tab`}>
          <div ref={chatList} className="iv-chat-scroll" role="log" aria-label="Диалог об интервью" aria-live="polite">
            {!assist.messages.length && <div className="iv-chat-suggestions">{['Кратко расскажи об опыте', 'Как работает с возражениями?', 'Что стоит уточнить на встрече?'].map(value => <button type="button" key={value} disabled={!!assist.busy || !assistant} onClick={() => { void submitChat(value); }}>{value}</button>)}</div>}
            {assist.messages.map(message => <div className="iv-chat-turn" key={message.id}>
              <p className="iv-chat-question">{message.query}</p>
              {message.answer && <><InterviewChatAnswer answer={message.answer} seek={seek} canSeek={transcriptSeekable} portal={portal}/>{message.answer.coverage.omitted > 0 && <small className="iv-chat-coverage">Ответ по выбранным фрагментам записи.</small>}</>}
              {message.error && <div className="iv-assistant-error" role="alert"><p>{message.error}</p><button type="button" disabled={!!assist.busy} onClick={() => { void assist.submit('chat', message.query, message.id); }}>Повторить</button></div>}
              {!message.answer && !message.error && <p className="iv-chat-pending" role="status">Читаю интервью…</p>}
            </div>)}
          </div>
        </div>}
        {assessment && <div hidden={tab !== 'comments' || commentHistory} className="iv-panel iv-assessment-panel" id={commentHistory ? undefined : `${id}-comments`} role="tabpanel" aria-labelledby={`${id}-comments-tab`}>
          <OrbitGraph key={assessment.revisionId} criteria={assessment.criteria} selectedId={assessmentFlow.selectedId} onSelect={assessmentFlow.selectCriterion}
            onEvidence={(evidence, criterionId) => { if (evidence.seconds !== null && seek(evidence.seconds)) assessmentFlow.source(evidence, criterionId); }}/>
        </div>}
        {<div hidden={tab !== 'comments' || (!!assessment && !commentHistory)} className="iv-panel iv-comments" id={assessment && !commentHistory ? undefined : `${id}-comments`} role="tabpanel" aria-labelledby={`${id}-comments-tab`}>
          {assessment && <button type="button" className="iv-comments-return" onClick={() => setCommentHistory(false)}><ChevronLeft size={14}/>К оценке навыков</button>}
          <div className="iv-comment-list">
            {!comments.length && commentTime === null && <div className="iv-no-comments"><MessageSquare size={30}/><span>Нет комментариев</span></div>}
            {!comments.length && commentTime !== null && <div className="iv-comment-context"><time>{time(commentTime)}</time>{transcriptSeekable && <blockquote>{paragraphs.filter(row => row.start <= commentTime).at(-1)?.text}</blockquote>}</div>}
            {comments.map(comment => <article className="iv-comment" key={comment.id}><div className="iv-comment-body"><div className="iv-comment-meta"><button type="button" className="iv-time-link" onClick={() => seek(comment.seconds)}>{time(comment.seconds)}</button><span>{new Date(comment.createdAt).toLocaleDateString('ru-RU', {day:'numeric', month:'short', timeZone:'Europe/Moscow'})}</span></div><p>{comment.text}</p></div></article>)}
          </div>
          {commentTime !== null && renderTimeComment ? <div className="iv-comment-editor" key={commentTime}>{renderTimeComment(commentTime, () => setCommentTime(null), commentRequest)}</div>
            : canComment && <button type="button" className="iv-new-comment" onClick={() => openComment()}><MessageSquare size={17}/>Написать</button>}
        </div>}
        {!readOnly && (tab === 'transcript' || tab === 'assistant') && <div className="iv-side-input">
          {tab === 'transcript' && <>
            {assist.searchError && <p className="iv-assistant-error" role="alert">{assist.searchError}</p>}
            {assist.search && <div className="iv-search-state" role="status"><span>{matchedRows.length ? `${hit + 1} / ${matchedRows.length}` : 'Подходящих фрагментов не найдено'}</span>
              {!!matchedRows.length && <><IconButton label="Предыдущее совпадение" onClick={() => setHit(value => (value + matchedRows.length - 1) % matchedRows.length)}><ChevronUp/></IconButton><IconButton label="Следующее совпадение" onClick={() => setHit(value => (value + 1) % matchedRows.length)}><ChevronDown/></IconButton></>}
              <button type="button" onClick={() => { assist.clearSearch(); setQuery(''); setFollow(true); }}>Сбросить</button>
            </div>}
          </>}
          <form className="iv-assistant-composer" onSubmit={event => { event.preventDefault(); if (tab === 'assistant') void submitChat(); else void submitSearch(); }}>
            {tab === 'assistant' ? <Sparkles size={19}/> : <Search size={19}/>}
            <input aria-label={tab === 'assistant' ? 'Спросить ИИ об интервью' : 'Поиск по смыслу в расшифровке'} maxLength={tab === 'assistant' ? 2000 : 300}
              placeholder={tab === 'assistant' ? 'Спросите ИИ об интервью…' : 'Найти по смыслу в расшифровке…'}
              title={tab === 'assistant' ? 'ИИ ответит по записи и покажет цитаты' : 'Jev выделит подходящие фрагменты расшифровки'}
              value={tab === 'assistant' ? chatQuery : query} onChange={event => tab === 'assistant' ? setChatQuery(event.target.value) : setQuery(event.target.value)}/>
            <button type="submit" aria-label={tab === 'assistant' ? 'Отправить вопрос ИИ' : 'Выделить фрагменты'} disabled={!!assist.busy || !assistant || (tab === 'assistant' ? chatQuery : query).trim().length < 2}>
              {(tab === 'assistant' && assist.busy === 'chat') || (tab === 'transcript' && assist.busy === 'search') ? <span className="iv-assistant-spinner"/> : <ArrowUp size={19}/>}
            </button>
          </form>
          {assist.busy === 'search' && tab === 'transcript' && <span className="iv-sr-only" role="status">Ищу подходящие фрагменты…</span>}
          {!assistant && <span className="iv-assistant-unavailable">Помощник появится, когда будет готова расшифровка этой записи.</span>}
        </div>}
      </aside>
      {!readOnly && <div className="iv-below-player">{assessment ? <FragmentQuestion key={assessment.revisionId} assessment={assessment} block={assessmentFlow.visible}
        overlapping={assessmentFlow.overlapping} playing={playing} commentOpen={!!assessmentFlow.playback.commentLock}
        onCommentOpen={assessmentFlow.openComment} onCommentClose={assessmentFlow.closeComment} onChoose={assessmentFlow.selectQuestion} fallback={reactions()}
        legacyComment={(canComment || comments.length > 0) ? <button type="button" className="iv-write" aria-label="Комментарии к видео" title="Комментарии к видео" onClick={openCommentHistory}><MessageSquare size={16}/></button> : null}/>
        : reactions()}</div>}
    </div>
    {toast && <div className="iv-toast" role="status"><Check size={15}/>{toast}</div>}
  </div></Tooltip.Provider>;
}

import { useCallback, useEffect, useId, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { AlignLeft, ArrowUp, Bookmark, Captions, CaptionsOff, Check, ChevronDown, Link as LinkIcon, Maximize2, MessageSquare, Minimize2, PanelRightClose, PanelRightOpen, Pause, Play, Redo2, Search, SmilePlus, Sparkles, Tv, Undo2, Volume2, VolumeX, X } from 'lucide-react';
import { parseMeetingTranscript } from '../connected-journey/transcript';
import type { RecordingReviewProps } from './InterviewReview';
import { interviewParagraphs, interviewTime as time, interviewTimeline } from './candidate-player-model';
import './candidate-interview-player.css';

type Tab = 'chapters' | 'transcript' | 'comments';
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
export function CandidateInterviewPlayer({ sourceId, name, media, chapters, fullChapters, transcriptText = '', emptyMessage,
  initialEvidenceIndex, renderTimeComment, comments = [], canComment = false, transcriptSeekable = true }: RecordingReviewProps) {
  const id = useId();
  const root = useRef<HTMLDivElement>(null);
  const video = useRef<HTMLVideoElement>(null);
  const side = useRef<HTMLElement>(null);
  const transcriptList = useRef<HTMLDivElement>(null);
  const chapterList = useRef<HTMLDivElement>(null);
  const lastFocus = useRef<HTMLElement | null>(null);
  const pendingSeek = useRef<number | null>(null);
  const hideTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const reactionTimers = useRef<ReturnType<typeof setTimeout>[]>([]);
  const nativeFull = useRef(false);
  const [full, setFull] = useState(false);
  const [theatre, setTheatre] = useState(false);
  const [tab, setTab] = useState<Tab>('transcript');
  const [playing, setPlaying] = useState(false);
  const [current, setCurrent] = useState(0);
  const [duration, setDuration] = useState(0);
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
  const [query, setQuery] = useState('');
  const [searchOpen, setSearchOpen] = useState(false);
  const [toast, setToast] = useState('');
  const [error, setError] = useState('');
  const [floating, setFloating] = useState<{ id: string; emoji: string }[]>([]);
  const paragraphs = useMemo(() => interviewParagraphs(parseMeetingTranscript(transcriptText)), [transcriptText]);
  const topics = useMemo(() => (fullChapters?.length ? fullChapters : chapters), [chapters, fullChapters]);
  const segments = useMemo(() => interviewTimeline(topics, duration), [topics, duration]);
  const activeIndex = paragraphs.reduce((found, row, index) => row.start <= current ? index : found, 0);
  const active = transcriptSeekable ? paragraphs[activeIndex] : undefined;
  const activeChapter = topics.reduce((found, row, index) => row.seconds !== null && row.seconds <= current ? index : found, -1);
  const matches = query.trim().length > 1 ? paragraphs.filter(row => row.text.toLocaleLowerCase('ru').includes(query.trim().toLocaleLowerCase('ru'))).slice(0, 30) : [];

  const notify = useCallback((message: string) => {
    setToast(message); clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(''), 2500);
  }, []);
  const followMoment = useCallback((smooth = false) => {
    const list = tab === 'transcript' ? transcriptList.current : tab === 'chapters' ? chapterList.current : null;
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
    if (!Number.isFinite(value) || value < 0) return;
    const player = video.current;
    if (!player || player.readyState < 1) { pendingSeek.current = value; return; }
    if (!Number.isFinite(player.duration) || value >= player.duration) { notify('Таймкод вне этой записи'); return; }
    player.currentTime = value; setCurrent(value); setFollow(true); pendingSeek.current = null;
  }, [notify]);
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
      if (!root.current?.contains(target) && !full) return;
      if (event.key === 'Escape') {
        if (menu) setMenu(null); else if (searchOpen) setSearchOpen(false); else if (full) exitFull();
        return;
      }
      if (target.closest('input, textarea, button, [contenteditable], [role="tab"]') || event.ctrlKey || event.metaKey || event.altKey) return;
      if (event.code === 'Space') { event.preventDefault(); togglePlay(); }
      if (event.key === 'ArrowRight') { event.preventDefault(); seek(Math.min(duration - .1, current + 5)); }
      if (event.key === 'ArrowLeft') { event.preventDefault(); seek(Math.max(0, current - 5)); }
      if (event.key.toLowerCase() === 'f') toggleFull();
      if (event.key.toLowerCase() === 'm') setMuted(value => !value);
    };
    window.addEventListener('keydown', key); return () => window.removeEventListener('keydown', key);
  }, [current, duration, full, menu, searchOpen, togglePlay, seek, toggleFull, exitFull]);
  useEffect(() => {
    if (!menu) return;
    const close = (event: PointerEvent) => { if (!(event.target as HTMLElement).closest('[data-iv-menu]')) setMenu(null); };
    window.addEventListener('pointerdown', close); return () => window.removeEventListener('pointerdown', close);
  }, [menu]);
  function openComment(at = current) {
    if (!canComment || !renderTimeComment) return;
    video.current?.pause(); setTheatre(false); setTab('comments'); setCommentTime(at); setMenu(null); setSearchOpen(false);
  }
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
  function chooseTab(next: Tab) { setTab(next); setMenu(null); }
  const reactions = (inPlayer = false) => <div className={`iv-reactions${inPlayer ? ' iv-reactions-inline' : ''}`}>
    {emoji.map((value, i) => <button type="button" className={`iv-emoji iv-emoji-${i}`} key={value} aria-label={`Реакция ${value}`} onClick={() => react(value)}>{value}</button>)}
    <div className="iv-menu-anchor" data-iv-menu><IconButton label="Другие реакции" expanded={menu === 'emoji'} onClick={() => setMenu(menu === 'emoji' ? null : 'emoji')}><SmilePlus/></IconButton>
      {menu === 'emoji' && <div className="iv-popover iv-emoji-menu" role="menu" aria-label="Реакции">{moreEmoji.map(value => <button type="button" role="menuitem" key={value} onClick={() => react(value)}>{value}</button>)}</div>}
    </div>
    {canComment && <><span className="iv-reaction-divider"/><button type="button" className="iv-write" aria-label="Написать комментарий" onClick={() => openComment()}><MessageSquare/>{inPlayer && <span>Написать</span>}</button></>}
  </div>;

  return <div ref={root} className={`iv-root${full ? ' iv-full' : ''}${theatre ? ' iv-theatre' : ''}`} data-source-id={sourceId}>
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
          onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)} onError={() => setError('Не удалось открыть запись')}/>
          : <div className="iv-media-empty">{emptyMessage || 'Записи пока нет'}</div>}
        <div className={`iv-video-title${captions ? ' iv-title-hidden' : ''}`} aria-hidden="true"><span className="iv-video-name">{name}</span></div>
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
            {full && reactions(true)}<span className="iv-control-spacer"/>
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
          {([{id:'chapters', label:'Ключевые моменты', icon:Bookmark}, {id:'transcript', label:'Расшифровка', icon:AlignLeft}, {id:'comments', label:'Комментарии', icon:MessageSquare}] as const).map((item, index, items) => <button type="button" key={item.id} id={`${id}-${item.id}-tab`} role="tab" aria-controls={`${id}-${item.id}`} aria-selected={tab === item.id} tabIndex={tab === item.id ? 0 : -1} aria-label={item.label} data-tip={item.label} onClick={() => chooseTab(item.id)} onKeyDown={event => {
            if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') { event.preventDefault(); const next = items[(index + (event.key === 'ArrowRight' ? 1 : 2)) % 3]; chooseTab(next.id); document.getElementById(`${id}-${next.id}-tab`)?.focus(); }
          }}><item.icon/>{item.id === 'comments' && comments.length > 0 && <i className="iv-comment-dot"/>}</button>)}
        </div><IconButton className="iv-panel-hide" label="Скрыть боковую панель" onClick={() => setTheatre(true)}><PanelRightClose/></IconButton></div>

        {tab === 'transcript' && <div className="iv-panel iv-transcript-panel" id={`${id}-transcript`} role="tabpanel" aria-labelledby={`${id}-transcript-tab`}>
          <div ref={transcriptList} className="iv-transcript" onWheel={() => setFollow(false)} onTouchMove={() => setFollow(false)}>
            {paragraphs.map((row, index) => <div key={`${row.start}:${index}`} className="iv-line" data-active={transcriptSeekable && index === activeIndex}>
              <button type="button" className="iv-line-content" disabled={!transcriptSeekable} onClick={() => seek(row.start)} aria-label={`${time(row.start)} ${row.text}`} aria-current={transcriptSeekable && index === activeIndex ? 'true' : undefined}><time>{time(row.start)}</time><span>{row.text}</span></button>
              {transcriptSeekable && <div className="iv-line-tools"><IconButton label={`Ссылка на ${time(row.start)}`} onClick={() => { void copyMoment(row.start); }}><LinkIcon/></IconButton>{canComment && <IconButton label={`Комментарий к ${time(row.start)}`} onClick={() => openComment(row.start)}><MessageSquare/></IconButton>}</div>}
            </div>)}
            {!paragraphs.length && <p className="iv-plain-transcript">{transcriptText.trim() && !/^\s*WEBVTT\b/.test(transcriptText) ? transcriptText : 'Расшифровки пока нет'}</p>}
          </div>
        </div>}
        {tab === 'chapters' && <div className="iv-panel iv-chapters-panel" id={`${id}-chapters`} role="tabpanel" aria-labelledby={`${id}-chapters-tab`}>
          <div ref={chapterList} className="iv-chapters" onWheel={() => setFollow(false)} onTouchMove={() => setFollow(false)}>
            {topics.map((chapter, index) => <button type="button" key={index} className={`iv-chapter${index === activeChapter ? ' iv-selected' : ''}`} data-active={index === activeChapter} aria-current={index === activeChapter ? 'true' : undefined} disabled={chapter.seconds === null} onClick={() => { if (chapter.seconds !== null) seek(chapter.seconds); }}><time>{chapter.seconds === null ? '—' : time(chapter.seconds)}</time><span>{chapter.title}</span>{index === activeChapter && <span className="iv-chapter-bars" aria-hidden="true"><i/><i/><i/></span>}</button>)}
            {!topics.length && <p className="iv-plain-transcript">Ключевых моментов пока нет</p>}
          </div>
        </div>}
        {tab !== 'comments' && !follow && transcriptSeekable && <button type="button" className="iv-follow" onClick={() => { setFollow(true); followMoment(true); }}><AlignLeft size={14}/>К текущему моменту<ChevronDown size={13}/></button>}
        {tab === 'comments' && <div className="iv-panel iv-comments" id={`${id}-comments`} role="tabpanel" aria-labelledby={`${id}-comments-tab`}>
          <div className="iv-comment-list">
            {!comments.length && commentTime === null && <div className="iv-no-comments"><MessageSquare size={30}/><span>Нет комментариев</span></div>}
            {!comments.length && commentTime !== null && <div className="iv-comment-context"><time>{time(commentTime)}</time>{transcriptSeekable && <blockquote>{paragraphs.filter(row => row.start <= commentTime).at(-1)?.text}</blockquote>}</div>}
            {comments.map(comment => <article className="iv-comment" key={comment.id}><div className="iv-comment-body"><div className="iv-comment-meta"><button type="button" className="iv-time-link" onClick={() => seek(comment.seconds)}>{time(comment.seconds)}</button><span>{new Date(comment.createdAt).toLocaleDateString('ru-RU', {day:'numeric', month:'short', timeZone:'Europe/Moscow'})}</span></div><p>{comment.text}</p></div></article>)}
          </div>
          {commentTime !== null && renderTimeComment ? <div className="iv-comment-editor" key={commentTime}>{renderTimeComment(commentTime, () => setCommentTime(null))}</div>
            : canComment && <button type="button" className="iv-new-comment" onClick={() => openComment()}><MessageSquare size={17}/>Написать</button>}
        </div>}
      </aside>
      <div className="iv-below-player"><form className="iv-ask" onSubmit={event => { event.preventDefault(); setSearchOpen(true); }}><Sparkles/><input aria-label="Найти в интервью" placeholder="Найти в интервью…" value={query} onChange={event => setQuery(event.target.value)}/><IconButton label="Поиск по расшифровке" onClick={() => setSearchOpen(true)}>{query ? <ArrowUp/> : <Search/>}</IconButton></form>{reactions()}</div>
    </div>
    {searchOpen && <section className="iv-search-panel" role="dialog" aria-label="Поиск по интервью"><header><span><Search size={18}/>Поиск по интервью</span><IconButton label="Закрыть поиск" onClick={() => setSearchOpen(false)}><X/></IconButton></header><form onSubmit={event => event.preventDefault()}><Search size={18}/><input autoFocus aria-label="Поисковый запрос" value={query} onChange={event => setQuery(event.target.value)} placeholder="Слово или фраза"/></form><div className="iv-search-results">{query.length > 1 && !matches.length && <p>Совпадений нет</p>}{matches.map((row, index) => <button type="button" key={index} disabled={!transcriptSeekable} onClick={() => { seek(row.start); setTheatre(false); chooseTab('transcript'); setSearchOpen(false); }}><time>{time(row.start)}</time><span>{row.text}</span></button>)}</div></section>}
    {toast && <div className="iv-toast" role="status"><Check size={15}/>{toast}</div>}
  </div>;
}

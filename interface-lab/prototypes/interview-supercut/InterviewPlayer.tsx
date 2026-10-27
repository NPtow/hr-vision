import { useCallback, useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import {
  AlignLeft, ArrowLeft, ArrowUp, Bookmark, Captions, CaptionsOff, Check,
  ChevronDown, Copy, FlaskConical, Link as LinkIcon, Maximize2, MessageSquare,
  Mic, Minimize2, PanelRightClose, PanelRightOpen, Pause, Play, Redo2, Search, SmilePlus, Sparkles, Trash2,
  Tv, Undo2, Volume2, VolumeX, X,
} from 'lucide-react';
import '@fontsource-variable/inter';
import './interview-player.css';

type Tab = 'chapters' | 'transcript' | 'comments';
type Paragraph = { start: number; end: number; speaker: string; text: string };
type Interview = {
  id: string; name: string; duration: number; video: string; poster: string;
  initialTime: number; transcript: Paragraph[];
  chapters: { start: number; title: string; description: string }[];
};
type Comment = { id: string; text: string; time: number | null; created: number; parent?: string };
type Reaction = { id: string; emoji: string; time: number };
type Menu = 'speed' | 'quality' | 'emoji' | 'voice' | null;
const storageKey = 'hrvision:local-supercut-prototype:378713:v1';
const emoji = ['❤️', '👍', '🔥', '👏', '🙌', '👀'];
const moreEmoji = ['💡', '✅', '💬', '🎯', '🤔', '⭐', '💪', '🚀'];

function time(seconds: number) {
  const s = Math.max(0, Math.floor(seconds));
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
}
function stored(): { comments: Comment[]; reactions: Reaction[] } {
  try {
    const value = JSON.parse(localStorage.getItem(storageKey) || '{}');
    return {
      comments: Array.isArray(value.comments) ? value.comments.filter((c: Comment) => typeof c.text === 'string' && typeof c.id === 'string' && (c.time === null || Number.isFinite(c.time))) : [],
      reactions: Array.isArray(value.reactions) ? value.reactions.filter((r: Reaction) => typeof r.emoji === 'string' && Number.isFinite(r.time)) : [],
    };
  } catch { return { comments: [], reactions: [] }; }
}
function IconButton({ label, children, onClick, className = '', pressed, ...rest }: {
  label: string; children: ReactNode; onClick?: () => void; className?: string; pressed?: boolean;
  disabled?: boolean; 'aria-expanded'?: boolean; 'aria-haspopup'?: 'menu' | 'dialog';
}) {
  return <button type="button" className={`sp-icon ${className}`} aria-label={label} data-tip={label}
    aria-pressed={pressed} onClick={onClick} {...rest}>{children}</button>;
}

export function InterviewPlayer({ initialView = 'review' }: { initialView?: 'review' | 'fullscreen' | 'comments' }) {
  const [interview, setInterview] = useState<Interview | null>(null);
  const [loadError, setLoadError] = useState('');
  const [playError, setPlayError] = useState('');
  const root = useRef<HTMLDivElement>(null);
  const video = useRef<HTMLVideoElement>(null);
  const transcriptList = useRef<HTMLDivElement>(null);
  const commentInput = useRef<HTMLTextAreaElement>(null);
  const searchInput = useRef<HTMLInputElement>(null);
  const [full, setFull] = useState(initialView === 'fullscreen');
  const [theatre, setTheatre] = useState(false);
  const [tab, setTab] = useState<Tab>(initialView === 'comments' ? 'comments' : 'transcript');
  const [playing, setPlaying] = useState(false);
  const [current, setCurrent] = useState(443);
  const [duration, setDuration] = useState(2184);
  const [volume, setVolume] = useState(0.8);
  const [muted, setMuted] = useState(false);
  const [captions, setCaptions] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [quality, setQuality] = useState('auto');
  const [resolution, setResolution] = useState(1080);
  const [menu, setMenu] = useState<Menu>(null);
  const [hoverTime, setHoverTime] = useState<number | null>(null);
  const [showControls, setShowControls] = useState(true);
  const hideControls = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const [follow, setFollow] = useState(true);
  const [comments, setComments] = useState<Comment[]>(() => stored().comments);
  const [reactions, setReactions] = useState<Reaction[]>(() => stored().reactions);
  const [floating, setFloating] = useState<Reaction[]>([]);
  const [composer, setComposer] = useState(initialView === 'comments');
  const [draft, setDraft] = useState('');
  const [anchor, setAnchor] = useState<number | null>(443);
  const [replyTo, setReplyTo] = useState<string | undefined>();
  const [query, setQuery] = useState('');
  const [searchOpen, setSearchOpen] = useState(false);
  const [info, setInfo] = useState(false);
  const [toast, setToast] = useState('');
  const previousFocus = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetch('/interview-media/interview.json', { signal: controller.signal })
      .then(r => { if (!r.ok) throw new Error('Не удалось загрузить интервью'); return r.json(); })
      .then((data: Interview) => {
        setInterview(data); setDuration(data.duration);
        const requested = Number(new URLSearchParams(location.search).get('t'));
        const initial = requested > 0 && requested < data.duration ? requested : data.initialTime;
        setCurrent(initial); setAnchor(initial);
      })
      .catch(e => { if (e.name !== 'AbortError') setLoadError('Не удалось загрузить интервью'); });
    return () => controller.abort();
  }, []);

  const notify = useCallback((message: string) => {
    setToast(message); clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(''), 2600);
  }, []);
  useEffect(() => {
    try { localStorage.setItem(storageKey, JSON.stringify({ comments, reactions })); }
    catch { notify('Не удалось сохранить на этом устройстве'); }
  }, [comments, reactions, notify]);
  useEffect(() => () => { clearTimeout(hideControls.current); clearTimeout(toastTimer.current); }, []);
  useEffect(() => {
    if (video.current) { video.current.volume = volume; video.current.muted = muted; video.current.playbackRate = speed; }
  }, [volume, muted, speed, interview]);

  const activeIndex = interview ? interview.transcript.reduce((found, row, i) => row.start <= current ? i : found, 0) : 0;
  const active = interview?.transcript[activeIndex];
  const activeChapter = interview?.chapters.reduce((found, row, i) => row.start <= current ? i : found, 0) ?? 0;
  const moveTranscript = useCallback((smooth = true) => {
    const list = transcriptList.current;
    const row = list?.querySelector<HTMLElement>('[data-active="true"]');
    if (list && row) list.scrollTo({ top: row.offsetTop - list.offsetTop - list.clientHeight * 0.32, behavior: smooth && !matchMedia('(prefers-reduced-motion: reduce)').matches ? 'smooth' : 'instant' });
  }, []);
  useEffect(() => {
    if (!follow || tab !== 'transcript') return;
    const frame = requestAnimationFrame(() => moveTranscript(playing));
    return () => cancelAnimationFrame(frame);
  }, [activeIndex, tab, full, theatre, follow, interview, moveTranscript, playing]);
  useEffect(() => { if (composer && tab === 'comments') commentInput.current?.focus(); }, [composer, tab]);

  function revealControls() {
    setShowControls(true); clearTimeout(hideControls.current);
    hideControls.current = setTimeout(() => setShowControls(false), 2200);
  }
  const seek = useCallback((value: number) => {
    const safe = Math.max(0, Math.min(duration - 0.05, value));
    if (video.current && video.current.readyState >= 1) video.current.currentTime = safe;
    setCurrent(safe); setFollow(true);
  }, [duration]);
  const togglePlay = useCallback(() => {
    const player = video.current;
    if (!player) return;
    if (player.paused) {
      setPlayError('');
      void player.play().catch(() => setPlayError('Видео не воспроизводится. Попробовать ещё раз'));
    } else player.pause();
  }, []);
  const exitFull = useCallback(() => {
    setFull(false); setMenu(null);
    if (document.fullscreenElement) void document.exitFullscreen().catch(() => {});
    requestAnimationFrame(() => previousFocus.current?.focus());
  }, []);
  const toggleFull = useCallback(() => {
    if (full) { exitFull(); return; }
    previousFocus.current = document.activeElement as HTMLElement;
    setFull(true); setMenu(null);
    // The same video node and transcript remain mounted when the workspace expands.
    if (root.current?.requestFullscreen) void root.current.requestFullscreen().catch(() => {});
  }, [full, exitFull]);
  useEffect(() => {
    const changed = () => { if (!document.fullscreenElement) setFull(false); };
    document.addEventListener('fullscreenchange', changed);
    return () => document.removeEventListener('fullscreenchange', changed);
  }, []);
  useEffect(() => {
    if (!full) return;
    const old = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = old; };
  }, [full]);
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      if (e.key === 'Escape') {
        if (menu) setMenu(null);
        else if (searchOpen) setSearchOpen(false);
        else if (composer) setComposer(false);
        else if (full) exitFull();
        return;
      }
      if (target.closest('input, textarea, [contenteditable], [role="tab"], [role="menu"]') || e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.code === 'Space') { e.preventDefault(); togglePlay(); }
      if (e.key === 'ArrowRight') { e.preventDefault(); seek((video.current?.currentTime ?? current) + 5); }
      if (e.key === 'ArrowLeft') { e.preventDefault(); seek((video.current?.currentTime ?? current) - 5); }
      if (e.key.toLowerCase() === 'f') toggleFull();
      if (e.key.toLowerCase() === 'm') setMuted(v => !v);
      if (e.key.toLowerCase() === 'c') setCaptions(v => !v);
    };
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  }, [current, full, composer, menu, searchOpen, seek, togglePlay, toggleFull, exitFull]);
  useEffect(() => {
    if (!menu) return;
    const dismiss = (e: PointerEvent) => { if (!(e.target as HTMLElement).closest('[data-menu-root]')) setMenu(null); };
    window.addEventListener('pointerdown', dismiss);
    return () => window.removeEventListener('pointerdown', dismiss);
  }, [menu]);

  function openComment(at = current, parent?: string) {
    video.current?.pause(); setTheatre(false); setTab('comments'); setComposer(true);
    setAnchor(at); setReplyTo(parent); setMenu(null); setSearchOpen(false);
  }
  function saveComment() {
    if (!draft.trim()) return;
    setComments(items => [...items, { id: crypto.randomUUID(), text: draft.trim(), time: anchor, parent: replyTo, created: Date.now() }]);
    setDraft(''); setComposer(false); setReplyTo(undefined); notify('Комментарий сохранён');
  }
  function react(value: string) {
    const item = { id: crypto.randomUUID(), emoji: value, time: current };
    setReactions(list => [...list, item]); setFloating(list => [...list, item]); setMenu(null);
  }
  async function copyMoment(at: number) {
    const url = new URL(location.href); url.searchParams.set('t', String(Math.floor(at)));
    try { await navigator.clipboard.writeText(url.toString()); notify(`Ссылка на ${time(at)} скопирована`); }
    catch { notify('Не удалось скопировать ссылку'); }
  }
  function selectTab(next: Tab) { setTab(next); setTheatre(false); setMenu(null); }
  const reactionButtons = (inPlayer = false) => <div className={`sp-reactions${inPlayer ? ' sp-reactions-inline' : ''}`}>
    {emoji.map((value, i) => <button type="button" key={value} className={`sp-emoji sp-emoji-${i}`} aria-label={`Реакция ${value}`} data-tip={value} onClick={() => react(value)}>{value}</button>)}
    <div className="sp-menu-anchor" data-menu-root>
      <IconButton label="Другие реакции" onClick={() => setMenu(menu === 'emoji' ? null : 'emoji')} aria-expanded={menu === 'emoji'}><SmilePlus/></IconButton>
      {menu === 'emoji' && <div className="sp-popover sp-emoji-menu" role="menu" aria-label="Реакции">{moreEmoji.map(value => <button type="button" role="menuitem" key={value} onClick={() => react(value)}>{value}</button>)}</div>}
    </div>
    <span className="sp-reaction-divider"/>
    <button type="button" className="sp-write" aria-label="Написать комментарий" data-tip="Комментарий к моменту" onClick={() => openComment()}><MessageSquare/>{inPlayer && <span>Написать</span>}</button>
  </div>;

  if (loadError) return <main className="sp-failed"><p>{loadError}</p><button onClick={() => location.reload()}>Попробовать ещё раз</button></main>;
  if (!interview) return <main className="sp-loading" aria-label="Загрузка интервью"><div/><aside/></main>;
  const matches = query.trim().length > 1 ? interview.transcript.filter(row => row.text.toLocaleLowerCase('ru').includes(query.trim().toLocaleLowerCase('ru'))).slice(0, 20) : [];

  return <div ref={root} className={`sp-root${full ? ' sp-full' : ''}${theatre ? ' sp-theatre' : ''}`}>
    <header className="sp-header">
      <div className="sp-brand" aria-label="HR Vision"><svg viewBox="0 0 38 30" aria-hidden="true"><path d="M17 1C7 0 0 8 1 15c1 5 7 5 11 2l9-6c6-5 3-9-4-10ZM21 29c10 1 17-7 16-14-1-5-7-5-11-2l-9 6c-6 5-3 9 4 10Z" fill="currentColor"/></svg><span>HR Vision</span></div>
      <div className="sp-lab-info"><IconButton label="О прототипе" onClick={() => setInfo(v => !v)}><FlaskConical size={17}/></IconButton>
        {info && <div className="sp-info"><strong>Локальный прототип</strong><p>Исходное интервью ДСА. Комментарии и реакции сохраняются только в этом браузере. Поиск работает по расшифровке.</p><a href="https://supercut.ai/share/fluently/rtpYwcxVQYLaPIMPdSaCXI" target="_blank" rel="noreferrer">Референс ↗</a></div>}
      </div>
    </header>

    <main className="sp-workspace">
      <section className={`sp-player${!playing || showControls || menu ? ' sp-controls-visible' : ''}`} aria-label={`Интервью — ${interview.name}`}
        onPointerMove={revealControls} onPointerLeave={() => { if (playing) setShowControls(false); }}>
        <video ref={video} src={interview.video} poster={interview.poster} preload="metadata" playsInline
          onClick={togglePlay} onDoubleClick={toggleFull} aria-label="Видеозапись интервью"
          onLoadedMetadata={e => {
            const player = e.currentTarget; setDuration(player.duration); setResolution(player.videoHeight);
            player.currentTime = Math.min(current, player.duration - 0.05); player.volume = volume; player.playbackRate = speed;
          }}
          onTimeUpdate={e => setCurrent(e.currentTarget.currentTime)}
          onPlay={() => { setPlaying(true); revealControls(); }} onPause={() => setPlaying(false)}
          onEnded={() => setPlaying(false)} onError={() => setPlayError('Видео не загрузилось. Попробовать ещё раз')}/>
        <div className="sp-video-title" aria-hidden="true"><span>{interview.name}</span></div>
        {theatre && <IconButton className="sp-panel-restore" label="Показать боковую панель" onClick={() => setTheatre(false)}><PanelRightOpen/></IconButton>}
        {playError && <button className="sp-media-error" onClick={() => { setPlayError(''); video.current?.load(); }}>{playError}</button>}
        {!playing && !playError && <button type="button" className="sp-big-play" aria-label="Воспроизвести интервью" onClick={togglePlay}><Play fill="currentColor" strokeWidth={0}/></button>}
        {captions && active && current < active.end + 1 && <div className="sp-captions"><span>{active.text}</span></div>}
        <div className="sp-floating" aria-hidden="true">{floating.map((item, i) => <span key={item.id} style={{ '--drift': `${(i % 3 - 1) * 35}px` } as CSSProperties} onAnimationEnd={() => setFloating(items => items.filter(r => r.id !== item.id))}>{item.emoji}</span>)}</div>

        <div className="sp-player-controls" onFocusCapture={() => setShowControls(true)}>
          <div className="sp-timeline" onPointerMove={e => { const rect = e.currentTarget.getBoundingClientRect(); setHoverTime(Math.max(0, Math.min(duration, (e.clientX - rect.left) / rect.width * duration))); }} onPointerLeave={() => setHoverTime(null)}>
            <div className="sp-track" aria-hidden="true">{interview.chapters.map((chapter, i) => {
              const end = interview.chapters[i + 1]?.start ?? duration;
              return <span key={chapter.start} style={{ flexGrow: end - chapter.start }}><i style={{ width: `${Math.min(100, Math.max(0, (current - chapter.start) / (end - chapter.start) * 100))}%` }}/></span>;
            })}</div>
            {comments.filter(c => c.time !== null && !c.parent).map(c => <i key={c.id} className="sp-timeline-comment" style={{ left: `${c.time! / duration * 100}%` }} title={`${time(c.time!)} · ${c.text}`}/>)}
            <input aria-label="Позиция видео" type="range" min="0" max={duration} step="0.1" value={current} onChange={e => seek(Number(e.target.value))} aria-valuetext={`${time(current)} из ${time(duration)}`}/>
            <span className="sp-playhead" aria-hidden="true" style={{ left: `${current / duration * 100}%` }}/>
            {hoverTime !== null && <div className="sp-time-preview" style={{ left: `${Math.max(8, Math.min(92, hoverTime / duration * 100))}%` }}><span>{interview.chapters.filter(c => c.start <= hoverTime).at(-1)?.title}</span><b>{time(hoverTime)}</b></div>}
          </div>
          <div className="sp-control-row">
            <IconButton label={playing ? 'Пауза' : 'Воспроизвести'} onClick={togglePlay}>{playing ? <Pause fill="currentColor" strokeWidth={0}/> : <Play fill="currentColor" strokeWidth={0}/>}</IconButton>
            <IconButton label="Назад на 5 секунд" className="sp-skip" onClick={() => seek(current - 5)}><Undo2/><small>5</small></IconButton>
            <IconButton label="Вперёд на 5 секунд" className="sp-skip" onClick={() => seek(current + 5)}><Redo2/><small>5</small></IconButton>
            <div className="sp-volume"><IconButton label={muted ? 'Включить звук' : 'Выключить звук'} onClick={() => setMuted(v => !v)}>{muted || volume === 0 ? <VolumeX/> : <Volume2/>}</IconButton><div className="sp-volume-slider"><input type="range" aria-label="Громкость" min="0" max="1" step="0.05" value={muted ? 0 : volume} onChange={e => { setVolume(Number(e.target.value)); setMuted(false); }}/></div></div>
            <div className="sp-clock"><span>{time(current)}</span><span className="sp-duration"> / {time(duration)}</span></div>
            {full && reactionButtons(true)}
            <span className="sp-control-spacer"/>
            <div className="sp-menu-anchor" data-menu-root>
              <button type="button" className="sp-speed sp-icon" aria-label="Скорость воспроизведения" data-tip="Скорость" aria-haspopup="menu" aria-expanded={menu === 'speed'} onClick={() => setMenu(menu === 'speed' ? null : 'speed')}>{speed}<span>×</span></button>
              {menu === 'speed' && <div className="sp-popover sp-option-menu" role="menu" aria-label="Скорость воспроизведения">{[0.75, 1, 1.25, 1.5, 1.75, 2].map(value => <button type="button" role="menuitemradio" aria-checked={speed === value} key={value} onClick={() => { setSpeed(value); setMenu(null); }}>{value}×{speed === value && <Check size={14}/>}</button>)}</div>}
            </div>
            <div className="sp-menu-anchor" data-menu-root>
              <IconButton label="Качество видео" onClick={() => setMenu(menu === 'quality' ? null : 'quality')} aria-expanded={menu === 'quality'} aria-haspopup="menu"><span className="sp-quality-badge">HD</span></IconButton>
              {menu === 'quality' && <div className="sp-popover sp-option-menu sp-quality-menu" role="menu" aria-label="Качество видео">{[{id:'auto',label:'Авто'},{id:'original',label:`Оригинал · ${resolution}p`}].map(item => <button type="button" key={item.id} role="menuitemradio" aria-checked={quality === item.id} onClick={() => { setQuality(item.id); setMenu(null); }}>{item.label}{quality === item.id && <Check size={14}/>}</button>)}</div>}
            </div>
            <IconButton label={captions ? 'Выключить субтитры' : 'Включить субтитры'} pressed={captions} onClick={() => setCaptions(v => !v)}>{captions ? <Captions/> : <CaptionsOff/>}</IconButton>
            <IconButton label={theatre ? 'Показать расшифровку' : 'Режим кинотеатра'} pressed={theatre} onClick={() => setTheatre(v => !v)}><Tv/></IconButton>
            <IconButton label={full ? 'Выйти из полноэкранного режима' : 'На весь экран'} pressed={full} onClick={toggleFull}>{full ? <Minimize2/> : <Maximize2/>}</IconButton>
          </div>
        </div>
      </section>

      <aside className="sp-sidebar" aria-label="Материалы интервью" hidden={theatre}>
        <div className="sp-side-heading">
          <div role="tablist" aria-label="Материалы интервью" className="sp-tabs">
            {([{id:'chapters',label:'Ключевые моменты',icon:Bookmark},{id:'transcript',label:'Расшифровка',icon:AlignLeft},{id:'comments',label:'Комментарии',icon:MessageSquare}] as const).map((item, i, items) => <button type="button" key={item.id} id={`sp-tab-${item.id}`} role="tab" aria-controls={`sp-panel-${item.id}`} aria-selected={tab === item.id} tabIndex={tab === item.id ? 0 : -1} aria-label={item.label} data-tip={item.label} onClick={() => selectTab(item.id)} onKeyDown={e => {
              if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') { e.preventDefault(); const next = items[(i + (e.key === 'ArrowRight' ? 1 : 2)) % 3]; selectTab(next.id); document.getElementById(`sp-tab-${next.id}`)?.focus(); }
            }}><item.icon/>{item.id === 'comments' && comments.length > 0 && <i className="sp-comment-dot"/>}</button>)}
          </div>
          <IconButton className="sp-panel-hide" label="Скрыть боковую панель" onClick={() => setTheatre(true)}><PanelRightClose/></IconButton>
        </div>

        {tab === 'transcript' && <div className="sp-panel sp-transcript-panel" id="sp-panel-transcript" role="tabpanel" aria-labelledby="sp-tab-transcript">
          <div ref={transcriptList} className="sp-transcript" onWheel={() => setFollow(false)} onTouchMove={() => setFollow(false)}>
            {interview.transcript.map((row, i) => <div key={i} className="sp-line" data-active={i === activeIndex}>
              <button type="button" className="sp-line-content" onClick={() => seek(row.start)} aria-label={`${time(row.start)} ${row.text}`} aria-current={i === activeIndex ? 'true' : undefined}><time>{time(row.start)}</time><span>{row.text}</span></button>
              <div className="sp-line-tools"><IconButton label={`Ссылка на ${time(row.start)}`} onClick={() => { void copyMoment(row.start); }}><LinkIcon size={15}/></IconButton><IconButton label={`Комментарий к ${time(row.start)}`} onClick={() => openComment(row.start)}><MessageSquare size={15}/></IconButton></div>
            </div>)}
          </div>
          {!follow && <button className="sp-follow" onClick={() => { setFollow(true); moveTranscript(); }}><AlignLeft size={14}/>К текущему моменту<ChevronDown size={13}/></button>}
        </div>}

        {tab === 'chapters' && <div className="sp-panel sp-chapters" id="sp-panel-chapters" role="tabpanel" aria-labelledby="sp-tab-chapters">
          {interview.chapters.map((chapter, i) => <button type="button" key={chapter.start} className={`sp-chapter${i === activeChapter ? ' sp-selected' : ''}`} onClick={() => seek(chapter.start)}><time>{time(chapter.start)}</time><span><strong>{chapter.title}</strong></span>{i === activeChapter && <span className="sp-chapter-bars"><i/><i/><i/></span>}</button>)}
        </div>}

        {tab === 'comments' && <div className="sp-panel sp-comments" id="sp-panel-comments" role="tabpanel" aria-labelledby="sp-tab-comments">
          <div className="sp-comment-list">
            {!comments.length && !composer && <div className="sp-no-comments"><MessageSquare size={30}/><span>Нет комментариев</span></div>}
            {!comments.length && composer && <div className="sp-comment-context"><time>{time(anchor ?? current)}</time><blockquote>{interview.transcript.filter(row => row.start <= (anchor ?? current)).at(-1)?.text}</blockquote></div>}
            {comments.filter(c => !c.parent).map(c => <article className="sp-comment" key={c.id}>
              <div className="sp-comment-avatar">Н</div><div className="sp-comment-body"><div className="sp-comment-meta"><strong>Никита</strong><span>{new Date(c.created).toLocaleTimeString('ru', {hour:'2-digit',minute:'2-digit'})}</span><IconButton className="sp-delete" label="Удалить локальный комментарий" onClick={() => setComments(items => items.filter(row => row.id !== c.id && row.parent !== c.id))}><Trash2 size={13}/></IconButton></div>
                {c.time !== null && <button className="sp-time-link" onClick={() => seek(c.time!)}>{time(c.time)}</button>}<p>{c.text}</p>
                <button className="sp-reply" onClick={() => openComment(c.time ?? current, c.id)}>Ответить</button>
                {comments.filter(reply => reply.parent === c.id).map(reply => <div className="sp-comment-reply" key={reply.id}><strong>Никита</strong><p>{reply.text}</p></div>)}
              </div>
            </article>)}
          </div>
          {composer ? <form className="sp-composer" onSubmit={e => { e.preventDefault(); saveComment(); }}>
            <div className="sp-composer-top"><span className="sp-comment-avatar">Н</span><strong>{replyTo ? 'Ответить' : 'Никита'}</strong><button type="button" className={`sp-anchor${anchor !== null ? ' sp-anchored' : ''}`} aria-pressed={anchor !== null} aria-label="Привязать комментарий к моменту" onClick={() => setAnchor(anchor === null ? current : null)}>{anchor !== null && <Check size={12}/>} {time(anchor ?? current)}</button><IconButton label="Закрыть комментарий" onClick={() => { setComposer(false); setReplyTo(undefined); }}><X size={16}/></IconButton></div>
            <textarea ref={commentInput} value={draft} onChange={e => setDraft(e.target.value)} placeholder="Написать комментарий…" aria-label="Комментарий" maxLength={4000} onKeyDown={e => { if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); saveComment(); } }}/>
            <div className="sp-composer-bottom"><span>⌘ ↵</span><button type="submit" className="sp-send" aria-label="Сохранить комментарий" disabled={!draft.trim()}><ArrowUp size={19}/></button></div>
          </form> : <button className="sp-new-comment" onClick={() => openComment()}><MessageSquare size={17}/>Написать</button>}
        </div>}
      </aside>

      <div className="sp-below-player">
        <form className="sp-ask" onSubmit={e => { e.preventDefault(); setSearchOpen(true); }}><Sparkles size={22}/><input ref={searchInput} aria-label="Найти в интервью" placeholder="Найти в интервью…" value={query} onChange={e => setQuery(e.target.value)} onFocus={() => { if (query) setSearchOpen(true); }}/>
          {query ? <IconButton label="Поиск по расшифровке" onClick={() => setSearchOpen(true)}><ArrowUp size={20}/></IconButton> : <div className="sp-menu-anchor" data-menu-root><IconButton label="Голосовой поиск" onClick={() => setMenu(menu === 'voice' ? null : 'voice')}><Mic size={20}/></IconButton>{menu === 'voice' && <div className="sp-popover sp-voice-info">В прототипе работает текстовый поиск по расшифровке.</div>}</div>}
        </form>
        {reactionButtons()}
      </div>
    </main>

    {searchOpen && <section className="sp-search-panel" aria-label="Поиск по интервью"><header><span><Search size={18}/>Поиск по интервью</span><IconButton label="Закрыть поиск" onClick={() => setSearchOpen(false)}><X size={18}/></IconButton></header><form onSubmit={e => e.preventDefault()}><Search size={18}/><input aria-label="Поисковый запрос" autoFocus value={query} onChange={e => setQuery(e.target.value)} placeholder="Слово или фраза"/></form><div className="sp-search-results">{query.length > 1 && !matches.length && <p>Совпадений нет</p>}{matches.map((row, i) => <button key={i} onClick={() => { seek(row.start); selectTab('transcript'); setSearchOpen(false); }}><time>{time(row.start)}</time><span>{row.text}</span></button>)}</div></section>}
    {toast && <div className="sp-toast" role="status"><Check size={15}/>{toast}</div>}
  </div>;
}

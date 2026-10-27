import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Dialog, Tooltip } from 'radix-ui';
import { AlignLeft, ArrowDownToLine, ArrowUpRight, Bookmark, Check, ChevronDown, FileText, Maximize2, MessageSquare, Minimize2, Pause, Play, RotateCcw, Volume2, VolumeX, X } from 'lucide-react';
import { candidateName, transcript, variants, type HeaderVariant } from './demo-data';
import brand from '../employer-workspace/assets/hr-vision-logo.svg';
import './candidate-header-lab.css';

function IconButton({ label, children, onClick, ...props }: {
  label: string; children: ReactNode; onClick?: () => void; 'aria-pressed'?: boolean;
}) {
  return <Tooltip.Root><Tooltip.Trigger asChild><button type="button" className="chl-icon" aria-label={label} onClick={onClick} {...props}>{children}</button></Tooltip.Trigger>
    <Tooltip.Portal><Tooltip.Content className="chl-tooltip" sideOffset={6}>{label}<Tooltip.Arrow/></Tooltip.Content></Tooltip.Portal>
  </Tooltip.Root>;
}

export function ResumeDocument() {
  return <article className="chl-document">
    <header><span>РЕЗЮМЕ</span><h1>{candidateName}</h1><p>Менеджер по продажам B2B</p><small>Москва · Полная занятость · Гибридный формат</small></header>
    <section><h2>О себе</h2><p>Работаю с корпоративными клиентами: от первого контакта до повторных продаж. Люблю разбираться в задаче клиента и находить решение, которое можно объяснить простыми словами.</p></section>
    <section><h2>Опыт работы</h2>
      <div className="chl-job"><time>2022 — сейчас</time><div><h3>Менеджер по работе с ключевыми клиентами</h3><p className="chl-company">Компания А</p><p>Вела переговоры с корпоративными заказчиками, готовила коммерческие предложения и сопровождала сделки. Развивала действующую базу и находила новые точки роста.</p></div></div>
      <div className="chl-job"><time>2019 — 2022</time><div><h3>Менеджер по продажам</h3><p className="chl-company">Компания Б</p><p>Работала с входящими обращениями и холодной базой. Проводила презентации, согласовывала условия и передавала проекты в реализацию.</p></div></div>
    </section>
    <section><h2>Навыки</h2><p>Переговоры · Выявление потребностей · B2B-продажи · CRM · Коммерческие предложения</p></section>
    <section><h2>Образование</h2><p>Высшее · Менеджмент</p></section>
    <footer>Демонстрационные данные для проверки интерфейса.</footer>
  </article>;
}

const downloadUrl = '/candidate-header-demo/resume.pdf';
function ResumeDownload({ text = false }: { text?: boolean }) {
  if (text) return <a className="chl-text-action" href={downloadUrl} download="Резюме.pdf" aria-label="Скачать резюме"><ArrowDownToLine size={18}/>Скачать</a>;
  return <Tooltip.Root><Tooltip.Trigger asChild><a className={text ? 'chl-text-action' : 'chl-icon'} href={downloadUrl} download="Резюме.pdf" aria-label="Скачать резюме"><ArrowDownToLine size={18}/>{text && 'Скачать'}</a></Tooltip.Trigger><Tooltip.Portal><Tooltip.Content className="chl-tooltip" sideOffset={6}>Скачать резюме<Tooltip.Arrow/></Tooltip.Content></Tooltip.Portal></Tooltip.Root>;
}

function ResumePopup({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const changeOpen = (next: boolean) => { setOpen(next); if (!next) setExpanded(false); };
  return <Dialog.Root open={open} onOpenChange={changeOpen}>
    <Dialog.Trigger asChild>{children}</Dialog.Trigger>
    <Dialog.Portal container={typeof document !== 'undefined' ? document.fullscreenElement as HTMLElement || undefined : undefined}>
      <Dialog.Overlay className="chl-scrim" onClick={() => changeOpen(false)}/>
      <Dialog.Content className={`chl-resume-popup${expanded ? ' chl-popup-wide' : ''}`} aria-describedby={undefined} onEscapeKeyDown={() => changeOpen(false)}>
        <header className="chl-popup-toolbar"><Dialog.Title>Резюме</Dialog.Title>
          <div><ResumeDownload text/><button type="button" className="chl-icon" aria-label={expanded ? 'Уменьшить резюме' : 'Развернуть резюме'} title={expanded ? 'Уменьшить резюме' : 'Развернуть резюме'} onClick={() => setExpanded(v => !v)}>{expanded ? <Minimize2 size={18}/> : <Maximize2 size={18}/>}</button>
            <Dialog.Close asChild><button type="button" className="chl-icon" aria-label="Закрыть резюме"><X size={21}/></button></Dialog.Close></div>
        </header>
        <div className="chl-document-scroll"><ResumeDocument/></div>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}

function ResumeLink({ onClick, open, panel = false }: { onClick?: () => void; open?: boolean; panel?: boolean }) {
  const button = <button type="button" className="chl-resume-link" aria-expanded={panel ? open : undefined} aria-controls={panel ? 'chl-right-panel' : undefined} onClick={onClick}><FileText size={18}/><span>Резюме</span>{panel && <ChevronDown size={14} className={open ? 'chl-flipped' : ''}/>}</button>;
  return panel ? button : <ResumePopup>{button}</ResumePopup>;
}

function ResponseButton() {
  const [response, setResponse] = useState('');
  const [open, setOpen] = useState(false);
  return <Dialog.Root open={open} onOpenChange={setOpen}>
    <Dialog.Trigger asChild><button type="button" className={`chl-response${response ? ' chl-responded' : ''}`}>{response ? <><Check size={17}/>{response}</> : <>Ответить<ArrowUpRight size={16}/></>}</button></Dialog.Trigger>
    <Dialog.Portal><Dialog.Overlay className="chl-scrim"/><Dialog.Content className="chl-decision" aria-describedby={undefined}>
      <Dialog.Close asChild><button type="button" className="chl-icon chl-decision-close" aria-label="Закрыть решение"><X size={20}/></button></Dialog.Close>
      <Dialog.Title>{candidateName}</Dialog.Title><p>Какой следующий шаг?</p>
      <button type="button" className="chl-decision-primary" onClick={() => { setResponse('Приглашена'); setOpen(false); }}>Позвать на встречу<ArrowUpRight size={18}/></button>
      <button type="button" className="chl-decision-secondary" onClick={() => { setResponse('Отказ'); setOpen(false); }}>Отказать</button>
      <small>Демонстрация: кандидату ничего не отправляется.</small>
    </Dialog.Content></Dialog.Portal>
  </Dialog.Root>;
}

export function Player({ overlay = false, current, setCurrent, playing, setPlaying }: {
  overlay?: boolean; current: number; setCurrent: (value: number) => void; playing: boolean; setPlaying: (value: boolean) => void;
}) {
  const player = useRef<HTMLDivElement>(null);
  const [muted, setMuted] = useState(false);
  const [full, setFull] = useState(false);
  const [speed, setSpeed] = useState(1);
  useEffect(() => {
    if (!playing) return;
    const tick = window.setInterval(() => setCurrent(Math.min(current + speed, 108)), 1000);
    return () => window.clearInterval(tick);
  }, [playing, current, speed, setCurrent]);
  useEffect(() => { if (current >= 108) setPlaying(false); }, [current, setPlaying]);
  useEffect(() => {
    const sync = () => setFull(document.fullscreenElement === player.current);
    document.addEventListener('fullscreenchange', sync);
    return () => document.removeEventListener('fullscreenchange', sync);
  }, []);
  function toggleFull() {
    if (document.fullscreenElement) void document.exitFullscreen();
    else void player.current?.requestFullscreen();
  }
  const stamp = `${String(Math.floor(current / 60)).padStart(2, '0')}:${String(Math.floor(current % 60)).padStart(2, '0')}`;
  return <div className={`chl-player${playing ? ' chl-playing' : ''}${overlay ? ' chl-overlay-player' : ''}`} ref={player}>
    <div className="chl-video-scene" aria-hidden="true"><div className="chl-person-disc">АГ</div><span>Интервью с рекрутером</span></div>
    {overlay && <div className="chl-video-heading"><div><span>Кандидат</span><h2>{candidateName}</h2></div><ResumeLink/></div>}
    {!playing && <button type="button" className="chl-play" aria-label="Воспроизвести демонстрацию" onClick={() => { if (current >= 108) setCurrent(0); setPlaying(true); }}><Play size={28} fill="currentColor" strokeWidth={0}/></button>}
    <div className="chl-controls">
      <input aria-label="Позиция записи" type="range" min="0" max="108" value={current} onChange={event => setCurrent(Number(event.target.value))} style={{ '--chl-progress': `${current / 108 * 100}%` } as React.CSSProperties}/>
      <div className="chl-control-row">
        <IconButton label={playing ? 'Пауза' : 'Воспроизвести'} onClick={() => { if (current >= 108) setCurrent(0); setPlaying(!playing); }}>{playing ? <Pause size={17} fill="currentColor"/> : <Play size={17} fill="currentColor"/>}</IconButton>
        <IconButton label="Назад на 5 секунд" onClick={() => setCurrent(Math.max(0, current - 5))}><RotateCcw size={19}/></IconButton>
        <IconButton label={muted ? 'Включить звук' : 'Выключить звук'} onClick={() => setMuted(v => !v)}>{muted ? <VolumeX size={20}/> : <Volume2 size={20}/>}</IconButton>
        <span className="chl-clock">{stamp}<span> / 01:48</span></span>
        <div className="chl-control-spacer"/>
        <button type="button" className="chl-speed" aria-label="Скорость воспроизведения" onClick={() => setSpeed(v => v === 1 ? 1.5 : v === 1.5 ? 2 : 1)}>{speed}×</button>
        <IconButton label={full ? 'Свернуть видео' : 'Видео на весь экран'} onClick={toggleFull}>{full ? <Minimize2 size={19}/> : <Maximize2 size={19}/>}</IconButton>
      </div>
    </div>
  </div>;
}

export function TranscriptPanel({ current, onSeek, resumeOpen, onCloseResume }: { current: number; onSeek: (seconds: number) => void; resumeOpen: boolean; onCloseResume: () => void }) {
  const [tab, setTab] = useState('transcript');
  const [comment, setComment] = useState('');
  const [comments, setComments] = useState<string[]>([]);
  const active = transcript.reduce((last, row, index) => row.seconds <= current ? index : last, 0);
  const list = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const item = list.current?.querySelector<HTMLElement>('[data-active="true"]');
    if (!item || !list.current) return;
    list.current.scrollTo({ top: Math.max(0, item.offsetTop - 110), behavior: 'smooth' });
  }, [active, tab, resumeOpen]);
  if (resumeOpen) return <aside key="resume" id="chl-right-panel" className="chl-side chl-side-resume" aria-label="Резюме рядом с видео">
    <header><h3>Резюме</h3><div><ResumeDownload/><ResumePopup><button type="button" className="chl-icon" aria-label="Открыть резюме попапом"><Maximize2 size={17}/></button></ResumePopup><IconButton label="Вернуться к расшифровке" onClick={onCloseResume}><X size={19}/></IconButton></div></header>
    <div className="chl-side-document"><ResumeDocument/></div>
  </aside>;
  return <aside key="transcript" id="chl-right-panel" className="chl-side" aria-label="Материалы интервью">
    <div className="chl-panel-tabs" role="tablist" aria-label="Материалы интервью">{[
      ['chapters', 'Темы', <Bookmark size={19}/>], ['transcript', 'Расшифровка', <AlignLeft size={20}/>], ['comments', 'Комментарии', <MessageSquare size={19}/>],
    ].map(([value, label, icon]) => <Tooltip.Root key={String(value)}><Tooltip.Trigger asChild><button id={`chl-tab-${value}`} type="button" role="tab" aria-selected={tab === value} aria-controls={`chl-panel-${value}`} aria-label={String(label)} tabIndex={tab === value ? 0 : -1} onKeyDown={event => {
      const options = ['chapters', 'transcript', 'comments'];
      if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') { event.preventDefault(); const next = options[(options.indexOf(tab) + (event.key === 'ArrowRight' ? 1 : 2)) % 3]; setTab(next); document.getElementById(`chl-tab-${next}`)?.focus(); }
    }} onClick={() => setTab(String(value))}>{icon}</button></Tooltip.Trigger><Tooltip.Portal><Tooltip.Content className="chl-tooltip" sideOffset={8}>{label}</Tooltip.Content></Tooltip.Portal></Tooltip.Root>)}</div>
    {tab === 'transcript' && <div id="chl-panel-transcript" role="tabpanel" aria-labelledby="chl-tab-transcript" className="chl-transcript" ref={list}>{transcript.map((row, i) => <button type="button" key={row.time} data-active={i === active} onClick={() => onSeek(row.seconds)}><time>{row.time}</time><span>{row.text}</span></button>)}</div>}
    {tab === 'chapters' && <div id="chl-panel-chapters" role="tabpanel" aria-labelledby="chl-tab-chapters" className="chl-chapters">{[['00:00', 'Опыт работы', 0], ['00:28', 'Первый контакт', 28], ['01:08', 'Пример сделки', 68]].map(([time, title, seconds]) => <button type="button" key={time} onClick={() => onSeek(Number(seconds))}><time>{time}</time><span>{title}</span><Play size={13}/></button>)}</div>}
    {tab === 'comments' && <div id="chl-panel-comments" role="tabpanel" aria-labelledby="chl-tab-comments" className="chl-comments"><div>{comments.length ? comments.map((text, i) => <p key={i}><strong>Вы</strong>{text}</p>) : <p className="chl-empty">Здесь будут комментарии к интервью.</p>}</div><form onSubmit={event => { event.preventDefault(); if (comment.trim()) { setComments(items => [...items, comment.trim()]); setComment(''); } }}><label className="chl-sr" htmlFor="chl-comment">Комментарий</label><input id="chl-comment" value={comment} placeholder="Написать комментарий…" onChange={event => setComment(event.target.value)}/><button type="submit" disabled={!comment.trim()} aria-label="Сохранить комментарий"><ArrowUpRight size={20}/></button></form></div>}
  </aside>;
}

function BeforeHeader() {
  return <div className="chl-before"><div><h2>{candidateName} · 4</h2><ResponseButton/></div><div className="chl-before-file"><div className="chl-before-icon"><FileText size={30}/></div><span><strong>Резюме — Анастасия Глухова.pdf</strong><small>556 КБ</small></span><ResumeDownload/><ResumePopup><button type="button" className="chl-before-open">Открыть</button></ResumePopup></div></div>;
}

export function CandidateHeaderLab({ initialVariant = 'inline' }: { initialVariant?: HeaderVariant }) {
  const [variant, setVariant] = useState<HeaderVariant>(initialVariant);
  const [before, setBefore] = useState(false);
  const [resumeOpen, setResumeOpen] = useState(false);
  const [current, setCurrent] = useState(35);
  const [playing, setPlaying] = useState(false);
  const option = variants.find(item => item.id === variant)!;
  if (new URLSearchParams(location.search).get('document') === '1') return <ResumeDocument/>;
  return <Tooltip.Provider delayDuration={400}><main className="chl-lab">
    <header className="chl-lab-toolbar"><div><span className="chl-lab-mark">HR</span><span>Лаборатория <span className="chl-lab-slash">/</span> Карточка кандидата</span></div><span className="chl-local">Макеты · данные условные</span></header>
    <section className="chl-lab-intro"><div><p>МЕНЬШЕ ШАПКИ. БОЛЬШЕ ИНТЕРВЬЮ.</p><h1>Резюме в один клик</h1></div><div className="chl-option-controls"><div className="chl-variant-tabs" role="group" aria-label="Вариант шапки">{variants.map(item => <button type="button" key={item.id} aria-pressed={variant === item.id && !before} onClick={() => { setVariant(item.id); setBefore(false); setResumeOpen(false); }}><span>{item.number}</span>{item.label}</button>)}</div><button type="button" className="chl-before-toggle" aria-pressed={before} onClick={() => { setBefore(v => !v); setResumeOpen(false); }}>{before ? 'Вернуть макет' : 'Как сейчас'}</button></div></section>
    <section className="chl-product-frame">
      <div className="chl-product-top"><img src={brand} alt="HR Vision"/><span>Менеджер по продажам B2B <span>/</span> Кандидаты</span></div>
      <article className={`chl-candidate chl-variant-${variant}${before ? ' chl-is-before' : ''}`}>
        {before ? <BeforeHeader/> : variant !== 'overlay' && <header className="chl-candidate-heading"><div className="chl-heading-copy"><h2>{candidateName}</h2>{variant === 'side' && <span>Менеджер по продажам B2B</span>}</div><div className="chl-heading-actions"><ResumeLink panel={variant === 'side'} open={resumeOpen} onClick={variant === 'side' ? () => setResumeOpen(v => !v) : undefined}/><ResponseButton/></div></header>}
        <div className={`chl-content${resumeOpen ? ' chl-with-resume' : ''}`}>
          <div className="chl-video-column"><Player overlay={!before && variant === 'overlay'} current={current} setCurrent={setCurrent} playing={playing} setPlaying={setPlaying}/>
            {!before && variant === 'overlay' && <div className="chl-video-footer"><span>Интервью с рекрутером</span><ResponseButton/></div>}
          </div>
          <TranscriptPanel current={current} onSeek={setCurrent} resumeOpen={resumeOpen && !before} onCloseResume={() => setResumeOpen(false)}/>
        </div>
      </article>
    </section>
    <footer className="chl-lab-caption"><div><span className="chl-option-number">{before ? 'ДО' : option.number}</span><p><strong>{before ? 'Текущая шапка' : option.label}</strong>{before ? 'Имя и файл занимают два больших ряда. Интервью начинается ниже.' : option.caption}</p></div><p className="chl-recommendation">{before ? 'Нажмите «Вернуть макет», чтобы сравнить высоту.' : option.tradeoff}</p></footer>
    <div className="chl-source-note"><span>Принципы Apple: <a href="https://support.apple.com/guide/mac-help/preview-a-file-mh14119/mac" target="_blank" rel="noreferrer">Quick Look ↗</a> · <a href="https://developer.apple.com/design/human-interface-guidelines/toolbars" target="_blank" rel="noreferrer">Toolbars ↗</a> · <a href="https://developer.apple.com/design/human-interface-guidelines/sheets" target="_blank" rel="noreferrer">Sheets ↗</a></span><span>Веб-адаптация · Воспроизведение имитируется</span></div>
  </main></Tooltip.Provider>;
}

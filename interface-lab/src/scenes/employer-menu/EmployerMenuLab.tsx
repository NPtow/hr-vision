import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { AnimatePresence, LayoutGroup, MotionConfig, motion, useReducedMotion } from 'motion/react';
import { Dialog, Popover, Tooltip } from 'radix-ui';
import {
  ArrowLeft, ArrowRight, BriefcaseBusiness, CalendarDays,
  Check, ChevronDown, ChevronLeft, ChevronRight, ExternalLink, Grid2X2,
  Info, Menu, MessageCircle, PanelLeftClose, PanelLeftOpen, Search,
  Send, Users, Video, X,
} from 'lucide-react';
import { candidates, type CandidateId } from '../agency-product/model';
import { InterviewReview } from '../interview-review/InterviewReview';
import { InterviewMediaProvider, LabMediaControl } from '../interview-review/InterviewMediaContext';
import './employer-menu.css';

// This is a navigation comparison, isolated from server-side hiring actions.
const variants = [
  { id: 'sidebar', name: 'Боковое меню', effect: 'Подвижная метка' },
  { id: 'rail', name: 'Компактная панель', effect: 'Раскрытие от края' },
  { id: 'top', name: 'Верхняя строка', effect: 'Скользящее подчёркивание' },
  { id: 'dock', name: 'Плавающее меню', effect: 'Мягкий подъём' },
  { id: 'launcher', name: 'Меню по кнопке', effect: 'Раскрытие от кнопки' },
] as const;
type Variant = typeof variants[number]['id'];
type Section = 'tasks' | 'meetings' | 'chat';
type TaskTab = 'candidates' | 'meetings' | 'pool';
const destinations = [
  { id: 'tasks', label: 'Задачи', icon: BriefcaseBusiness },
  { id: 'meetings', label: 'Встречи', icon: CalendarDays },
  { id: 'chat', label: 'Чат', icon: MessageCircle },
] as const;
const spring = { type: 'spring' as const, stiffness: 420, damping: 41, mass: 1 };
const productLink = '/iframe.html?id=hr-vision-product--start&viewMode=story';
const role = 'Менеджер по работе с клиентами';

function readVariant(): Variant {
  const value = new URLSearchParams(location.search).get('menu');
  return variants.find(v => v.id === value)?.id || 'top';
}

function Hint({ label, children, enabled = true }: { label: string; children: ReactNode; enabled?: boolean }) {
  return <Tooltip.Root open={enabled ? undefined : false}><Tooltip.Trigger asChild>{children}</Tooltip.Trigger>
    <Tooltip.Portal><Tooltip.Content className="hml-tooltip" side="right" sideOffset={10} collisionPadding={12}>{label}<Tooltip.Arrow/></Tooltip.Content></Tooltip.Portal>
  </Tooltip.Root>;
}

function Brand({ onClick }: { onClick: () => void }) {
  return <button className="hml-brand" onClick={onClick} aria-label="HR Vision · задачи найма">HR Vision<span>.</span></button>;
}

function Avatar({ id = 'anna', manager = false }: { id?: CandidateId; manager?: boolean }) {
  return <span className={`hml-avatar hml-avatar-${manager ? 'manager' : id}`} aria-hidden="true">{manager ? 'ИП' : candidates.find(c => c.id === id)!.initials}</span>;
}

function Account() {
  return <Popover.Root><Popover.Trigger className="hml-account" aria-label="Аккаунт Ивана Петрова"><Avatar manager/><span>Иван Петров</span><ChevronDown size={14}/></Popover.Trigger>
    <Popover.Portal><Popover.Content className="hml-surface hml-account-menu" sideOffset={10} align="end" collisionPadding={16}>
      <strong>Иван Петров</strong><p>Сфера</p>
      <a href={productLink} target="_blank" rel="noreferrer">Открыть рабочий сервис<ExternalLink size={15}/></a>
    </Popover.Content></Popover.Portal>
  </Popover.Root>;
}

function Navigation({ section, onChange, shape = 'list', collapsed = false }: {
  section: Section; onChange: (section: Section) => void; shape?: 'list' | 'top' | 'dock' | 'grid'; collapsed?: boolean;
}) {
  const group = useId();
  const reduce = useReducedMotion();
  return <LayoutGroup id={group}><nav className={`hml-nav hml-nav-${shape}${collapsed ? ' hml-nav-collapsed' : ''}`} aria-label="Разделы работодателя">
    {destinations.map(({ id, label, icon: Icon }) => {
      const active = section === id;
      const link = <motion.a href={`#${id}`} aria-current={active ? 'page' : undefined} aria-label={label}
        onClick={event => { event.preventDefault(); onChange(id); }}
        whileHover={shape === 'dock' && !reduce ? { y: -2 } : undefined}
        whileTap={shape === 'dock' && !reduce ? { y: 0 } : undefined} transition={spring}>
        {active && <motion.span className="hml-active-mark" aria-hidden="true" layoutId="active" transition={reduce ? { duration: 0 } : spring}/>}
        <Icon size={19} strokeWidth={1.75}/><span className="hml-nav-label">{label}</span>
      </motion.a>;
      return <span className="hml-nav-item" key={id}><Hint label={label} enabled={collapsed}>{link}</Hint></span>;
    })}
  </nav></LayoutGroup>;
}

function Launcher({ section, onChange, mobile = false }: { section: Section; onChange: (v: Section) => void; mobile?: boolean }) {
  const [open, setOpen] = useState(false);
  const reduce = useReducedMotion();
  return <Popover.Root open={open} onOpenChange={setOpen}>
    <Popover.Trigger className={`hml-launcher-trigger${mobile ? ' hml-mobile-menu' : ''}`} aria-label="Открыть разделы">
      {mobile ? <Menu size={20}/> : <Grid2X2 size={19}/>}<span>{destinations.find(d => d.id === section)!.label}</span><ChevronDown size={14}/>
    </Popover.Trigger>
    <AnimatePresence>{open && <Popover.Portal forceMount key="launcher"><Popover.Content forceMount asChild sideOffset={10} align="start" collisionPadding={16}>
      <motion.div className="hml-surface hml-launcher" style={{ transformOrigin: 'var(--radix-popover-content-transform-origin)' }}
        initial={{ opacity: 0, scale: reduce ? 1 : .97, y: reduce ? 0 : -4 }} animate={{ opacity: 1, scale: 1, y: 0 }} exit={{ opacity: 0, scale: reduce ? 1 : .97, y: reduce ? 0 : -4 }} transition={{ ...spring, opacity: { duration: .12 } }}>
        <Navigation section={section} shape="grid" onChange={next => { onChange(next); setOpen(false); }}/>
      </motion.div>
    </Popover.Content></Popover.Portal>}</AnimatePresence>
  </Popover.Root>;
}

function SideMenu({ compact, section, onChange }: { compact: boolean; section: Section; onChange: (v: Section) => void }) {
  const [expanded, setExpanded] = useState(false);
  const reduce = useReducedMotion();
  const toggle = useRef<HTMLButtonElement>(null);
  const navId = useId();
  return <div className={`hml-side-slot ${compact ? 'hml-rail-slot' : ''}`}>
    <motion.aside className={`hml-sidebar ${compact ? 'hml-rail' : ''} ${expanded ? 'hml-expanded' : ''}`}
      initial={false} animate={{ width: compact && !expanded ? 72 : 208 }} transition={reduce ? { duration: 0 } : spring}
      onKeyDown={event => { if (compact && expanded && event.key === 'Escape') { toggle.current?.focus(); setExpanded(false); event.stopPropagation(); } }}>
      {compact ? <button className="hml-rail-toggle" ref={toggle} aria-label={expanded ? 'Свернуть меню' : 'Раскрыть меню'} aria-expanded={expanded} aria-controls={navId} onClick={() => setExpanded(v => !v)}>
        {expanded ? <PanelLeftClose size={20}/> : <PanelLeftOpen size={20}/>}<span className={expanded ? '' : 'hml-visually-hidden'}>Разделы</span>
      </button> : <div className="hml-workspace-name"><span className="hml-company-mark">С</span>Сфера</div>}
      <div id={navId}><Navigation section={section} collapsed={compact && !expanded} onChange={next => { onChange(next); if (compact) setExpanded(false); }}/></div>
    </motion.aside>
  </div>;
}

function Tasks({ onOpen }: { onOpen: () => void }) {
  return <div className="hml-page hml-tasks">
    <div className="hml-page-heading"><h1>Задачи найма</h1><span className="hml-company-caption">Сфера</span></div>
    <button className="hml-task-card" onClick={onOpen}>
      <div className="hml-task-symbol"><Users size={24} strokeWidth={1.5}/></div>
      <div className="hml-task-copy"><h2>{role}</h2><p>Вернуть клиентов к повторным покупкам</p>
        <div className="hml-task-bottom"><div className="hml-avatar-stack">{candidates.map(c => <Avatar key={c.id} id={c.id}/>)}</div><span>3 кандидата</span></div>
      </div><span className="hml-task-open"><span>Открыть подборку</span><ArrowRight size={20}/></span>
    </button>
    <div className="hml-next-meeting"><span className="hml-calendar-square"><span>ОКТ</span><b>8</b></span><div><strong>Встреча с Анной Мироновой</strong><p>11:00 · 30 минут · МСК</p></div><span className="hml-status"><Check size={14}/>Подтверждена</span></div>
  </div>;
}

function Meetings({ onOpen, onCandidate }: { onOpen: () => void; onCandidate: (id: CandidateId) => void }) {
  return <div className="hml-page hml-meetings"><div className="hml-page-heading"><h1>Встречи</h1><span>Октябрь</span></div>
    <div className="hml-week" aria-label="Неделя с 5 по 11 октября">{['Пн','Вт','Ср','Чт','Пт','Сб','Вс'].map((day, i) => <div key={day} className={i === 3 ? 'hml-week-selected' : ''}><span>{day}</span><b>{i + 5}</b>{i === 3 && <i/>}</div>)}</div>
    <div className="hml-meeting-card"><div className="hml-meeting-time">11:00<span>11:30</span></div><div className="hml-meeting-body"><span className="hml-status"><Check size={13}/>Подтверждена</span><h2>Анна Миронова</h2><p>{role}</p><div className="hml-meeting-actions"><button className="hml-primary" onClick={onOpen}><Video size={16}/>Открыть встречу</button><button className="hml-text-button" onClick={() => onCandidate('anna')}>Материалы кандидата<ArrowRight size={15}/></button></div></div></div>
  </div>;
}

function Messages({ selected, onSelect, messages, send }: { selected: CandidateId; onSelect: (id: CandidateId) => void; messages: Record<CandidateId, string[]>; send: (text: string) => void }) {
  const [text, setText] = useState('');
  const candidate = candidates.find(c => c.id === selected)!;
  return <div className="hml-chat"><aside className="hml-threads"><h1>Чат</h1>{candidates.map(c => <button key={c.id} aria-pressed={c.id === selected} onClick={() => { onSelect(c.id); setText(''); }}><Avatar id={c.id}/><span><strong>{c.name}</strong><small>{c.id === 'anna' ? 'До встречи в четверг!' : 'Обсуждение вакансии'}</small></span></button>)}</aside>
    <section className="hml-conversation"><header><Avatar id={candidate.id}/><strong>{candidate.name}</strong></header><div className="hml-bubbles"><p className="hml-message-in">{selected === 'anna' ? 'Время подходит, до встречи в четверг!' : 'Здравствуйте! Готова обсудить вакансию.'.replace('Готова', selected === 'mikhail' ? 'Готов' : 'Готова')}</p>{messages[selected].map((m, i) => <p className="hml-message-out" key={i}>{m}</p>)}</div>
      <form onSubmit={event => { event.preventDefault(); if (text.trim()) { send(text.trim()); setText(''); } }}><label className="hml-visually-hidden" htmlFor="hml-message">Сообщение</label><input id="hml-message" value={text} onChange={e => setText(e.target.value)} placeholder="Сообщение"/><button className="hml-primary" disabled={!text.trim()} aria-label="Добавить сообщение в макет"><Send size={18}/></button></form>
    </section></div>;
}

function CandidateWorkspace({ selected, onSelect, back, tab, setTab, onMeeting, onChat, mobileProfile, setMobileProfile }: {
  selected: CandidateId; onSelect: (id: CandidateId) => void; back: () => void; tab: TaskTab; setTab: (tab: TaskTab) => void; onMeeting: () => void; onChat: () => void; mobileProfile: boolean; setMobileProfile: (v: boolean) => void;
}) {
  const [query, setQuery] = useState('');
  const [detail, setDetail] = useState<'interview' | 'experience' | 'terms'>('interview');
  const person = candidates.find(c => c.id === selected)!;
  return <div className="hml-task-workspace">
    <header className="hml-task-heading"><button className="hml-back" onClick={back}><ArrowLeft size={16}/><span>Задачи</span></button><h1>{role}</h1></header>
    <nav className="hml-task-tabs" aria-label="Внутри задачи">{([{ id:'candidates', label:'Кандидаты', count:3 }, { id:'meetings', label:'Встречи', count:1 }, { id:'pool', label:'Пул для оффера', count:0 }] as const).map(t => <button key={t.id} aria-current={t.id === tab ? 'page' : undefined} onClick={() => setTab(t.id)}>{t.label}{t.count > 0 && <span>{t.count}</span>}</button>)}</nav>
    {tab === 'candidates' && <div className={`hml-candidates ${mobileProfile ? 'hml-show-profile' : ''}`}>
      <aside className="hml-candidate-list"><label className="hml-search"><Search size={16}/><span className="hml-visually-hidden">Найти кандидата</span><input placeholder="Найти кандидата" value={query} onChange={e => setQuery(e.target.value)}/></label>{candidates.filter(c => c.name.toLowerCase().includes(query.toLowerCase())).map(c => <button className="hml-person" key={c.id} aria-pressed={c.id === selected} onClick={() => { onSelect(c.id); setMobileProfile(true); }}><Avatar id={c.id}/><span><strong>{c.name}</strong><small>{c.experience} · {c.city}</small></span><ChevronRight size={14}/></button>)}{!candidates.some(c => c.name.toLowerCase().includes(query.toLowerCase())) && <p className="hml-no-results">Никого не нашли</p>}</aside>
      <section className="hml-profile"><button className="hml-mobile-back" onClick={() => setMobileProfile(false)}><ArrowLeft size={16}/>Кандидаты</button><div className="hml-profile-heading"><div><h2>{person.name}</h2><p>{person.experience} · {person.city}</p></div><button className="hml-icon-button" aria-label={`Написать ${person.name}`} onClick={onChat}><MessageCircle size={20}/></button></div>
        <div className="hml-profile-tabs" role="group" aria-label="Материалы кандидата">{([{ id:'interview', label:'Интервью' },{ id:'experience', label:'Опыт' },{ id:'terms', label:'Условия' }] as const).map(t => <button key={t.id} aria-pressed={detail === t.id} onClick={() => setDetail(t.id)}>{t.label}</button>)}</div>
        {detail === 'interview' && <InterviewReview person={person} compact/>}
        {detail === 'experience' && <div className="hml-profile-prose"><p>{person.summary}</p>{person.history.map(h => <article key={h.company}><small>{h.years}</small><h3>{h.company}</h3><p>{h.role}</p></article>)}</div>}
        {detail === 'terms' && <div className="hml-profile-prose"><h3>{person.salary}</h3><p>{person.motivation}</p><h3>Выход на работу</h3><p>{person.availability}</p></div>}
      </section></div>}
    {tab === 'meetings' && <Meetings onOpen={onMeeting} onCandidate={id => { onSelect(id); setTab('candidates'); setMobileProfile(true); }}/>}
    {tab === 'pool' && <div className="hml-empty"><Users size={28} strokeWidth={1.3}/><h2>Пока никого не выбрали</h2><p>После встреч здесь будут кандидаты для оффера.</p><button className="hml-primary" onClick={() => setTab('candidates')}>К кандидатам<ArrowRight size={16}/></button></div>}
  </div>;
}

function MeetingDialog({ open, setOpen, onMaterials, restoreFocus }: { open: boolean; setOpen: (open: boolean) => void; onMaterials: () => void; restoreFocus: () => void }) {
  const movedToMaterials = useRef(false);
  return <Dialog.Root open={open} onOpenChange={setOpen}><Dialog.Portal><Dialog.Overlay className="hml-overlay"/><Dialog.Content className="hml-surface hml-dialog" onOpenAutoFocus={() => { movedToMaterials.current = false; }} onCloseAutoFocus={event => { event.preventDefault(); if (!movedToMaterials.current) restoreFocus(); }}><Dialog.Close className="hml-close" aria-label="Закрыть встречу"><X size={19}/></Dialog.Close><span className="hml-status"><Check size={14}/>Подтверждена</span><Dialog.Title>Встреча с Анной</Dialog.Title><Dialog.Description>8 октября · 11:00 МСК · 30 минут</Dialog.Description><div className="hml-meeting-people"><Avatar manager/><span>Иван Петров</span><span className="hml-people-divider"/><Avatar/><span>Анна Миронова</span></div><button className="hml-primary" onClick={() => { movedToMaterials.current = true; setOpen(false); onMaterials(); }}>Материалы кандидата<ArrowRight size={16}/></button><a className="hml-work-link" href={productLink} target="_blank" rel="noreferrer">Созвон в рабочем сервисе<ExternalLink size={14}/></a></Dialog.Content></Dialog.Portal></Dialog.Root>;
}

function GalleryInfo() {
  return <Popover.Root><Popover.Trigger className="hml-icon-button" aria-label="О макетах"><Info size={18}/></Popover.Trigger><Popover.Portal><Popover.Content className="hml-surface hml-info" align="end" sideOffset={12} collisionPadding={16}>
    <strong>Сравниваем меню</strong><p>Пять вариантов на одном сценарии. Кандидаты и встречи здесь вымышленные, действия не меняют рабочий подбор.</p><p>Видео, главы и таймлайн используют текущий компонент. Для проверки можно открыть свой файл.</p><LabMediaControl/>
    <a href={productLink} target="_blank" rel="noreferrer">Рабочий сервис<ExternalLink size={14}/></a>
  </Popover.Content></Popover.Portal></Popover.Root>;
}

export function EmployerMenuLab() {
  const [variant, setVariant] = useState<Variant>(readVariant);
  const [section, setSection] = useState<Section>('tasks');
  const [taskOpen, setTaskOpen] = useState(false);
  const [taskTab, setTaskTab] = useState<TaskTab>('candidates');
  const [selected, setSelected] = useState<CandidateId>('anna');
  const [mobileProfile, setMobileProfile] = useState(false);
  const [meeting, setMeeting] = useState(false);
  const [messages, setMessages] = useState<Record<CandidateId, string[]>>({ anna:[], mikhail:[], elena:[] });
  const current = variants.find(v => v.id === variant)!;
  const index = variants.indexOf(current);
  const content = useRef<HTMLDivElement>(null);
  const meetingTrigger = useRef<HTMLElement | null>(null);
  function chooseVariant(next: Variant) {
    setVariant(next);
    const url = new URL(location.href); url.searchParams.set('menu', next); history.replaceState({}, '', url);
  }
  useEffect(() => { const sync = () => setVariant(readVariant()); window.addEventListener('popstate', sync); return () => window.removeEventListener('popstate', sync); }, []);
  function navigate(next: Section) { setSection(next); if (next === 'tasks') setTaskOpen(false); content.current?.scrollTo({ top:0 }); }
  function openCandidate(id: CandidateId) { setSelected(id); setSection('tasks'); setTaskOpen(true); setTaskTab('candidates'); setMobileProfile(true); content.current?.scrollTo({ top:0 }); requestAnimationFrame(() => { const heading = content.current?.querySelector<HTMLElement>('.hml-profile h2'); if (heading) { heading.tabIndex = -1; heading.focus({ preventScroll:true }); } }); }
  function openMeeting() { meetingTrigger.current = document.activeElement as HTMLElement; setMeeting(true); }
  return <MotionConfig reducedMotion="user"><Tooltip.Provider delayDuration={250}><InterviewMediaProvider>
    <div className="hml-lab">
      <header className="hml-gallery-bar"><div className="hml-gallery-brand">HR Vision<span>Макеты</span></div><nav aria-label="Варианты меню" className="hml-variants">{variants.map((v, i) => <button key={v.id} aria-pressed={v.id === variant} onClick={() => chooseVariant(v.id)}><span>{String(i + 1).padStart(2, '0')}</span>{v.name}</button>)}</nav><GalleryInfo/></header>
      <div className="hml-gallery-heading"><h1>Меню работодателя</h1><div><span>{current.effect}</span><button className="hml-icon-button" aria-label="Предыдущий вариант" onClick={() => chooseVariant(variants[(index + 4) % 5].id)}><ChevronLeft size={18}/></button><span className="hml-counter">{index + 1} / 5</span><button className="hml-icon-button" aria-label="Следующий вариант" onClick={() => chooseVariant(variants[(index + 1) % 5].id)}><ChevronRight size={18}/></button></div></div>
      <section className={`hml-app hml-variant-${variant}`} aria-label={`Макет: ${current.name}`}>
        <header className="hml-app-header"><Brand onClick={() => navigate('tasks')}/>
          {variant === 'top' && <Navigation section={section} onChange={navigate} shape="top"/>}
          {variant === 'launcher' && <Launcher section={section} onChange={navigate}/>}
          {(variant === 'sidebar' || variant === 'rail') && <div className="hml-mobile-nav"><Launcher mobile section={section} onChange={navigate}/></div>}
          <Account/>
        </header>
        <div className="hml-app-body">
          {(variant === 'sidebar' || variant === 'rail') && <SideMenu key={variant} compact={variant === 'rail'} section={section} onChange={navigate}/>}
          <div className="hml-content" ref={content}>
            {section === 'tasks' && (taskOpen ? <CandidateWorkspace selected={selected} onSelect={setSelected} back={() => setTaskOpen(false)} tab={taskTab} setTab={setTaskTab} onMeeting={openMeeting} onChat={() => setSection('chat')} mobileProfile={mobileProfile} setMobileProfile={setMobileProfile}/> : <Tasks onOpen={() => { setTaskOpen(true); setTaskTab('candidates'); setMobileProfile(false); }}/>) }
            {section === 'meetings' && <Meetings onOpen={openMeeting} onCandidate={openCandidate}/>}
            {section === 'chat' && <Messages selected={selected} onSelect={setSelected} messages={messages} send={text => setMessages(all => ({ ...all, [selected]: [...all[selected], text] }))}/>}
          </div>
          {variant === 'dock' && <div className="hml-dock"><Navigation shape="dock" section={section} onChange={navigate}/></div>}
        </div>
      </section>
      <footer className="hml-gallery-footer"><a href={productLink} target="_blank" rel="noreferrer">Рабочий сервис<ExternalLink size={13}/></a></footer>
    </div>
    <MeetingDialog open={meeting} setOpen={setMeeting} onMaterials={() => openCandidate('anna')} restoreFocus={() => meetingTrigger.current?.focus()}/>
    </InterviewMediaProvider></Tooltip.Provider></MotionConfig>;
}

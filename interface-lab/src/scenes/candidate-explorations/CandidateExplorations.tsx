import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { AnimatePresence, LayoutGroup, MotionConfig, motion, useReducedMotion } from 'motion/react';
import { ArrowLeft, ArrowRight, CalendarDays, Check, ChevronDown, Columns3, ExternalLink, House, Monitor, Pause, Play, RotateCcw, Smartphone, Sparkles, UserRound, Video, X } from 'lucide-react';
import { Dialog } from 'radix-ui';
import logo from '../employer-workspace/assets/hr-vision-logo.svg';
import { SearchVariants } from './SearchVariants';
import { CalendarVariants } from './CalendarVariants';
import { ProfileVariants } from './ProfileVariants';
import { CelebrationOrbit } from './CelebrationOrbit';
import { CelebrationPath } from './CelebrationPath';
import { CelebrationCharacter } from './CelebrationCharacter';
import { CelebrationTypography } from './CelebrationTypography';
import { CelebrationObject } from './CelebrationObject';
import { concepts, initialAvailability, initialProfile, initialVacancies } from './data';
import { references } from './references';
import { stageLabels, type Availability, type CelebrationProps, type JourneyEvent, type Profile, type Screen, type SearchFilter, type Vacancy, type VacancyAction } from './types';
import '@fontsource-variable/inter';
import './explorations.css';

const screenNames: Record<Screen, string> = { search: 'Поиск', calendar: 'Календарь', profile: 'Профиль' };
const celebrations = [CelebrationOrbit, CelebrationPath, CelebrationCharacter, CelebrationTypography, CelebrationObject];
const celebrationNames = ['Орбита возможностей', 'Всё сложилось', 'Своя дорога', 'Шаги к результату', 'Счастливый портфель', 'Дай пять', 'Новая глава', 'Хорошо устроилась', 'Моё место', 'Пропуск в новое'];
const ord = (n: number) => String(n + 1).padStart(2, '0');
const noop = () => {};

function seedVacancies(): Vacancy[] {
  return initialVacancies.map(v => {
    const history: JourneyEvent[] = [{ id: `${v.id}-0`, label: 'Предложение получено', tone: 'neutral' }];
    if (v.stage !== 'new') history.push({ id: `${v.id}-1`, label: 'Вы участвуете', tone: 'blue' }, { id: `${v.id}-2`, label: 'Время встречи подтверждено', detail: `${v.meetingDay} октября · ${v.meetingTime}`, tone: 'blue' });
    if (v.stage === 'offer') history.push({ id: `${v.id}-3`, label: 'Встреча состоялась', tone: 'green', kind: 'meeting_completed', day: v.meetingDay, time: v.meetingTime }, { id: `${v.id}-4`, label: 'Предложение о работе', detail: v.salary, tone: 'green' });
    return { ...v, history };
  });
}

function Celebration({ index, ...props }: Omit<CelebrationProps, 'variant'> & { index: number }) {
  const Component = celebrations[Math.floor(index / 2) % celebrations.length];
  return <Component {...props} variant={index % 2}/>;
}

type ProductProps = {
  variant: number; screen: Screen; navigate: (value: Screen) => void; vacancies: Vacancy[];
  profile: Profile; saveProfile: (value: Profile) => void; availability: Availability[];
  setAvailability: (value: Availability[]) => void; selectedId: string; select: (id: string) => void;
  act: (id: string, action: VacancyAction) => void; filter: SearchFilter; setFilter: (value: SearchFilter) => void;
  runId: number; celebrationIndex: number; showPaused: boolean; resume: () => void; history: () => void;
};

function Product(props: ProductProps) {
  const uid = useId();
  const reduced = useReducedMotion();
  const { screen, navigate, profile, variant } = props;
  return <LayoutGroup id={uid}><div className={`cex-product cex-concept-${variant}`}>
    <header className="cex-product-header"><img src={logo} alt="HR Vision"/><span className="cex-header-note">Личный кабинет</span>
      <button className={`cex-profile-trigger ${screen === 'profile' ? 'is-active' : ''}`} aria-label="Открыть профиль" aria-pressed={screen === 'profile'} onClick={() => navigate('profile')}><span>{profile.name.split(' ').map(x => x[0]).slice(0, 2).join('')}</span><UserRound size={17}/></button>
    </header>
    <div className="cex-product-body"><nav className="cex-product-nav" aria-label="Кабинет кандидата">{(['search', 'calendar'] as Screen[]).map(s => {
      const Icon = s === 'search' ? House : CalendarDays;
      return <button key={s} onClick={() => navigate(s)} aria-current={screen === s ? 'page' : undefined}><Icon size={20}/><span>{screenNames[s]}</span>{screen === s && <motion.i layoutId="product-nav" transition={{ type: 'spring', bounce: 0, duration: .28 }}/>}</button>;
    })}<span className="cex-nav-foot">Ваш следующий<br/>хороший шаг.</span></nav>
    <main className="cex-product-content"><AnimatePresence mode="wait" initial={false}><motion.div className="cex-screen" key={screen} initial={{ opacity: 0, y: reduced ? 0 : 7 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: reduced ? 0 : -5 }} transition={{ duration: .16 }}>
      {screen === 'profile' && <button className="cex-back" onClick={() => navigate('search')}><ArrowLeft size={15}/>К поиску</button>}
      {screen === 'search' && !profile.searching && props.showPaused ? <Celebration index={props.celebrationIndex} runId={props.runId} onResume={props.resume} onProcesses={props.history}/>
        : screen === 'search' ? <>{!profile.searching && <div className="cex-paused-ribbon"><span>Поиск на паузе. Все процессы — в истории.</span><button onClick={props.resume}>Возобновить поиск<ArrowRight size={15}/></button></div>}<SearchVariants variant={variant} vacancies={props.vacancies} selectedId={props.selectedId} select={props.select} act={props.act} openCalendar={() => navigate('calendar')} filter={props.filter} setFilter={props.setFilter}/></>
        : screen === 'calendar' ? <CalendarVariants variant={variant} vacancies={props.vacancies} availability={props.availability} setAvailability={props.setAvailability} act={props.act} openVacancy={id => { const job = props.vacancies.find(v => v.id === id); props.select(id); if (job?.archived && !profile.searching) props.history(); else { props.setFilter(job?.archived || ['accepted', 'declined'].includes(job?.stage ?? '') ? 'history' : job?.stage === 'new' ? 'new' : 'process'); navigate('search'); } }}/>
        : <ProfileVariants variant={variant} profile={profile} save={props.saveProfile}/>}
    </motion.div></AnimatePresence></main></div>
  </div></LayoutGroup>;
}

function BoardColumn({ index, open }: { index: number; open: (index: number, screen: Screen) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(index < 2);
  useEffect(() => { const node = ref.current; if (!node) return; const observer = new IntersectionObserver(entries => { if (entries.some(x => x.isIntersecting)) { setVisible(true); observer.disconnect(); } }, { rootMargin: '100px' }); observer.observe(node); return () => observer.disconnect(); }, []);
  return <div className="cex-board-column" ref={ref}><header><b>{ord(index)}</b><h2>{concepts[index].name}</h2><p>{concepts[index].idea}</p></header>{(['search', 'calendar', 'profile'] as Screen[]).map(screen => <article className="cex-board-screen" key={screen}><button className="cex-board-screen-label" onClick={() => open(index, screen)}>{screenNames[screen]}<ArrowRight size={14}/></button><div className="cex-thumbnail"><div className="cex-thumbnail-inner" inert>{visible && <MotionConfig reducedMotion="always"><Product variant={index} screen={screen} navigate={noop} vacancies={seedVacancies()} profile={initialProfile} saveProfile={noop} availability={initialAvailability} setAvailability={noop} selectedId="north" select={noop} act={noop} filter="new" setFilter={noop} runId={0} celebrationIndex={0} showPaused={false} resume={noop} history={noop}/></MotionConfig>}</div></div></article>)}</div>;
}

function readNumber(key: string, fallback: number, max: number) {
  const raw = new URLSearchParams(location.search).get(key);
  if (raw === null) return fallback;
  const value = Number(raw);
  return Number.isInteger(value) && value >= 1 && value <= max ? value - 1 : fallback;
}

export type CandidateExplorationsProps = { initialVariant?: number; initialScreen?: Screen; initialMode?: 'workspace' | 'board' | 'celebrations' };
export function CandidateExplorations({ initialVariant = 0, initialScreen = 'search', initialMode = 'workspace' }: CandidateExplorationsProps) {
  const [variant, setVariant] = useState(() => readNumber('variant', initialVariant, 10));
  const [screen, setScreen] = useState<Screen>(() => { const s = new URLSearchParams(location.search).get('screen'); return s === 'calendar' || s === 'profile' ? s : initialScreen; });
  const [mode, setMode] = useState(initialMode);
  const [phone, setPhone] = useState(false);
  const [vacancies, setVacancies] = useState(seedVacancies);
  const [profile, setProfile] = useState({ ...initialProfile });
  const [availability, setAvailability] = useState(initialAvailability.map(x => ({ ...x })));
  const [selectedId, setSelectedId] = useState('north');
  const [filter, setFilter] = useState<SearchFilter>('new');
  const [runId, setRunId] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [toast, setToast] = useState('');
  const [call, setCall] = useState<Vacancy | null>(null);
  const [inCall, setInCall] = useState(false);
  const [celebrationIndex, setCelebrationIndex] = useState(0);
  const [showPaused, setShowPaused] = useState(true);
  const [sources, setSources] = useState(false);
  const productRef = useRef<HTMLDivElement>(null);
  const sequence = useRef(0);
  const selected = vacancies.find(v => v.id === selectedId) ?? vacancies[0];

  useEffect(() => { if (!toast) return; const timer = setTimeout(() => setToast(''), 3500); return () => clearTimeout(timer); }, [toast]);
  useEffect(() => { const url = new URL(location.href); url.searchParams.set('variant', String(variant + 1)); url.searchParams.set('screen', screen); history.replaceState(null, '', url); }, [variant, screen]);
  function scrollTop() { productRef.current?.scrollTo({ top: 0 }); }
  function navigate(s: Screen) { setPlaying(false); setScreen(s); if (s === 'search') setShowPaused(true); scrollTop(); }
  function mutate(id: string, stage: Vacancy['stage'], label: string, detail?: string, tone: JourneyEvent['tone'] = 'blue') {
    setVacancies(values => values.map(v => v.id === id ? { ...v, stage, history: [...v.history ?? [], { id: `${id}-${Date.now()}-${sequence.current++}`, label, detail, tone, ...(label === 'Встреча состоялась' ? { kind: 'meeting_completed' as const, day: v.meetingDay, time: v.meetingTime } : {}) }] } : v));
  }
  function act(id: string, action: VacancyAction) {
    setPlaying(false);
    const v = vacancies.find(x => x.id === id); if (!v || v.archived) return;
    setSelectedId(id);
    if (action === 'accept' && v.stage === 'new') { mutate(id, 'applied', 'Вы участвуете', 'Ждём предложения времени'); setFilter('process'); setToast('Вы участвуете. Следующий шаг появится здесь.'); }
    if (action === 'confirm' && v.stage === 'proposed') { mutate(id, 'confirmed', 'Время подтверждено', `${v.meetingDay} октября · ${v.meetingTime}`); setToast('Встреча добавлена в календарь'); }
    if (action === 'reschedule' && ['proposed', 'confirmed'].includes(v.stage)) { mutate(id, 'applied', 'Запрошено другое время', 'Участие в вакансии сохранено'); setToast('Вы остаётесь в процессе. Ждём другое время.'); }
    if (action === 'join' && v.stage === 'confirmed') { setCall(v); setInCall(false); }
    if (action === 'continue' && v.stage === 'completed') { mutate(id, 'followup', 'Вы хотите продолжить', 'Ждём ответа компании'); setToast('Ваше решение сохранено'); }
    if (action === 'acceptOffer' && v.stage === 'offer') { mutate(id, 'accepted', 'Оффер принят', 'Дальше компания согласует с вами оформление', 'green'); setFilter('history'); setToast('Оффер принят. Условия сохранены в истории.'); }
    if (action === 'decline') { mutate(id, 'declined', v.stage === 'offer' ? 'Вы отказались от оффера' : 'Вы завершили участие', undefined, 'neutral'); setFilter('history'); setToast('Процесс сохранён в истории'); }
  }
  function advance() {
    setPlaying(false); if (!selected || selected.archived) return;
    if (selected.stage === 'applied') { mutate(selected.id, 'proposed', 'Компания предложила время', `${selected.meetingDay} октября · ${selected.meetingTime}`); setFilter('process'); }
    if (selected.stage === 'confirmed') { mutate(selected.id, 'completed', 'Встреча состоялась', 'Теперь можно решить, хотите ли вы продолжить', 'green'); setCall(null); setFilter('process'); }
    if (selected.stage === 'followup') { mutate(selected.id, 'offer', 'Компания прислала оффер', selected.salary, 'green'); setFilter('process'); }
    setScreen('search');
  }
  function extraMeeting() { if (selected && selected.stage === 'followup') { mutate(selected.id, 'proposed', 'Приглашение на следующую встречу', 'Ещё один разговор с командой'); setFilter('process'); setScreen('search'); } }
  function resume() {
    setProfile(p => ({ ...p, searching: true })); setShowPaused(true);
    setFilter('new'); setScreen('search'); setToast('Поиск возобновлён. Прошлые процессы остались в истории.');
  }
  function newBatch() {
    if (!profile.searching) return;
    const newJobs = initialVacancies.filter(v => v.stage === 'new').map(v => ({ ...v, id: `${v.id}-new-${Date.now()}`, history: [{ id: `new-${v.id}`, label: 'Новое предложение', tone: 'neutral' as const }] }));
    setVacancies(v => [...newJobs, ...v]); setSelectedId(newJobs[0].id); setFilter('new'); setScreen('search'); setShowPaused(true); setToast('В макете появилось два новых предложения');
  }
  function saveProfile(value: Profile) {
    const paused = profile.searching && !value.searching;
    const resumed = !profile.searching && value.searching;
    setProfile(value); setToast('Профиль сохранён');
    if (paused) {
      setVacancies(items => items.map(v => ({ ...v, archived: true, history: [...v.history ?? [], { id: `${v.id}-paused-${Date.now()}`, label: 'Поиск остановлен', detail: 'Процесс сохранён в истории', tone: 'neutral' }] })));
      setFilter('history'); setScreen('search'); setShowPaused(true); setRunId(x => x + 1); setToast('Поиск остановлен. Все процессы сохранены в истории.'); scrollTop();
    }
    if (resumed) resume();
  }
  function showHistory() { setShowPaused(false); setFilter('history'); setScreen('search'); }
  function reset() {
    setPlaying(false); sequence.current = 0; setVacancies(seedVacancies()); setProfile({ ...initialProfile }); setAvailability(initialAvailability.map(x => ({ ...x }))); setSelectedId('north'); setFilter('new'); setScreen(initialScreen); setCall(null); setInCall(false); setToast(''); setShowPaused(true); setRunId(x => x + 1); scrollTop();
  }
  function startDemo() {
    reset(); setScreen('search'); setPlaying(true);
  }
  useEffect(() => {
    if (!playing) return;
    let step = 0;
    const events: { stage: Vacancy['stage']; label: string; tone: JourneyEvent['tone']; filter: SearchFilter }[] = [
      { stage: 'applied', label: 'Вы участвуете', tone: 'blue', filter: 'process' },
      { stage: 'proposed', label: 'Компания предложила время', tone: 'blue', filter: 'process' },
      { stage: 'confirmed', label: 'Время подтверждено', tone: 'blue', filter: 'process' },
      { stage: 'completed', label: 'Встреча состоялась', tone: 'green', filter: 'process' },
      { stage: 'followup', label: 'Вы хотите продолжить', tone: 'blue', filter: 'process' },
      { stage: 'offer', label: 'Компания прислала оффер', tone: 'green', filter: 'process' },
      { stage: 'accepted', label: 'Оффер принят', tone: 'green', filter: 'history' },
    ];
    const timer = setInterval(() => { const event = events[step++]; if (!event) { setPlaying(false); return; } mutate('north', event.stage, event.label, undefined, event.tone); setFilter(event.filter); setSelectedId('north'); if (step === events.length) setPlaying(false); }, 2600);
    return () => clearInterval(timer);
  }, [playing]);

  const productProps: ProductProps = { variant, screen, navigate, vacancies, profile, saveProfile, availability, setAvailability: value => { setAvailability(value); setToast('Доступное время сохранено'); }, selectedId, select: setSelectedId, act, filter, setFilter, runId, celebrationIndex, showPaused, resume, history: showHistory };
  const eventTitle = !selected ? 'Нет процесса' : ({ applied: 'Компания предложила время', confirmed: 'Встреча завершилась', followup: 'Компания прислала оффер' } as Partial<Record<Vacancy['stage'], string>>)[selected.stage];
  function openBoard(index: number, s: Screen) { setVariant(index); setScreen(s); setMode('workspace'); setPlaying(false); scrollTop(); }

  return <MotionConfig reducedMotion="user"><div className="cex-root">
    <header className="cex-lab-header"><div className="cex-lab-brand"><span className="cex-lab-mark"><Columns3 size={20}/></span><div><span>HR Vision · лаборатория</span><h1>Один поиск. Весь путь.</h1></div></div><div className="cex-lab-status">Интерактивные макеты<span>Данные и события — примеры</span></div></header>
    <div className="cex-lab-tools"><div className="cex-lab-modes" role="group" aria-label="Режим лаборатории"><button aria-pressed={mode === 'workspace'} onClick={() => setMode('workspace')}>30 экранов</button><button aria-pressed={mode === 'board'} onClick={() => { setMode('board'); setPlaying(false); }}>Сравнить по столбцам</button><button aria-pressed={mode === 'celebrations'} onClick={() => { setMode('celebrations'); setPlaying(false); }}>Поиск на паузе <span>10</span></button></div><button className="cex-sources-button" onClick={() => setSources(true)}>Референсы<ExternalLink size={14}/></button></div>
    {mode === 'board' ? <section className="cex-board" aria-label="Десять вариантов по столбцам">{concepts.map((_, index) => <BoardColumn key={index} index={index} open={openBoard}/>)}</section>
      : <><div className="cex-workbench">
        <aside className="cex-concepts"><span className="cex-overline">{mode === 'celebrations' ? 'Механика завершения' : '10 подходов'}</span>{(mode === 'celebrations' ? celebrationNames.map(name => ({ name })) : concepts).map((v, index) => <button key={v.name} aria-pressed={(mode === 'celebrations' ? celebrationIndex : variant) === index} onClick={() => { setPlaying(false); if (mode === 'celebrations') { setCelebrationIndex(index); setRunId(x => x + 1); } else setVariant(index); }}><span>{ord(index)}</span><strong>{v.name}</strong><ArrowRight size={14}/></button>)}<p className="cex-concepts-note">{mode === 'celebrations' ? 'Пять направлений, по две механики. Короткая реакция вместо бесконечного движения.' : 'В каждом варианте — поиск, календарь и профиль. Переключение сохраняет ваш прогресс.'}</p></aside>
        <section className="cex-stage"><div className="cex-stage-toolbar"><div className="cex-screen-tabs" role="group" aria-label="Экраны макета">{mode === 'workspace' ? (['search', 'calendar', 'profile'] as Screen[]).map(s => <button key={s} aria-pressed={screen === s} onClick={() => navigate(s)}>{screenNames[s]}</button>) : <strong>{celebrationNames[celebrationIndex]}</strong>}</div><div className="cex-device"><button aria-pressed={!phone} aria-label="Версия для компьютера" onClick={() => setPhone(false)}><Monitor size={17}/></button><button aria-pressed={phone} aria-label="Версия для телефона" onClick={() => setPhone(true)}><Smartphone size={17}/></button><button aria-label={mode === 'celebrations' ? 'Повторить анимацию' : 'Сбросить сценарий'} onClick={() => mode === 'celebrations' ? setRunId(x => x + 1) : reset()}><RotateCcw size={16}/></button></div></div>
          <div className={`cex-device-stage ${phone ? 'is-phone' : ''}`}><div className="cex-viewport" ref={productRef} onPointerDownCapture={() => setPlaying(false)}>
            {mode === 'celebrations' ? <div className="cex-product cex-celebration-preview"><header className="cex-product-header"><img src={logo} alt="HR Vision"/><span className="cex-profile-trigger"><UserRound size={19}/></span></header><Celebration index={celebrationIndex} runId={runId} onResume={() => { setMode('workspace'); setVacancies(items => items.map(v => ({ ...v, archived: true }))); resume(); }} onProcesses={() => { setMode('workspace'); setProfile(p => ({ ...p, searching: false })); setVacancies(items => items.map(v => ({ ...v, archived: true }))); showHistory(); }}/></div>
              : <Product key={runId} {...productProps}/>}
          </div></div>
          <div className="cex-design-caption"><span>{mode === 'workspace' ? concepts[variant].motion : 'Анимацию можно повторить. Reduced motion использует спокойное появление.'}</span><span>{phone ? 'Телефон · 390 px' : 'Компьютер'}</span></div>
          {mode === 'workspace' && <div className="cex-simulator"><div><span className="cex-overline">События в макете</span><p>{playing ? 'Автопоказ. Любое действие остановит воспроизведение.' : selected ? `${selected.company} · ${stageLabels[selected.stage]}` : 'Пустое состояние поиска'}</p></div><div className="cex-simulator-actions"><button onClick={() => playing ? setPlaying(false) : startDemo()}>{playing ? <Pause size={15}/> : <Play size={15}/>} {playing ? 'Остановить' : 'Пройти автоматически'}</button><button className="cex-event-button" disabled={!eventTitle || !!selected?.archived} onClick={advance}>{eventTitle ?? 'Следующий шаг — ваше решение'}<ArrowRight size={15}/></button>{selected?.stage === 'followup' && <button onClick={extraMeeting}>Ещё одна встреча</button>}<button disabled={!profile.searching} onClick={newBatch}>Появились новые предложения</button><button className="cex-empty-button" onClick={() => { setPlaying(false); setVacancies([]); setScreen('search'); }}>Нет предложений</button></div></div>}
        </section>
      </div></>}
    <footer className="cex-lab-footer"><span>Сначала действие человека, затем ответ компании. Новые события добавляют историю.</span><span>Никакие решения и сообщения из макета не отправляются.</span></footer>
    <AnimatePresence>{toast && <motion.div className="cex-toast" role="status" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 6 }}><Check size={16}/>{toast}</motion.div>}</AnimatePresence>
    <Dialog.Root open={!!call} onOpenChange={open => { if (!open) setCall(null); }}><Dialog.Portal><Dialog.Overlay className="cex-overlay"/><Dialog.Content className="cex-dialog"><Dialog.Title>{inCall ? 'Вы во встрече' : 'Встреча с командой'}</Dialog.Title><Dialog.Description>{call?.company} · {call?.role}</Dialog.Description><Dialog.Close className="cex-dialog-close" aria-label="Закрыть"><X size={19}/></Dialog.Close><div className="cex-video-placeholder"><span>АМ</span><p>{inCall ? 'Анна Миронова' : 'Камера и микрофон не запрашиваются в макете'}</p></div><p className="cex-dialog-note">Выход из звонка не завершает встречу. В лаборатории её завершение задаётся кнопкой «Встреча завершилась».</p><button className="cex-primary" onClick={() => inCall ? setCall(null) : setInCall(true)}><Video size={17}/>{inCall ? 'Выйти из встречи' : 'Войти во встречу'}</button></Dialog.Content></Dialog.Portal></Dialog.Root>
    <Dialog.Root open={sources} onOpenChange={setSources}><Dialog.Portal><Dialog.Overlay className="cex-overlay"/><Dialog.Content className="cex-dialog cex-source-dialog"><Dialog.Title>На что опирались</Dialog.Title><Dialog.Description>Референсы взаимодействия и официальная документация. Это наши варианты HR Vision, а не копии чужих продуктов.</Dialog.Description><Dialog.Close className="cex-dialog-close" aria-label="Закрыть"><X size={19}/></Dialog.Close><div className="cex-source-list">{references.map(ref => <a key={ref.url} href={ref.url} target="_blank" rel="noreferrer"><strong>{ref.name}<ExternalLink size={15}/></strong><p>{ref.pattern}</p></a>)}<a href="https://motion.dev/docs/react-layout-animations" target="_blank" rel="noreferrer"><strong>Motion for React · Context7<ExternalLink size={15}/></strong><p>LayoutGroup, AnimatePresence и useReducedMotion. Пружины, связанные переходы и адаптация для reduced motion.</p></a></div></Dialog.Content></Dialog.Portal></Dialog.Root>
  </div></MotionConfig>;
}
